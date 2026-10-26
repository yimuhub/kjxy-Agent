"""多助手群聊：``chat_participants`` 成员表。

Revision ID: 0025_chat_participants
Revises: 0024_yard_agent_visibility
Create Date: 2026-10-26

背景（v2.0 W4 群聊）：会话从"每轮独立路由"升级为"固定成员 + 应答策略"
（全员答 / 点名答 / 自愿抢答）。现有 ``AgentTeam.members_json`` 是有序接力并
折叠成一个答案，语义不同，不复用；成员名单单独落表，历史消息仍逐条落
``chat_messages``（不改表，多来源靠 ``choice_json`` 区分）。

- 一行一位成员，``position`` 决定头像条与应答顺序，服务层限 1..8 位
  （SQLite 的 CHECK 表达不了基数，与 ``AgentTeam`` 的 2..8 口径一致）。
- ``agent_name``/``agent_code`` 存加入时快照：助手之后改名或下架都不影响
  历史"是谁答的"，点名策略也能按当时的名字命中。
- 边界列 ``yard_id``/``created_by`` 冗余自会话：所有业务查询带小院边界，
  不依赖 join；会话私有，同小院他人亦不可见。
- 两条唯一约束：同会话位次不重（顺序稳定）、同会话助手不重（覆盖名单走
  先删后插，约束兜住并发写）。

可逆：downgrade 删表删索引，不动既有会话与消息。
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_chat_participants"
down_revision = "0024_yard_agent_visibility"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_participants",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("yard_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("agent_code", sa.String(100), nullable=False, server_default=""),
        sa.Column("agent_name", sa.String(100), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.UniqueConstraint("session_id", "position", name="uq_chat_participants_session_position"),
        sa.UniqueConstraint("session_id", "agent_id", name="uq_chat_participants_session_agent"),
    )
    op.create_index("ix_chat_participants_session_id", "chat_participants", ["session_id"])
    op.create_index("ix_chat_participants_yard_id", "chat_participants", ["yard_id"])
    op.create_index("ix_chat_participants_created_by", "chat_participants", ["created_by"])
    op.create_index(
        "ix_chat_participants_session_position", "chat_participants", ["session_id", "position"]
    )


def downgrade() -> None:
    op.drop_index("ix_chat_participants_session_position", table_name="chat_participants")
    op.drop_index("ix_chat_participants_created_by", table_name="chat_participants")
    op.drop_index("ix_chat_participants_yard_id", table_name="chat_participants")
    op.drop_index("ix_chat_participants_session_id", table_name="chat_participants")
    op.drop_table("chat_participants")
