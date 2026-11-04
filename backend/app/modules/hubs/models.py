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
    __table_args__ = (
        UniqueConstraint("hub_id", "user_id", name="uq_hub_members_hub_user"),
    )

    hub_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False, default=ROLE_VIEWER)


__all__ = [
    "HubMember",
    "MODE_FOCUS",
    "MODE_LABELS",
    "MODE_MUNICIPAL",
    "MODE_PROVINCE",
    "MODE_STATION",
    "ROLE_MANAGER",
    "ROLE_VIEWER",
    "VALID_HUB_ROLES",
    "VALID_MODES",
    "YardHub",
]
