"""Agent 运行步骤 checkpoint：agent_run_steps 表 + suppliers.supports_tools 能力位。

Revision ID: 0013_run_steps_checkpoint
Revises: 0012_alert_workorder
Create Date: 2026-10-21

背景（P0 内核）：LLM function-call 工具循环需要
1. 可恢复的步骤级 checkpoint（agent_run_steps）：每轮 LLM 调用 / 每次工具
   执行 / 最终收敛各落一行，retry 从失败步续跑时已 ok 的工具步不重放副作用；
   run_id 复用现有运行句柄（声明式 = task_id，工厂 = factory_runs.id）。
2. 供应商 function-calling 能力位（suppliers.supports_tools，默认 false）：
   仅显式声明 true 的供应商才允许进入工具循环，未声明保持原单轮路径。

可逆：downgrade 删表 + 删列，不动任何业务数据行。
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_run_steps_checkpoint"
down_revision = "0012_alert_workorder"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_run_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("run_id", sa.String(64), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("step_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="ok"),
        sa.Column("tool_code", sa.String(100), nullable=True),
        sa.Column("input_json", sa.JSON(), nullable=True),
        sa.Column("output_json", sa.JSON(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_text", sa.Text(), nullable=True),
    )
    op.create_index("ix_agent_run_steps_tenant_id", "agent_run_steps", ["tenant_id"])
    op.create_index("ix_agent_run_steps_run_id", "agent_run_steps", ["run_id"])

    op.add_column(
        "suppliers",
        # function-calling 能力位：默认 false（未显式声明的供应商零回归）
        sa.Column(
            "supports_tools",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("suppliers", "supports_tools")
    op.drop_index("ix_agent_run_steps_run_id", table_name="agent_run_steps")
    op.drop_index("ix_agent_run_steps_tenant_id", table_name="agent_run_steps")
    op.drop_table("agent_run_steps")
