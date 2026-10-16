"""工具模块 Pydantic 契约。"""

from pydantic import BaseModel, Field


class ToolOut(BaseModel):
    code: str
    version: str
    name: str
    description: str
    input_schema: dict
    output_schema: dict | None = None
    required_permissions: list[str] = Field(default_factory=list)
    risk_level: str
    side_effect: bool = False
    timeout_seconds: int = 30
    enabled: bool = True


class ToolExecuteRequest(BaseModel):
    code: str
    params: dict = Field(default_factory=dict)


class ToolExecuteResponse(BaseModel):
    code: str
    result: dict
    latency_ms: int
