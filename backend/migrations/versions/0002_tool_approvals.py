"""高风险工具审批单表 tool_approvals（第一期 1.3）。

Revision ID: 0002_tool_approvals
Revises: 0001_initial
Create Date: 2026-10-16
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_tool_approvals"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def _ts():
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "tool_approvals",
        *_ts(),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("tool_code", sa.String(100), nullable=False),
        sa.Column("params", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("params_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("requested_by", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("reason", sa.Text, nullable=True),
        sa.Column("decided_by", sa.String(36), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text, nullable=True),
    )
    op.create_index("ix_tool_approvals_tenant_id", "tool_approvals", ["tenant_id"])
    op.create_index("ix_tool_approvals_tool_code", "tool_approvals", ["tool_code"])
    op.create_index("ix_tool_approvals_params_hash", "tool_approvals", ["params_hash"])
    op.create_index("ix_tool_approvals_status", "tool_approvals", ["status"])
    op.create_index("ix_tool_approvals_task_id", "tool_approvals", ["task_id"])


def downgrade() -> None:
    op.drop_table("tool_approvals")
