"""助手展示名同小院唯一：清洗存量重名 + 建 ``uq_yard_agents_yard_name``。

Revision ID: 0023_yard_agent_unique_name
Revises: 0022_rename_tenant_to_yard
Create Date: 2026-10-26

用户要求"所有助手的名称不能重复"，指的是汉字展示名（``name``），不是 ``agent_code``。
``yard_agents`` 原本只有 ``(yard_id, agent_code, version)`` 唯一，同小院里重名无人管
（0022 之前实测 36 行里已有 10 组重名，多为工厂批量产出与联调造出的数据）。

顺序很关键：**先清洗、后建约束**。直接建约束会在存量数据上失败。

清洗规则（按 2026-10-26 拍板）：同 ``(yard_id, name)`` 组内按创建时间保留最早一条原名，
其余依次加 ``-2`` / ``-3`` 后缀；若后缀名本身已被占用（用户手工建过同名），继续往后
找空位。这是无损的：不删任何助手、不改 ``agent_code``，会话与班组引用的都是 id，
改名不影响引用完整性。列宽 ``String(100)``，加后缀前先按剩余宽度截断原名，避免
PostgreSQL 超长报错。

另外先把 ``name`` 做 ``trim``：新校验器在写入时已去首尾空格，但存量行可能带尾空格；
不 trim 的话，"助手 " 与 "助手" 会被判为两个名字，约束一建立却又立刻冲突。

downgrade 只删约束：后缀改名无法逆推（无法知道哪个名字原本不带后缀），
故数据部分为前向不可逆，已在此说明。平台注册的 4 个专业助手不在本表，不受影响。
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_yard_agent_unique_name"
down_revision = "0022_rename_tenant_to_yard"
branch_labels = None
depends_on = None

_NAME_MAX = 100
_TABLE = "yard_agents"


def _bind():
    return op.get_bind()


def _trim_names() -> None:
    _bind().execute(sa.text(f'UPDATE "{_TABLE}" SET "name" = trim("name")'))


def _fits(name: str) -> bool:
    return len(name) <= _NAME_MAX


def _candidate(base: str, seq: int) -> str:
    """原名 + 递增后缀；超宽时先截断原名，保证总长不越过列宽。"""
    suffix = f"-{seq}"
    keep = _NAME_MAX - len(suffix)
    return (base[:keep] if len(base) > keep else base) + suffix


def _dedupe_names() -> None:
    bind = _bind()
    rows = bind.execute(
        sa.text(
            f'SELECT "id", "yard_id", "name" FROM "{_TABLE}" '
            'ORDER BY "yard_id", "name", "created_at", "id"'
        )
    ).fetchall()
    seen: set[tuple[str, str]] = set()
    for agent_id, yard_id, name in rows:
        key = (yard_id, name)
        if key not in seen:
            seen.add(key)
            continue
        seq = 2
        taken = {n for (_y, n) in seen if _y == yard_id}
        while True:
            new_name = _candidate(name, seq)
            if new_name not in taken and _fits(new_name):
                break
            seq += 1
        bind.execute(
            sa.text(f'UPDATE "{_TABLE}" SET "name" = :name WHERE "id" = :id').bindparams(
                name=new_name, id=agent_id
            )
        )
        seen.add((yard_id, new_name))


def upgrade() -> None:
    _trim_names()
    _dedupe_names()
    if _bind().dialect.name == "postgresql":
        op.create_unique_constraint("uq_yard_agents_yard_name", _TABLE, ["yard_id", "name"])
        return
    # SQLite 不支持 ALTER TABLE ADD CONSTRAINT，只能 batch 重建表
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.create_unique_constraint("uq_yard_agents_yard_name", ["yard_id", "name"])


def downgrade() -> None:
    if _bind().dialect.name == "postgresql":
        op.drop_constraint("uq_yard_agents_yard_name", _TABLE, type_="unique")
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_constraint("uq_yard_agents_yard_name", type_="unique")
