"""对话内生成文档：chat_attachments 增加格式 / DocModel 快照 / 生成标记。

Revision ID: 0029_chat_attachments_document
Revises: 0028_chat_session_target
Create Date: 2026-10-27

新增列：
- ``doc_format``：md/docx/xlsx/pptx/pdf；用户上传的附件为空；
- ``doc_model_json``：DocModel 快照，在线预览直接由它渲染（不解析 Office 文件）；
- ``generated``：是否由对话生成，存量行一律 False。
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_chat_attachments_document"
down_revision = "0028_chat_session_target"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_attachments",
        sa.Column("doc_format", sa.String(10), nullable=True),
    )
    op.add_column(
        "chat_attachments",
        sa.Column("doc_model_json", sa.JSON(), nullable=True),
    )
    op.add_column(
        "chat_attachments",
        sa.Column("generated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("chat_attachments", "generated")
    op.drop_column("chat_attachments", "doc_model_json")
    op.drop_column("chat_attachments", "doc_format")
