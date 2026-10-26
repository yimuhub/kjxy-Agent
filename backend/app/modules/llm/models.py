"""LLM 调用记录模型。

只存 prompt 的 sha256 摘要（prompt_sha），绝不存原文，满足"管理员可看调用量/
失败原因/成本概况，但普通用户不能读他人日志正文"的产品约束。
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, _utcnow


class LLMCall(Base, TimestampMixin):
    __tablename__ = "llm_calls"

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
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


class LLMTrace(Base):
    """模型调用观测轨迹（W4）：append-only，只插不改。

    与 llm_calls 的分工：llm_calls 是配额/成本记账（success|error + 估算成本），
    llm_traces 是**观测**视角——带用途（purpose）与调用方引用（agent_ref/run_id），
    供「模型调用观测」卡片按日×模型聚合。fake provider 同样落行。

    隐私口径（硬约束）：prompt_digest 只存 ``sha256 前 12 位|前 200 字摘要``，
    绝不存 prompt 全文；error_text 截断 300 字符且只来自受控错误码文案。
    """

    __tablename__ = "llm_traces"
    __table_args__ = (Index("ix_llm_traces_created_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    # 平台级无小院上下文调用理论上不存在（complete_with_logging 强制小院），
    # 列仍按可空建模：观测数据缺失小院归属时宁缺勿错填。
    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 负责人驾驶舱：调用归属用户/会话（历史数据可空，新调用尽量透传）
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 命中的供应商行 id（settings 兜底 provider 无供应商 → null，不编造）
    provider_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="unknown")
    # declarative|factory|eval|tool|embedding|platform（platform=调用方未透传）
    purpose: Mapped[str] = mapped_column(String(20), nullable=False, default="platform")
    # 调用方引用：声明式 Agent 存 agent_code，工厂存 factory run id
    agent_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # 所属运行 id（= LLMMetadata.task_id：声明式 run_id / 工厂 task id）
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False)  # ok|error
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_digest: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(String(300), nullable=True)
