"""对话模式：``chat_sessions`` / ``chat_messages``（会话 + 消息持久化）。

Revision ID: 0017_chat_sessions
Revises: 0016_mcp_servers
Create Date: 2026-10-22

背景：对话模式复用 supervisor 三级路由（W5b）执行，但 supervisor 只存问题
摘要（sha12），会话要能回放就得存正文：

- ``chat_sessions``：会话，对创建者私有（tenant_id + created_by 双重边界），
  ``title`` 缺省由首条消息自动继承；
- ``chat_messages``：消息，``seq`` 会话内序号（(session_id, seq) 唯一约束
  兜底并发乱序）；assistant 行携带路由存证（routed_by/choice_json/reason），
  执行失败也以 ``status=failed`` + ``error_code`` 入库——历史诚实可查。

可逆：downgrade 删两表，不动任何既有业务数据行。
"""

import sqlalchemy as sa
from alembic import op

revision = "0017_chat_sessions"
down_revision = "0016_mcp_servers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        # 创建者：会话私有边界（同租户他人也访问不到）
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=True),
    )
    op.create_index("ix_chat_sessions_tenant_id", "chat_sessions", ["tenant_id"])
    op.create_index("ix_chat_sessions_created_by", "chat_sessions", ["created_by"])

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        # 冗余租户列：业务查询全部带租户边界，不依赖 join
        sa.Column("tenant_id", sa.String(36), nullable=False),
        # 会话内序号（1 递增）
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="ok"),
        # ---- 以下仅 assistant 行使用：supervisor 路由结果随行存证 ----
        sa.Column("routed_by", sa.String(10), nullable=True),
        sa.Column("choice_json", sa.JSON(), nullable=True),
        sa.Column("reason", sa.String(200), nullable=True),
        sa.Column("error_code", sa.String(50), nullable=True),
        sa.UniqueConstraint("session_id", "seq", name="uq_chat_messages_session_seq"),
    )
    op.create_index("ix_chat_messages_session_id", "chat_messages", ["session_id"])
    op.create_index("ix_chat_messages_tenant_id", "chat_messages", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_chat_messages_tenant_id", table_name="chat_messages")
    op.drop_index("ix_chat_messages_session_id", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_created_by", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_tenant_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")
