"""一句话业务 Schema（v3.22）。"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

QuickModule = Literal["farm", "diagnosis", "work_order"]


class QuickActionPreviewIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    module: QuickModule
    text: str = Field(min_length=2, max_length=500)
    #: 页面上下文：在某个地块详情里说话时，可省去地块名
    plot_id: str | None = Field(default=None, max_length=36)
    #: 业务日（前端可传当前日期）；缺省用服务器本地日期
    today: date | None = None


class QuickActionConfirmIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    module: QuickModule
    action: str = Field(min_length=1, max_length=40)
    payload: dict[str, Any]
    #: 预览返回的指纹：确认时必须一致，防止预览后被改动或过期
    fingerprint: str = Field(min_length=32, max_length=64)


class QuickActionPreviewOut(BaseModel):
    module: str
    action: str
    payload: dict[str, Any]
    summary: str
    missing: list[str] = Field(default_factory=list)
    ready: bool = True
    source: str = "rule"
    fingerprint: str


class QuickActionConfirmOut(BaseModel):
    module: str
    action: str
    created: dict[str, Any]
    summary: str
