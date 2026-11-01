"""供应商多模型目录 + 官方 API 供应商 + 官方模型通道预留（v3.33）

背景：``suppliers`` 原先只有单列 ``model_id``，一个供应商只能代表一个物理模型。
本迁移把「模型」提升为一等实体：

- ``supplier_models``：一个供应商下可登记多个不同模态的模型（文本/图像/视频/语音/
  向量/重排），承载协议、能力位、单价、上下文档位与测试结果；
- ``suppliers.default_model_id``：供应商默认模型（不设 FK，避免与 supplier_models
  的 supplier_id 形成循环依赖，SQLite 批处理建表顺序也更容易）；
- ``suppliers.channel_kind`` / ``relay_channel_id``：区分用户自建 / 官方供应商同步 /
  官方通道；
- ``llm_bindings.supplier_model_id``：绑定可直接指向模型行（保留 model_name 兼容）；
- ``relay_channels``：官方/私有 API 供应商通道（目录地址、加密密钥、验签公钥）；
- ``official_plans`` / ``official_entitlements`` / ``official_orders`` /
  ``official_webhook_events``：官方模型通道的套餐、权益、订单与支付回调幂等表。

回填：每个现有 suppliers 行生成一条同 model_key 的 supplier_models，并把
``default_model_id`` 指向它。纯增量迁移，downgrade 完整可回滚。

Revision ID: 0056_supplier_models_relay
Revises: 0055_quota_policies
Create Date: 2026-11-02
"""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0056_supplier_models_relay"
down_revision = "0055_quota_policies"
branch_labels = None
depends_on = None

#: provider_type → (modality, protocol) 的推断口径（与 service 层保持一致）
_IMAGE_PROVIDER_TYPES = {"openai_images", "dashscope_image"}
_VIDEO_PROVIDER_TYPES = {"openai_video"}


def _infer(provider_type: str) -> tuple[str, str]:
    if provider_type in _IMAGE_PROVIDER_TYPES:
        return "image", provider_type
    if provider_type in _VIDEO_PROVIDER_TYPES:
        return "video", "openai_video"
    if provider_type == "anthropic":
        return "text", "anthropic"
    if provider_type == "gemini":
        return "text", "gemini"
    return "text", "openai_chat"


def upgrade() -> None:
    op.create_table(
        "supplier_models",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "supplier_id",
            sa.String(length=36),
            sa.ForeignKey("suppliers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_key", sa.String(length=100), nullable=False),
        sa.Column("upstream_model_id", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("modality", sa.String(length=20), nullable=False, server_default="text"),
        sa.Column("protocol", sa.String(length=30), nullable=False, server_default="openai_chat"),
        sa.Column("input_modalities", sa.JSON(), nullable=True),
        sa.Column("output_modalities", sa.JSON(), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("context_window", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column("endpoint_path", sa.String(length=200), nullable=True),
        sa.Column("pricing", sa.JSON(), nullable=True),
        sa.Column("default_params", sa.JSON(), nullable=True),
        sa.Column("limits_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="manual"),
        sa.Column("external_id", sa.String(length=200), nullable=True),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("test_ok", sa.Boolean(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("supplier_id", "model_key", name="uq_supplier_models_key"),
    )
    op.create_index("ix_supplier_models_supplier_id", "supplier_models", ["supplier_id"])
    op.create_index("ix_supplier_models_modality", "supplier_models", ["modality"])

    with op.batch_alter_table("suppliers") as batch:
        # 不设 FK：supplier_models.supplier_id 已指向 suppliers，再加反向 FK 会形成循环
        batch.add_column(sa.Column("default_model_id", sa.String(length=36), nullable=True))
        batch.add_column(
            sa.Column("channel_kind", sa.String(length=16), nullable=False, server_default="user")
        )
        batch.add_column(sa.Column("relay_channel_id", sa.String(length=36), nullable=True))

    with op.batch_alter_table("llm_bindings") as batch:
        batch.add_column(
            sa.Column(
                "supplier_model_id",
                sa.String(length=36),
                sa.ForeignKey(
                    "supplier_models.id",
                    ondelete="SET NULL",
                    name="fk_llm_bindings_supplier_model_id",
                ),
                nullable=True,
            )
        )

    op.create_table(
        "relay_channels",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="private"),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("public_key", sa.String(length=200), nullable=True),
        sa.Column("webhook_secret_enc", sa.Text(), nullable=True),
        sa.Column("catalog_path", sa.String(length=200), nullable=False, server_default="/models"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="unconfigured"),
        sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_relay_channels_yard_id", "relay_channels", ["yard_id"])
    op.create_index("ix_relay_channels_kind", "relay_channels", ["kind"])

    op.create_table(
        "official_plans",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("model_key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("billing_unit", sa.String(length=20), nullable=False, server_default="tokens"),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
        sa.Column("trial_tokens", sa.Float(), nullable=False, server_default="0"),
        sa.Column("trial_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("model_key", name="uq_official_plans_model_key"),
    )

    op.create_table(
        "official_entitlements",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column(
            "plan_id",
            sa.String(length=36),
            sa.ForeignKey("official_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("model_key", sa.String(length=100), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False, server_default="trial"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("quota_total", sa.Float(), nullable=False, server_default="0"),
        sa.Column("quota_used", sa.Float(), nullable=False, server_default="0"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("external_ref", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_official_entitlements_yard_model",
        "official_entitlements",
        ["yard_id", "model_key"],
    )

    op.create_table(
        "official_orders",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("yard_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "plan_id",
            sa.String(length=36),
            sa.ForeignKey("official_plans.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(length=8), nullable=False, server_default="CNY"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("external_order_id", sa.String(length=200), nullable=True),
        sa.Column("payment_url", sa.String(length=500), nullable=True),
        sa.Column("idempotency_key", sa.String(length=100), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_official_orders_idempotency"),
    )

    op.create_table(
        "official_webhook_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_id", sa.String(length=100), nullable=False),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("payload_digest", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("event_id", name="uq_official_webhook_events_event_id"),
    )

    _backfill_supplier_models()


def _backfill_supplier_models() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, model_id, provider_type FROM suppliers")
    ).fetchall()
    now = datetime.now(UTC)
    for supplier_id, model_id, provider_type in rows:
        model_id = (model_id or "").strip()
        if not model_id:
            continue
        modality, protocol = _infer(str(provider_type or ""))
        model_row_id = str(uuid.uuid4())
        bind.execute(
            sa.text(
                "INSERT INTO supplier_models ("
                "id, supplier_id, model_key, upstream_model_id, display_name, modality, "
                "protocol, input_modalities, output_modalities, capabilities, context_window, "
                "max_output_tokens, endpoint_path, pricing, default_params, limits_json, status, "
                "source, external_id, last_test_at, test_ok, latency_ms, last_error, "
                "created_at, updated_at"
                ") VALUES ("
                ":id, :supplier_id, :model_key, :upstream_model_id, :display_name, :modality, "
                ":protocol, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, 1, "
                "'import', NULL, NULL, NULL, NULL, NULL, :created_at, :updated_at"
                ")"
            ),
            {
                "id": model_row_id,
                "supplier_id": supplier_id,
                "model_key": model_id[:100],
                "upstream_model_id": model_id[:200],
                "display_name": model_id[:120],
                "modality": modality,
                "protocol": protocol,
                "created_at": now,
                "updated_at": now,
            },
        )
        bind.execute(
            sa.text("UPDATE suppliers SET default_model_id = :mid WHERE id = :sid"),
            {"mid": model_row_id, "sid": supplier_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_bindings") as batch:
        batch.drop_column("supplier_model_id")

    with op.batch_alter_table("suppliers") as batch:
        batch.drop_column("relay_channel_id")
        batch.drop_column("channel_kind")
        batch.drop_column("default_model_id")

    op.drop_table("official_webhook_events")
    op.drop_table("official_orders")
    op.drop_index("ix_official_entitlements_yard_model", table_name="official_entitlements")
    op.drop_table("official_entitlements")
    op.drop_table("official_plans")

    op.drop_index("ix_relay_channels_kind", table_name="relay_channels")
    op.drop_index("ix_relay_channels_yard_id", table_name="relay_channels")
    op.drop_table("relay_channels")

    op.drop_index("ix_supplier_models_modality", table_name="supplier_models")
    op.drop_index("ix_supplier_models_supplier_id", table_name="supplier_models")
    op.drop_table("supplier_models")
