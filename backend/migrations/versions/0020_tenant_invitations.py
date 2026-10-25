"""成员邀请：``tenant_invitations`` 表（一次性令牌邀请链接）。

Revision ID: 0020_tenant_invitations
Revises: 0019_bot_gateway
Create Date: 2026-10-26

背景：成员管理此前只能添加已注册账号，新空间无法拉同事。邀请链接以一次性
令牌（库内仅存 sha256 摘要）授予指定角色，支持过期与撤销；接受时可创建新账号
或让既有账号加入新空间。

可逆：downgrade 只删除本表，不动既有成员与账号数据。
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_tenant_invitations"
down_revision = "0019_bot_gateway"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_invitations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("note", sa.String(120), nullable=True),
        # pending | accepted | revoked
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("accepted_user_id", sa.String(36), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_tenant_invitations_token_hash", "tenant_invitations", ["token_hash"], unique=True
    )
    op.create_index("ix_tenant_invitations_tenant", "tenant_invitations", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_tenant_invitations_tenant", table_name="tenant_invitations")
    op.drop_index("ix_tenant_invitations_token_hash", table_name="tenant_invitations")
    op.drop_table("tenant_invitations")
