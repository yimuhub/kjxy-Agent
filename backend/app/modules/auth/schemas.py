"""认证模块 Schema。"""

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    # 旧客户端仍会 POST tenant_id：用校验别名同时接受两种拼写，
    # 对外字段与序列化结果保持 yard_id 不变（extra 默认为 ignore，不会 422）。
    model_config = ConfigDict(populate_by_name=True)

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)
    yard_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("yard_id", "tenant_id"),
        serialization_alias="yard_id",
        description="多小院用户登录时指定；单小院用户自动选择",
    )


class YardBrief(BaseModel):
    yard_id: str
    yard_name: str
    roles: list[str]


class UserOut(BaseModel):
    user_id: str
    username: str
    email: str | None
    phone: str | None = None
    display_name: str
    status: str
    must_change_password: bool = False
    last_login_at: datetime | None = None

    model_config = {"from_attributes": True}


class LoginResponseData(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut
    roles: list[str] = Field(default_factory=list)
    yards: list[YardBrief]


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class RefreshResponseData(BaseModel):
    access_token: str
    # 轮换后的新刷新令牌；旧令牌在本次刷新后立即作废
    refresh_token: str
    token_type: str = "bearer"


class LogoutRequest(BaseModel):
    """登出可选携带 refresh_token：带上即精确吊销该刷新会话。"""

    refresh_token: str | None = Field(default=None, min_length=1)


class MeResponseData(BaseModel):
    user: UserOut
    roles: list[str]
    yards: list[YardBrief]


class ProfileUpdateRequest(BaseModel):
    """自助资料：只允许改动显示名与联系方式。"""

    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=32)


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=1, max_length=128)


class ChangePasswordResponseData(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    must_change_password: bool = False
