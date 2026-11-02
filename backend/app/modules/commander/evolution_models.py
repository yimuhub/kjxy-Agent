"""指挥官月度迭代记录：每月一份版本说明、指标与可回滚快照（迁移 0063）。"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class CommanderIteration(Base, TimestampMixin):
    __tablename__ = "commander_iterations"
    __table_args__ = (
        UniqueConstraint("yard_id", "month_key", name="uq_commander_iterations_month"),
        Index("ix_commander_iterations_yard_created", "yard_id", "created_at"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: YYYY-MM
    month_key: Mapped[str] = mapped_column(String(7), nullable=False)
    #: V-YYYYMM-N
    version_label: Mapped[str] = mapped_column(String(20), nullable=False)
    persona: Mapped[str] = mapped_column(String(16), nullable=False, default="balanced")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    report_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    changes_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
