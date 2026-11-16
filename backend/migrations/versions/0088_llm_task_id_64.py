"""放宽 llm_calls.task_id 到 64 字符（2026-11-16 走查修复）。

背景：班组综合阶段写入 ``team-summary-<36 位 run uuid>`` 共 49 字符，而列宽为
``varchar(36)``，导致 LLM 已成功返回、却在落库瞬间抛
``StringDataRightTruncation``，整条班组 run 失败（本次实测在 run
14903889 之前复现三次）。

修复策略：列宽放宽到 64（与 chat/multimodal 已有的 ``[:64]`` 约定一致），
同时保留既有数据；代码侧也不再依赖"前缀 + 完整 uuid"这种超长拼法。

Revision ID: 0088_llm_task_id_64
Revises: 0087_document_parse_attempt
"""

import sqlalchemy as sa
from alembic import op

revision = "0088_llm_task_id_64"
down_revision = "0087_document_parse_attempt"
branch_labels = None
depends_on = None

_TABLE = "llm_calls"
_COLUMN = "task_id"
_TARGET_LENGTH = 64


def _column_length(bind, table: str, column: str) -> int | None:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return None
    for col in inspector.get_columns(table):
        if col["name"] == column:
            return getattr(col["type"], "length", None)
    return None


def upgrade() -> None:
    bind = op.get_bind()
    current = _column_length(bind, _TABLE, _COLUMN)
    if current is None or current >= _TARGET_LENGTH:
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column(
            _COLUMN,
            existing_type=sa.String(length=current),
            type_=sa.String(length=_TARGET_LENGTH),
            existing_nullable=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    current = _column_length(bind, _TABLE, _COLUMN)
    if current is None or current <= 36:
        return
    with op.batch_alter_table(_TABLE) as batch:
        batch.alter_column(
            _COLUMN,
            existing_type=sa.String(length=current),
            type_=sa.String(length=36),
            existing_nullable=True,
        )
