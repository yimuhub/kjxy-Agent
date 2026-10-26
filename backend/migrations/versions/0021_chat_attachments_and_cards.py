"""对话增强：``chat_attachments`` 表 + ``chat_messages`` 结构化载荷列。

Revision ID: 0021_chat_attachments_and_cards
Revises: 0020_tenant_invitations
Create Date: 2026-10-26

背景：对话页要同时支持"带资料提问"与"对话内生成能力"：

- ``chat_attachments``：会话附件（照片/文档）元数据表。文件本体仍在文件
  存储后端（随机键，不落原始文件名），本表只存元数据与抽取文本；附件与会话
  同边界（tenant_id + created_by），会话删除时级联清理。
- ``chat_messages.payload_json``：assistant 消息的结构化载荷（能力卡片），
  与纯文本 content 并存——正文给人看，卡片给前端渲染可操作控件。
- ``chat_messages.steps_json``：过程步骤（正在思考/正在阅读…），历史回放
  可复现当时的执行路径。
- ``chat_messages.attachments_json``：消息引用的附件 id 列表。
- ``chat_messages.status`` 增加 aborted 取值（用户主动停止），列宽足够，
  无需改列类型。

可逆：downgrade 删表删列，不动既有会话/消息业务数据行。
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_chat_attachments_and_cards"
down_revision = "0020_tenant_invitations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "chat_attachments",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("mime", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="ready"),
        sa.Column("extracted_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("error", sa.String(300), nullable=True),
    )
    op.create_index("ix_chat_attachments_session_id", "chat_attachments", ["session_id"])
    op.create_index("ix_chat_attachments_tenant_id", "chat_attachments", ["tenant_id"])
    op.create_index("ix_chat_attachments_created_by", "chat_attachments", ["created_by"])

    op.add_column(
        "chat_messages",
        sa.Column("payload_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column("steps_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column("attachments_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("chat_messages", "attachments_json")
    op.drop_column("chat_messages", "steps_json")
    op.drop_column("chat_messages", "payload_json")
    op.drop_index("ix_chat_attachments_created_by", table_name="chat_attachments")
    op.drop_index("ix_chat_attachments_tenant_id", table_name="chat_attachments")
    op.drop_index("ix_chat_attachments_session_id", table_name="chat_attachments")
    op.drop_table("chat_attachments")
