"""A2A 模型（v3.20）：入站令牌、出站协作方、双向任务记录。

约定：
- 入站令牌只存 sha256（接口只展示前缀），令牌本身在小院设置里生成一次后不再回显；
- 协作方的鉴权令牌视同密钥，Fernet 加密入库（复用 llm/supplier_crypto）；
- ``a2a_tasks`` 同时记录入站（别的平台调我们）与出站（我们调别人）任务，
  ``op_key`` 是幂等键：入站用对端 messageId，出站由本平台生成，(yard_id, op_key) 唯一。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

DIRECTION_INBOUND = "inbound"
DIRECTION_OUTBOUND = "outbound"

STATE_SUBMITTED = "submitted"
STATE_WORKING = "working"
STATE_COMPLETED = "completed"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"

TERMINAL_STATES = frozenset({STATE_COMPLETED, STATE_FAILED, STATE_CANCELED})


class A2aEndpoint(Base, TimestampMixin):
    """小院对外协作入口：一个令牌标识一个小院（v3.20 不做逐对端令牌）。"""

    __tablename__ = "a2a_endpoints"
    __table_args__ = (UniqueConstraint("yard_id", name="uq_a2a_endpoints_yard"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: 展示用前缀（如 ``a2a_ab12cd34``），不足以还原完整令牌
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class A2aPeer(Base, TimestampMixin):
    """出站协作方：远端 A2A 平台的地址、鉴权与 Agent Card 快照。"""

    __tablename__ = "a2a_peers"
    __table_args__ = (UniqueConstraint("yard_id", "name", name="uq_a2a_peers_yard_name"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: 远端平台根地址（不含 well-known 路径），保存与每次外呼前都过 net_guard
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    token_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    #: 探测成功时的 Agent Card 快照；发任务时优先用 card.url
    card_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    card_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class A2aTask(Base, TimestampMixin):
    """一条 A2A 任务（入站或出站），状态机见 TERMINAL_STATES。"""

    __tablename__ = "a2a_tasks"
    __table_args__ = (
        UniqueConstraint("yard_id", "op_key", name="uq_a2a_tasks_yard_op"),
        Index("ix_a2a_tasks_yard_created", "yard_id", "created_at"),
        Index("ix_a2a_tasks_yard_direction", "yard_id", "direction"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    peer_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    skill_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=STATE_SUBMITTED)
    request_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: 对端任务 id（出站时来自响应；入站时等于本平台任务 id）
    remote_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    context_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: 幂等键：入站 = 对端 messageId，出站 = 本平台生成
    op_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
