"""组织架构 ORM：组织单元树 + 岗位（可绑定 Agent）。

口径：
- 一切按 ``yard_id`` 隔离；跨院读写一律 404；
- 组织单元用 ``parent_id`` 自关联，kind 区分主体/合作社/部门/小组；
- 岗位挂在单元下，``agent_id`` 可选绑定本小院 Agent（软关联，删除 Agent 不级联删岗位）；
- 删除保护：有子单元或岗位的单元不允许直接删除，先归档。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

UNIT_KINDS = ("company", "coop", "department", "team")
UNIT_KIND_LABELS = {
    "company": "组织主体",
    "coop": "合作社",
    "department": "部门",
    "team": "小组",
}

UNIT_STATUS_ACTIVE = "active"
UNIT_STATUS_ARCHIVED = "archived"
UNIT_STATUSES = (UNIT_STATUS_ACTIVE, UNIT_STATUS_ARCHIVED)

SOURCE_MANUAL = "manual"
SOURCE_KNOWLEDGE = "knowledge"
SOURCE_GENERATED = "generated"
UNIT_SOURCES = (SOURCE_MANUAL, SOURCE_KNOWLEDGE, SOURCE_GENERATED)

MAX_DEPTH = 8

PROPOSAL_STATUS_GENERATING = "generating"
PROPOSAL_STATUS_READY = "ready"
PROPOSAL_STATUS_APPLIED = "applied"
PROPOSAL_STATUS_DISCARDED = "discarded"
PROPOSAL_STATUS_FAILED = "failed"
PROPOSAL_STATUSES = (
    PROPOSAL_STATUS_GENERATING,
    PROPOSAL_STATUS_READY,
    PROPOSAL_STATUS_APPLIED,
    PROPOSAL_STATUS_DISCARDED,
    PROPOSAL_STATUS_FAILED,
)


class OrgUnit(Base, TimestampMixin):
    __tablename__ = "org_units"
    __table_args__ = (
        UniqueConstraint("yard_id", "code", name="uq_org_units_yard_code"),
        Index("ix_org_units_yard_parent", "yard_id", "parent_id"),
        Index("ix_org_units_yard_sort", "yard_id", "sort_order"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    parent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="department")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=UNIT_STATUS_ACTIVE)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default=SOURCE_MANUAL)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class OrgPosition(Base, TimestampMixin):
    __tablename__ = "org_positions"
    __table_args__ = (
        Index("ix_org_positions_yard_unit", "yard_id", "org_unit_id"),
        Index("ix_org_positions_yard_sort", "yard_id", "sort_order"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    org_unit_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    responsibilities: Mapped[str | None] = mapped_column(Text, nullable=True)
    headcount: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 软关联：不建外键，避免 Agent 删除/版本化时级联；查询时按小院复核
    agent_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=UNIT_STATUS_ACTIVE)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class OrgProposal(Base, TimestampMixin):
    """知识库推导出的组织架构提案（v3.58）。

    只保存模型产出的结构化建议与证据引用；用户确认 apply 之前不碰真实组织表。
    """

    __tablename__ = "org_proposals"
    __table_args__ = (Index("ix_org_proposals_yard_status", "yard_id", "status"),)

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    instruction: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=PROPOSAL_STATUS_GENERATING
    )
    proposal_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    evidence_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    token_usage_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False, default="")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    applied_result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
