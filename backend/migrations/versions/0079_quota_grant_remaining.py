"""0079 配额：临时额度剩余余额（v3.72）。

商用审查 B4：加油包 100 万 / 30 天挂在自然月窗口上，月初窗口用量清零后
同一笔额度又被全额放行，有效期内最多可消费 2 倍购买量。

修法：``quota_grants`` 增加 ``remaining``，每次记账按「先用本窗口基线、
超出部分再扣临时额度余额」扣减，余额跨月连续，有效期内累计消费不超过
购买量。存量数据回填 ``remaining = amount``（视为未消费）。

只加可空列，SQLite/Postgres 均可直接 ADD COLUMN；dev 环境 create_all
已按新模型建表，存在即跳过。
"""

import sqlalchemy as sa
from alembic import op

revision = "0079_quota_grant_remaining"
down_revision = "0078_billing_order_code_unique"
branch_labels = None
depends_on = None

_TABLE = "quota_grants"
_COLUMN = "remaining"


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, name: str) -> bool:
    return any(col["name"] == name for col in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE) or _has_column(bind, _TABLE, _COLUMN):
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.Float(), nullable=True))
    bind.execute(sa.text(f'UPDATE "{_TABLE}" SET "{_COLUMN}" = "amount" WHERE "{_COLUMN}" IS NULL'))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE) and _has_column(bind, _TABLE, _COLUMN):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
