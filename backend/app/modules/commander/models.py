"""指挥官运行台账 ORM 模型（迁移 0058）。

一张 ``commander_runs`` = 一次「计划—选兵—并行—汇总」；其下 ``commander_subtasks``
按 ``seq`` 排序，``depends_on_json`` 只保存**更早 seq**，天然无环。

设计口径（对齐报告 §5.9）：
- 运行级 token 累计在 ``commander_runs.tokens_total``，子任务级分别记 input/output；
- ``plan_json`` 保存已 clamp 的计划（含 ``fallback`` 标记与自动创建 Agent 记录）；
- 子任务状态 pending|running|ok|error|skipped，运行状态见 ``VALID_RUN_STATUSES``。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: 运行状态（前后端稳定契约，只追加不改含义）
RUN_QUEUED = "queued"
RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_PARTIAL = "partial"
RUN_FAILED = "failed"
RUN_CANCELLED = "cancelled"
VALID_RUN_STATUSES = (
    RUN_QUEUED,
    RUN_RUNNING,
    RUN_COMPLETED,
    RUN_PARTIAL,
    RUN_FAILED,
    RUN_CANCELLED,
)

#: 子任务状态
SUB_PENDING = "pending"
SUB_RUNNING = "running"
SUB_OK = "ok"
SUB_ERROR = "error"
SUB_SKIPPED = "skipped"
VALID_SUB_STATUSES = (SUB_PENDING, SUB_RUNNING, SUB_OK, SUB_ERROR, SUB_SKIPPED)

#: 子任务执行者来源
SOURCE_EXISTING = "existing"
SOURCE_GENERATED = "generated"
SOURCE_COMMANDER = "commander"
VALID_AGENT_SOURCES = (SOURCE_EXISTING, SOURCE_GENERATED, SOURCE_COMMANDER)


class CommanderRun(Base, TimestampMixin):
    """一次指挥官运行。所有读取必须带 yard_id 边界。"""

    __tablename__ = "commander_runs"

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: 已 clamp 的计划（goal/success_criteria/subtasks/fallback/generated_agents）
    plan_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(12), index=True, nullable=False, default=RUN_QUEUED)
    max_parallel: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    effective_parallel: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tokens_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: 发起人当轮角色快照：执行器自建 Session 时重建 YardContext（权限不放大）
    roles_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    #: 计划创建时会话的授权模式快照（ask|auto|full）：full = 完全控制，工具不再逐次询问
    approval_mode: Mapped[str] = mapped_column(String(8), nullable=False, default="ask")


class CommanderSubtask(Base, TimestampMixin):
    """一个子任务。一个子任务必须落一个执行者（Agent 或指挥官代执行）。"""

    __tablename__ = "commander_subtasks"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_commander_subtasks_run_seq"),)

    run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_output: Mapped[str] = mapped_column(Text, nullable=False, default="")
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    agent_source: Mapped[str] = mapped_column(String(12), nullable=False, default=SOURCE_COMMANDER)
    status: Mapped[str] = mapped_column(String(12), index=True, nullable=False, default=SUB_PENDING)
    #: 只保存更早的 seq（不可成环）
    depends_on_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 产物卡列表（文档/图片/链接）；无产物为 None
    artifact_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    #: 自主补救轨迹（[{round, action, agent_name, code, message}]），供汇总解释与排障
    remediation_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
