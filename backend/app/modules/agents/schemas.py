"""Agent 统一运行接口的数据模型（Pydantic v2），对应开发文档 §8.2。"""

from typing import Any

from pydantic import BaseModel, Field


class AgentContext(BaseModel):
    task_id: str
    tenant_id: str
    actor_id: str
    request_id: str
    allowed_tools: list[str] = Field(default_factory=list)
    deadline_seconds: int = Field(gt=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    # 调用者角色：工具执行必须沿用请求者的权限，缺失会导致 TOOL_FORBIDDEN。
    roles: list[str] = Field(default_factory=list)


class AgentResult(BaseModel):
    status: str = Field(description="succeeded | failed | awaiting_review")
    output: dict[str, Any] = Field(default_factory=dict)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
