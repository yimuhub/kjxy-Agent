"""跨院只读分享链接（v3.12）

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_share_links"
down_revision = "0041_alerts_work_orders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "share_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("resource_type", sa.String(20), nullable=False),
        sa.Column("resource_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_share_links_yard_id", "share_links", ["yard_id"])
    op.create_index("ix_share_links_token_hash", "share_links", ["token_hash"], unique=True)
    op.create_index("ix_share_links_yard_created", "share_links", ["yard_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_share_links_yard_created", table_name="share_links")
    op.drop_index("ix_share_links_token_hash", table_name="share_links")
    op.drop_index("ix_share_links_yard_id", table_name="share_links")
    op.drop_table("share_links")
