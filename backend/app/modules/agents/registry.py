"""Agent 注册表：按 code 注册 / 获取 Agent 实例，查询已注册定义。"""

from typing import Any

from app.core.errors import AppError


class AgentRegistry:
    def __init__(self) -> None:
        self._agents: dict[str, Any] = {}

    def register(self, code: str, agent: Any) -> None:
        """注册 Agent 实例。实例须满足 Agent Protocol（code/version 属性 + async run）。"""
        self._agents[code] = agent

    def get(self, code: str) -> Any:
        try:
            return self._agents[code]
        except KeyError:
            raise AppError(
                "INVALID_REQUEST", f"未注册的 Agent：{code}。", http_status=400
            ) from None

    def is_enabled(self, code: str) -> bool:
        agent = self.get(code)
        definition = getattr(agent, "definition", None)
        if isinstance(definition, dict):
            return bool(definition.get("enabled", True))
        return True

    def definitions(self) -> list[dict[str, Any]]:
        """返回所有已注册 Agent 的元数据（供管理端/调试查看）。"""
        result = []
        for code, agent in self._agents.items():
            definition = getattr(agent, "definition", None)
            if isinstance(definition, dict):
                result.append(definition)
            else:
                result.append(
                    {
                        "code": getattr(agent, "code", code),
                        "version": getattr(agent, "version", "0.0.0"),
                        "enabled": True,
                    }
                )
        return result

    def __contains__(self, code: str) -> bool:
        return code in self._agents

    def __len__(self) -> int:
        return len(self._agents)
