"""会话列表视图的复合索引（v3.15）

对话列表的查询形状是固定的：先按 (yard_id, created_by, archived_at) 收窄，再按
「置顶在前 → 置顶时间倒序 → 更新时间倒序」排序。0043 之前只有 yard_id / created_by
两条单列索引，收窄全靠回表；几百上千条会话时每次打开列表、每次切换「进行中/已归档」
都要全表扫再排序。索引只加不改数据，回滚就是删索引。

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-28
"""

from alembic import op

revision = "0044_chat_session_view_index"
down_revision = "0043_chat_session_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_chat_sessions_owner_view",
        "chat_sessions",
        ["yard_id", "created_by", "archived_at", "pinned_at", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_owner_view", table_name="chat_sessions")
