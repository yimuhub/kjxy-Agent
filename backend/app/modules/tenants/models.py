"""租户与成员数据模型。"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

ROLES = ["platform_admin", "tenant_admin", "expert", "operator", "viewer", "service_worker"]


class Tenant(Base, TimestampMixin):
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # active | suspended
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    province: Mapped[str | None] = mapped_column(String(64), nullable=True)
    city: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scene_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    config_json: Mapped[dict | None] = mapped_column("config", JSON, nullable=True)


class TenantMembership(Base, TimestampMixin):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        Index("ix_membership_tenant", "tenant_id"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    # active | disabled
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")


class TenantInvitation(Base, TimestampMixin):
    """成员邀请：一次性令牌邀请链接，避免管理员必须先知道对方已注册账号。

    令牌只存 sha256 摘要（明文仅在创建响应中出现一次）；状态机
    ``pending → accepted | revoked``，过期由 expires_at 判定（不改状态，便于审计）。
    """

    __tablename__ = "tenant_invitations"
    __table_args__ = (Index("ix_tenant_invitations_tenant", "tenant_id"),)

    tenant_id: Mapped[str] = mapped_column(String(36), ForeignKey("tenants.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # pending | accepted | revoked
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    accepted_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
