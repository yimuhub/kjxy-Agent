"""DeckPlan：可质检、可渲染、可降级的中文演示计划。

为什么不再直接用 DocModel 出 PPT：DocModel 只有「段落/列表/表格」，无法表达
一页一个结论、KPI 卡片、真实图表、时间轴这些汇报语言，渲染器也没有版式选择权。
DeckPlan 把「讲什么 + 用什么版式」显式建模，好处是：

- **可校验**：每页只允许一种内容版式（避免一页塞满要点又配图表），
  图表类别与系列等长，表格 6 列 8 行封顶，未知字段一律拒绝；
- **可降级**：模型不可用时从 DocModel 确定性推导出封面/目录/章节/要点/结尾，
  不生成空白 PPT；素材确实撑不起一页结论时给 ``DECK_PLAN_TOO_THIN``，
  而不是靠灌水页凑数；
- **可质检**：``deck_qa`` 直接读这份模型 + 渲染后的几何信息，问题能定位到页。

字段口径见 ``DeckSlide``；``parse_deck_plan`` 是模型输出的唯一入口。
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.core.errors import AppError
from app.modules.docs.model import DocBlock, DocModel

SlideType = Literal[
    "cover",
    "agenda",
    "section",
    "statement",
    "bullets",
    "two_column",
    "kpi",
    "chart",
    "table",
    "timeline",
    "process",
    "comparison",
    "quote",
    "image",
    "closing",
]

SLIDE_TYPES: tuple[str, ...] = (
    "cover",
    "agenda",
    "section",
    "statement",
    "bullets",
    "two_column",
    "kpi",
    "chart",
    "table",
    "timeline",
    "process",
    "comparison",
    "quote",
    "image",
    "closing",
)

MIN_SLIDES = 5
MAX_SLIDES = 30
MAX_TITLE = 60
MAX_KEY_MESSAGE = 120
MAX_BULLETS = 6
MAX_BULLET_CHARS = 100
MAX_TABLE_COLS = 6
MAX_TABLE_ROWS = 8

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


def _trim(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _non_empty(value: str, field: str) -> str:
    if not str(value or "").strip():
        raise ValueError(f"{field} 不能为空")
    return str(value).strip()


def _sentences(text: str) -> list[str]:
    """按中文标点切句，保留标点；用于把长段落拆成「一句一个要点」。"""
    parts = re.split(r"(?<=[。！？!?；;])", str(text or ""))
    return [p.strip() for p in parts if p.strip()]


class DeckColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=30)
    items: list[str] = Field(min_length=1, max_length=5)

    @field_validator("items")
    @classmethod
    def _items_ok(cls, value: list[str]) -> list[str]:
        out = [_non_empty(item, "column.items") for item in value]
        if any(len(item) > 60 for item in out):
            raise ValueError("column.items 每条不超过 60 字")
        return out


class DeckKpi(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=12)
    unit: str = Field(default="", max_length=8)
    label: str = Field(min_length=1, max_length=40)


class DeckChartSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=30)
    values: list[float] = Field(default_factory=list, max_length=12)


class DeckChart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bar", "line", "pie"] = "bar"
    categories: list[str] = Field(default_factory=list, max_length=12)
    series: list[DeckChartSeries] = Field(default_factory=list, max_length=3)
    unit: str = Field(default="", max_length=8)
    source: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def _series_aligned(self) -> DeckChart:
        for series in self.series:
            if len(series.values) != len(self.categories):
                raise ValueError(
                    f"图表系列「{series.name}」有 {len(series.values)} 个值，"
                    f"但类别有 {len(self.categories)} 个，两者必须一致"
                )
        if self.type == "pie" and len(self.series) > 1:
            raise ValueError("饼图只支持一个系列")
        return self

    @property
    def is_empty(self) -> bool:
        return not self.categories or not self.series


class DeckTable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    columns: list[str] = Field(min_length=1, max_length=MAX_TABLE_COLS)
    rows: list[list[str]] = Field(default_factory=list, max_length=MAX_TABLE_ROWS)
    source: str = Field(default="", max_length=100)

    @model_validator(mode="after")
    def _rows_aligned(self) -> DeckTable:
        for index, row in enumerate(self.rows):
            if len(row) != len(self.columns):
                raise ValueError(
                    f"表格第 {index + 1} 行有 {len(row)} 个单元格，"
                    f"与表头 {len(self.columns)} 列不一致"
                )
        return self


class DeckTimelineItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: str = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=30)
    text: str = Field(default="", max_length=60)


class DeckProcessItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str = Field(default="", max_length=8)
    title: str = Field(min_length=1, max_length=20)
    text: str = Field(default="", max_length=60)


class DeckComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    left_title: str = Field(min_length=1, max_length=20)
    left_items: list[str] = Field(min_length=1, max_length=5)
    right_title: str = Field(min_length=1, max_length=20)
    right_items: list[str] = Field(min_length=1, max_length=5)

    @field_validator("left_items", "right_items")
    @classmethod
    def _compare_items(cls, value: list[str]) -> list[str]:
        out = [_non_empty(item, "comparison.items") for item in value]
        if any(len(item) > 60 for item in out):
            raise ValueError("comparison.items 每条不超过 60 字")
        return out


class DeckImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slot: str = Field(min_length=1, max_length=40)
    prompt: str = Field(default="", max_length=300)
    alt: str = Field(default="", max_length=100)


#: 版式 → 该版式必须携带的内容字段
_REQUIRED_CONTENT: dict[str, tuple[str, ...]] = {
    "cover": (),
    "agenda": ("bullets",),
    "section": (),
    "statement": (),
    "bullets": ("bullets",),
    "two_column": ("columns",),
    "kpi": ("kpis",),
    "chart": ("chart",),
    "table": ("table",),
    "timeline": ("timeline",),
    "process": ("process",),
    "comparison": ("comparison",),
    "quote": (),
    "image": ("image",),
    "closing": (),
}

#: 承载「一页内容」的字段：同一页只允许出现其中一种
_CONTENT_FIELDS: tuple[str, ...] = (
    "bullets",
    "columns",
    "kpis",
    "chart",
    "table",
    "timeline",
    "process",
    "comparison",
    "image",
)


class DeckSlide(BaseModel):
    """一页 = 一句核心信息 + 一种承载方式。"""

    model_config = ConfigDict(extra="forbid")

    type: SlideType
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    key_message: str = Field(default="", max_length=MAX_KEY_MESSAGE)
    bullets: list[str] = Field(default_factory=list, max_length=MAX_BULLETS)
    columns: list[DeckColumn] = Field(default_factory=list, max_length=2)
    kpis: list[DeckKpi] = Field(default_factory=list, max_length=4)
    chart: DeckChart | None = None
    table: DeckTable | None = None
    timeline: list[DeckTimelineItem] = Field(default_factory=list, max_length=6)
    process: list[DeckProcessItem] = Field(default_factory=list, max_length=6)
    comparison: DeckComparison | None = None
    image: DeckImage | None = None
    notes: str = Field(default="", max_length=600)
    source: str = Field(default="", max_length=120)

    @field_validator("title")
    @classmethod
    def _title_ok(cls, value: str) -> str:
        return _non_empty(value, "title")

    @field_validator("bullets")
    @classmethod
    def _bullets_ok(cls, value: list[str]) -> list[str]:
        out = [_non_empty(item, "bullets") for item in value]
        if any(len(item) > MAX_BULLET_CHARS for item in out):
            raise ValueError(f"bullets 每条不超过 {MAX_BULLET_CHARS} 字")
        return out

    @model_validator(mode="after")
    def _one_core_message(self) -> DeckSlide:
        filled = [name for name in _CONTENT_FIELDS if getattr(self, name)]
        if len(filled) > 1:
            raise ValueError(
                "每页只允许一种内容版式，当前同时出现：" + "、".join(filled) + "（请拆页）"
            )
        required = _REQUIRED_CONTENT[self.type]
        missing = [name for name in required if name not in filled]
        if missing:
            raise ValueError(f"{self.type} 版式缺少内容字段：" + "、".join(missing))
        if self.type in ("statement", "quote") and not self.key_message.strip():
            raise ValueError(f"{self.type} 版式需要 key_message（本页的核心观点）")
        if self.type == "chart" and self.chart is not None and self.chart.is_empty:
            raise ValueError("chart 版式需要 categories 与 series 数据")
        if self.type == "table" and self.table is not None and not self.table.rows:
            raise ValueError("table 版式至少需要 1 行数据")
        return self


class DeckPlan(BaseModel):
    """一份演示计划：主题 + 定位 + 叙事线 + 5-30 页。"""

    model_config = ConfigDict(extra="forbid")

    theme: str = Field(default="tech_blue", max_length=40)
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    subtitle: str = Field(default="", max_length=MAX_KEY_MESSAGE)
    audience: str = Field(default="", max_length=80)
    storyline: str = Field(default="", max_length=500)
    slides: list[DeckSlide] = Field(min_length=MIN_SLIDES, max_length=MAX_SLIDES)

    @field_validator("title")
    @classmethod
    def _title_ok(cls, value: str) -> str:
        return _non_empty(value, "title")

    @model_validator(mode="after")
    def _cover_first(self) -> DeckPlan:
        if self.slides and self.slides[0].type != "cover":
            raise ValueError("第 1 页应为 cover（封面）")
        return self


def deck_plan_json_schema() -> dict[str, Any]:
    """给提示词/调试用的 JSON Schema（不含 pydantic 的 $defs 引用展开）。"""
    return DeckPlan.model_json_schema()


def _extract_payload(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    fenced = _JSON_FENCE_RE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    match = _JSON_OBJECT_RE.search(text)
    if not match:
        raise AppError(
            "DECK_PLAN_INVALID",
            "演示计划解析失败：模型没有返回 JSON 对象。",
            http_status=422,
            details={"errors": [{"loc": "json", "message": "未找到 JSON 对象"}]},
        )
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise AppError(
            "DECK_PLAN_INVALID",
            "演示计划解析失败：JSON 语法有误。",
            http_status=422,
            details={"errors": [{"loc": "json", "message": e.msg, "position": e.pos}]},
        ) from e
    if not isinstance(payload, dict):
        raise AppError(
            "DECK_PLAN_INVALID",
            "演示计划必须是一个 JSON 对象。",
            http_status=422,
            details={"errors": [{"loc": "json", "message": "顶层不是对象"}]},
        )
    return payload


def _validation_details(error: ValidationError) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for item in error.errors(include_url=False):
        details.append(
            {
                "loc": ".".join(str(part) for part in item.get("loc", ())) or "root",
                "message": str(item.get("msg", "")),
            }
        )
    return details


def deck_plan_payload(raw: str | dict[str, Any]) -> dict[str, Any]:
    """把模型输出（文本或字典）解成待校验的 payload；只做 JSON 提取，不做 schema 校验。

    拆出来供对话链路在严格校验前先做形状修复（``repair_deck_plan``），
    避免「字段形状不对」被当成「内容不合法」直接降级成模板。
    """
    payload = _extract_payload(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, dict):
        raise AppError(
            "DECK_PLAN_INVALID",
            "演示计划必须是一个 JSON 对象。",
            http_status=422,
            details={"errors": [{"loc": "json", "message": "顶层不是对象"}]},
        )
    return payload


def parse_deck_plan(raw: str | dict[str, Any]) -> DeckPlan:
    """解析模型输出的 DeckPlan（字符串或 dict）；不合法统一抛 422。"""
    payload = deck_plan_payload(raw)
    try:
        return DeckPlan.model_validate(payload)
    except ValidationError as e:
        errors = _validation_details(e)
        raise AppError(
            "DECK_PLAN_INVALID",
            "演示计划不合法，请检查每页标题、要点与图表数据。",
            http_status=422,
            details={"errors": errors[:5]},
        ) from e


# --------------------------------------------------------------------------- #
# 形状修复：只修「字段形状」，不放松硬约束
# --------------------------------------------------------------------------- #

#: 计划级文本字段（数字会被转成字符串）
_PLAN_TEXT_FIELDS: tuple[str, ...] = ("theme", "title", "subtitle", "audience", "storyline")
#: 页面级文本字段
_SLIDE_TEXT_FIELDS: tuple[str, ...] = ("title", "key_message", "notes", "source")
#: 页面级字符串列表字段
_SLIDE_TEXT_LIST_FIELDS: tuple[str, ...] = ("bullets",)
#: 页面级对象列表字段（columns/kpis/timeline/process 的元素是对象）
_SLIDE_OBJECT_LIST_FIELDS: tuple[str, ...] = ("columns", "kpis", "timeline", "process")


def _repair_text(value: Any, path: str, fields: list[str]) -> Any:
    """文本字段：数字转字符串、null 归空串。

    null 归空串不会放松「必填标题」：空标题在严格校验里仍会失败并触发降级。
    """
    if value is None:
        fields.append(path)
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        fields.append(path)
        return str(value)
    return value


def _repair_list(value: Any, path: str, fields: list[str]) -> list[Any]:
    """列表字段：null→[]、字符串→[字符串]、对象→[对象]，其余标量包成单元素列表。"""
    if isinstance(value, list):
        return value
    fields.append(path)
    if value is None:
        return []
    return [value]


def _repair_text_list(value: Any, path: str, fields: list[str]) -> list[Any]:
    """字符串列表：先修列表形状，再逐个把数字转成字符串。"""
    items = _repair_list(value, path, fields)
    return [_repair_text(item, f"{path}[{index}]", fields) for index, item in enumerate(items)]


def _repair_rows(value: Any, path: str, fields: list[str]) -> list[Any]:
    """表格行：外层按列表修形状，每行再按字符串列表修一遍。"""
    rows = _repair_list(value, path, fields)
    return [_repair_text_list(row, f"{path}[{index}]", fields) for index, row in enumerate(rows)]


def _repair_column(raw: Any, path: str, fields: list[str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    item = dict(raw)
    if "title" in item:
        item["title"] = _repair_text(item["title"], f"{path}.title", fields)
    if "items" in item:
        item["items"] = _repair_text_list(item["items"], f"{path}.items", fields)
    return item


def _repair_kpi(raw: Any, path: str, fields: list[str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    item = dict(raw)
    for key in ("value", "unit", "label"):
        if key in item:
            item[key] = _repair_text(item[key], f"{path}.{key}", fields)
    return item


def _repair_timeline_item(raw: Any, path: str, fields: list[str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    item = dict(raw)
    for key in ("time", "title", "text"):
        if key in item:
            item[key] = _repair_text(item[key], f"{path}.{key}", fields)
    return item


def _repair_process_item(raw: Any, path: str, fields: list[str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    item = dict(raw)
    for key in ("step", "title", "text"):
        if key in item:
            item[key] = _repair_text(item[key], f"{path}.{key}", fields)
    return item


def _repair_chart(raw: Any, path: str, fields: list[str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    item = dict(raw)
    if "categories" in item:
        item["categories"] = _repair_text_list(item["categories"], f"{path}.categories", fields)
    if "series" in item:
        series = _repair_list(item["series"], f"{path}.series", fields)
        repaired_series: list[Any] = []
        for index, entry in enumerate(series):
            series_path = f"{path}.series[{index}]"
            if not isinstance(entry, dict):
                repaired_series.append(entry)
                continue
            one = dict(entry)
            if "name" in one:
                one["name"] = _repair_text(one["name"], f"{series_path}.name", fields)
            if "values" in one:
                one["values"] = _repair_list(one["values"], f"{series_path}.values", fields)
            repaired_series.append(one)
        item["series"] = repaired_series
    for key in ("unit", "source"):
        if key in item:
            item[key] = _repair_text(item[key], f"{path}.{key}", fields)
    return item


def _repair_table(raw: Any, path: str, fields: list[str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    item = dict(raw)
    if "columns" in item:
        item["columns"] = _repair_text_list(item["columns"], f"{path}.columns", fields)
    if "rows" in item:
        item["rows"] = _repair_rows(item["rows"], f"{path}.rows", fields)
    if "source" in item:
        item["source"] = _repair_text(item["source"], f"{path}.source", fields)
    return item


def _repair_comparison(raw: Any, path: str, fields: list[str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    item = dict(raw)
    for key in ("left_title", "right_title"):
        if key in item:
            item[key] = _repair_text(item[key], f"{path}.{key}", fields)
    for key in ("left_items", "right_items"):
        if key in item:
            item[key] = _repair_text_list(item[key], f"{path}.{key}", fields)
    return item


def _repair_image(raw: Any, path: str, fields: list[str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    item = dict(raw)
    for key in ("slot", "prompt", "alt"):
        if key in item:
            item[key] = _repair_text(item[key], f"{path}.{key}", fields)
    return item


def _repair_slide(raw: Any, path: str, fields: list[str]) -> Any:
    if not isinstance(raw, dict):
        return raw
    slide = dict(raw)
    for key in _SLIDE_TEXT_FIELDS:
        if key in slide:
            slide[key] = _repair_text(slide[key], f"{path}.{key}", fields)
    for key in _SLIDE_TEXT_LIST_FIELDS:
        if key in slide:
            slide[key] = _repair_text_list(slide[key], f"{path}.{key}", fields)
    for key in _SLIDE_OBJECT_LIST_FIELDS:
        if key not in slide:
            continue
        items = _repair_list(slide[key], f"{path}.{key}", fields)
        if key == "columns":
            slide[key] = [
                _repair_column(item, f"{path}.{key}[{index}]", fields)
                for index, item in enumerate(items)
            ]
        elif key == "kpis":
            slide[key] = [
                _repair_kpi(item, f"{path}.{key}[{index}]", fields)
                for index, item in enumerate(items)
            ]
        elif key == "timeline":
            slide[key] = [
                _repair_timeline_item(item, f"{path}.{key}[{index}]", fields)
                for index, item in enumerate(items)
            ]
        else:
            slide[key] = [
                _repair_process_item(item, f"{path}.{key}[{index}]", fields)
                for index, item in enumerate(items)
            ]
    if "chart" in slide:
        slide["chart"] = _repair_chart(slide["chart"], f"{path}.chart", fields)
    if "table" in slide:
        slide["table"] = _repair_table(slide["table"], f"{path}.table", fields)
    if "comparison" in slide:
        slide["comparison"] = _repair_comparison(slide["comparison"], f"{path}.comparison", fields)
    if "image" in slide:
        slide["image"] = _repair_image(slide["image"], f"{path}.image", fields)
    return slide


def repair_deck_plan(raw: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """在严格校验前修复模型输出的「字段形状」，返回 (修复后的计划, 修复字段路径)。

    只做低风险归一，避免把「能用的计划」误判成非法：

    - 约定为列表的字段（bullets/columns/kpis/timeline/process 及其嵌套的
      categories/series/values/rows/items 等）：null→[]、字符串→[字符串]、
      对象→[对象]；
    - 文本字段（标题/要点/备注/数值标签等）收到数字时转成字符串，null 归空串。

    刻意**不**做：截断页数、改写版式枚举、补默认标题、丢弃未知字段、修正表格
    行列对齐。页数上限、版式枚举、必填标题、每页一种版式等仍是硬约束，修复后
    仍不合法就照旧降级。
    """
    plan = copy.deepcopy(raw) if isinstance(raw, dict) else {}
    fields: list[str] = []
    for key in _PLAN_TEXT_FIELDS:
        if key in plan:
            plan[key] = _repair_text(plan[key], key, fields)
    if "slides" in plan:
        slides = _repair_list(plan["slides"], "slides", fields)
        plan["slides"] = [
            _repair_slide(slide, f"slides.{index}", fields) for index, slide in enumerate(slides)
        ]
    return plan, fields


# --------------------------------------------------------------------------- #
# DocModel → DeckPlan 确定性降级
# --------------------------------------------------------------------------- #

_THEME_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("党建", "政务", "政府", "党委", "支部", "政策", "公文"), "gov_red"),
    (("农业", "种植", "养殖", "合作社", "农田", "灌溉", "农机", "农技", "小院"), "agri_green"),
    (("财务", "经营", "商务", "投资", "融资", "市场", "销售", "品牌"), "business_navy"),
    (("学术", "论文", "研究", "课题", "实验", "答辩"), "academic"),
)


def guess_theme(text: str, fallback: str = "tech_blue") -> str:
    blob = (text or "").lower()
    for words, name in _THEME_HINTS:
        if any(word in blob for word in words):
            return name
    return fallback


def _cover(model: DocModel, theme: str) -> DeckSlide:
    subtitle = model.subject or model.author
    return DeckSlide(
        type="cover",
        title=_trim(model.title, MAX_TITLE),
        key_message=_trim(subtitle, MAX_KEY_MESSAGE),
        notes=f"{model.title}｜汇报人：{model.author}" if model.author else model.title,
    )


def _closing() -> DeckSlide:
    return DeckSlide(
        type="closing",
        title="谢谢",
        key_message="欢迎提问与交流",
        notes="结束页：留出问答时间，必要时回到关键结论页。",
    )


def _agenda(sections: list[str]) -> DeckSlide:
    return DeckSlide(
        type="agenda",
        title="目录",
        bullets=[_trim(item, MAX_BULLET_CHARS) for item in sections[:MAX_BULLETS]],
        notes="先给听众一张地图，再进入正文。",
    )


def _bullets_slides(title: str, items: list[str], per_slide: int) -> list[DeckSlide]:
    clean = [_trim(item, MAX_BULLET_CHARS) for item in items if str(item).strip()]
    chunks = [clean[i : i + per_slide] for i in range(0, len(clean), per_slide)]
    out: list[DeckSlide] = []
    for index, chunk in enumerate(chunks):
        out.append(
            DeckSlide(
                type="bullets",
                title=_trim(title if index == 0 else f"{title}（续）", MAX_TITLE),
                bullets=chunk,
                notes="；".join(chunk),
            )
        )
    return out


def _statement_slides(title: str, text: str) -> list[DeckSlide]:
    sentences = _sentences(text)
    first = sentences[0] if sentences else text
    rest = "".join(sentences[1:]) if len(sentences) > 1 else ""
    slides = [
        DeckSlide(
            type="statement",
            title=_trim(title, MAX_TITLE),
            key_message=_trim(first, MAX_KEY_MESSAGE),
            notes=_trim(text, 600),
        )
    ]
    if len(rest) > MAX_KEY_MESSAGE:
        slides.append(
            DeckSlide(
                type="statement",
                title=_trim(f"{title}（续）", MAX_TITLE),
                key_message=_trim(rest, MAX_KEY_MESSAGE),
                notes=_trim(text, 600),
            )
        )
    return slides


def _table_slides(title: str, block: DocBlock) -> list[DeckSlide]:
    columns = [_trim(col, 30) for col in block.header[:MAX_TABLE_COLS]]
    rows = [[_trim(cell, 60) for cell in row[: len(columns)]] for row in block.rows]
    rows = [row for row in rows if len(row) == len(columns)]
    chunks = [rows[i : i + MAX_TABLE_ROWS] for i in range(0, len(rows), MAX_TABLE_ROWS)] or [[]]
    out: list[DeckSlide] = []
    for index, chunk in enumerate(chunks):
        if not chunk:
            continue
        out.append(
            DeckSlide(
                type="table",
                title=_trim(title if index == 0 else f"{title}（续）", MAX_TITLE),
                table=DeckTable(columns=columns, rows=chunk),
                notes=f"{title}：{len(chunk)} 行数据，原始列 {len(block.header)} 列。",
            )
        )
    return out


def _build_slides(model: DocModel, theme: str, per_slide: int) -> list[DeckSlide]:
    body: list[DeckSlide] = []
    sections: list[str] = []
    current = _trim(model.title, MAX_TITLE)
    for block in model.blocks:
        if block.type == "heading":
            current = _trim(block.text, MAX_TITLE)
            sections.append(current)
            body.append(DeckSlide(type="section", title=current, notes=current))
        elif block.type in ("bullets", "numbered"):
            body.extend(_bullets_slides(current, block.items, per_slide))
        elif block.type in ("paragraph", "code"):
            body.extend(_statement_slides(current, block.text))
        elif block.type == "table":
            body.extend(_table_slides(current, block))
        # pagebreak：演示文稿分页由版式决定，降级时忽略

    if not body:
        raise AppError(
            "DECK_PLAN_EMPTY",
            "文档里没有可用于演示的内容，请补充要点后再生成 PPT。",
            http_status=422,
            details={"title": model.title},
        )

    slides = [_cover(model, theme)]
    if sections:
        slides.append(_agenda(list(dict.fromkeys(sections))))
    slides.extend(body)
    slides.append(_closing())
    return slides


def plan_from_doc_model(model: DocModel, *, allow_short: bool = False) -> DeckPlan:
    """确定性降级：DocModel → DeckPlan（每块落到最贴合的版式）。

    - heading → section，其后的 bullets/paragraph/table 归到该章节；
    - 首块 → cover，末页 → closing；
    - 要点按 5 条/页切分，页数不足时改按 3 条/页重排（不灌水、不编造）。

    ``allow_short``：仅给「旧接口必须出文件」的渲染兜底用——素材确实撑不起
    5 页时允许生成 2-4 页的短稿；模型生成链路仍按 5 页下限拒绝，
    避免拿空白页凑数。
    """
    theme = guess_theme(f"{model.title} {model.subject}")
    slides = _build_slides(model, theme, per_slide=5)
    if len(slides) < MIN_SLIDES:
        slides = _build_slides(model, theme, per_slide=3)
    if len(slides) > MAX_SLIDES:
        slides = slides[: MAX_SLIDES - 1] + [_closing()]
    fields: dict[str, Any] = {
        "theme": theme,
        "title": _trim(model.title, MAX_TITLE),
        "subtitle": _trim(model.subject, MAX_KEY_MESSAGE),
        "audience": _trim(model.author, 80),
        "storyline": "；".join(dict.fromkeys(slide.title for slide in slides[1:-1]))[:500],
        "slides": slides,
    }
    if len(slides) < MIN_SLIDES:
        if not allow_short:
            raise AppError(
                "DECK_PLAN_TOO_THIN",
                f"素材只够 {len(slides)} 页，无法支撑一份演示文稿，请补充章节与要点。",
                http_status=422,
                details={"slides": len(slides), "min_slides": MIN_SLIDES},
            )
        return DeckPlan.model_construct(**fields)
    return DeckPlan(**fields)


# --------------------------------------------------------------------------- #
# DeckPlan → DocModel（只用于在线预览与快照，不参与 PPT 渲染）
# --------------------------------------------------------------------------- #


def _slide_texts(slide: DeckSlide) -> list[str]:
    if slide.bullets:
        return list(slide.bullets)
    if slide.columns:
        return [f"{col.title}：{'、'.join(col.items)}" for col in slide.columns]
    if slide.kpis:
        return [f"{kpi.value}{kpi.unit}｜{kpi.label}" for kpi in slide.kpis]
    if slide.chart and not slide.chart.is_empty:
        series = slide.chart.series[0]
        pairs = [
            f"{cat} {val}" for cat, val in zip(slide.chart.categories, series.values, strict=False)
        ]
        return [f"图表（{slide.chart.type}）：" + "、".join(pairs)]
    if slide.timeline:
        return [f"{item.time}｜{item.title} {item.text}".strip() for item in slide.timeline]
    if slide.process:
        return [
            f"{item.step or '·'} {item.title}：{item.text}".strip("：") for item in slide.process
        ]
    if slide.comparison:
        left = "、".join(slide.comparison.left_items)
        right = "、".join(slide.comparison.right_items)
        return [
            f"{slide.comparison.left_title}：{left}",
            f"{slide.comparison.right_title}：{right}",
        ]
    if slide.image:
        return [f"图片位：{slide.image.slot}（{slide.image.alt or slide.image.prompt}）"]
    return []


def doc_model_from_plan(plan: DeckPlan) -> DocModel:
    """把演示计划压成文本版 DocModel（预览/下载快照用，保持可读）。"""
    blocks: list[DocBlock] = []
    for slide in plan.slides:
        blocks.append(DocBlock(type="heading", level=2, text=slide.title))
        if slide.key_message:
            blocks.append(DocBlock(type="paragraph", text=slide.key_message))
        if slide.table is not None:
            blocks.append(
                DocBlock(
                    type="table",
                    header=list(slide.table.columns),
                    rows=[list(row) for row in slide.table.rows],
                )
            )
        texts = _slide_texts(slide)
        if texts:
            blocks.append(DocBlock(type="bullets", items=texts[:MAX_BULLETS]))
    if not blocks:  # pragma: no cover - DeckPlan 至少 5 页，理论不可达
        raise AppError("DECK_PLAN_EMPTY", "演示计划没有内容。", http_status=422)
    return DocModel(
        title=plan.title,
        subject=plan.subtitle,
        author=plan.audience,
        blocks=blocks[:300],
    )


__all__ = [
    "MAX_BULLETS",
    "MAX_KEY_MESSAGE",
    "MAX_SLIDES",
    "MAX_TABLE_COLS",
    "MAX_TABLE_ROWS",
    "MAX_TITLE",
    "MIN_SLIDES",
    "SLIDE_TYPES",
    "DeckChart",
    "DeckChartSeries",
    "DeckColumn",
    "DeckComparison",
    "DeckImage",
    "DeckKpi",
    "DeckPlan",
    "DeckProcessItem",
    "DeckSlide",
    "DeckTable",
    "DeckTimelineItem",
    "deck_plan_json_schema",
    "deck_plan_payload",
    "doc_model_from_plan",
    "guess_theme",
    "parse_deck_plan",
    "plan_from_doc_model",
    "repair_deck_plan",
]
