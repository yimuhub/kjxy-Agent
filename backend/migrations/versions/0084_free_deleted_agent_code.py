"""释放历史软删除智能体占用的 agent_code 唯一槽（v3.78）。

``yard_agents`` 上有 ``(yard_id, agent_code, version)`` 唯一约束。旧版软删除只改
``name`` 释放展示名槽位，却不改 ``agent_code``，导致用户删除一个智能体后再用同编码
重建会撞 409 AGENT_VERSION_CONFLICT「请改用新版本号或编辑现有配置」——而那条已删除
行在 UI 不可见也不可编辑，形成死路。新版 ``delete_agent`` 已在删除时给 agent_code
追加 ``__deleted_<id8>`` 后缀释放槽位；本迁移把存量已软删除行按同一口径补齐。

幂等：只处理 status='deleted' 且 agent_code 尚未带 ``__deleted_`` 后缀的行。
纯数据清洗，downgrade 为无操作（原始编码形态不可逆）。

Revision ID: 0084_free_deleted_agent_code
Revises: 0083_normalize_bound_skills
"""

import sqlalchemy as sa
from alembic import op

revision = "0084_free_deleted_agent_code"
down_revision = "0083_normalize_bound_skills"
branch_labels = None
depends_on = None


def _has_table(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_table(bind, "yard_agents"):
        return
    rows = bind.execute(
        sa.text(
            "SELECT id, agent_code FROM yard_agents "
            "WHERE status = 'deleted' AND agent_code NOT LIKE '%__deleted_%'"
        )
    ).fetchall()
    for agent_id, code in rows:
        code = str(code or "")
        suffix = f"__deleted_{str(agent_id)[:8]}"
        new_code = f"{code[: 100 - len(suffix)]}{suffix}"
        bind.execute(
            sa.text("UPDATE yard_agents SET agent_code = :code WHERE id = :id").bindparams(
                code=new_code, id=agent_id
            )
        )


def downgrade() -> None:
    # 纯数据清洗：原始编码不可逆，无操作。
    pass