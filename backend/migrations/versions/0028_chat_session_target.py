"""会话执行对象标识（会话列表区分普通对话/锁定助手/群聊）。

Revision ID: 0028_chat_session_target
Revises: 0027_agent_skills_and_token_usage
Create Date: 2026-10-27

新增列：
- ``chat_sessions.target``：auto（自动路由）/ agent（锁定某助手）/ team（班组）；
- ``chat_sessions.target_code``：锁定助手的 agent_code（仅 target=agent 时有值）。
群聊不单独存取值：会话列表按 chat_participants 是否有成员行实时判定。
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_chat_session_target"
down_revision = "0027_agent_skills_and_token_usage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("target", sa.String(10), nullable=False, server_default="auto"),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("target_code", sa.String(100), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_sessions", "target_code")
    op.drop_column("chat_sessions", "target")
