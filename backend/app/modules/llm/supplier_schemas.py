"""模型供应商 Pydantic 契约。

密钥字段（api_key）只在入参出现：创建/更新/测试时使用，用完即弃，
绝不出现在任何出参中；列表/详情统一返回 api_key_masked。
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderType = Literal[
    "deepseek",
    "qwen",
    "moonshot",
    "openai",
    "openai_compatible",
    # v3.26 视觉域：图像/视频供应商与对话供应商分开配置，能力绑定按 purpose 选取
    "openai_images",
    "dashscope_image",
    "openai_video",
    # v3.33 官方供应商/官方通道：目录同步出的统一协议适配
    "official_relay",
]

#: 模型模态（v3.33）：一个供应商下可同时存在多种模态的模型
Modality = Literal[
    "text",
    "image",
    "video",
    "audio",
    "multimodal",
    "embedding",
    "rerank",
    "asr",
    "tts",
]

#: 上游协议适配：决定走哪套请求/响应形态
Protocol = Literal[
    "openai_chat",
    "openai_images",
    "dashscope_image",
    "openai_video",
    "anthropic",
    "gemini",
    "openai_audio",
    "openai_embedding",
    "custom",
]

ModelSource = Literal["manual", "import", "probe", "relay", "official"]
ChannelKind = Literal["user", "relay", "official"]

STATUS_ENABLED = 1
STATUS_DISABLED = 2


class SupplierCreate(BaseModel):
    # 归属（yard_id）只能来自登录上下文，请求体出现任何未知字段一律 4xx 拒绝（ADR-0005）
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=40, description="逻辑名，如 deepseek-main")
    provider_type: ProviderType
    base_url: str = Field(min_length=1, max_length=500)
    model_id: str = Field(min_length=1, max_length=100, description="上游物理模型名")
    api_key: str = Field(
        min_length=1, max_length=2000, description="明文 Key，仅本次使用并加密落库"
    )
    models: str | None = Field(
        default=None, max_length=2000, description="模型白名单，逗号分隔；空=全量"
    )
    #: 创建时勾选的可用模型：服务端为每个模型落一行 supplier_models，
    #: 默认模型会被自动补进列表；不传则至少创建默认模型那一行。
    available_models: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(
        default=None, max_length=200, description="可用模型（上游物理名）列表"
    )
    priority: int = Field(default=0, description="越大越优先")
    # v3.32 金额额度：单价（元/千 token）；不填则按 Provider 默认常量估算
    price_in_per_1k: float | None = Field(default=None, ge=0, le=10000)
    price_out_per_1k: float | None = Field(default=None, ge=0, le=10000)
    scope: Literal["yard", "global"] = Field(
        default="yard", description="yard=仅本小院可见，global=全局共享（仅平台管理员）"
    )


class SupplierUpdate(BaseModel):
    # 同 SupplierCreate：禁止通过请求体伪造 yard_id 等归属字段
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=40)
    provider_type: ProviderType | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    model_id: str | None = Field(default=None, min_length=1, max_length=100)
    api_key: str | None = Field(
        default=None, min_length=1, max_length=2000, description="提供则轮换密钥，不提供则保持不变"
    )
    models: str | None = Field(default=None, max_length=2000)
    #: 勾选的可用模型：未勾选的已有模型置为停用（不删除，保留绑定与历史）；
    #: 不传表示不改动现有模型选择。
    available_models: list[Annotated[str, Field(min_length=1, max_length=200)]] | None = Field(
        default=None, max_length=200, description="可用模型（上游物理名）列表"
    )
    priority: int | None = None
    # function-calling 能力位：true 才允许声明式 Agent 对该供应商启用工具循环
    supports_tools: bool | None = None
    price_in_per_1k: float | None = Field(default=None, ge=0, le=10000)
    price_out_per_1k: float | None = Field(default=None, ge=0, le=10000)


class SupplierOut(BaseModel):
    id: str
    yard_id: str | None
    name: str
    provider_type: str
    base_url: str
    model_id: str
    api_key_masked: str
    models: str | None
    priority: int
    status: int
    is_active: bool = False
    # function-calling 能力位（默认 false：未显式声明的供应商不进工具循环）
    supports_tools: bool = False
    # 金额额度用的单价（元/千 token）；None = 未配置
    price_in_per_1k: float | None = None
    price_out_per_1k: float | None = None
    # 小院管理员视角：平台全局行（yard_id 为 NULL）只读（不可增删改/探测，仅可设为本小院生效）；
    # 官方通道供应商由平台维护，同样只读。平台管理员恒为 false。
    readonly: bool = False
    # v3.33：供应商多模型目录与通道类型（user/relay/official）
    default_model_id: str | None = None
    channel_kind: str = "user"
    consecutive_failures: int = 0
    test_time: str | None = None
    response_time_ms: int | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str

    model_config = {"extra": "ignore"}


class SupplierTestBody(BaseModel):
    """未落库候选供应商的连通性测试。"""

    provider_type: ProviderType
    base_url: str = Field(min_length=1, max_length=500)
    model_id: str = Field(min_length=1, max_length=100)
    api_key: str = Field(min_length=1, max_length=2000)


class SupplierTestResult(BaseModel):
    ok: bool
    latency_ms: int
    models: list[str] = Field(default_factory=list)
    note: str = ""


class SetActiveBody(BaseModel):
    supplier_id: str = Field(min_length=1)


# ---------- 模型列表探测（v1.3 §A） ----------

#: 探测接口可接受的类型：供应商域既有类型 + custom（等价 openai_compatible）
#: + anthropic/gemini（无统一列表端点，探测恒返回 unsupported）
ProbeProviderType = Literal[
    "deepseek",
    "qwen",
    "moonshot",
    "openai",
    "openai_compatible",
    "custom",
    "anthropic",
    "gemini",
    # v3.36：模板里的图像/视频/官方供应商类型也必须能点「探测模型」，
    # 否则会因 Literal 校验直接 422（“请求参数有误”）。
    "openai_images",
    "dashscope_image",
    "openai_video",
    "official_relay",
]


class SupplierProbeModelsBody(BaseModel):
    """模型列表探测入参：api_key 只在本次请求内存中瞬时使用，绝不落库/日志/审计。

    api_key 缺省时：若传了 supplier_id 则用该供应商已存密钥（小院可见范围内）；
    两者都没有则不带鉴权头探测（本机 Ollama / LM Studio 通常无需 Key）。
    """

    provider_type: ProbeProviderType
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=2000)
    supplier_id: str | None = Field(default=None, max_length=64)


class ProbeModelItem(BaseModel):
    """上游模型条目：id=提交给上游的物理模型名与其一致；label=下拉展示名。"""

    id: str
    label: str
    # v3.33：按模型名关键字猜测的模态（只作导入预填，绝不代替人工确认）
    guessed_modality: str | None = None


class ProbeModelsResult(BaseModel):
    """探测结果：source 三态，error/unsupported 时 message 给出诚实原因。"""

    models: list[ProbeModelItem] = Field(default_factory=list)
    source: Literal["remote", "unsupported", "error"]
    message: str | None = None


class ProviderTemplate(BaseModel):
    provider_type: str
    label: str
    base_url: str
    model_id: str
    note: str = ""


# ---------- 供应商多模型目录（v3.33） ----------


class SupplierModelCreate(BaseModel):
    """新建模型：``model_key`` 供应商内唯一，``upstream_model_id`` 是上游物理名。"""

    model_config = ConfigDict(extra="forbid")

    model_key: str = Field(min_length=1, max_length=100)
    upstream_model_id: str = Field(min_length=1, max_length=200)
    display_name: str = Field(default="", max_length=120)
    modality: Modality = "text"
    protocol: Protocol = "openai_chat"
    input_modalities: list[str] | None = Field(default=None, max_length=16)
    output_modalities: list[str] | None = Field(default=None, max_length=16)
    capabilities: dict[str, Any] | None = None
    context_window: int | None = Field(default=None, ge=0, le=100_000_000)
    max_output_tokens: int | None = Field(default=None, ge=0, le=100_000_000)
    endpoint_path: str | None = Field(default=None, max_length=200)
    pricing: dict[str, Any] | None = None
    default_params: dict[str, Any] | None = None
    limits_json: dict[str, Any] | None = None
    status: int = Field(default=1, ge=1, le=2)
    source: ModelSource = "manual"
    external_id: str | None = Field(default=None, max_length=200)


class SupplierModelUpdate(BaseModel):
    """更新模型：未提供的字段保持不变（``exclude_unset`` 语义）。"""

    model_config = ConfigDict(extra="forbid")

    model_key: str | None = Field(default=None, min_length=1, max_length=100)
    upstream_model_id: str | None = Field(default=None, min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=120)
    modality: Modality | None = None
    protocol: Protocol | None = None
    input_modalities: list[str] | None = Field(default=None, max_length=16)
    output_modalities: list[str] | None = Field(default=None, max_length=16)
    capabilities: dict[str, Any] | None = None
    context_window: int | None = Field(default=None, ge=0, le=100_000_000)
    max_output_tokens: int | None = Field(default=None, ge=0, le=100_000_000)
    endpoint_path: str | None = Field(default=None, max_length=200)
    pricing: dict[str, Any] | None = None
    default_params: dict[str, Any] | None = None
    limits_json: dict[str, Any] | None = None
    status: int | None = Field(default=None, ge=1, le=2)


class SupplierModelOut(BaseModel):
    id: str
    supplier_id: str
    model_key: str
    upstream_model_id: str
    display_name: str = ""
    modality: str = "text"
    protocol: str = "openai_chat"
    input_modalities: list[str] | None = None
    output_modalities: list[str] | None = None
    capabilities: dict[str, Any] | None = None
    context_window: int | None = None
    max_output_tokens: int | None = None
    endpoint_path: str | None = None
    pricing: dict[str, Any] | None = None
    default_params: dict[str, Any] | None = None
    limits_json: dict[str, Any] | None = None
    status: int = 1
    source: str = "manual"
    external_id: str | None = None
    test_ok: bool | None = None
    latency_ms: int | None = None
    last_error: str | None = None
    last_test_at: str | None = None
    created_at: str
    updated_at: str

    model_config = {"extra": "ignore"}


class SupplierImportSupplier(BaseModel):
    """导入时的供应商信息：``supplier_id`` 为空时据此创建（走现有加密逻辑）。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=40)
    provider_type: ProviderType | None = None
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=2000)
    # 旧字段：逗号分隔白名单，导入时同步维护，保证旧调用方仍能读
    models: str | None = Field(default=None, max_length=2000)
    scope: Literal["yard", "global"] = "yard"


class SupplierImportBundle(BaseModel):
    """导入包：要么给结构化 ``models``，要么给 ``content`` 文本（JSON/YAML/CSV）。

    ``api_key`` 只用于创建供应商，绝不进入任何预览/结果/审计。
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, ge=1, le=1)
    supplier: SupplierImportSupplier | None = None
    models: list[SupplierModelCreate] = Field(default_factory=list)
    # 文本导入模式：content + format；format 缺省按内容自动识别
    content: str | None = Field(default=None, max_length=2_000_000)
    format: Literal["json", "yaml", "csv"] | None = None


class SupplierImportPreviewItem(BaseModel):
    action: Literal["create", "update", "skip", "conflict", "error"]
    model_key: str = ""
    error: str | None = None


class SupplierImportPreview(BaseModel):
    items: list[SupplierImportPreviewItem] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)
    supplier_action: str = "existing"
    supplier_id: str | None = None


class SupplierImportResult(BaseModel):
    supplier_id: str
    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = Field(default_factory=list)


class SupplierImportUrlBody(BaseModel):
    """按 URL 导入目录/配置文件（严格 SSRF 校验、2MB 上限、最多 3 次重定向）。"""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2000)


class SupplierModelTestResult(BaseModel):
    ok: bool
    latency_ms: int = 0
    message: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class SupplierModelsBatchTestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_ids: list[str] = Field(default_factory=list, max_length=50)


# ---------- 官方供应商通道（v3.33） ----------


class RelayChannelCreate(BaseModel):
    """新建官方供应商通道：密钥只入参，出参一律打码。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    kind: Literal["official", "private"] = "private"
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=2000)
    public_key: str | None = Field(default=None, max_length=200)
    webhook_secret: str | None = Field(default=None, max_length=2000)
    catalog_path: str = Field(default="/models", max_length=200)
    enabled: bool = True
    scope: Literal["yard", "global"] = "yard"


class RelayChannelUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=80)
    kind: Literal["official", "private"] | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=2000)
    public_key: str | None = Field(default=None, max_length=200)
    webhook_secret: str | None = Field(default=None, max_length=2000)
    catalog_path: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None


class RelayChannelOut(BaseModel):
    id: str
    yard_id: str | None = None
    name: str
    kind: str
    base_url: str
    api_key_masked: str = ""
    public_key: str | None = None
    has_webhook_secret: bool = False
    catalog_path: str = "/models"
    enabled: bool = True
    status: str = "unconfigured"
    last_sync_at: str | None = None
    last_error: str | None = None
    readonly: bool = False
    created_at: str
    updated_at: str

    model_config = {"extra": "ignore"}


class RelaySyncResult(BaseModel):
    supplier_id: str
    created: int = 0
    updated: int = 0
    removed: int = 0
    items: list[dict[str, Any]] = Field(default_factory=list)
    source: str = "relay"
    message: str = ""


# ---------- 官方模型通道（v3.33） ----------


class OfficialPlanOut(BaseModel):
    id: str
    model_key: str
    name: str
    billing_unit: str
    price: float
    currency: str = "CNY"
    trial_tokens: float = 0
    trial_days: int = 0
    active: bool = True

    model_config = {"extra": "ignore"}


class OfficialTrialBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_key: str = Field(min_length=1, max_length=100)
    plan_code: str | None = Field(default=None, max_length=100)


class OfficialEntitlementOut(BaseModel):
    id: str
    yard_id: str
    user_id: str | None = None
    plan_id: str | None = None
    model_key: str
    source: str
    status: str
    quota_total: float = 0
    quota_used: float = 0
    starts_at: str
    expires_at: str | None = None

    model_config = {"extra": "ignore"}


class OfficialOrderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_key: str = Field(min_length=1, max_length=100)
    plan_code: str | None = Field(default=None, max_length=100)


class OfficialOrderOut(BaseModel):
    id: str
    yard_id: str
    user_id: str
    plan_id: str | None = None
    amount: float
    currency: str = "CNY"
    status: str
    external_order_id: str | None = None
    payment_url: str | None = None
    idempotency_key: str
    paid_at: str | None = None
    created_at: str

    model_config = {"extra": "ignore"}


class OfficialWebhookBody(BaseModel):
    """支付回调报文（签名对 raw body 校验，签名/时间戳也可走请求头）。"""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=100)
    event_type: str = Field(min_length=1, max_length=50)
    payload: dict[str, Any] = Field(default_factory=dict)
    signature: str = Field(default="", max_length=200)
    timestamp: str = Field(default="", max_length=40)
