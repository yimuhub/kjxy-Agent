"""0074 品牌与门户（v3.67）。

``platform_branding`` 单行承载 logo / 名称 / 主色 / 对外域名；空值在出参里按
默认文案补齐，所以不需要种子数据。``platform_grants`` 承载平台级逐人授权
（``branding:manage``）：与 ``yard_member_grants`` 不同，它不绑小院，只有平台
超管能写，避免小院负责人把平台品牌权限授给成员。
"""

import sqlalchemy as sa
from alembic import op

revision = "0074_platform_branding"
down_revision = "0073_hub_yards"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_branding",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("app_name", sa.String(40), nullable=False, server_default=""),
        sa.Column("short_name", sa.String(8), nullable=False, server_default=""),
        sa.Column("login_tagline", sa.String(40), nullable=False, server_default=""),
        sa.Column("footer_text", sa.String(80), nullable=False, server_default=""),
        sa.Column("primary_color", sa.String(7), nullable=False, server_default=""),
        sa.Column("logo_key", sa.String(255), nullable=True),
        sa.Column("logo_mime", sa.String(64), nullable=True),
        sa.Column("domains_json", sa.JSON(), nullable=False),
        sa.Column("updated_by", sa.String(36), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "platform_grants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False, index=True),
        sa.Column("grants_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", name="uq_platform_grants_user"),
    )


def downgrade() -> None:
    op.drop_table("platform_grants")
    op.drop_table("platform_branding")
