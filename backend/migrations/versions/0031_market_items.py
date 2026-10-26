"""插件/技能市场条目缓存。

Revision ID: 0031_market_items
Revises: 0030_identity_rbac
Create Date: 2026-10-27
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_market_items"
down_revision = "0030_identity_rbac"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "market_items",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("item_key", sa.String(220), nullable=False, unique=True),
        sa.Column("item_type", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("code", sa.String(120), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("version", sa.String(50), nullable=False, server_default="1.0.0"),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("license", sa.String(50), nullable=True),
        sa.Column("author", sa.String(120), nullable=True),
        sa.Column("checksum", sa.String(64), nullable=True),
        sa.Column("risk_level", sa.String(16), nullable=False, server_default="low"),
        sa.Column("status", sa.String(16), nullable=False, server_default="available"),
        sa.Column("definition_json", sa.JSON(), nullable=True),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_market_items_type_source", "market_items", ["item_type", "source"], unique=False
    )
    op.create_index("ix_market_items_status", "market_items", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_market_items_status", table_name="market_items")
    op.drop_index("ix_market_items_type_source", table_name="market_items")
    op.drop_table("market_items")
