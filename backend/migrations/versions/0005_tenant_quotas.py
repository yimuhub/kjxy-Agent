"""租户配额表：tenant_quotas（第五期 5.2 配额计量）。

Revision ID: 0005_tenant_quotas
Revises: 0004_skill_rollout
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_tenant_quotas"
down_revision = "0004_skill_rollout"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tenant_quotas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("tenant_id", sa.String(36), nullable=False, unique=True),
        sa.Column("llm_calls_limit", sa.Integer, nullable=False, server_default="1000"),
        sa.Column("llm_calls_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("knowledge_docs_limit", sa.Integer, nullable=False, server_default="100"),
        sa.Column("knowledge_docs_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tasks_limit", sa.Integer, nullable=False, server_default="500"),
        sa.Column("tasks_used", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_tenant_quotas_tenant_id", "tenant_quotas", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_tenant_quotas_tenant_id", table_name="tenant_quotas")
    op.drop_table("tenant_quotas")
