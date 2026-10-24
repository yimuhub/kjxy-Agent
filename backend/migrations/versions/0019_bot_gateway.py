"""机器人网关：``bot_configs`` / ``bot_conversations`` / ``bot_message_logs``（Wave C）。

Revision ID: 0019_bot_gateway
Revises: 0018_pair_devices
Create Date: 2026-10-24

背景：IM 平台（企微智能机器人 / 钉钉 Stream / 飞书长连接）以出向 WebSocket 接入。
``bot_configs`` 存接入配置（凭据 Fernet 密文 + 连接状态 + 失败熔断计数）；
``bot_conversations`` 是平台会话 ↔ 租户的映射表（群 per-group、私聊 per-user，
陌生会话需管理员批准）；``bot_message_logs`` 是双向消息账本，**只存 sha12 摘要
与长度**，正文只允许出现在会话上下文滑窗字段里（用途受限、定期清理）。

可逆：downgrade 依次删三表与索引，不动任何既有业务数据行。
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_bot_gateway"
down_revision = "0018_pair_devices"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bot_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        # connecting|online|offline|error：连接态由 manager 回填
        sa.Column("status", sa.String(20), nullable=False, server_default="offline"),
        sa.Column("status_reason", sa.String(300), nullable=True),
        # 凭据只存 Fernet 密文（BotID+Secret 视同密钥，明文永不出库）
        sa.Column("credentials_enc", sa.Text(), nullable=False),
        sa.Column("binding_mode", sa.String(20), nullable=False, server_default="auto"),
        sa.Column("bound_agent_id", sa.String(36), nullable=True),
        sa.Column("require_approval", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_bot_configs_tenant_name"),
    )
    op.create_index("ix_bot_configs_tenant_id", "bot_configs", ["tenant_id"])

    op.create_table(
        "bot_conversations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("bot_config_id", sa.String(36), nullable=False),
        sa.Column("platform", sa.String(30), nullable=False),
        # 平台侧会话标识（企微 chatid / 钉钉 conversationId / 飞书 chat_id）
        sa.Column("external_conv_id", sa.String(191), nullable=False),
        sa.Column("external_conv_type", sa.String(10), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_msg_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("msg_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        # 多轮上下文滑窗（明文，仅限上下文用途；长期不活动由清理任务清空）
        sa.Column("context_cache_json", sa.JSON(), nullable=True),
        sa.UniqueConstraint(
            "tenant_id", "platform", "external_conv_id", name="uq_bot_conv_tenant_platform_ext"
        ),
    )
    op.create_index("ix_bot_conversations_tenant_id", "bot_conversations", ["tenant_id"])
    op.create_index("ix_bot_conversations_bot_config_id", "bot_conversations", ["bot_config_id"])

    op.create_table(
        "bot_message_logs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("direction", sa.String(5), nullable=False),
        # 正文只存 sha256 前 12 位：可关联同一句话，拿不到内容
        sa.Column("content_sha12", sa.String(12), nullable=False),
        sa.Column("length", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("routed_by", sa.String(20), nullable=True),
        sa.Column("reason_summary", sa.String(200), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_bot_message_logs_tenant_id", "bot_message_logs", ["tenant_id"])
    op.create_index("ix_bot_message_logs_conversation_id", "bot_message_logs", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_bot_message_logs_conversation_id", table_name="bot_message_logs")
    op.drop_index("ix_bot_message_logs_tenant_id", table_name="bot_message_logs")
    op.drop_table("bot_message_logs")
    op.drop_index("ix_bot_conversations_bot_config_id", table_name="bot_conversations")
    op.drop_index("ix_bot_conversations_tenant_id", table_name="bot_conversations")
    op.drop_table("bot_conversations")
    op.drop_index("ix_bot_configs_tenant_id", table_name="bot_configs")
    op.drop_table("bot_configs")
