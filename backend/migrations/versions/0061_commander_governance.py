"""监督测评与版本快照表（v3.44 P1）

- ``commander_evaluations``：每周一份测评报告，``(yard_id, week_key)`` 唯一；
- ``commander_snapshots``：可回滚的指挥官设置/人格快照。

纯增量迁移，可完整 downgrade。
"""

import sqlalchemy as sa
from alembic import op

revision = "0061_commander_governance"
down_revision = "0060_commander_memory"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commander_evaluations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("week_key", sa.String(16), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("persona", sa.String(16), nullable=False, server_default="balanced"),
        sa.Column("previous_persona", sa.String(16), nullable=True),
        sa.Column("persona_applied", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(8), nullable=False, server_default="rule"),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scores_json", sa.JSON(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("report_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("strengths_json", sa.JSON(), nullable=False),
        sa.Column("shortfalls_json", sa.JSON(), nullable=False),
        sa.Column("recommendations_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("yard_id", "week_key", name="uq_commander_evaluations_week"),
    )
    op.create_index("ix_commander_evaluations_yard_id", "commander_evaluations", ["yard_id"])
    op.create_index(
        "ix_commander_evaluations_yard_created",
        "commander_evaluations",
        ["yard_id", "created_at"],
    )

    op.create_table(
        "commander_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("label", sa.String(60), nullable=False, server_default=""),
        sa.Column("reason", sa.String(200), nullable=False, server_default=""),
        sa.Column("source", sa.String(12), nullable=False, server_default="manual"),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
    )
    op.create_index("ix_commander_snapshots_yard_id", "commander_snapshots", ["yard_id"])
    op.create_index(
        "ix_commander_snapshots_yard_created",
        "commander_snapshots",
        ["yard_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_commander_snapshots_yard_created", table_name="commander_snapshots")
    op.drop_index("ix_commander_snapshots_yard_id", table_name="commander_snapshots")
    op.drop_table("commander_snapshots")
    op.drop_index("ix_commander_evaluations_yard_created", table_name="commander_evaluations")
    op.drop_index("ix_commander_evaluations_yard_id", table_name="commander_evaluations")
    op.drop_table("commander_evaluations")
