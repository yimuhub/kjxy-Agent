"""告警一键工单：device_alerts 增加 workorder_id 可空列（A 线收官）。

Revision ID: 0012_alert_workorder
Revises: 0011_device_core
Create Date: 2026-10-19

背景：告警记录支持"一键开工单"——工单实体复用 tasks 表
（task_type=workorder.dispatch），本列存该任务 id 作为幂等锚点：
已有值的告警再次开工单直接返回既有工单号，不重复创建。

可逆：downgrade 仅删列，不动任何数据行。
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_alert_workorder"
down_revision = "0011_device_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "device_alerts",
        # 指向 tasks.id（workorder.dispatch 任务行）；空=该告警尚未开工单
        sa.Column("workorder_id", sa.String(36), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("device_alerts", "workorder_id")
