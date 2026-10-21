"""MCP 服务器 Pydantic 契约。extra=forbid：tenant_id 等归属字段禁止由请求体注入。"""

from pydantic import BaseModel, ConfigDict, Field


class McpServerCreate(BaseModel):
    """创建请求：url 必填（提交即 SSRF 校验 + Fernet 加密）；headers 逐值加密存。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8, max_length=1000)
    headers: dict[str, str] | None = Field(default=None)
    transport: str = Field(default="streamable_http", max_length=30)
    enabled: bool = True


class McpServerUpdate(BaseModel):
    """编辑请求：改名/停用/换 url（重填即换，headers 整体重填即换）；全可选。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = Field(default=None, min_length=8, max_length=1000)
    headers: dict[str, str] | None = None
    enabled: bool | None = None
