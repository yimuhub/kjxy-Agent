"""分级额度接口出入参（v3.32）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ScopeType = Literal["yard", "agent", "agent_group", "model", "supplier"]
Metric = Literal["tokens", "calls", "cost"]
WindowType = Literal["day", "month"]
OnExceed = Literal["warn", "block", "approve"]


class PolicyUpsertRequest(BaseModel):
    scope_type: ScopeType
    scope_key: str = Field(default="", max_length=100, description="yard 作用域留空")
    metric: Metric = "tokens"
    window: WindowType = "month"
    limit_value: float = Field(
        ge=-1,
        description="tokens/calls 为个数，cost 为元；-1=无限制，0=零余额（拒绝服务），>0=硬上限",
    )
    on_exceed: OnExceed = "approve"
    note: str | None = Field(default=None, max_length=200)
    enabled: bool = True


class QuotaRequestBody(BaseModel):
    policy_id: str = Field(min_length=1, max_length=36)
    requested_limit: float = Field(gt=0)
    reason: str = Field(default="", max_length=500)
    valid_days: int = Field(default=7, ge=0, le=90)


class QuotaDecisionRequest(BaseModel):
    note: str = Field(default="", max_length=500)
    #: 审批人可下调批准额度；空 = 按申请值
    approved_limit: float | None = Field(default=None, gt=0)


class EnvironmentRequest(BaseModel):
    environment: Literal["trial", "standard", "production"]
