"""组织架构推导提案表（v3.58）

只保存模型建议与证据引用，应用前不改动 org_units/org_positions。
"""

import sqlalchemy as sa
from alembic import op

revision = "0066_org_proposals"
down_revision = "0065_org_structure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "org_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(120), nullable=False, server_default=""),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False, server_default="generating"),
        sa.Column("proposal_json", sa.JSON(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("token_usage_json", sa.JSON(), nullable=True),
        sa.Column("model", sa.String(100), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(20), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("applied_by", sa.String(36), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_result_json", sa.JSON(), nullable=True),
    )
    op.create_index("ix_org_proposals_yard_id", "org_proposals", ["yard_id"])
    op.create_index("ix_org_proposals_task_id", "org_proposals", ["task_id"])
    op.create_index("ix_org_proposals_yard_status", "org_proposals", ["yard_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_org_proposals_yard_status", table_name="org_proposals")
    op.drop_index("ix_org_proposals_task_id", table_name="org_proposals")
    op.drop_index("ix_org_proposals_yard_id", table_name="org_proposals")
    op.drop_table("org_proposals")
