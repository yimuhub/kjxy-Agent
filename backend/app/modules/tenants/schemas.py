"""租户模块 Schema。"""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator


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
    """公开自助注册请求。

    密码长度不在此写死：由 signup 业务层按 settings.password_min_length 校验
    （生产环境再强制不低于 10 位）。
    """

    tenant_name: str = Field(..., min_length=2, max_length=64)
    admin_username: str = Field(..., min_length=3, max_length=32)
    password: str = Field(..., min_length=1, max_length=128)
    contact: str | None = Field(default=None, max_length=128)


class TenantSignupResponse(BaseModel):
    tenant_id: str
    user_id: str
    access_token: str
    token_type: str = "Bearer"


class QuotaAdminPatchRequest(BaseModel):
    """运营调整租户配额限额：三个限额字段均 ge=0，至少提供一个。"""

    llm_calls_limit: int | None = Field(default=None, ge=0, description="LLM 调用限额")
    knowledge_docs_limit: int | None = Field(default=None, ge=0, description="知识文档限额")
    tasks_limit: int | None = Field(default=None, ge=0, description="任务限额")

    @model_validator(mode="after")
    def _require_at_least_one(self) -> "QuotaAdminPatchRequest":
        if (
            self.llm_calls_limit is None
            and self.knowledge_docs_limit is None
            and self.tasks_limit is None
        ):
            raise ValueError("至少提供一个配额限额字段")
        return self
