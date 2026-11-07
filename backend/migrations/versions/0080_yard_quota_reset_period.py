"""0080 legacy 配额：月清零幂等标记（v3.72）。

商用审查 O6：``quota.monthly_reset`` 原来用 Redis 键判定「当月是否已清」，
桌面单机没有 Redis 时永远不清；Redis 被 flush 后还会重复清，把当月用量抹掉。

修法：``yard_quotas`` 增加 ``reset_period``（YYYY-MM），清零改成数据库条件 UPDATE
（NULL 或非本月才更新），无需外部缓存即可保证当月只清一次，多进程并发也安全。
存量行回填当前月份，避免在升级当月把已经累计的用量清零。

只加可空列 + 一次回填 UPDATE，SQLite/Postgres 均可直接执行；dev 环境 create_all
已按新模型建表，存在即跳过。
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0080_yard_quota_reset_period"
down_revision = "0079_quota_grant_remaining"
branch_labels = None
depends_on = None

_TABLE = "yard_quotas"
_COLUMN = "reset_period"


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_column(bind, table: str, name: str) -> bool:
    return any(col["name"] == name for col in sa.inspect(bind).get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE) or _has_column(bind, _TABLE, _COLUMN):
        return
    op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(7), nullable=True))
    # 回填当前月份：升级当月中已有用量，不应被首次巡检误清
    period = datetime.now(UTC).strftime("%Y-%m")
    bind.execute(
        sa.text(f'UPDATE "{_TABLE}" SET "{_COLUMN}" = :period WHERE "{_COLUMN}" IS NULL'),
        {"period": period},
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE) and _has_column(bind, _TABLE, _COLUMN):
        with op.batch_alter_table(_TABLE) as batch:
            batch.drop_column(_COLUMN)
