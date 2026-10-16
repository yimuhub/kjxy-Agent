"""Agent 运行模块：注册、执行上下文、运行限制、工作流编排。"""

from app.modules.agents.definitions import AGENT_DEFINITIONS, get_definition
from app.modules.agents.orchestrator import OrchestratorAgent, default_tool_executor
from app.modules.agents.protocol import Agent
from app.modules.agents.registry import AgentRegistry
from app.modules.agents.schemas import AgentContext, AgentResult
from app.modules.agents.workflow_runner import load_workflow, run_workflow

__all__ = [
    "Agent",
    "AgentContext",
    "AgentResult",
    "AgentRegistry",
    "OrchestratorAgent",
    "AGENT_DEFINITIONS",
    "get_definition",
    "default_tool_executor",
    "load_workflow",
    "run_workflow",
]


def build_default_registry() -> AgentRegistry:
    """构建默认注册表：注册 orchestrator；agriculture_qa 等由各自模块自行注册。"""
    registry = AgentRegistry()
    registry.register(OrchestratorAgent.code, OrchestratorAgent())
    return registry


default_registry = build_default_registry()

#: 全局共享注册表：各专业 Agent 模块 import 时向此注册（见各模块 agent.py 的 _register_agent）。
#: HTTP 层（agents/router.py）与 orchestrator 都从这里取 Agent 实例。
global_registry = AgentRegistry()
global_registry.register(OrchestratorAgent.code, OrchestratorAgent())


def get_global_registry() -> AgentRegistry:
    return global_registry
