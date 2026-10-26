"""推送通道 Pydantic 契约。extra=forbid：yard_id 等归属字段禁止由请求体注入。"""

from pydantic import BaseModel, ConfigDict, Field


class PushChannelCreate(BaseModel):
    """创建请求。URL 可先缺省（建好通道后 PATCH 补录），提交即 SSRF 校验 + 加密。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(min_length=1, max_length=30)
    url: str | None = Field(default=None, max_length=1000)
    events: list[str] = Field(default_factory=list)
    enabled: bool = True


class PushChannelUpdate(BaseModel):
    """编辑请求：全部可选；提交 url = 重填即换（无独立轮换端点），同时清零失败计数。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: str | None = Field(default=None, min_length=1, max_length=30)
    url: str | None = Field(default=None, min_length=1, max_length=1000)
    events: list[str] | None = None
    enabled: bool | None = None
