"""农业台账：地块、种植周期、农事记录（v3.8）

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_farm_ledger"
down_revision = "0038_refresh_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farm_plots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("crop", sa.String(60), nullable=False),
        sa.Column("area_mu", sa.Float(), nullable=True),
        sa.Column("location", sa.String(200), nullable=False),
        sa.Column("soil_type", sa.String(40), nullable=False),
        sa.Column("irrigation_type", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("yard_id", "name", name="uq_farm_plots_yard_name"),
    )
    op.create_index("ix_farm_plots_yard_id", "farm_plots", ["yard_id"])
    op.create_index("ix_farm_plots_yard_status", "farm_plots", ["yard_id", "status"])

    op.create_table(
        "farm_cycles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("plot_id", sa.String(36), nullable=False),
        sa.Column("crop", sa.String(60), nullable=False),
        sa.Column("variety", sa.String(60), nullable=False),
        sa.Column("planted_on", sa.Date(), nullable=True),
        sa.Column("expected_harvest_on", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
    )
    op.create_index("ix_farm_cycles_yard_id", "farm_cycles", ["yard_id"])
    op.create_index("ix_farm_cycles_plot_id", "farm_cycles", ["plot_id"])
    op.create_index("ix_farm_cycles_yard_plot", "farm_cycles", ["yard_id", "plot_id"])

    op.create_table(
        "farm_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("plot_id", sa.String(36), nullable=False),
        sa.Column("cycle_id", sa.String(36), nullable=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(20), nullable=False),
        sa.Column("operator_name", sa.String(60), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
    )
    op.create_index("ix_farm_records_yard_id", "farm_records", ["yard_id"])
    op.create_index("ix_farm_records_yard_date", "farm_records", ["yard_id", "occurred_on"])
    op.create_index(
        "ix_farm_records_yard_plot_date", "farm_records", ["yard_id", "plot_id", "occurred_on"]
    )


def downgrade() -> None:
    op.drop_index("ix_farm_records_yard_plot_date", table_name="farm_records")
    op.drop_index("ix_farm_records_yard_date", table_name="farm_records")
    op.drop_index("ix_farm_records_yard_id", table_name="farm_records")
    op.drop_table("farm_records")
    op.drop_index("ix_farm_cycles_yard_plot", table_name="farm_cycles")
    op.drop_index("ix_farm_cycles_plot_id", table_name="farm_cycles")
    op.drop_index("ix_farm_cycles_yard_id", table_name="farm_cycles")
    op.drop_table("farm_cycles")
    op.drop_index("ix_farm_plots_yard_status", table_name="farm_plots")
    op.drop_index("ix_farm_plots_yard_id", table_name="farm_plots")
    op.drop_table("farm_plots")
