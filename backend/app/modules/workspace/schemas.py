"""工作区接口出入参（v3.63）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceProjectCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=80)
    root_path: str = Field(min_length=1, max_length=1024)
    description: str | None = Field(default=None, max_length=2000)
    #: 允许修改的相对路径 glob；不传等同 ``["**/*"]``
    allowed_paths: list[str] | None = Field(default=None, max_length=50)


class WorkspacePreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    plugin_id: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(max_length=4_000_000)


class WorkspaceRollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    #: 文件在应用后又被人改过时，必须显式 force 才覆盖
    force: bool = False


__all__ = [
    "WorkspacePreviewRequest",
    "WorkspaceProjectCreate",
    "WorkspaceRollbackRequest",
]
