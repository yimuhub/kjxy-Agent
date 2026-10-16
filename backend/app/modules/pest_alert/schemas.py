"""虫情预警的输入/输出 Pydantic 模型（Pydantic v2）。

本文件由脚手架 app.modules.agents.scaffold 自动生成：
字段为示例骨架，接入真实业务时按需增删字段（保持 Input/Output 命名约定）。
"""

from typing import Any

from pydantic import BaseModel, Field


class PestAlertInput(BaseModel):
    """虫情预警输入（示例字段，TODO 按业务替换）。"""

    query: str = Field(min_length=1, description="示例：用户提问 / 任务描述")
    top_k: int = Field(default=5, ge=1, le=20, description="示例：知识检索条数")


class PestAlertOutput(BaseModel):
    """虫情预警输出（示例字段，TODO 按业务替换）。"""

    answer: str = Field(description="示例：拼装后的回答正文")
    sources: list[dict[str, Any]] = Field(
        default_factory=list, description="示例：检索到的证据条目"
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="示例：结果置信度")
    warnings: list[str] = Field(default_factory=list, description="示例：告警信息")
