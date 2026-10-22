"""插件模块 Schema。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PluginType = Literal["workflow", "knowledge_pack", "domain_pack"]
# workflow 型（v1.3-B C2）扩展状态：pending_review|rejected 只出现在租户级 workflow 插件上
PluginStatus = Literal["enabled", "disabled", "pending_review", "rejected"]


class PluginOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    plugin_id: str
    tenant_id: str | None
    version: str
    type: str
    status: str
    name: str
    description: str | None = None
    checksum_ok: bool
    installed_at: datetime | None
    created_at: datetime
    # v1.3-B：auto=自动挖掘草稿（manifest._auto），manual=人工注册/文件插件
    created_from: str = "manual"


class ScanError(BaseModel):
    path: str
    error: str


class ScanWarning(BaseModel):
    plugin_id: str
    path: str
    message: str


class ScanReport(BaseModel):
    model_config = ConfigDict(extra="ignore")

    discovered: int = 0
    registered: int = 0
    errors: list[ScanError] = Field(default_factory=list)
    checksum_warnings: list[ScanWarning] = Field(default_factory=list)


class PluginListQuery(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: PluginType | None = None
    status: PluginStatus | None = None


# ---------------------------------------------------------------- v1.3-B workflow 声明式插件
class PluginRegister(BaseModel):
    """workflow 插件注册请求：body 只带 manifest 本体，服务端补全溯源字段。"""

    model_config = ConfigDict(extra="ignore")

    manifest: dict


class PluginReviewIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decision: Literal["approve", "reject"]


class PluginRunIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    params: dict = Field(default_factory=dict)


class PluginRunOut(BaseModel):
    outputs: dict = Field(default_factory=dict)
    final: str = ""
