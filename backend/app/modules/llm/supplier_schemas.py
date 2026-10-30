"""模型供应商 Pydantic 契约。

密钥字段（api_key）只在入参出现：创建/更新/测试时使用，用完即弃，
绝不出现在任何出参中；列表/详情统一返回 api_key_masked。
"""

from typing import Literal

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
]

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
    # 平台管理员恒为 false。默认 false，兼容未感知该字段的旧前端。
    readonly: bool = False
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
