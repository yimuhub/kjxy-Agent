"""离线写入队列与冲突合并（v3.19）

给台账（地块/周期/农事记录）与工单加两类列：
- ``revision``：乐观并发版本号，每次成功写入 +1；客户端带着旧版本保存时后端拒绝并回传当前副本；
- ``client_op_id``：离线队列的幂等键，(yard_id, client_op_id) 唯一，重放不会重复落库。

工单事件同样补 ``client_op_id``，让离线回执重放只记一次。纯增量迁移，可完整 downgrade。

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0047_offline_sync"
down_revision = "0046_sso_providers"
branch_labels = None
depends_on = None

_TABLES = ("farm_plots", "farm_cycles", "farm_records", "work_orders")
_CONSTRAINTS = {
    "farm_plots": "uq_farm_plots_yard_op",
    "farm_cycles": "uq_farm_cycles_yard_op",
    "farm_records": "uq_farm_records_yard_op",
    "work_orders": "uq_work_orders_yard_op",
}
_EVENT_CONSTRAINT = "uq_work_order_events_yard_op"


def upgrade() -> None:
    # batch 模式：SQLite 不支持 ALTER 加约束，批量模式会自动重建表；PG 下等价于普通 ALTER。
    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("revision", sa.Integer(), nullable=False, server_default="1")
            )
            batch.add_column(sa.Column("client_op_id", sa.String(length=64), nullable=True))
            batch.create_unique_constraint(
                _CONSTRAINTS[table], ["yard_id", "client_op_id"]
            )

    with op.batch_alter_table("work_order_events") as batch:
        batch.add_column(sa.Column("client_op_id", sa.String(length=64), nullable=True))
        batch.create_unique_constraint(_EVENT_CONSTRAINT, ["yard_id", "client_op_id"])


def downgrade() -> None:
    with op.batch_alter_table("work_order_events") as batch:
        batch.drop_constraint(_EVENT_CONSTRAINT, type_="unique")
        batch.drop_column("client_op_id")

    for table in _TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(_CONSTRAINTS[table], type_="unique")
            batch.drop_column("client_op_id")
            batch.drop_column("revision")
