"""SSO/OIDC 模型（v3.18）：小院级 IdP、外部身份绑定、一次性换取码。"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class SsoProvider(Base, TimestampMixin):
    """小院配置的一个 OIDC 身份提供方。

    client_secret 只存密文（复用 SUPPLIER_KEY_SECRET 的 Fernet）；接口一律只回
    ``client_secret_configured``，绝不回明文。
    """

    __tablename__ = "sso_providers"
    __table_args__ = (
        UniqueConstraint("yard_id", "name", name="uq_sso_provider_yard_name"),
        Index("ix_sso_providers_yard_enabled", "yard_id", "enabled"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    issuer: Mapped[str] = mapped_column(String(500), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    client_secret_enc: Mapped[str] = mapped_column(Text, nullable=False)
    redirect_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    #: 回调成功后浏览器落地的前端地址（如 https://host/sso/callback）；为空则取
    #: redirect_uri 的 origin + /sso/callback
    frontend_redirect_uri: Mapped[str | None] = mapped_column(String(500), nullable=True)
    scopes: Mapped[str] = mapped_column(String(255), nullable=False, default="openid email profile")
    #: 组/角色 claim 名（Keycloak 常见 groups，Azure 常见 roles）
    group_claim: Mapped[str] = mapped_column(String(80), nullable=False, default="groups")
    #: {组名: 小院角色}；平台管理员/负责人不可由映射授予
    role_mapping_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    default_role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="viewer", server_default="viewer"
    )
    #: 邮箱域白名单（小写、不含 @）；空列表=不限制
    email_domains_json: Mapped[list | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    auto_create_users: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    link_by_email: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    require_email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SsoIdentity(Base, TimestampMixin):
    """外部身份与本地用户的绑定：同一 IdP 下 subject 唯一。"""

    __tablename__ = "sso_identities"
    __table_args__ = (
        UniqueConstraint("idp_id", "subject", name="uq_sso_identity_idp_subject"),
        Index("ix_sso_identities_user", "user_id"),
    )

    idp_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sso_providers.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SsoExchangeCode(Base, TimestampMixin):
    """回调换取登录态的一次性短码：只存 sha256，用过即作废。"""

    __tablename__ = "sso_exchange_codes"
    __table_args__ = (Index("ix_sso_exchange_code_hash", "code_hash", unique=True),)

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
