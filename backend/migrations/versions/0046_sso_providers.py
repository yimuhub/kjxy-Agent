"""SSO/OIDC 接入（v3.18）

新增三张表：
- sso_providers：小院级 OIDC 配置，client_secret 只存 Fernet 密文；
- sso_identities：外部 subject ↔ 本地用户绑定，(idp_id, subject) 唯一；
- sso_exchange_codes：回调后换取登录态的一次性短码，只存 sha256。

纯增量迁移；回滚即删三张表，不影响既有账号与登录。

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0046_sso_providers"
down_revision = "0045_skill_attestation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sso_providers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("issuer", sa.String(length=500), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("client_secret_enc", sa.Text(), nullable=False),
        sa.Column("redirect_uri", sa.String(length=500), nullable=False),
        sa.Column("frontend_redirect_uri", sa.String(length=500), nullable=True),
        sa.Column(
            "scopes", sa.String(length=255), nullable=False, server_default="openid email profile"
        ),
        sa.Column("group_claim", sa.String(length=80), nullable=False, server_default="groups"),
        sa.Column("role_mapping_json", sa.JSON(), nullable=True),
        sa.Column("default_role", sa.String(length=32), nullable=False, server_default="viewer"),
        sa.Column("email_domains_json", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("auto_create_users", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("link_by_email", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("require_email_verified", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("yard_id", "name", name="uq_sso_provider_yard_name"),
    )
    op.create_index("ix_sso_providers_yard_id", "sso_providers", ["yard_id"])
    op.create_index("ix_sso_providers_yard_enabled", "sso_providers", ["yard_id", "enabled"])

    op.create_table(
        "sso_identities",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("idp_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["idp_id"], ["sso_providers.id"]),
        sa.UniqueConstraint("idp_id", "subject", name="uq_sso_identity_idp_subject"),
    )
    op.create_index("ix_sso_identities_idp_id", "sso_identities", ["idp_id"])
    op.create_index("ix_sso_identities_user", "sso_identities", ["user_id"])

    op.create_table(
        "sso_exchange_codes",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("yard_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_sso_exchange_code_hash", "sso_exchange_codes", ["code_hash"], unique=True)
    # 每次登录签发短码时顺手清理过期行，避免表无限增长
    op.create_index("ix_sso_exchange_codes_expires_at", "sso_exchange_codes", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_sso_exchange_codes_expires_at", table_name="sso_exchange_codes")
    op.drop_index("ix_sso_exchange_code_hash", table_name="sso_exchange_codes")
    op.drop_table("sso_exchange_codes")

    op.drop_index("ix_sso_identities_user", table_name="sso_identities")
    op.drop_index("ix_sso_identities_idp_id", table_name="sso_identities")
    op.drop_table("sso_identities")

    op.drop_index("ix_sso_providers_yard_enabled", table_name="sso_providers")
    op.drop_index("ix_sso_providers_yard_id", table_name="sso_providers")
    op.drop_table("sso_providers")
