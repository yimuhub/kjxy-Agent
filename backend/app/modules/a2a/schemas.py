"""A2A 请求契约。extra=forbid：yard_id 等归属字段禁止由请求体注入。"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class A2aPeerCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=8, max_length=500)
    #: 远端要求的 Bearer 令牌；存 Fernet 密文，接口只回「是否已配置」
    token: str | None = Field(default=None, max_length=2000)
    enabled: bool = True


class A2aPeerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    #: 留空表示沿用已存令牌；传 ``clear_token=true`` 才清空
    token: str | None = Field(default=None, max_length=2000)
    clear_token: bool | None = None
    enabled: bool | None = None


class A2aEndpointUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


class A2aTaskSend(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=80)
    input_text: str = Field(default="", max_length=8000)


class JsonRpcRequest(BaseModel):
    """入站 JSON-RPC 2.0 信封（A2A 客户端可能携带额外字段，故允许 extra）。"""

    jsonrpc: str = "2.0"
    id: int | str | None = None
    method: str = Field(min_length=1, max_length=80)
    params: dict[str, Any] | None = None
