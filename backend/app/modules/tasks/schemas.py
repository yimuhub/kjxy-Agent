"""任务模块 API Schema（Pydantic v2）。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.modules.tasks.titles import TASK_TYPE_LABELS, task_title


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

    @computed_field  # type: ignore[prop-decorator]
    @property
    def task_type_label(self) -> str:
        return TASK_TYPE_LABELS.get(self.task_type, self.task_type)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def title(self) -> str:
        """可读任务名称：类型中文名 + 输入主体（v3.29）。"""
        return task_title(self.task_type, self.input_json)


class TaskEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    event_type: str
    message: str
    progress: float | None = None
    payload_json: dict[str, Any] | None = None
    created_at: datetime
