"""模型供应商 ORM：suppliers + supplier_models + 官方中转通道（v3.33）。

- suppliers.yard_id 为 NULL 表示全局共享供应商，所有小院可见；
  非空则仅归属小院可见。
- status：1=启用，2=禁用（连续探测失败 3 次自动置 2）。
- yard_active_suppliers：小院级"当前生效供应商"（一小院一行）。
- supplier_models：一个供应商下的多个模型（不同模态/协议/单价），
  ``suppliers.default_model_id`` 指向默认模型（故意不设 FK，避免循环依赖）。
- channel_kind：user=用户自建，relay=中转站同步，official=官方通道。
- relay_channels / official_*：官方与私有 API 中转站、官方套餐/权益/订单/回调幂等。
"""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin

#: 渠道类型
CHANNEL_KIND_USER = "user"
CHANNEL_KIND_RELAY = "relay"
CHANNEL_KIND_OFFICIAL = "official"

#: 官方通道 provider_type（中转站与官方共用同一协议适配）
PROVIDER_TYPE_OFFICIAL_RELAY = "official_relay"


class Supplier(Base, TimestampMixin):
    __tablename__ = "suppliers"

    # NULL=全局共享；非空=仅归属小院可见
    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(40), nullable=False)  # 逻辑名，如 deepseek-main
    provider_type: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # deepseek|qwen|moonshot|openai|openai_compatible|official_relay
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)  # 上游物理模型名
    api_key_enc: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet 加密，永存密文
    models: Mapped[str | None] = mapped_column(Text, nullable=True)  # 白名单，逗号分隔，空=全量
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 越大越优先
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 1=启用/2=禁用
    # function-calling 能力位：仅显式声明 true 的供应商才允许进入工具循环
    # （迁移 0013；默认 false → 旧行为零回归）。后端 PATCH 可设，前端表单 P2 补。
    supports_tools: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # v3.32 金额额度：单价（元 / 千 token）；空 = 未配置，按 Provider 默认常量估算
    price_in_per_1k: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_out_per_1k: Mapped[float | None] = mapped_column(Float, nullable=True)
    test_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # v3.33 多模型目录：默认模型行（不设 FK，避免与 supplier_models.supplier_id 循环）
    default_model_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # user=用户自建；relay=中转站同步；official=官方通道（小院只读）
    channel_kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CHANNEL_KIND_USER, server_default=CHANNEL_KIND_USER
    )
    relay_channel_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class SupplierModel(Base, TimestampMixin):
    """供应商下的一个模型：不同模态/协议/单价各自成行。

    ``model_key`` 是本平台内部逻辑名（唯一于供应商内），``upstream_model_id``
    是提交给上游的物理模型名（可为带版本的长串）。``pricing`` 结构约定：
    ``{"input_per_1k": 0.001, "output_per_1k": 0.002, "currency": "CNY"}``。
    """

    __tablename__ = "supplier_models"
    __table_args__ = (
        UniqueConstraint("supplier_id", "model_key", name="uq_supplier_models_key"),
    )

    supplier_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("suppliers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_key: Mapped[str] = mapped_column(String(100), nullable=False)
    upstream_model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # text|image|video|audio|multimodal|embedding|rerank|asr|tts
    modality: Mapped[str] = mapped_column(
        String(20), nullable=False, default="text", index=True
    )
    # openai_chat|openai_images|dashscope_image|openai_video|anthropic|gemini|
    # openai_audio|openai_embedding|custom
    protocol: Mapped[str] = mapped_column(String(30), nullable=False, default="openai_chat")
    input_modalities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    output_modalities: Mapped[list | None] = mapped_column(JSON, nullable=True)
    capabilities: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    context_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    endpoint_path: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pricing: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    default_params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    limits_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # manual|import|probe|relay|official
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class RelayChannel(Base, TimestampMixin):
    """API 中转站通道：官方（平台级）或私有（小院级）。

    ``yard_id`` 为 NULL = 平台级；官方通道以 ``kind='official'`` + ``enabled``
    作为「官方模型通道已开通」的开关。密钥与回调密钥均为 Fernet 密文。
    """

    __tablename__ = "relay_channels"

    yard_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # official=官方通道；private=私有中转站
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="private", index=True
    )
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    api_key_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Ed25519 公钥（base64 原始 32 字节或 PEM），配置后目录响应必须带签名
    public_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    webhook_secret_enc: Mapped[str | None] = mapped_column(Text, nullable=True)
    catalog_path: Mapped[str] = mapped_column(String(200), nullable=False, default="/models")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # unconfigured|ok|error
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="unconfigured")
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class OfficialPlan(Base, TimestampMixin):
    """官方模型套餐（运营维护；服务端金额只信这张表）。"""

    __tablename__ = "official_plans"
    __table_args__ = (UniqueConstraint("model_key", name="uq_official_plans_model_key"),)

    model_key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # tokens|calls|image|video|request
    billing_unit: Mapped[str] = mapped_column(String(20), nullable=False, default="tokens")
    price: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    trial_tokens: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    trial_days: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class OfficialEntitlement(Base, TimestampMixin):
    """某小院对某官方模型的权益（试用或购买）。额度先记在本行，不再伪造调用量。"""

    __tablename__ = "official_entitlements"

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("official_plans.id", ondelete="SET NULL"), nullable=True
    )
    model_key: Mapped[str] = mapped_column(String(100), nullable=False)
    # trial|purchase
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="trial")
    # active|expired|revoked
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    quota_total: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    quota_used: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)


class OfficialOrder(Base, TimestampMixin):
    """官方模型订单：pending→paid/failed/refunded。金额只由服务端按套餐写入。"""

    __tablename__ = "official_orders"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_official_orders_idempotency"),
    )

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    plan_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("official_plans.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    # pending|paid|failed|refunded
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    external_order_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payment_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OfficialWebhookEvent(Base):
    """支付回调幂等表：同一 event_id 只处理一次，payload 摘要用于审计比对。"""

    __tablename__ = "official_webhook_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_official_webhook_events_event_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    payload_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class YardActiveSupplier(Base, TimestampMixin):
    __tablename__ = "yard_active_suppliers"

    yard_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    supplier_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("suppliers.id"), nullable=True
    )
