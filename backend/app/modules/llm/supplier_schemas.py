"""模型供应商 Pydantic 契约。

密钥字段（api_key）只在入参出现：创建/更新/测试时使用，用完即弃，
绝不出现在任何出参中；列表/详情统一返回 api_key_masked。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ProviderType = Literal["deepseek", "qwen", "moonshot", "openai", "openai_compatible"]

STATUS_ENABLED = 1
STATUS_DISABLED = 2


class SupplierCreate(BaseModel):
    # 归属（tenant_id）只能来自登录上下文，请求体出现任何未知字段一律 4xx 拒绝（ADR-0005）
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
    scope: Literal["tenant", "global"] = Field(
        default="tenant", description="tenant=仅本租户可见，global=全局共享（仅平台管理员）"
    )


class SupplierUpdate(BaseModel):
    # 同 SupplierCreate：禁止通过请求体伪造 tenant_id 等归属字段
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


class SupplierOut(BaseModel):
    id: str
    tenant_id: str | None
    name: str
    provider_type: str
    base_url: str
    model_id: str
    api_key_masked: str
    models: str | None
    priority: int
    status: int
    is_active: bool = False
    # 租户管理员视角：平台全局行（tenant_id 为 NULL）只读（不可增删改/探测，仅可设为本租户生效）；
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


class ProviderTemplate(BaseModel):
    provider_type: str
    label: str
    base_url: str
    model_id: str
    note: str = ""
