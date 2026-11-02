"""指挥官经验资产：标准工作流、失败黑名单与技能片段（迁移 0062）。

三类资产都按小院隔离：
- ``commander_workflows``：成功运行自动沉淀的可复用步骤模板；
- ``commander_failure_cases``：同类场景失败达阈值后进入避坑清单；
- ``commander_skill_snippets``：从 Worker 成功样本萃取的技能候选与正式技能。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

WORKFLOW_ACTIVE = "active"
WORKFLOW_DRAFT = "draft"
WORKFLOW_ARCHIVED = "archived"
VALID_WORKFLOW_STATUSES = (WORKFLOW_ACTIVE, WORKFLOW_DRAFT, WORKFLOW_ARCHIVED)

FAILURE_WATCHING = "watching"
FAILURE_ACTIVE = "active"
FAILURE_RESOLVED = "resolved"
VALID_FAILURE_STATUSES = (FAILURE_WATCHING, FAILURE_ACTIVE, FAILURE_RESOLVED)

SKILL_CANDIDATE = "candidate"
SKILL_ACTIVE = "active"
SKILL_ARCHIVED = "archived"
VALID_SKILL_STATUSES = (SKILL_CANDIDATE, SKILL_ACTIVE, SKILL_ARCHIVED)

SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"
VALID_ASSET_SOURCES = (SOURCE_AUTO, SOURCE_MANUAL)


class CommanderWorkflow(Base, TimestampMixin):
    """标准工作流：步骤、成本基准、版本与成功/失败计数。"""

    __tablename__ = "commander_workflows"
    __table_args__ = (
        UniqueConstraint("yard_id", "code", name="uq_commander_workflows_code"),
        Index("ix_commander_workflows_yard_status", "yard_id", "status"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    #: 适用场景（原始目标文本，用于检索匹配）
    scene: Mapped[str] = mapped_column(Text, nullable=False, default="")
    preconditions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    #: [{title, instruction, expected_output, agent_code?}]
    steps_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    cost_baseline_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=WORKFLOW_ACTIVE)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SOURCE_AUTO)
    #: 已沉淀的来源运行，防止同一 run 重复计数（只保留最近 30 个）
    source_run_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class CommanderFailureCase(Base, TimestampMixin):
    """失败黑名单：同场景同错误签名累计，达阈值后进入规划规避提示。"""

    __tablename__ = "commander_failure_cases"
    __table_args__ = (
        UniqueConstraint("yard_id", "signature", name="uq_commander_failure_cases_sig"),
        Index("ix_commander_failure_cases_yard_status", "yard_id", "status"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    #: 场景关键词 + 错误码 + 错误摘要的稳定哈希
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    scene: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    error_code: Mapped[str] = mapped_column(String(50), nullable=False, default="UNKNOWN")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    root_cause: Mapped[str] = mapped_column(Text, nullable=False, default="")
    avoidance: Mapped[str] = mapped_column(Text, nullable=False, default="")
    occurrences: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=FAILURE_WATCHING)
    source_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    #: 已计入的来源运行，防止同一 run 重复累计（只保留最近 50 个）
    seen_run_ids_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommanderSkillSnippet(Base, TimestampMixin):
    """技能片段：Worker 的稳定执行范式，先候选、验证后转正。"""

    __tablename__ = "commander_skill_snippets"
    __table_args__ = (
        UniqueConstraint("yard_id", "code", name="uq_commander_skill_snippets_code"),
        Index("ix_commander_skill_snippets_yard_status", "yard_id", "status"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    scene: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    source_agent_code: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    success_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SKILL_CANDIDATE)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=SOURCE_AUTO)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: 证据样本：[{run_id, subtask_run_seq, title, status}]
    evidence_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
