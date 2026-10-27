"""班组 ORM 模型（迁移 0014）。

三张表构成班组协作的完整账本：

- ``AgentTeam``：班组定义。``members_json`` 是**有序**成员数组
  ``[{agent_id, role_name, instruction, is_critic}]``（数量 2..8 由服务层校验，
  SQLite 侧无法用 CHECK 表达数组基数）；同小院内班组名唯一。
- ``TeamRun``：一次班组运行。``members_json`` 为运行时成员快照——班组事后
  改名/换成员不影响既有 run 的续跑与审计口径；``current_member`` 指向当前
  执行位（断点位置）；成员失败即 ``failed`` 停在断点。
- ``TeamRunMember``：成员接力台账。``task_id`` = 该成员声明式 run 的**预置
  run_id**（先落库再执行）：崩溃/失败后按 W1 的 agent_run_steps checkpoint
  续跑，已 ok 的工具副作用不重放；``status`` 生命周期
  pending → running → ok | error。
"""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

# 班组运行状态（与迁移默认值/服务层保持一致）
RUN_QUEUED = "queued"
RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_FAILED = "failed"
VALID_RUN_STATUSES = (RUN_QUEUED, RUN_RUNNING, RUN_COMPLETED, RUN_FAILED)

# 成员步状态
MEMBER_PENDING = "pending"
MEMBER_RUNNING = "running"
MEMBER_OK = "ok"
MEMBER_ERROR = "error"
VALID_MEMBER_STATUSES = (MEMBER_PENDING, MEMBER_RUNNING, MEMBER_OK, MEMBER_ERROR)


class AgentTeam(Base, TimestampMixin):
    __tablename__ = "agent_teams"
    __table_args__ = (UniqueConstraint("yard_id", "name", name="uq_agent_teams_yard_name"),)

    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 展示/选型提示（不参与调度：各成员仍走各自 Agent 的供应商解析链）
    model_hint: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 有序成员数组：[{agent_id, role_name, instruction, is_critic}]（服务层校验 2..8）
    members_json: Mapped[list] = mapped_column(JSON, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    schedule_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    schedule_created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )


class TeamRun(Base, TimestampMixin):
    __tablename__ = "team_runs"

    team_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    yard_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    team_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    output_format: Mapped[str] = mapped_column(String(8), nullable=False, default="text")
    research_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    document_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 原始问题：每个成员的接力输入都以它为前缀重建
    input: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=RUN_QUEUED)
    # 最终答案（completed 时写入）
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 运行时成员定义快照（与 agent_teams.members_json 同构）
    members_json: Mapped[list] = mapped_column(JSON, nullable=False)
    # 当前执行位（成员 index；重试从这里附近继续）
    current_member: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TeamRunMember(Base, TimestampMixin):
    __tablename__ = "team_run_members"

    team_run_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    # 接力顺序位（班组成员数组下标）
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    member_agent_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 预置的声明式 run_id（= agent_run_steps.run_id）：先落库再执行
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default=MEMBER_PENDING)
    # 该成员实际收到的接力输入（截断预览，可审计拼接是否正确）
    input_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
