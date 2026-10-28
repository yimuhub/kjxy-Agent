"""会话管理与权限模式（v3.15）

chat_sessions 追加：置顶 / 归档 / 未读 / 分支来源 / 本会话工具授权模式。
approval_mode 缺省 ask，即保持"高风险工具必须人工审批"的既有行为（零回归）。

tool_approvals 追加 ``chat_session_id``：授权模式 auto 的语义是"同一工具+同一参数
指纹在**本会话内**已被人工批准过则放行"，所以审批单必须知道自己源自哪个会话；
缺省为 NULL，历史审批单不参与会话内复用，不会被误放行。

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_chat_session_ops"
down_revision = "0042_share_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "chat_sessions", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "chat_sessions",
        sa.Column("unread", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("approval_mode", sa.String(12), nullable=False, server_default="ask"),
    )
    op.add_column("chat_sessions", sa.Column("parent_session_id", sa.String(36), nullable=True))
    op.add_column("chat_sessions", sa.Column("parent_message_id", sa.String(36), nullable=True))
    op.create_index(
        "ix_chat_sessions_parent_session_id", "chat_sessions", ["parent_session_id"]
    )
    op.add_column(
        "tool_approvals", sa.Column("chat_session_id", sa.String(36), nullable=True)
    )
    op.create_index(
        "ix_tool_approvals_chat_session_id", "tool_approvals", ["chat_session_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_tool_approvals_chat_session_id", table_name="tool_approvals")
    op.drop_column("tool_approvals", "chat_session_id")
    op.drop_index("ix_chat_sessions_parent_session_id", table_name="chat_sessions")
    op.drop_column("chat_sessions", "parent_message_id")
    op.drop_column("chat_sessions", "parent_session_id")
    op.drop_column("chat_sessions", "approval_mode")
    op.drop_column("chat_sessions", "unread")
    op.drop_column("chat_sessions", "archived_at")
    op.drop_column("chat_sessions", "pinned_at")
