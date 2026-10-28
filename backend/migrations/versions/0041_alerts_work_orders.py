"""告警、工单与回执（v3.10）

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_alerts_work_orders"
down_revision = "0040_diagnosis_loop"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farm_alerts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("plot_id", sa.String(36), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("work_order_id", sa.String(36), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
    )
    op.create_index("ix_farm_alerts_yard_id", "farm_alerts", ["yard_id"])
    op.create_index("ix_farm_alerts_yard_created", "farm_alerts", ["yard_id", "created_at"])

    op.create_table(
        "work_orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(36), nullable=True),
        sa.Column("plot_id", sa.String(36), nullable=True),
        sa.Column("assignee_id", sa.String(36), nullable=True),
        sa.Column("assignee_name", sa.String(60), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=True),
        sa.Column("escalation_level", sa.Integer(), nullable=False),
        sa.Column("resolution", sa.Text(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(36), nullable=True),
    )
    op.create_index("ix_work_orders_yard_id", "work_orders", ["yard_id"])
    op.create_index("ix_work_orders_yard_status", "work_orders", ["yard_id", "status"])
    op.create_index("ix_work_orders_yard_due", "work_orders", ["yard_id", "due_on"])

    op.create_table(
        "work_order_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("work_order_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(36), nullable=True),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=True),
    )
    op.create_index("ix_work_order_events_yard_id", "work_order_events", ["yard_id"])
    op.create_index(
        "ix_work_order_events_work_order_id", "work_order_events", ["work_order_id"]
    )
    op.create_index(
        "ix_work_order_events_yard_order",
        "work_order_events",
        ["yard_id", "work_order_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_work_order_events_yard_order", table_name="work_order_events")
    op.drop_index("ix_work_order_events_work_order_id", table_name="work_order_events")
    op.drop_index("ix_work_order_events_yard_id", table_name="work_order_events")
    op.drop_table("work_order_events")
    op.drop_index("ix_work_orders_yard_due", table_name="work_orders")
    op.drop_index("ix_work_orders_yard_status", table_name="work_orders")
    op.drop_index("ix_work_orders_yard_id", table_name="work_orders")
    op.drop_table("work_orders")
    op.drop_index("ix_farm_alerts_yard_created", table_name="farm_alerts")
    op.drop_index("ix_farm_alerts_yard_id", table_name="farm_alerts")
    op.drop_table("farm_alerts")
