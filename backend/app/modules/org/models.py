"""组织架构 ORM：组织单元树 + 岗位（可绑定 Agent）。

口径：
- 一切按 ``yard_id`` 隔离；跨院读写一律 404；
- 组织单元用 ``parent_id`` 自关联，kind 区分主体/合作社/部门/小组；
- 岗位挂在单元下，``agent_id`` 可选绑定本小院 Agent（软关联，删除 Agent 不级联删岗位）；
- 删除保护：有子单元或岗位的单元不允许直接删除，先归档。
"""

from sqlalchemy import Index, Integer, String, Text, UniqueConstraint
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
