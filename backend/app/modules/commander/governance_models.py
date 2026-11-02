"""指挥官治理台账：监督测评报告与版本快照（迁移 0061）。

监督测评 Agent 只写 ``commander_evaluations``，指挥官代码不得修改历史报告；
快照保存「可回滚的指挥官运行范式」，当前包含设置与人格，后续阶段可追加工作流资产。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: 人格标签（前后端稳定契约）
PERSONA_AGGRESSIVE = "aggressive"
PERSONA_STEADY = "steady"
PERSONA_BALANCED = "balanced"
VALID_PERSONAS = (PERSONA_AGGRESSIVE, PERSONA_STEADY, PERSONA_BALANCED)

#: 测评来源
EVAL_SOURCE_LLM = "llm"
EVAL_SOURCE_RULE = "rule"

#: 快照来源
SNAPSHOT_MANUAL = "manual"
SNAPSHOT_PERSONA = "persona"
SNAPSHOT_RESTORE = "restore"


class CommanderEvaluation(Base, TimestampMixin):
    """一次周度测评。``(yard_id, week_key)`` 唯一，重跑覆盖同一周而不是堆历史。"""

    __tablename__ = "commander_evaluations"
    __table_args__ = (
        UniqueConstraint("yard_id", "week_key", name="uq_commander_evaluations_week"),
        Index("ix_commander_evaluations_yard_created", "yard_id", "created_at"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    #: ISO 周，如 2026-W45
    week_key: Mapped[str] = mapped_column(String(16), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    persona: Mapped[str] = mapped_column(String(16), nullable=False, default=PERSONA_BALANCED)
    previous_persona: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: 本次报告是否实际更新了指挥官人格（锁定/进化锁/关闭自动人格时为 false）
    persona_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(8), nullable=False, default=EVAL_SOURCE_RULE)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scores_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    report_md: Mapped[str] = mapped_column(Text, nullable=False, default="")
    strengths_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    shortfalls_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    recommendations_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    #: 证据快照：运行数、重试、失败码、成本等，便于复盘与解释评分
    metrics_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class CommanderSnapshot(Base, TimestampMixin):
    """可回滚的指挥官版本快照。"""

    __tablename__ = "commander_snapshots"
    __table_args__ = (Index("ix_commander_snapshots_yard_created", "yard_id", "created_at"),)

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    label: Mapped[str] = mapped_column(String(60), nullable=False, default="")
    reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    source: Mapped[str] = mapped_column(String(12), nullable=False, default=SNAPSHOT_MANUAL)
    #: {"settings": {...}, "persona": "...", "app": "3.44.0"}
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


__all__ = [
    "EVAL_SOURCE_LLM",
    "EVAL_SOURCE_RULE",
    "PERSONA_AGGRESSIVE",
    "PERSONA_BALANCED",
    "PERSONA_STEADY",
    "SNAPSHOT_MANUAL",
    "SNAPSHOT_PERSONA",
    "SNAPSHOT_RESTORE",
    "VALID_PERSONAS",
    "CommanderEvaluation",
    "CommanderSnapshot",
]
