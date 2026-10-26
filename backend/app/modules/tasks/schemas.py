"""任务模块 API Schema（Pydantic v2）。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskCreate(BaseModel):
    task_type: str = Field(min_length=1, max_length=100)
    input: dict[str, Any] = Field(default_factory=dict)
    priority: str = Field(default="normal", min_length=1, max_length=20)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    yard_id: str
    created_by: str
    task_type: str
    status: str
    priority: str
    input_json: dict[str, Any]
    result_json: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


class TaskEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    event_type: str
    message: str
    progress: float | None = None
    payload_json: dict[str, Any] | None = None
    created_at: datetime
