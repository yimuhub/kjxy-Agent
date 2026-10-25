"""认证模块 Schema。"""

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    tenant_id: str | None = Field(
        default=None, description="多租户用户登录时指定；单租户用户自动选择"
    )


class TenantBrief(BaseModel):
    tenant_id: str
    tenant_name: str
    roles: list[str]


class UserOut(BaseModel):
    user_id: str
    username: str
    email: str | None
    display_name: str
    status: str
    last_login_at: datetime | None = None

    model_config = {"from_attributes": True}


class LoginResponseData(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut
    roles: list[str] = Field(default_factory=list)
    tenants: list[TenantBrief]


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class RefreshResponseData(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResponseData(BaseModel):
    user: UserOut
    roles: list[str]
    tenants: list[TenantBrief]
