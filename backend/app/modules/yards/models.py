"""小院与成员数据模型。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

ROLES = [
    "platform_admin",
    "yard_owner",
    "yard_admin",
    "expert",
    "operator",
    "viewer",
    "service_worker",
]

#: 可分配给成员的常规角色（负责人只能通过“转让”产生）
YARD_ASSIGNABLE_ROLES = ["yard_admin", "expert", "operator", "viewer"]


class Yard(Base, TimestampMixin):
    __tablename__ = "yards"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # active | suspended
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    province: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scene_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_json: Mapped[dict | None] = mapped_column("config", JSON, nullable=True)
    # 小院负责人（唯一，不可被移除/停用，只能转让）
    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # v3.65：所属枢纽（可空；通用基座上的运营模式，见 hubs 模块）
    hub_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


class YardSettings(Base, TimestampMixin):
    """小院设置域：搜索、注册、安全策略等；值由代码侧 schema 校验。"""

    __tablename__ = "yard_settings"
    __table_args__ = (UniqueConstraint("yard_id", "key", name="uq_yard_settings_yard_key"),)

    yard_id: Mapped[str] = mapped_column(String(36), ForeignKey("yards.id"), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    value_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class YardRole(Base, TimestampMixin):
    """小院角色定义：系统角色可覆盖显示名/权限，也承载自定义角色。"""

    __tablename__ = "yard_roles"
    __table_args__ = (UniqueConstraint("yard_id", "code", name="uq_yard_roles_yard_code"),)

    yard_id: Mapped[str] = mapped_column(String(36), ForeignKey("yards.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    permissions_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # 系统角色 code 不可删除；权限仍可在保护规则内调整
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class YardMemberGrant(Base, TimestampMixin):
    """负责人对单个管理员的敏感数据授权（会话正文/用量明细/财务）。"""

    __tablename__ = "yard_member_grants"
    __table_args__ = (
        UniqueConstraint("yard_id", "user_id", name="uq_yard_member_grants_yard_user"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), ForeignKey("yards.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    grants_json: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)


class YardJoinRequest(Base, TimestampMixin):
    """注册/入小院审核：可加入现有小院，也可申请新建小院。"""

    __tablename__ = "yard_join_requests"
    __table_args__ = (Index("ix_yard_join_requests_yard_status", "yard_id", "status"),)

    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="join")  # join|new_yard
    yard_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reject_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)


class YardMembership(Base, TimestampMixin):
    __tablename__ = "yard_memberships"
    __table_args__ = (
        UniqueConstraint("yard_id", "user_id", name="uq_membership_yard_user"),
        Index("ix_membership_yard", "yard_id"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), ForeignKey("yards.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # active | pending | disabled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class YardInvitation(Base, TimestampMixin):
    """成员邀请：一次性令牌邀请链接，避免管理员必须先知道对方已注册账号。

    令牌只存 sha256 摘要（明文仅在创建响应中出现一次）；状态机
    ``pending → accepted | revoked``，过期由 expires_at 判定（不改状态，便于审计）。
    """

    __tablename__ = "yard_invitations"
    __table_args__ = (Index("ix_yard_invitations_yard", "yard_id"),)

    yard_id: Mapped[str] = mapped_column(String(36), ForeignKey("yards.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # pending | accepted | revoked
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    accepted_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
