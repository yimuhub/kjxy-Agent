"""refresh_sessions：刷新令牌轮换与登出吊销（v3.7）

Revision ID: 0038
Revises: 0037
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0038_refresh_sessions"
down_revision = "0037_chat_citations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_refresh_sessions_jti", "refresh_sessions", ["jti"], unique=True)
    op.create_index("ix_refresh_sessions_user_id", "refresh_sessions", ["user_id"])
    op.create_index(
        "ix_refresh_sessions_user_active", "refresh_sessions", ["user_id", "revoked_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_user_active", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_id", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_jti", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
