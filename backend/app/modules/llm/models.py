"""LLM 调用记录模型。

只存 prompt 的 sha256 摘要（prompt_sha），绝不存原文，满足"管理员可看调用量/
失败原因/成本概况，但普通用户不能读他人日志正文"的产品约束。
"""

from sqlalchemy import Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class LLMCall(Base, TimestampMixin):
    __tablename__ = "llm_calls"

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)  # fake|openai_compatible
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # success|error
    error_category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    prompt_sha: Mapped[str] = mapped_column(String(64), nullable=False)  # sha256 hex
