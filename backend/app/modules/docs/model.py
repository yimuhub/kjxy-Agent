"""统一文档模型（DocModel）：对话内生成的各类文档都从这里渲染。

块类型刻意保持最小集：标题 / 段落 / 无序列表 / 有序列表 / 表格 / 代码 / 分页。
复杂排版（图片、页眉页脚、目录）留到后续版本——先把「能生成、能预览、能下载」做稳。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.errors import AppError

#: 支持的导出格式（顺序即默认优先级）
DOC_FORMATS: tuple[str, ...] = ("md", "docx", "xlsx", "pptx", "pdf")

MAX_BLOCKS = 300
MAX_TEXT = 20_000
MAX_TABLE_ROWS = 200
MAX_TABLE_COLS = 20
MAX_TITLE = 100

BlockType = Literal["heading", "paragraph", "bullets", "numbered", "table", "code", "pagebreak"]


class DocBlock(BaseModel):
    """单个内容块。字段按类型取用，未用字段保持空值。"""

    model_config = ConfigDict(extra="forbid")

    type: BlockType
    level: int = Field(default=2, ge=1, le=3)
    text: str = Field(default="", max_length=MAX_TEXT)
    items: list[str] = Field(default_factory=list, max_length=200)
    header: list[str] = Field(default_factory=list, max_length=MAX_TABLE_COLS)
    rows: list[list[str]] = Field(default_factory=list, max_length=MAX_TABLE_ROWS)

    @model_validator(mode="after")
    def _validate_by_type(self) -> DocBlock:
        if self.type == "heading" and not self.text.strip():
            raise ValueError("heading 需要非空 text")
        if self.type in ("paragraph", "code") and not self.text.strip():
            raise ValueError(f"{self.type} 需要非空 text")
        if self.type in ("bullets", "numbered"):
            if not self.items:
                raise ValueError(f"{self.type} 需要至少 1 个条目")
            if any(not str(i).strip() for i in self.items):
                raise ValueError(f"{self.type} 的条目不能为空")
        if self.type == "table":
            if not self.header:
                raise ValueError("table 需要表头")
            if len(self.header) > MAX_TABLE_COLS:
                raise ValueError(f"table 列数不能超过 {MAX_TABLE_COLS}")
            for row in self.rows:
                if len(row) != len(self.header):
                    raise ValueError("table 每行列数必须与表头一致")
        if self.type == "pagebreak" and (self.text or self.items or self.header or self.rows):
            raise ValueError("pagebreak 不携带内容")
        return self


class DocModel(BaseModel):
    """文档模型：标题 + 可选主题/作者 + 有序内容块。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE)
    subject: str = Field(default="", max_length=MAX_TITLE)
    author: str = Field(default="", max_length=MAX_TITLE)
    blocks: list[DocBlock] = Field(min_length=1, max_length=MAX_BLOCKS)


def parse_model(raw: dict[str, Any]) -> DocModel:
    """把请求体里的 model 解析成 DocModel；任何不合法都归一为 400 DOC_MODEL_INVALID。"""
    if not isinstance(raw, dict):
        raise AppError("DOC_MODEL_INVALID", "文档模型必须是对象。", http_status=400)
    try:
        model = DocModel.model_validate(raw)
    except ValidationError as e:
        raise AppError(
            "DOC_MODEL_INVALID",
            "文档模型不合法，请检查标题、正文块与表格行列。",
            http_status=400,
            details={"errors": e.errors(include_url=False)[:5]},
        ) from e
    total = len(model.title) + sum(
        len(b.text)
        + sum(len(i) for i in b.items)
        + sum(len(c) for c in b.header)
        + sum(len(c) for row in b.rows for c in row)
        for b in model.blocks
    )
    if total > MAX_TEXT * 3:
        raise AppError(
            "DOC_TOO_LARGE",
            "文档内容过长，请精简后再试。",
            http_status=400,
            details={"chars": total},
        )
    return model
