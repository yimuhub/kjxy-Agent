"""SSO/OIDC 请求与响应 Schema（v3.18）。"""

from pydantic import BaseModel, ConfigDict, Field


class SsoProviderBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    issuer: str = Field(min_length=8, max_length=500)
    client_id: str = Field(min_length=1, max_length=255)
    redirect_uri: str = Field(min_length=8, max_length=500)
    frontend_redirect_uri: str | None = Field(default=None, max_length=500)
    scopes: str = Field(default="openid email profile", max_length=255)
    group_claim: str = Field(default="groups", min_length=1, max_length=80)
    role_mapping: dict[str, str] = Field(default_factory=dict)
    default_role: str = Field(default="viewer", max_length=32)
    email_domains: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = True
    auto_create_users: bool = True
    link_by_email: bool = True
    require_email_verified: bool = True


class SsoProviderCreate(SsoProviderBase):
    client_secret: str = Field(min_length=1, max_length=2000)


class SsoProviderUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    issuer: str | None = Field(default=None, min_length=8, max_length=500)
    client_id: str | None = Field(default=None, min_length=1, max_length=255)
    #: 留空表示沿用已存密钥；传空字符串等于不修改
    client_secret: str | None = Field(default=None, max_length=2000)
    redirect_uri: str | None = Field(default=None, min_length=8, max_length=500)
    frontend_redirect_uri: str | None = Field(default=None, max_length=500)
    scopes: str | None = Field(default=None, max_length=255)
    group_claim: str | None = Field(default=None, min_length=1, max_length=80)
    role_mapping: dict[str, str] | None = None
    default_role: str | None = Field(default=None, max_length=32)
    email_domains: list[str] | None = Field(default=None, max_length=50)
    enabled: bool | None = None
    auto_create_users: bool | None = None
    link_by_email: bool | None = None
    require_email_verified: bool | None = None


class SsoExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=20, max_length=200)


class SsoStartOut(BaseModel):
    authorize_url: str
    state: str
