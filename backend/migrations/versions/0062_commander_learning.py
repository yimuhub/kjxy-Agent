"""指挥官经验资产表（v3.45 P2）

- ``commander_workflows``：成功运行沉淀的标准工作流；
- ``commander_failure_cases``：失败达阈值的黑名单；
- ``commander_skill_snippets``：跨 Agent 技能候选与正式技能。

纯增量迁移，可完整 downgrade。
"""

import sqlalchemy as sa
from alembic import op

revision = "0062_commander_learning"
down_revision = "0061_commander_governance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commander_workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("scene", sa.Text(), nullable=False, server_default=""),
        sa.Column("preconditions", sa.Text(), nullable=False, server_default=""),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("cost_baseline_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("source_run_ids_json", sa.JSON(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("yard_id", "code", name="uq_commander_workflows_code"),
    )
    op.create_index(
        "ix_commander_workflows_yard_status", "commander_workflows", ["yard_id", "status"]
    )

    op.create_table(
        "commander_failure_cases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("scene", sa.String(500), nullable=False, server_default=""),
        sa.Column("error_code", sa.String(50), nullable=False, server_default="UNKNOWN"),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("root_cause", sa.Text(), nullable=False, server_default=""),
        sa.Column("avoidance", sa.Text(), nullable=False, server_default=""),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="watching"),
        sa.Column("source_run_id", sa.String(36), nullable=True),
        sa.Column("seen_run_ids_json", sa.JSON(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("yard_id", "signature", name="uq_commander_failure_cases_sig"),
    )
    op.create_index(
        "ix_commander_failure_cases_yard_status", "commander_failure_cases", ["yard_id", "status"]
    )

    op.create_table(
        "commander_skill_snippets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("scene", sa.Text(), nullable=False, server_default=""),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_agent_code", sa.String(100), nullable=False, server_default=""),
        sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success_rate", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(16), nullable=False, server_default="candidate"),
        sa.Column("source", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.UniqueConstraint("yard_id", "code", name="uq_commander_skill_snippets_code"),
    )
    op.create_index(
        "ix_commander_skill_snippets_yard_status",
        "commander_skill_snippets",
        ["yard_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_commander_skill_snippets_yard_status", table_name="commander_skill_snippets"
    )
    op.drop_table("commander_skill_snippets")
    op.drop_index(
        "ix_commander_failure_cases_yard_status", table_name="commander_failure_cases"
    )
    op.drop_table("commander_failure_cases")
    op.drop_index("ix_commander_workflows_yard_status", table_name="commander_workflows")
    op.drop_table("commander_workflows")
