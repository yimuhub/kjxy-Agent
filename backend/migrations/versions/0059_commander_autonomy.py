"""指挥官自主补救与完全控制继承（v3.40）

- ``commander_runs.approval_mode``：计划创建时会话授权模式快照（ask|auto|full）。
  ``full`` 即用户选择的「完全控制」，子任务工具调用不再逐次询问；
- ``commander_subtasks.remediation_json``：自主补救轨迹（重试/换人/新建/自执行），
  用于汇总解释「为什么没完成」与排障。

纯增量迁移，可完整 downgrade。

Revision ID: 0059_commander_autonomy
Revises: 0058_commander
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0059_commander_autonomy"
down_revision = "0058_commander"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("commander_runs") as batch:
        batch.add_column(
            sa.Column(
                "approval_mode",
                sa.String(length=8),
                nullable=False,
                server_default="ask",
            )
        )
    with op.batch_alter_table("commander_subtasks") as batch:
        batch.add_column(sa.Column("remediation_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("commander_subtasks") as batch:
        batch.drop_column("remediation_json")
    with op.batch_alter_table("commander_runs") as batch:
        batch.drop_column("approval_mode")
