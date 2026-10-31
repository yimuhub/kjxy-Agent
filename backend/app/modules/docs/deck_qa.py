"""演示质检（DeckQA）：结构可量化，视觉不冒充。

两层口径，绝不混为一谈：

1. **结构质检**（始终执行、确定性）：形状越界、文本框重叠、文字溢出、字号过小、
   标题过长、要点过多、图表空数据、表格过密、配色对比度、重复标题、页数异常。
   问题定位到页，返回 ``DeckQAReport``，可被 ``repair_deck_plan`` 确定性地修 2 轮。
2. **视觉质检**（可选）：只检测本机是否存在 ``soffice``，有则把 pptx 转成图片
   供人工复核；没有就明确返回 ``skipped`` 并说明原因——**跳过不等于通过**。

注意：结构质检读的是渲染后的真实几何（``build_presentation``），不是「按模板猜」。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.docs import render_pptx
from app.modules.docs.deck_plan import (
    MAX_BULLETS,
    MAX_KEY_MESSAGE,
    MAX_SLIDES,
    MAX_TABLE_COLS,
    MAX_TABLE_ROWS,
    MAX_TITLE,
    MIN_SLIDES,
    DeckPlan,
    DeckSlide,
)
from app.modules.docs.deck_theme import THEME_NAMES, contrast_ratio, get_theme

EMU = 914400.0
#: 版心允许的估算误差（文字高度估算天然保守，留 15% 余量避免误报）
OVERFLOW_TOLERANCE = 1.15
#: 两个文本框相交面积超过较小者的 15% 即判为重叠
OVERLAP_RATIO = 0.15
#: 正文字号下限
MIN_FONT_PT = 12
_SKIP_NAMES = ("qa:deco", "qa:footer", "qa:pageno", "qa:axis", "qa:dot")
#: 导航页：与内容页同名不算重复标题
_NAV_TYPES = ("cover", "agenda", "section", "closing")

Severity = Literal["error", "warn", "info"]


class DeckIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: Severity
    slide: int | None = None
    detail: str
    suggestion: str = ""


class DeckQAReport(BaseModel):
    """结构质检报告：``ok`` 只看结构错误；视觉状态单列在 ``visual``。"""

    model_config = ConfigDict(extra="forbid")

    ok: bool
    score: int = Field(ge=0, le=100)
    issues: list[DeckIssue] = Field(default_factory=list)
    slide_count: int = 0
    theme: str = ""
    rounds: int = 0
    visual: dict[str, Any] = Field(default_factory=dict)

    @property
    def errors(self) -> list[DeckIssue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[DeckIssue]:
        return [issue for issue in self.issues if issue.severity == "warn"]

    def summary(self) -> str:
        """给用户看的一句话（只说做过的事，不编造视觉评分）。"""
        if self.ok:
            extra = len(self.warnings) + len(self.issues) - len(self.errors) - len(self.warnings)
            tail = f"，{extra} 处提示" if extra > 0 else ""
            head = f"已通过结构质检（{self.score} 分{tail}）"
        else:
            first = self.errors[0]
            where = f"第 {first.slide} 页" if first.slide else "整体"
            head = f"结构质检仍有 {len(self.errors)} 项待处理（{where}：{first.code}）"
        status = self.visual.get("status")
        if status == "skipped":
            head += "；视觉质检未执行（未检测到 soffice），不计为视觉通过"
        elif status == "rendered":
            head += f"；已导出 {len(self.visual.get('images') or [])} 张页面图供人工复核"
        return head


# --------------------------------------------------------------------------- #
# 结构质检
# --------------------------------------------------------------------------- #


def _issue(
    code: str, severity: Severity, detail: str, *, slide: int | None = None, suggestion: str = ""
) -> DeckIssue:
    return DeckIssue(
        code=code, severity=severity, slide=slide, detail=detail, suggestion=suggestion
    )


def _theme_issues(plan: DeckPlan) -> list[DeckIssue]:
    issues: list[DeckIssue] = []
    theme = get_theme(plan.theme)
    if plan.theme not in THEME_NAMES:
        issues.append(
            _issue(
                "THEME_UNKNOWN",
                "warn",
                f"主题「{plan.theme}」不在内置清单，已回落到 {theme.name}。",
                suggestion="使用 gov_red / tech_blue / agri_green 等内置主题。",
            )
        )
    for role, color in (
        ("正文", theme.neutral_dark),
        ("次级文字", theme.neutral_mid),
        ("主色", theme.primary),
        ("辅助色", theme.accent),
    ):
        ratio = contrast_ratio(color, theme.background)
        if ratio < 4.5:
            issues.append(
                _issue(
                    "CONTRAST_LOW",
                    "error",
                    f"{role} {color} 与背景 {theme.background} 对比度 {ratio}:1，低于 4.5:1。",
                    suggestion="换更深的颜色，或调大字号/加粗以补偿可读性。",
                )
            )
    return issues


def _plan_issues(plan: DeckPlan) -> list[DeckIssue]:
    issues = _theme_issues(plan)
    if len(plan.slides) < MIN_SLIDES:
        issues.append(
            _issue(
                "SLIDE_COUNT_LOW",
                "warn",
                f"只有 {len(plan.slides)} 页，汇报材料通常至少 {MIN_SLIDES} 页。",
                suggestion="补充现状/证据/行动页，或明确这是一页纸结论。",
            )
        )
    if len(plan.slides) > MAX_SLIDES:
        issues.append(
            _issue(
                "SLIDE_COUNT_HIGH",
                "error",
                f"页数 {len(plan.slides)} 超过上限 {MAX_SLIDES}。",
                suggestion="合并同类页，把细节移入备注。",
            )
        )
    if plan.slides and plan.slides[0].type != "cover":
        issues.append(
            _issue("COVER_MISSING", "error", "第一页不是封面。", slide=1,
                   suggestion="把第 1 页改为 cover。")
        )
    if plan.slides and plan.slides[-1].type != "closing":
        issues.append(
            _issue("CLOSING_MISSING", "warn", "最后一页不是结尾页。",
                   slide=len(plan.slides), suggestion="补一页 closing 收束。")
        )
    if not plan.storyline.strip():
        issues.append(
            _issue("STORYLINE_MISSING", "info", "没有填写叙事线 storyline。",
                   suggestion="用一句话写清「现状—问题—方案—行动」的主线。")
        )

    content_seen: dict[str, int] = {}
    for index, slide in enumerate(plan.slides, start=1):
        issues.extend(_slide_issues(slide, index))
        key = slide.title.strip()
        # 导航页（封面/目录/章节/结尾）与内容页同名是正常结构，只对并列内容页判重
        if slide.type in _NAV_TYPES:
            continue
        if key in content_seen:
            issues.append(
                _issue(
                    "TITLE_DUPLICATE",
                    "warn",
                    f"标题「{key}」与第 {content_seen[key]} 页重复。",
                    slide=index,
                    suggestion="改写其中一页的标题，或加「（续）」并说明差异。",
                )
            )
        else:
            content_seen[key] = index
    return issues


def _slide_issues(slide: DeckSlide, index: int) -> list[DeckIssue]:
    issues: list[DeckIssue] = []
    if not slide.title.strip():
        issues.append(_issue("TITLE_EMPTY", "error", "标题为空。", slide=index))
    elif len(slide.title) > MAX_TITLE:
        issues.append(
            _issue(
                "TITLE_TOO_LONG",
                "error",
                f"标题 {len(slide.title)} 字，超过 {MAX_TITLE} 字。",
                slide=index,
                suggestion="压缩成一句结论，细节移到 key_message。",
            )
        )
    if len(slide.key_message) > MAX_KEY_MESSAGE:
        issues.append(
            _issue(
                "KEY_MESSAGE_TOO_LONG",
                "error",
                f"核心信息 {len(slide.key_message)} 字，超过 {MAX_KEY_MESSAGE} 字。",
                slide=index,
                suggestion="只留一句结论，展开内容放到要点或备注。",
            )
        )
    if len(slide.bullets) > MAX_BULLETS:
        issues.append(
            _issue(
                "TOO_MANY_BULLETS",
                "error",
                f"{len(slide.bullets)} 条要点，超过 {MAX_BULLETS} 条。",
                slide=index,
                suggestion="拆页或合并相近要点。",
            )
        )
    long_bullets = [item for item in slide.bullets if len(item) > 100]
    if long_bullets:
        issues.append(
            _issue(
                "BULLET_TOO_LONG",
                "error",
                f"{len(long_bullets)} 条要点超过 100 字。",
                slide=index,
                suggestion="每条压到一行半以内，长解释放备注。",
            )
        )
    if slide.chart is not None:
        chart = slide.chart
        if not chart.categories or not chart.series:
            issues.append(
                _issue("CHART_EMPTY", "error", "图表没有类别或系列数据。", slide=index,
                       suggestion="补数据，或改成 statement 讲结论。")
            )
        else:
            bad = [s.name for s in chart.series if len(s.values) != len(chart.categories)]
            if bad:
                issues.append(
                    _issue(
                        "CHART_SERIES_LENGTH",
                        "error",
                        f"系列 {bad} 的数值个数与类别数 {len(chart.categories)} 不一致。",
                        slide=index,
                        suggestion="逐类别补齐数值，不要留空洞。",
                    )
                )
            if len(chart.categories) > 8:
                issues.append(
                    _issue(
                        "CHART_TOO_DENSE",
                        "warn",
                        f"类别 {len(chart.categories)} 个，图上标签会拥挤。",
                        slide=index,
                        suggestion="合并小类别或改成表格。",
                    )
                )
    if slide.table is not None:
        table = slide.table
        cells = len(table.columns) * max(1, len(table.rows))
        if len(table.columns) > MAX_TABLE_COLS or len(table.rows) > MAX_TABLE_ROWS:
            issues.append(
                _issue(
                    "TABLE_DENSE",
                    "error",
                    f"表格 {len(table.columns)} 列 × {len(table.rows)} 行，超过 "
                    f"{MAX_TABLE_COLS} 列 / {MAX_TABLE_ROWS} 行。",
                    slide=index,
                    suggestion="拆表或只保留支撑结论的列与行。",
                )
            )
        elif cells > 40:
            issues.append(
                _issue(
                    "TABLE_DENSE",
                    "warn",
                    f"表格 {cells} 个单元格，投影时会显得密。",
                    slide=index,
                    suggestion="压到 40 格以内，或分两页。",
                )
            )
    return issues


def _shape_bounds(shape) -> tuple[float, float, float, float] | None:
    if shape.left is None or shape.top is None or shape.width is None or shape.height is None:
        return None
    return (
        shape.left / EMU,
        shape.top / EMU,
        shape.width / EMU,
        shape.height / EMU,
    )


def _inspect_presentation(prs) -> list[DeckIssue]:
    """检查渲染后的真实几何：越界、重叠、溢出、字号。"""
    issues: list[DeckIssue] = []
    width = prs.slide_width / EMU
    height = prs.slide_height / EMU
    if abs(width - render_pptx.SLIDE_W) > 0.05 or abs(height - render_pptx.SLIDE_H) > 0.05:
        issues.append(
            _issue(
                "SLIDE_SIZE",
                "warn",
                f"页面尺寸 {width:.2f}×{height:.2f} 英寸，不是 16:9（13.333×7.5）。",
            )
        )
    for index, slide in enumerate(prs.slides, start=1):
        text_shapes = []
        for shape in slide.shapes:
            box = _shape_bounds(shape)
            if box is None:
                continue
            left, top, w, h = box
            if left < -0.02 or top < -0.02 or left + w > width + 0.02 or top + h > height + 0.02:
                issues.append(
                    _issue(
                        "SHAPE_OUT_OF_BOUNDS",
                        "error",
                        f"形状「{shape.name}」超出页面（{left:.2f},{top:.2f},"
                        f"{w:.2f}×{h:.2f}）。",
                        slide=index,
                        suggestion="收进 0.6 英寸页边距内。",
                    )
                )
            if shape.name in _SKIP_NAMES:
                continue
            if shape.has_text_frame and shape.text_frame.text.strip():
                text_shapes.append((shape, box))
                issues.extend(_text_shape_issues(shape, box, index))
        for i in range(len(text_shapes)):
            for j in range(i + 1, len(text_shapes)):
                issue = _overlap_issue(text_shapes[i], text_shapes[j], index)
                if issue is not None:
                    issues.append(issue)
    return issues


def _text_shape_issues(shape, box, index: int) -> list[DeckIssue]:
    issues: list[DeckIssue] = []
    left, top, w, h = box
    frame = shape.text_frame
    paragraphs: list[str] = []
    sizes: list[int] = []
    for paragraph in frame.paragraphs:
        text = "".join(run.text for run in paragraph.runs)
        if not text:
            continue
        paragraphs.append(text)
        for run in paragraph.runs:
            if run.font.size is not None:
                sizes.append(int(round(run.font.size.pt)))
    if not sizes:
        return issues
    smallest = min(sizes)
    preview = paragraphs[0][:14] if paragraphs else shape.name
    if smallest < MIN_FONT_PT:
        issues.append(
            _issue(
                "FONT_TOO_SMALL",
                "error",
                f"「{preview}」有 {smallest}pt 文字，投影与打印都偏小。",
                slide=index,
                suggestion="正文不小于 12pt；内容太多就减行，不要缩字号。",
            )
        )
    if paragraphs:
        is_title = shape.name == "qa:title"
        needed = render_pptx.measure_text_height(
            paragraphs,
            smallest,
            max(0.2, w - 0.05),
            spacing_pt=0 if is_title else 8,
            line_spacing=1.12 if is_title else 1.28,
        )
        available = max(0.2, h - 0.04)
        if needed > available * OVERFLOW_TOLERANCE:
            issues.append(
                _issue(
                    "TEXT_OVERFLOW",
                    "error",
                    f"「{preview}」预计需要 {needed:.2f} 英寸高，盒子只有 "
                    f"{available:.2f} 英寸。",
                    slide=index,
                    suggestion="减少要点、压缩标题，或换更宽松的版式。",
                )
            )
    return issues


def _overlap_issue(a, b, index: int) -> DeckIssue | None:
    (a_shape, (ax, ay, aw, ah)) = a
    (b_shape, (bx, by, bw, bh)) = b
    left = max(ax, bx)
    top = max(ay, by)
    right = min(ax + aw, bx + bw)
    bottom = min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return None
    overlap = (right - left) * (bottom - top)
    smaller = min(aw * ah, bw * bh)
    if smaller <= 0 or overlap / smaller <= OVERLAP_RATIO:
        return None
    return _issue(
        "TEXT_OVERLAP",
        "error",
        f"「{a_shape.name}」与「{b_shape.name}」文本区域重叠。",
        slide=index,
        suggestion="调整版式间距或减少一页内的元素。",
    )


def _score(issues: list[DeckIssue]) -> int:
    penalty = 0
    for issue in issues:
        penalty += {"error": 12, "warn": 3, "info": 1}[issue.severity]
    return max(0, min(100, 100 - penalty))


def inspect_presentation(prs) -> list[DeckIssue]:
    """对已渲染的 Presentation 做几何检查（测试与外部复核复用）。"""
    return _inspect_presentation(prs)


def qa_deck_plan(plan: DeckPlan, *, visual: bool = False, rounds: int = 0) -> DeckQAReport:
    """对演示计划做结构质检（默认不触发视觉质检）。"""
    issues = _plan_issues(plan)
    try:
        prs = render_pptx.build_presentation(plan)
    except Exception as e:  # noqa: BLE001 —— 渲染失败本身就是可质检的问题
        issues.append(
            _issue(
                "RENDER_FAILED",
                "error",
                f"渲染失败：{type(e).__name__}: {e}",
                suggestion="检查版式所需字段是否齐全。",
            )
        )
    else:
        issues.extend(_inspect_presentation(prs))
    report = DeckQAReport(
        ok=not any(issue.severity == "error" for issue in issues),
        score=_score(issues),
        issues=issues,
        slide_count=len(plan.slides),
        theme=plan.theme,
        rounds=rounds,
        visual={"status": "not_requested"},
    )
    if visual:
        report.visual = visual_qa(plan)
    return report


# --------------------------------------------------------------------------- #
# 确定性修复
# --------------------------------------------------------------------------- #


def _trim_text(text: str, limit: int) -> str:
    clean = " ".join(str(text or "").split())
    return clean if len(clean) <= limit else clean[: limit - 1] + "…"


def _to_statement(slide: DeckSlide) -> None:
    slide.type = "statement"
    slide.bullets = []
    slide.columns = []
    slide.kpis = []
    slide.chart = None
    slide.table = None
    slide.timeline = []
    slide.process = []
    slide.comparison = None
    slide.image = None
    if not slide.key_message.strip():
        slide.key_message = "本页数据待补充：请给出类别与数值后重新生成。"


def _shrink(slide: DeckSlide) -> bool:
    """按版式丢掉最不重要的一个元素；返回是否真的瘦身了。"""
    if len(slide.bullets) > 1:
        slide.bullets = slide.bullets[:-1]
        return True
    if slide.columns:
        last = slide.columns[-1]
        if len(last.items) > 1:
            last.items = last.items[:-1]
            return True
        if len(slide.columns) > 1:
            slide.columns = slide.columns[:-1]
            return True
    if len(slide.kpis) > 1:
        slide.kpis = slide.kpis[:-1]
        return True
    if len(slide.timeline) > 1:
        slide.timeline = slide.timeline[:-1]
        return True
    if len(slide.process) > 1:
        slide.process = slide.process[:-1]
        return True
    if slide.comparison is not None:
        comp = slide.comparison
        if len(comp.left_items) > 1 or len(comp.right_items) > 1:
            comp.left_items = comp.left_items[:-1] or comp.left_items
            comp.right_items = comp.right_items[:-1] or comp.right_items
            return True
    if slide.table is not None and len(slide.table.rows) > 1:
        slide.table.rows = slide.table.rows[:-1]
        return True
    return False


def repair_deck_plan(plan: DeckPlan, report: DeckQAReport) -> DeckPlan:
    """按报告做确定性修正（不调模型、不编内容），最多修 2 轮由调用方控制。

    修正手段只有四类：截断（标题/要点/单元格）、删减（去掉最不重要的元素）、
    换版式（图表数据残缺 → statement）、以及「减内容换字号」——字号由主题阶梯
    决定，单页没有独立字号字段，所以「字号过小 / 溢出」的修法是删到自动适配
    回到 ≥12pt，而不是偷偷把 9pt 字塞回去。
    """
    repaired = plan.model_copy(deep=True)
    touched_overflow: set[int] = set()
    for issue in report.issues:
        if issue.slide is None:
            continue
        index = issue.slide - 1
        if index < 0 or index >= len(repaired.slides):
            continue
        slide = repaired.slides[index]
        if issue.code == "TITLE_TOO_LONG":
            slide.title = _trim_text(slide.title, MAX_TITLE)
        elif issue.code == "TITLE_DUPLICATE":
            tail = "（续）"
            slide.title = _trim_text(slide.title, MAX_TITLE - len(tail)) + tail
        elif issue.code == "KEY_MESSAGE_TOO_LONG":
            slide.key_message = _trim_text(slide.key_message, MAX_KEY_MESSAGE)
        elif issue.code == "TOO_MANY_BULLETS":
            slide.bullets = slide.bullets[:MAX_BULLETS]
        elif issue.code == "BULLET_TOO_LONG":
            slide.bullets = [_trim_text(item, 100) for item in slide.bullets]
        elif issue.code in ("CHART_EMPTY", "CHART_SERIES_LENGTH", "CHART_TOO_DENSE"):
            _to_statement(slide)
        elif issue.code == "TABLE_DENSE":
            if slide.table is not None:
                table = slide.table
                columns = table.columns[:MAX_TABLE_COLS]
                rows = [row[: len(columns)] for row in table.rows[:MAX_TABLE_ROWS]]
                slide.table = table.model_copy(update={"columns": columns, "rows": rows})
                if not slide.table.rows:
                    _to_statement(slide)
        elif issue.code in (
            "FONT_TOO_SMALL",
            "TEXT_OVERFLOW",
            "TEXT_OVERLAP",
            "SHAPE_OUT_OF_BOUNDS",
        ):
            touched_overflow.add(index)
    # 溢出类问题统一瘦身，直到该页不再需要（最多处理 2 轮）
    for _ in range(2):
        changed = False
        for index in touched_overflow:
            if index < len(repaired.slides) and _shrink(repaired.slides[index]):
                changed = True
        if not changed:
            break
    try:
        return DeckPlan.model_validate(repaired.model_dump())
    except Exception:  # noqa: BLE001 —— 修坏了就退回原计划，绝不产出非法模型
        return plan


def qa_and_repair(
    plan: DeckPlan, *, max_rounds: int = 2, visual: bool = False
) -> tuple[DeckPlan, DeckQAReport, int]:
    """质检 + 修复循环（默认最多 2 轮），返回 (计划, 最终报告, 修复轮数)。"""
    report = qa_deck_plan(plan)
    rounds = 0
    current = plan
    for _ in range(max_rounds):
        if report.ok:
            break
        candidate = repair_deck_plan(current, report)
        if candidate.model_dump() == current.model_dump():
            break
        current = candidate
        rounds += 1
        report = qa_deck_plan(current, rounds=rounds)
    if visual:
        report.visual = visual_qa(current)
    return current, report, rounds


# --------------------------------------------------------------------------- #
# 可选视觉质检（只检测 soffice，不强依赖）
# --------------------------------------------------------------------------- #


def visual_qa_available() -> bool:
    """本机是否具备 LibreOffice 命令行（``soffice``）。"""
    return shutil.which("soffice") is not None


def render_pngs(
    pptx_path: str | Path, out_dir: str | Path | None = None, *, timeout: int = 180
) -> list[Path]:
    """用 soffice 导页图：先转 PDF，再按是否有 pdftoppm 逐页转 PNG。

    只用于人工/后续模型复核，不参与 ``ok`` 判定；缺工具时返回已能产出的结果。
    """
    if not visual_qa_available():
        return []
    source = Path(pptx_path)
    target = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(prefix="deck-png-"))
    target.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(target), str(source)],
            check=True,
            capture_output=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return []
    pdf = target / f"{source.stem}.pdf"
    if shutil.which("pdftoppm") and pdf.exists():
        try:
            subprocess.run(
                ["pdftoppm", "-png", "-r", "110", str(pdf), str(target / "slide")],
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError):
            return [pdf]
        return sorted(target.glob("slide-*.png")) or [pdf]
    if shutil.which("mutool") and pdf.exists():
        try:
            subprocess.run(
                ["mutool", "draw", "-o", str(target / "slide-%03d.png"), "-r", "110", str(pdf)],
                check=True,
                capture_output=True,
                timeout=timeout,
            )
        except (subprocess.SubprocessError, OSError):
            return [pdf]
        return sorted(target.glob("slide-*.png")) or [pdf]
    # 没有栅格化工具：返回 PDF，明确不是逐页 PNG
    return [pdf] if pdf.exists() else []


def visual_qa(plan: DeckPlan, *, out_dir: str | Path | None = None) -> dict[str, Any]:
    """可选视觉质检：只负责导图，不做模型级打分，也不冒充通过。"""
    if not visual_qa_available():
        return {
            "status": "skipped",
            "reason": (
                "未检测到 soffice（LibreOffice），本机未执行视觉质检；"
                "结构质检结果不代表视觉通过。"
            ),
            "images": [],
        }
    with tempfile.TemporaryDirectory(prefix="deck-visual-") as tmp:
        deck = Path(tmp) / "deck.pptx"
        deck.write_bytes(render_pptx.to_pptx_plan(plan))
        images = render_pngs(deck, out_dir or Path(tmp) / "png")
        if not images:
            return {
                "status": "skipped",
                "reason": "soffice 存在但导出失败（可能缺少 PDF 栅格化工具），未完成视觉质检。",
                "images": [],
            }
        return {
            "status": "rendered",
            "reason": "已导出页面图片，供人工复核；未做模型级视觉评分。",
            "images": [str(path) for path in images],
        }


__all__ = [
    "DeckIssue",
    "DeckQAReport",
    "inspect_presentation",
    "qa_and_repair",
    "qa_deck_plan",
    "render_pngs",
    "repair_deck_plan",
    "visual_qa",
    "visual_qa_available",
]
