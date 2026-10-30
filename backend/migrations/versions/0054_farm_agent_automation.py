"""Agent 自动执行（v3.28）

- ``farm_agent_runs``：每次自动执行的证据摘要、规则发现、结论与消耗；
- ``farm_agent_actions``：每个动作的产出与去向（告警/工单/诊断/台账/药剂/地块），
  ``pending`` 即「待人工确认」，带 ``dedupe_key`` 做降噪；
- ``farm_records.source``/``agent_run_id``、``farm_alerts.agent_run_id``、
  ``work_orders.agent_run_id``：让自动产物可追溯、在界面上标「Agent 自动」。

纯增量迁移，可完整 downgrade。

Revision ID: 0054_farm_agent_automation
Revises: 0053_diagnosis_attachments
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0054_farm_agent_automation"
down_revision = "0053_diagnosis_attachments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "farm_agent_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("window_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("findings_json", sa.JSON(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("tokens_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_farm_agent_runs_yard_id", "farm_agent_runs", ["yard_id"])
    op.create_index("ix_farm_agent_runs_yard_created", "farm_agent_runs", ["yard_id", "created_at"])
    op.create_index("ix_farm_agent_runs_yard_status", "farm_agent_runs", ["yard_id", "status"])

    op.create_table(
        "farm_agent_actions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("rule_code", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("policy", sa.String(length=16), nullable=False, server_default="confirm"),
        sa.Column("severity", sa.String(length=16), nullable=False, server_default="normal"),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("target_type", sa.String(length=24), nullable=True),
        sa.Column("target_id", sa.String(length=36), nullable=True),
        sa.Column("dedupe_key", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("error", sa.String(length=300), nullable=True),
        sa.Column("confirmed_by", sa.String(length=36), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_farm_agent_actions_yard_id", "farm_agent_actions", ["yard_id"])
    op.create_index("ix_farm_agent_actions_run_id", "farm_agent_actions", ["run_id"])
    op.create_index(
        "ix_farm_agent_actions_yard_status", "farm_agent_actions", ["yard_id", "status"]
    )
    op.create_index("ix_farm_agent_actions_yard_run", "farm_agent_actions", ["yard_id", "run_id"])
    op.create_index(
        "ix_farm_agent_actions_yard_dedupe", "farm_agent_actions", ["yard_id", "dedupe_key"]
    )

    op.add_column(
        "farm_records",
        sa.Column("source", sa.String(length=16), nullable=False, server_default="manual"),
    )
    op.add_column("farm_records", sa.Column("agent_run_id", sa.String(length=36), nullable=True))
    op.add_column("farm_alerts", sa.Column("agent_run_id", sa.String(length=36), nullable=True))
    op.add_column("work_orders", sa.Column("agent_run_id", sa.String(length=36), nullable=True))


def downgrade() -> None:
    op.drop_column("work_orders", "agent_run_id")
    op.drop_column("farm_alerts", "agent_run_id")
    op.drop_column("farm_records", "agent_run_id")
    op.drop_column("farm_records", "source")

    op.drop_index("ix_farm_agent_actions_yard_dedupe", table_name="farm_agent_actions")
    op.drop_index("ix_farm_agent_actions_yard_run", table_name="farm_agent_actions")
    op.drop_index("ix_farm_agent_actions_yard_status", table_name="farm_agent_actions")
    op.drop_index("ix_farm_agent_actions_run_id", table_name="farm_agent_actions")
    op.drop_index("ix_farm_agent_actions_yard_id", table_name="farm_agent_actions")
    op.drop_table("farm_agent_actions")

    op.drop_index("ix_farm_agent_runs_yard_status", table_name="farm_agent_runs")
    op.drop_index("ix_farm_agent_runs_yard_created", table_name="farm_agent_runs")
    op.drop_index("ix_farm_agent_runs_yard_id", table_name="farm_agent_runs")
    op.drop_table("farm_agent_runs")
