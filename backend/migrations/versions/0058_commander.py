"""指挥官运行台账与会话执行模式（v3.38）

- ``commander_runs``：一次「计划—选兵—并行—汇总」的运行主记录（计划、并发、
  降级、token、耗时、状态）；
- ``commander_subtasks``：子任务 DAG（depends_on 只指向更早 seq，天然无环）；
- ``chat_sessions.execution_mode``：``standard`` / ``commander``，默认 standard。

纯增量迁移，可完整 downgrade；不修改既有列语义。

Revision ID: 0058_commander
Revises: 0057_official_library
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0058_commander"
down_revision = "0057_official_library"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "commander_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False, server_default=""),
        sa.Column("plan_json", sa.JSON(), nullable=True),
        # queued | running | completed | partial | failed | cancelled
        sa.Column("status", sa.String(length=12), nullable=False, server_default="queued"),
        sa.Column("max_parallel", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("effective_parallel", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tokens_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        # 发起人当轮角色快照（执行器自建 Session 时重建 YardContext）
        sa.Column("roles_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_commander_runs_yard_id", "commander_runs", ["yard_id"])
    op.create_index("ix_commander_runs_session_id", "commander_runs", ["session_id"])
    op.create_index("ix_commander_runs_user_id", "commander_runs", ["user_id"])
    op.create_index("ix_commander_runs_status", "commander_runs", ["status"])

    op.create_table(
        "commander_subtasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("instruction", sa.Text(), nullable=False, server_default=""),
        sa.Column("expected_output", sa.Text(), nullable=False, server_default=""),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("agent_code", sa.String(length=100), nullable=True),
        sa.Column("agent_name", sa.String(length=100), nullable=True),
        # existing | generated | commander
        sa.Column("agent_source", sa.String(length=12), nullable=False, server_default="commander"),
        # pending | running | ok | error | skipped
        sa.Column("status", sa.String(length=12), nullable=False, server_default="pending"),
        sa.Column("depends_on_json", sa.JSON(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_excerpt", sa.Text(), nullable=True),
        sa.Column("artifact_json", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "seq", name="uq_commander_subtasks_run_seq"),
    )
    op.create_index("ix_commander_subtasks_run_id", "commander_subtasks", ["run_id"])
    op.create_index("ix_commander_subtasks_yard_id", "commander_subtasks", ["yard_id"])
    op.create_index("ix_commander_subtasks_status", "commander_subtasks", ["status"])

    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(
            sa.Column(
                "execution_mode",
                sa.String(length=12),
                nullable=False,
                server_default="standard",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_column("execution_mode")

    op.drop_index("ix_commander_subtasks_status", table_name="commander_subtasks")
    op.drop_index("ix_commander_subtasks_yard_id", table_name="commander_subtasks")
    op.drop_index("ix_commander_subtasks_run_id", table_name="commander_subtasks")
    op.drop_table("commander_subtasks")

    op.drop_index("ix_commander_runs_status", table_name="commander_runs")
    op.drop_index("ix_commander_runs_user_id", table_name="commander_runs")
    op.drop_index("ix_commander_runs_session_id", table_name="commander_runs")
    op.drop_index("ix_commander_runs_yard_id", table_name="commander_runs")
    op.drop_table("commander_runs")
