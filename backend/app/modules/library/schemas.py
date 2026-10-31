"""官方库接口出入参（v3.35）。

归属只认登录上下文：``yard_id`` 仅平台管理员创建私有源时可显式指定。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["official", "private"]
Channel = Literal["stable", "beta"]
ItemType = Literal["skill", "plugin", "agent_template", "deck_theme", "doc_template"]


class SourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    kind: SourceKind = "private"
    base_url: str = Field(min_length=8, max_length=500)
    channel: Channel = "stable"
    #: 发布者 Ed25519 公钥（base64url 32 字节）；不填时源为 unconfigured
    public_key_b64: str = Field(default="", max_length=200)
    key_id: str | None = Field(default=None, max_length=32)
    enabled: bool = True
    #: 仅平台管理员创建私有源时生效；官方源必须为空
    yard_id: str | None = Field(default=None, max_length=36)
    metadata: dict | None = None


class SourceUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    channel: Channel | None = None
    public_key_b64: str | None = Field(default=None, max_length=200)
    key_id: str | None = Field(default=None, max_length=32)
    enabled: bool | None = None
    metadata: dict | None = None


class ItemInstallRequest(BaseModel):
    #: 指定版本；缺省安装该 item_key 的最新可用版本
    version: str | None = Field(default=None, max_length=50)


class PinRequest(BaseModel):
    pinned: bool = True


class AutoUpdateRequest(BaseModel):
    #: 兼容前端 {auto_update: bool} 与脚本/文档 {enabled: bool} 两种写法
    enabled: bool | None = None
    auto_update: bool | None = None

    @property
    def value(self) -> bool:
        if self.enabled is not None:
            return self.enabled
        if self.auto_update is not None:
            return self.auto_update
        return True


class SourceSyncRequest(BaseModel):
    """手动同步无参数；保留空模型以统一 OpenAPI 形状。"""

    force: bool = False


__all__ = [
    "AutoUpdateRequest",
    "ItemInstallRequest",
    "PinRequest",
    "SourceCreateRequest",
    "SourceSyncRequest",
    "SourceUpdateRequest",
]
