"""LLM 模块 Pydantic 契约。"""

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str
    content: str


class LLMMetadata(BaseModel):
    tenant_id: str | None = None
    task_id: str | None = None
    request_id: str | None = None

    model_config = {"extra": "ignore"}


class LLMRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    temperature: float | None = Field(default=None, ge=0, le=2)
    metadata: LLMMetadata = Field(default_factory=LLMMetadata)


class LLMResponse(BaseModel):
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str
    latency_ms: int
    finish_reason: str | None = None


class LLMStatusOut(BaseModel):
    provider: str
    model: str
    configured: bool
    mode: str  # mock|live|unknown；绝不包含密钥
