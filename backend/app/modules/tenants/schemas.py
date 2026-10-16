"""租户模块 Schema。"""

from datetime import datetime

from pydantic import BaseModel, Field


class TenantOut(BaseModel):
    tenant_id: str
    name: str
    status: str
    province: str | None = None
    city: str | None = None
    scene_type: str | None = None
    config: dict | None = None


class TenantMemberOut(BaseModel):
    user_id: str
    username: str
    display_name: str
    email: str | None = None
    role: str
    status: str
    joined_at: datetime


class AddMemberRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    role: str = Field(..., description="tenant_admin|expert|operator|viewer|service_worker")
    display_name: str | None = Field(default=None, max_length=64)


class TenantSignupRequest(BaseModel):
    """公开自助注册请求。"""

    tenant_name: str = Field(..., min_length=2, max_length=64)
    admin_username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=6, max_length=128)
    contact: str | None = Field(default=None, max_length=128)


class TenantSignupResponse(BaseModel):
    tenant_id: str
    user_id: str
    access_token: str
    token_type: str = "Bearer"
