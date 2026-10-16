"""农业问答 Schema。

QAOutput 与 contracts/json-schema/qa-output.schema.json 保持一致；
字段增删时必须同步更新该契约文件（契约为准）。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QAInput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    question: str = Field(min_length=1, max_length=2000, description="用户问题")
    top_k: int = Field(default=5, ge=1, le=20, description="检索片段数")
    mode: Literal["sync", "async"] = Field(
        default="sync", description="sync=同步返回，async=走任务队列"
    )
    crop_type: str | None = Field(
        default=None, max_length=100, description="作物类型（可选，用于检索加权）"
    )


class CitationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(description="文档 ID")
    chunk_id: str = Field(description="片段 ID")
    title: str = Field(description="文档标题")
    source: str = Field(description="资料来源")
    page_number: int | None = Field(default=None, description="页码（如有）")
    quote: str = Field(description="引用原文")


class QAOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(description="回答正文")
    citations: list[CitationOut] = Field(default_factory=list, description="引用列表")
    warnings: list[str] = Field(default_factory=list, description="风险提示/免责声明")
    needs_expert_review: bool = Field(description="是否需要专家复核")
