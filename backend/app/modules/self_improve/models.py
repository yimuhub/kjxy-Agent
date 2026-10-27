"""定时任务与自我总结模型（迁移 0034）。

``yard_jobs`` 是**可复用的调度底座**：本轮承载 ``self_improve``（自我总结提升），
后续财务/管理类定时 Agent 可直接复用同一张表与同一套租约执行逻辑。

诚实边界：
- 调度与租约判定以数据库时间为准（``next_run_at``/``lease_until``），多实例
  并发时靠租约互斥，不依赖进程内锁；
- 自我总结只沉淀**摘要与提案**（偏好记忆/技能草稿/知识候选），不存会话原文。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: 任务种类（可追加；同一小院同一 kind 唯一）
JOB_SELF_IMPROVE = "self_improve"

JOB_STATUS_ACTIVE = "active"
JOB_STATUS_PAUSED = "paused"

RUN_RUNNING = "running"
RUN_SUCCESS = "success"
RUN_FAILED = "failed"
RUN_SKIPPED = "skipped"

TRIGGER_SCHEDULE = "schedule"
TRIGGER_MANUAL = "manual"

MEMORY_ACTIVE = "active"
MEMORY_SUPERSEDED = "superseded"


class YardJob(Base, TimestampMixin):
    """小院定时任务：调度配置 + 下次运行时间 + 执行租约。"""

    __tablename__ = "yard_jobs"
    __table_args__ = (
        UniqueConstraint("yard_id", "kind", name="uq_yard_jobs_yard_kind"),
        Index("ix_yard_jobs_due", "status", "next_run_at"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # active | paused
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=JOB_STATUS_ACTIVE, server_default=JOB_STATUS_ACTIVE
    )
    schedule_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 租约：到期前其他实例跳过，防重复执行（进程崩溃后租约自然过期）
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class YardJobRun(Base, TimestampMixin):
    """一次任务运行的台账：耗时、tokens、产出条数与失败原因。"""

    __tablename__ = "yard_job_runs"
    __table_args__ = (Index("ix_yard_job_runs_yard_created", "yard_id", "created_at"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # running | success | failed | skipped
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RUN_RUNNING, server_default=RUN_RUNNING
    )
    # schedule | manual
    trigger: Mapped[str] = mapped_column(
        String(16), nullable=False, default=TRIGGER_SCHEDULE, server_default=TRIGGER_SCHEDULE
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    output_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SelfImproveMemory(Base, TimestampMixin):
    """用户偏好记忆：只存主题偏好与表达习惯（摘要），不存会话原文。

    ``user_id`` 为空表示小院级记忆（``scope=yard``）；per_member 时按成员隔离，
    仅本人与负责人可见。
    """

    __tablename__ = "self_improve_memories"
    __table_args__ = (Index("ix_self_improve_memories_yard_user", "yard_id", "user_id"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    tags_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    window_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # active | superseded（新一轮总结会接管旧记忆，旧行保留审计）
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=MEMORY_ACTIVE, server_default=MEMORY_ACTIVE
    )
