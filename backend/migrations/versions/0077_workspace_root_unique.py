"""0077 workspace：同一宿主目录只允许一个 active 工作区（v3.72）。

商用审查 S1：两个小院对同一宿主路径的独立注册必须被拒绝。服务层已加预检，
本迁移补数据库兜底——``root_path`` 上的部分唯一索引（仅 ``status='active'``），
并发注册由索引裁决；archived 行不受约束，归档后目录可重新注册。

存量清洗：同 ``root_path`` 多条 active 时保留创建最早的一条，其余归档
（软状态变更，不删数据；如需恢复须先归档现占用者）。先清洗后建索引，
否则存量重复会让建索引直接失败。

dev 环境 create_all 已按新模型带索引，存在即跳过。
"""

import sqlalchemy as sa
from alembic import op

revision = "0077_workspace_root_unique"
down_revision = "0076_billing_codes_orders"
branch_labels = None
depends_on = None

_TABLE = "workspace_projects"
_INDEX = "uq_workspace_projects_root_active"


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _has_index(bind, table: str, name: str) -> bool:
    return any(ix["name"] == name for ix in sa.inspect(bind).get_indexes(table))


def _archive_duplicate_roots(bind) -> None:
    rows = bind.execute(
        sa.text(
            f'SELECT "id", "root_path" FROM "{_TABLE}" WHERE "status" = \'active\' '
            'ORDER BY "created_at", "id"'
        )
    ).fetchall()
    seen: set[str] = set()
    for row_id, root_path in rows:
        if root_path in seen:
            bind.execute(
                sa.text(
                    f'UPDATE "{_TABLE}" SET "status" = \'archived\' WHERE "id" = :id'
                ).bindparams(id=row_id)
            )
        else:
            seen.add(root_path)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, _TABLE) or _has_index(bind, _TABLE, _INDEX):
        return
    _archive_duplicate_roots(bind)
    op.create_index(
        _INDEX,
        _TABLE,
        ["root_path"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if _has_table(bind, _TABLE) and _has_index(bind, _TABLE, _INDEX):
        op.drop_index(_INDEX, table_name=_TABLE)
