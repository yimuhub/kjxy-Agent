"""知识文档解析代数计数器（2026-11-13 二次深审修正）。

背景：看门狗僵尸回收与人工重解析都会把 ``processing`` 重置回 ``queued``，
此时上一个解析执行者可能还活着（只是慢/网络卡）。此前它收尾时无条件写
切块并把状态置 ``ready``，会与新一轮解析**并发交错写同一份文档的切块**
（重复 embedding 消耗、切块删除/插入交错）。

新增 ``parse_attempt``：每次 CAS 抢占（queued→processing）自增 1；收尾与
失败标记必须带 ``WHERE parse_attempt = 抢到的代数``，被超越者静默退出。
纯新增列，SQLite/Postgres 均可直接执行；存量行回填 0（下一次解析即从 1 起）。

Revision ID: 0087_document_parse_attempt
Revises: 0086_billing_plans
"""

import sqlalchemy as sa
from alembic import op

revision = "0087_document_parse_attempt"
down_revision = "0086_billing_plans"
branch_labels = None
depends_on = None

_TABLE = "knowledge_documents"
_COLUMN = "parse_attempt"


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE) or _has_column(bind, _TABLE, _COLUMN):
        return
    # NOT NULL + server_default：存量行回填 0，新行由 ORM default 接管
    op.add_column(
        _TABLE,
        sa.Column(_COLUMN, sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE) or not _has_column(bind, _TABLE, _COLUMN):
        return
    op.drop_column(_TABLE, _COLUMN)
