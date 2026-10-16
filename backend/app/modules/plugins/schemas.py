"""插件模块 Schema。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PluginType = Literal["workflow", "knowledge_pack", "domain_pack"]
PluginStatus = Literal["enabled", "disabled"]


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
