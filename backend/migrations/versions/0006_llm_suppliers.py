"""模型供应商表：suppliers + tenant_active_suppliers（架构补强 A 组）。

Revision ID: 0006_llm_suppliers
Revises: 0005_tenant_quotas
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0006_llm_suppliers"
down_revision = "0005_tenant_quotas"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # NULL=全局共享；非空=仅归属租户可见
        sa.Column("tenant_id", sa.String(36), nullable=True),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("provider_type", sa.String(30), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=False),
        sa.Column("models", sa.Text(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("test_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_time_ms", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])
    op.create_index("ix_suppliers_status", "suppliers", ["status"])

    op.create_table(
        "tenant_active_suppliers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False, unique=True),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("suppliers.id"), nullable=True),
    )
    op.create_index(
        "ix_tenant_active_suppliers_tenant_id", "tenant_active_suppliers", ["tenant_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tenant_active_suppliers_tenant_id", table_name="tenant_active_suppliers")
    op.drop_table("tenant_active_suppliers")
    op.drop_index("ix_suppliers_status", table_name="suppliers")
    op.drop_index("ix_suppliers_tenant_id", table_name="suppliers")
    op.drop_table("suppliers")
