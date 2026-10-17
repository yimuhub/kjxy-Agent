"""知识库 API Schema。ORM 模型与 Pydantic Schema 分层，不直接裸返回 ORM 对象。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    title: str
    mime_type: str
    source: str
    status: str
    checksum: str
    uploaded_by: str | None
    error_message: str | None
    page_count: int | None
    created_at: datetime
    updated_at: datetime


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=20)


class ReparseOut(BaseModel):
    """重新解析响应：data 契约 {task_id, document_id, status}（前端已按此开发）。

    task_id 为 broker 任务 ID；broker 不可用而降级本机同步解析时为空串。
    """

    task_id: str
    document_id: str
    status: str


class ChunkHitOut(BaseModel):
    document_id: str
    chunk_id: str
    title: str
    source: str
    page_number: int | None
    quote: str
    score: float
    trust_level: str


class SearchResponse(BaseModel):
    hits: list[ChunkHitOut]
