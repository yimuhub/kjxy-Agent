"""指挥官月度迭代表（v3.46 P3）

``commander_iterations``：每月一份版本说明、指标与快照引用；
``(yard_id, month_key)`` 唯一，重复执行只返回已有版本。

纯增量迁移，可完整 downgrade。
"""

import sqlalchemy as sa
from alembic import op

revision = "0063_commander_iterations"
down_revision = "0062_commander_learning"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commander_iterations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("month_key", sa.String(7), nullable=False),
        sa.Column("version_label", sa.String(20), nullable=False),
        sa.Column("persona", sa.String(16), nullable=False, server_default="balanced"),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("report_md", sa.Text(), nullable=False, server_default=""),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("changes_json", sa.JSON(), nullable=False),
        sa.Column("snapshot_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("yard_id", "month_key", name="uq_commander_iterations_month"),
    )
    op.create_index(
        "ix_commander_iterations_yard_created",
        "commander_iterations",
        ["yard_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_commander_iterations_yard_created", table_name="commander_iterations")
    op.drop_table("commander_iterations")
