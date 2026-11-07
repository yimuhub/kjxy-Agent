"""0078 billing：兑换码与订单一对一唯一约束（v3.72）。

商用审查 B2：并发兑换同一码曾可能建多张订单、重复入账。服务层已改为
CAS 原子占用（码 unused→redeemed、订单 pending→paid/cancelled），本迁移
补数据库兜底——``billing_orders.code_id`` 唯一索引，同一码只允许一张订单。

``code_id`` 可空（对公转账订单无码）：SQLite/Postgres 唯一索引视 NULL 互不
相等，多张无码订单不受限，无需部分索引。

存量清洗：同一 ``code_id`` 多张订单时保留创建最早的一张，其余把 ``code_id``
置 NULL（解绑，不删行、不改状态/额度），使唯一索引可建立。历史重复入账的
额度 reconciliation 不在本迁移范围（新模块正常无重复；如有须人工对账）。

dev 环境 create_all 已按新模型带索引，存在即跳过。
"""

import sqlalchemy as sa
from alembic import op

revision = "0078_billing_order_code_unique"
down_revision = "0077_workspace_root_unique"
branch_labels = None
depends_on = None

_TABLE = "billing_orders"
_INDEX = "uq_billing_orders_code_id"


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_index(bind, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in sa.inspect(bind).get_indexes(table))


def _detach_duplicate_code_orders(bind) -> None:
    rows = bind.execute(
        sa.text(
            f'SELECT "id", "code_id" FROM "{_TABLE}" WHERE "code_id" IS NOT NULL '
            'ORDER BY "created_at", "id"'
        )
    ).fetchall()
    seen: set[str] = set()
    for row_id, code_id in rows:
        if code_id in seen:
            bind.execute(
                sa.text(f'UPDATE "{_TABLE}" SET "code_id" = NULL WHERE "id" = :id').bindparams(
                    id=row_id
                )
            )
        else:
            seen.add(code_id)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE) or _has_index(bind, _TABLE, _INDEX):
        return
    _detach_duplicate_code_orders(bind)
    op.create_index(_INDEX, _TABLE, ["code_id"], unique=True)


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE) and _has_index(bind, _TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
