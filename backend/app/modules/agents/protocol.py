"""Agent 统一运行接口（Protocol），对应开发文档 §8.2。"""

from typing import Any, Protocol, runtime_checkable

from app.modules.agents.schemas import AgentContext, AgentResult


@runtime_checkable
class Agent(Protocol):
    code: str
    version: str

    async def run(
        self,
        input_data: dict[str, Any],
        context: AgentContext,
    ) -> AgentResult: ...
