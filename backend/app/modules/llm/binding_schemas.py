"""模型绑定 Pydantic 契约（v3.25）。

供应商归属（``yard_id``）永远来自登录上下文，请求体禁止伪造；
未知字段一律 4xx 拒绝，与供应商域同一套纪律（ADR-0005）。
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScopeType = Literal["global", "purpose", "agent"]


class BindingEntry(BaseModel):
    """同层兜底链上的一条：供应商 + 可选模型覆盖 + 顺序 + 开关。"""

    model_config = ConfigDict(extra="forbid")

    supplier_id: str = Field(min_length=1, max_length=64)
    model_name: str | None = Field(default=None, max_length=100, description="覆盖上游物理模型名")
    priority: int = Field(default=0, ge=-1000, le=1000, description="越大越优先")
    enabled: bool = True


class BindingsReplace(BaseModel):
    """整层替换：一次提交某（scope_type, scope_key）下的全部条目。"""

    model_config = ConfigDict(extra="forbid")

    scope_type: ScopeType
    scope_key: str = Field(default="", max_length=100)
    entries: list[BindingEntry] = Field(default_factory=list)


class BindingOut(BaseModel):
    id: str
    yard_id: str | None
    scope_type: str
    scope_key: str
    supplier_id: str
    supplier_name: str = ""
    provider_type: str = ""
    model_id: str = ""
    model_name: str | None = None
    priority: int
    enabled: bool
    created_at: str
    updated_at: str

    model_config = {"extra": "ignore"}


class ResolutionHop(BaseModel):
    """解析链上的一档：这一层为什么用/不用它。"""

    level: str
    reason: str
    supplier_id: str | None = None
    supplier_name: str | None = None
    model_name: str | None = None
    usable: bool = False
    note: str | None = None


class ResolutionOut(BaseModel):
    """「这次调用会用谁」：按顺序给出候选与首个可用项。"""

    purpose: str | None = None
    purpose_label: str | None = None
    agent_code: str | None = None
    candidate_supplier_ids: list[str] = Field(default_factory=list)
    resolved_supplier_id: str | None = None
    resolved_model: str | None = None
    resolved_level: str = ""
    hops: list[ResolutionHop] = Field(default_factory=list)


class BindingTestBody(BaseModel):
    scope_type: ScopeType
    scope_key: str = Field(default="", max_length=100)
    prompt: str = Field(default="连通性测试，请回复 ok。", max_length=200)


class BindingTestOut(BaseModel):
    ok: bool
    attempted: list[dict[str, str]] = Field(default_factory=list)
    resolved_supplier_id: str | None = None
    model: str | None = None
    latency_ms: int = 0
    note: str = ""
