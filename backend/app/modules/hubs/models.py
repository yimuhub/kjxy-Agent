"""枢纽 ORM（迁移 0071，v3.65）：通用基座上的运营模式。

四种模式（mode）对应全国调研的三种真实治理形态 + 独立小院默认态：
- province   省级科协模式（江苏：培养单位/省级联络站为枢纽，小院跨市分散）
- municipal  市级全域模式（邯郸：市级专班强枢纽，县域全覆盖）
- station    实验站枢纽模式（曲周：实体实验站统管小院群，一院一策）
- standalone 独立小院（不建枢纽，默认态）

差异参数化在枢纽上（同一平台可同时存在不同模式的枢纽），聚合/权限结构共用。
"""

from __future__ import annotations

from sqlalchemy import String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

MODE_PROVINCE = "province"
MODE_MUNICIPAL = "municipal"
MODE_STATION = "station"
VALID_MODES = (MODE_PROVINCE, MODE_MUNICIPAL, MODE_STATION)

ROLE_MANAGER = "manager"
ROLE_VIEWER = "viewer"
VALID_HUB_ROLES = (ROLE_MANAGER, ROLE_VIEWER)

#: v3.67 枢纽↔小院关系类型：一个小院可同时被多个枢纽以不同口径关联
#: - manage  日常管理（实验站/市专班统管，唯一可作「主归属」的口径）
#: - review  评审归口（省科协评价，不干预小院内部管理）
#: - observe 观察（只读纳入，如示范跟踪）
RELATION_MANAGE = "manage"
RELATION_REVIEW = "review"
RELATION_OBSERVE = "observe"
VALID_RELATION_TYPES = (RELATION_MANAGE, RELATION_REVIEW, RELATION_OBSERVE)
RELATION_LABELS = {
    RELATION_MANAGE: "日常管理",
    RELATION_REVIEW: "评审归口",
    RELATION_OBSERVE: "观察",
}

#: 模式的中文与工作台侧重（前端文案同源）
MODE_LABELS = {
    MODE_PROVINCE: "省级科协模式（培养单位枢纽）",
    MODE_MUNICIPAL: "市级全域模式（市级专班）",
    MODE_STATION: "实验站枢纽模式（小院群）",
}

#: 每种模式工作台的侧重说明（前端按 mode 渲染）
MODE_FOCUS = {
    MODE_PROVINCE: "跨市分散小院 · 按培养单位聚合 · 达标监测（100 篇锚点）",
    MODE_MUNICIPAL: "县域全覆盖 · 全域推广进度 · 断更跟进",
    MODE_STATION: "小院群横向对比 · 一院一策 · 类型分布",
}


class YardHub(Base, TimestampMixin):
    __tablename__ = "yard_hubs"

    name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    mode: Mapped[str] = mapped_column(String(12), nullable=False)
    region: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)


class HubMember(Base, TimestampMixin):
    __tablename__ = "hub_members"
    __table_args__ = (UniqueConstraint("hub_id", "user_id", name="uq_hub_members_hub_user"),)

    hub_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False, default=ROLE_VIEWER)


class HubYard(Base, TimestampMixin):
    """枢纽↔小院多对多关系（v3.67，迁移 0073）。

    替代 ``yards.hub_id`` 的单归属：一个院可同时「被实验站日常管理」且
    「被省科协评审归口」。``yards.hub_id`` 保留为**主归属**（manage 口径），
    老数据在迁移中幂等回填为一条 manage 关系。
    """

    __tablename__ = "hub_yards"
    __table_args__ = (UniqueConstraint("hub_id", "yard_id", name="uq_hub_yards_hub_yard"),)

    hub_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    relation_type: Mapped[str] = mapped_column(String(12), nullable=False, default=RELATION_MANAGE)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False, default="")


__all__ = [
    "HubMember",
    "HubYard",
    "MODE_FOCUS",
    "MODE_LABELS",
    "MODE_MUNICIPAL",
    "MODE_PROVINCE",
    "MODE_STATION",
    "RELATION_LABELS",
    "RELATION_MANAGE",
    "RELATION_OBSERVE",
    "RELATION_REVIEW",
    "ROLE_MANAGER",
    "ROLE_VIEWER",
    "VALID_HUB_ROLES",
    "VALID_MODES",
    "VALID_RELATION_TYPES",
    "YardHub",
]
