"""工作日志/月报 ORM（迁移 0070，v3.58）。

对应科技小院的硬性考核链路：全国科技小院服务管理平台要求研究生填报工作
日志与月报，并直接挂钩研究生实践考核与小院年度评价（调研见
docs/research-jiangsu-science-yards.md）。本表承接「自动草稿 → 人工确认 →
提交 → 审核」的本地闭环：

- ``period_key``：daily=YYYY-MM-DD，monthly=YYYY-MM；同人同 kind 同期唯一；
- ``sources_json``：草稿聚合来源（会话数/任务数/台账条数等计数与引用 id），
  正文不含对话原文——只写工作事实，符合平台「客观可追溯」口径；
- 状态机：draft → submitted → (approved | draft 驳回)。
"""

from __future__ import annotations

from sqlalchemy import JSON, Date, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

KIND_DAILY = "daily"
KIND_MONTHLY = "monthly"
VALID_KINDS = (KIND_DAILY, KIND_MONTHLY)

STATUS_DRAFT = "draft"
STATUS_SUBMITTED = "submitted"
STATUS_APPROVED = "approved"
VALID_STATUSES = (STATUS_DRAFT, STATUS_SUBMITTED, STATUS_APPROVED)


class YardJournal(Base, TimestampMixin):
    __tablename__ = "yard_journal"
    __table_args__ = (
        UniqueConstraint(
            "yard_id",
            "author_user_id",
            "kind",
            "period_key",
            name="uq_journal_yard_author_kind_period",
        ),
        Index("ix_journal_yard_period", "yard_id", "kind", "period_key"),
        Index("ix_journal_author", "author_user_id"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    author_user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False, default=KIND_DAILY)
    period_key: Mapped[str] = mapped_column(String(10), nullable=False)
    entry_date: Mapped[object] = mapped_column(Date, nullable=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    content_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sources_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default=STATUS_DRAFT)
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(500), nullable=True)


__all__ = [
    "KIND_DAILY",
    "KIND_MONTHLY",
    "STATUS_APPROVED",
    "STATUS_DRAFT",
    "STATUS_SUBMITTED",
    "VALID_KINDS",
    "VALID_STATUSES",
    "YardJournal",
]
