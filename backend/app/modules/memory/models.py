"""统一记忆表：情景、摘要、偏好与事实。

设计口径：
- ``scope_type/scope_key`` 决定谁能读：user（本人）、yard（本小院）、agent（该助手）；
- ``visibility`` 是读边界：private 仅本人/负责人，yard 本小院可读，agent 仅绑定该助手；
- ``source_type/source_id`` 用于去重与追溯：一次运行一条情景记忆，一天一条日摘要；
- embedding 与知识库共用跨方言类型，SQLite 回退 JSON，PostgreSQL 走 pgvector。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin
from app.modules.knowledge.models import EmbeddingType

SCOPE_USER = "user"
SCOPE_YARD = "yard"
SCOPE_AGENT = "agent"
VALID_SCOPES = (SCOPE_USER, SCOPE_YARD, SCOPE_AGENT)

KIND_EPISODE = "episode"
KIND_SUMMARY = "summary"
KIND_PREFERENCE = "preference"
KIND_FACT = "fact"
VALID_KINDS = (KIND_EPISODE, KIND_SUMMARY, KIND_PREFERENCE, KIND_FACT)

VISIBILITY_PRIVATE = "private"
VISIBILITY_YARD = "yard"
VISIBILITY_AGENT = "agent"
VALID_VISIBILITIES = (VISIBILITY_PRIVATE, VISIBILITY_YARD, VISIBILITY_AGENT)

STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_DELETED = "deleted"
VALID_STATUSES = (STATUS_ACTIVE, STATUS_SUPERSEDED, STATUS_DELETED)

SOURCE_RUN = "run"
SOURCE_DAILY = "daily"
SOURCE_MANUAL = "manual"
SOURCE_CHAT = "chat"
VALID_SOURCE_TYPES = (SOURCE_RUN, SOURCE_DAILY, SOURCE_MANUAL, SOURCE_CHAT)


class AgentMemory(Base, TimestampMixin):
    __tablename__ = "agent_memories"
    __table_args__ = (
        UniqueConstraint(
            "yard_id",
            "kind",
            "source_type",
            "source_id",
            name="uq_agent_memories_source",
        ),
        Index("ix_agent_memories_scope", "yard_id", "scope_type", "scope_key", "kind"),
        Index("ix_agent_memories_created", "yard_id", "created_at"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False, default=SCOPE_YARD)
    #: user → user_id；agent → agent_code；yard → 空串
    scope_key: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: 0..1；参与检索排序，数值越高越优先
    importance: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    #: 0..1；模型推测的事实低于用户明确陈述
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SOURCE_MANUAL, index=True
    )
    #: run_id / YYYY-MM-DD / 空；与 kind+source_type 组成去重键
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    visibility: Mapped[str] = mapped_column(
        String(16), nullable=False, default=VISIBILITY_PRIVATE, index=True
    )
    #: 向量列；与知识库共用 ``EmbeddingType``，读回已收敛为 ``list[float]``。
    embedding: Mapped[list[float] | None] = mapped_column(EmbeddingType(), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=STATUS_ACTIVE, index=True
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


__all__ = [
    "KIND_EPISODE",
    "KIND_FACT",
    "KIND_PREFERENCE",
    "KIND_SUMMARY",
    "SCOPE_AGENT",
    "SCOPE_USER",
    "SCOPE_YARD",
    "SOURCE_CHAT",
    "SOURCE_DAILY",
    "SOURCE_MANUAL",
    "SOURCE_RUN",
    "STATUS_ACTIVE",
    "STATUS_DELETED",
    "STATUS_SUPERSEDED",
    "VISIBILITY_AGENT",
    "VISIBILITY_PRIVATE",
    "VISIBILITY_YARD",
    "AgentMemory",
]
