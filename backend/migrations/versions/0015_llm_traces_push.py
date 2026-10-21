"""观测与推送通道：``llm_traces`` + ``push_channels``（W4）。

Revision ID: 0015_llm_traces_push
Revises: 0014_agent_teams
Create Date: 2026-10-21

背景：
1. ``llm_traces``：模型调用观测轨迹（append-only）。与 llm_calls 记账不同，
   本表面向**观测**：purpose（declarative|factory|eval|tool|embedding|platform）
   + agent_ref/run_id 调用方引用，供按日×模型聚合。**隐私口径**：prompt_digest
   只存 ``sha256 前 12 位|前 200 字摘要``，绝不存 prompt 全文。
2. ``push_channels``：租户事件推送通道。URL 内含机器人 key，**视同密钥**——
   url_enc 存 Fernet 密文（复用 llm/supplier_crypto），接口只回掩码；
   events_json 为订阅事件数组（device_alert|workorder_created|team_run_failed）；
   连续失败 5 次自动 enabled=false（复刻 devices 连接器熔断纪律）。

可逆：downgrade 依序删两表，不动任何既有业务数据行。
"""

import sqlalchemy as sa
from alembic import op

revision = "0015_llm_traces_push"
down_revision = "0014_agent_teams"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_traces",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=True),
        # 命中的供应商行 id（settings 兜底 provider 无供应商 → NULL）
        sa.Column("provider_id", sa.String(36), nullable=True),
        sa.Column("model", sa.String(100), nullable=False, server_default="unknown"),
        sa.Column("purpose", sa.String(20), nullable=False, server_default="platform"),
        # 调用方引用：agent_code 或 factory run id
        sa.Column("agent_ref", sa.String(100), nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        # sha256 前 12 位 + 前 200 字摘要（隐私口径：绝不存全文）
        sa.Column("prompt_digest", sa.Text(), nullable=True),
        sa.Column("error_text", sa.String(300), nullable=True),
    )
    op.create_index("ix_llm_traces_created_at", "llm_traces", ["created_at"])
    op.create_index("ix_llm_traces_tenant_id", "llm_traces", ["tenant_id"])

    op.create_table(
        "push_channels",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        # wecom_robot|dingtalk|generic_webhook
        sa.Column("kind", sa.String(30), nullable=False),
        # URL 含机器人 key，视同密钥：Fernet 密文入库，接口只回掩码
        sa.Column("url_enc", sa.Text(), nullable=True),
        # 订阅事件数组：[{device_alert|workorder_created|team_run_failed}, ...]
        sa.Column("events_json", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.UniqueConstraint("tenant_id", "name", name="uq_push_channels_tenant_name"),
    )
    op.create_index("ix_push_channels_tenant_id", "push_channels", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_push_channels_tenant_id", table_name="push_channels")
    op.drop_table("push_channels")
    op.drop_index("ix_llm_traces_tenant_id", table_name="llm_traces")
    op.drop_index("ix_llm_traces_created_at", table_name="llm_traces")
    op.drop_table("llm_traces")
