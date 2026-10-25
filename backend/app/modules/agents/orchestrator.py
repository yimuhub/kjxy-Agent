"""总控调度 Agent（code="orchestrator"）：接收任务、选择工作流/专业 Agent、汇总结果。

- 工作流定义优先加载 plugins/workflows/knowledge_qa_v1.json；
  文件不存在时使用内置默认三节点定义（retrieve → answer → validate）。
- 专业 Agent（如 agriculture_qa）只通过 AgentRegistry 调用，不直接 import
  其实现（真实实现由 app/modules/agriculture/qa_agent.py 提供并自行注册）。
"""

import copy
from typing import Any

from app.core.errors import AppError
from app.modules.agents.definitions import get_definition
from app.modules.agents.protocol import Agent  # noqa: F401 - 导出协议供类型检查
from app.modules.agents.registry import AgentRegistry
from app.modules.agents.schemas import AgentContext, AgentResult
from app.modules.agents.workflow_runner import REPO_ROOT, load_workflow, run_workflow

WORKFLOW_FILE = REPO_ROOT / "plugins" / "workflows" / "knowledge_qa_v1.json"

DEFAULT_QA_WORKFLOW: dict[str, Any] = {
    "workflow_code": "knowledge_qa_v1",
    "version": "1.0.0",
    "nodes": [
        {"id": "retrieve", "type": "tool", "name": "knowledge.search"},
        {"id": "answer", "type": "agent", "name": "agriculture_qa"},
        {"id": "validate", "type": "validation", "name": "qa_output_schema"},
    ],
    "edges": [
        {"from": "retrieve", "to": "answer"},
        {"from": "answer", "to": "validate"},
    ],
    "limits": {"max_steps": 8, "timeout_seconds": 120},
}


def default_tool_executor():
    """默认工具执行器：委托给 tools 模块（由 tools 主责 Agent 提供
    app/modules/tools/executor.py::execute_tool(name, params)）；缺失时降级为
    TOOL_NOT_FOUND，避免 orchestrator 在 tools 模块就绪前无法 import。"""
    try:
        from app.modules.tools.executor import execute_tool  # type: ignore
    except ImportError:

        async def _unavailable(name: str, params: dict) -> Any:
            raise AppError("TOOL_NOT_FOUND", f"工具未启用：{name}。", http_status=404)

        return _unavailable
    return execute_tool


class OrchestratorAgent:
    code = "orchestrator"
    version = "1.0.0"
    definition = get_definition("orchestrator")

    def __init__(self, tool_executor=None, registry: AgentRegistry | None = None) -> None:
        self._tool_executor = tool_executor
        self._registry = registry

    def _get_tool_executor(self):
        return self._tool_executor or default_tool_executor()

    def _get_registry(self) -> AgentRegistry:
        if self._registry is not None:
            return self._registry
        # 总控运行的工作流可能引用任意专业 Agent，必须使用全局注册表，
        # 而不是只含自身的临时表，否则 answer 节点会报未注册。
        from app.modules.agents import get_global_registry

        registry = get_global_registry()
        if self.code not in registry:
            registry.register(self.code, self)
        return registry

    def _load_definition(self) -> dict[str, Any]:
        if WORKFLOW_FILE.exists():
            return load_workflow(WORKFLOW_FILE)
        return copy.deepcopy(DEFAULT_QA_WORKFLOW)

    async def run(self, input_data: dict[str, Any], context: AgentContext) -> AgentResult:
        registry = self._get_registry()
        tool_executor = self._get_tool_executor()
        try:
            wf_result = await run_workflow(
                self._load_definition(), dict(input_data), context, tool_executor, registry
            )
        except AppError as e:
            return AgentResult(
                status="failed",
                output={"error_code": e.code, "message": e.message},
                warnings=["工作流执行失败。"],
            )
        except Exception:  # noqa: BLE001 - 不向调用方泄漏内部堆栈
            return AgentResult(
                status="failed",
                output={"error_code": "INTERNAL_ERROR", "message": "编排执行异常。"},
                warnings=["工作流执行失败。"],
            )
        return self._summarize(wf_result)

    def _summarize(self, wf_result: dict[str, Any]) -> AgentResult:
        """汇总工作流结果：取最后一个成功的 agent 节点的 AgentResult。"""
        trace = wf_result.get("trace", [])
        outputs = wf_result.get("outputs", {})
        last_agent_result: dict | None = None
        for entry in trace:
            if entry.get("type") == "agent" and entry.get("status") == "succeeded":
                payload = outputs.get(entry["node_id"], {})
                if isinstance(payload, dict) and "agent_result" in payload:
                    last_agent_result = payload["agent_result"]
        if last_agent_result:
            try:
                return AgentResult(**last_agent_result)
            except Exception:  # noqa: BLE001
                pass
        return AgentResult(
            status="succeeded",
            output={"workflow": wf_result.get("status"), "outputs": outputs},
            warnings=["工作流未产生 Agent 结果，已返回原始输出。"],
        )
