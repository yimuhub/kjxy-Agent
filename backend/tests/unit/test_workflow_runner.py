"""工作流运行器单元测试：三节点工作流通行 + 非法输入被拒绝。"""

import asyncio
import copy
import json

import pytest
from app.core.errors import AppError
from app.modules.agents.orchestrator import OrchestratorAgent
from app.modules.agents.registry import AgentRegistry
from app.modules.agents.schemas import AgentContext, AgentResult
from app.modules.agents.workflow_runner import load_workflow, run_workflow

VALID_QA_OUTPUT = {
    "answer": "根据已检索资料，建议在分蘖期重点观察有效分蘖数。",
    "citations": [],
    "warnings": ["本回答仅供技术参考。"],
    "needs_expert_review": False,
}

THREE_NODE_WORKFLOW = {
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
    "limits": {"max_steps": 8, "timeout_seconds": 60},
}


def make_ctx(**kwargs) -> AgentContext:
    base = dict(
        task_id="task-1",
        tenant_id="tenant-1",
        actor_id="user-1",
        request_id="req-1",
        allowed_tools=["knowledge.search"],
        deadline_seconds=60,
    )
    base.update(kwargs)
    return AgentContext(**base)


class StubQaAgent:
    code = "agriculture_qa"
    version = "1.0.0"
    definition = {"code": "agriculture_qa", "version": "1.0.0", "enabled": True}

    def __init__(self, output=None, delay: float = 0.0):
        self._output = output if output is not None else VALID_QA_OUTPUT
        self._delay = delay
        self.calls: list[dict] = []

    async def run(self, input_data, context) -> AgentResult:
        self.calls.append(dict(input_data))
        if self._delay:
            await asyncio.sleep(self._delay)
        return AgentResult(
            status="succeeded",
            output=self._output,
            citations=[{"document_id": "doc-1", "chunk_id": "c-1"}],
            warnings=["stub"],
            confidence=0.8,
        )


async def fake_tool_executor(name: str, params: dict) -> dict:
    assert name == "knowledge.search"
    return {"chunks": [{"chunk_id": "c-1", "score": 0.9}], "params": params}


def make_registry(agent=None) -> AgentRegistry:
    registry = AgentRegistry()
    registry.register("agriculture_qa", agent or StubQaAgent())
    return registry


async def test_three_node_workflow_success():
    result = await run_workflow(
        copy.deepcopy(THREE_NODE_WORKFLOW),
        {"question": "水稻分蘖期观察什么？"},
        make_ctx(),
        fake_tool_executor,
        make_registry(),
    )
    assert result["status"] == "succeeded"
    assert [s["node_id"] for s in result["trace"]] == ["retrieve", "answer", "validate"]
    assert all(s["status"] == "succeeded" for s in result["trace"])
    assert result["outputs"]["answer"]["agent_result"]["output"] == VALID_QA_OUTPUT
    # tool 节点默认透传 input_data 作为 params
    tool_params = result["outputs"]["retrieve"]["tool_result"]["params"]
    assert tool_params["question"] == "水稻分蘖期观察什么？"


async def test_tool_executor_may_be_sync():
    def sync_tool(name, params):
        return {"ok": True}

    wf = copy.deepcopy(THREE_NODE_WORKFLOW)
    wf["nodes"] = [wf["nodes"][0]]
    wf["edges"] = []
    result = await run_workflow(wf, {"q": "x"}, make_ctx(), sync_tool, make_registry())
    assert result["status"] == "succeeded"


async def test_timeout_is_rejected():
    wf = copy.deepcopy(THREE_NODE_WORKFLOW)
    wf["limits"]["timeout_seconds"] = 1
    registry = make_registry(StubQaAgent(delay=5))
    with pytest.raises(AppError) as exc_info:
        await run_workflow(wf, {"question": "x"}, make_ctx(), fake_tool_executor, registry)
    assert exc_info.value.code == "INTERNAL_ERROR"


async def test_illegal_node_type_is_rejected():
    wf = copy.deepcopy(THREE_NODE_WORKFLOW)
    wf["nodes"].append({"id": "evil", "type": "loop", "name": "loop"})
    with pytest.raises(AppError) as exc_info:
        await run_workflow(wf, {}, make_ctx(), fake_tool_executor, make_registry())
    assert exc_info.value.code == "INVALID_REQUEST"
    assert exc_info.value.http_status == 400


async def test_cycle_is_rejected():
    wf = copy.deepcopy(THREE_NODE_WORKFLOW)
    wf["edges"].append({"from": "validate", "to": "retrieve"})
    with pytest.raises(AppError) as exc_info:
        await run_workflow(wf, {}, make_ctx(), fake_tool_executor, make_registry())
    assert exc_info.value.code == "INVALID_REQUEST"


async def test_unknown_agent_is_rejected():
    with pytest.raises(AppError) as exc_info:
        await run_workflow(
            copy.deepcopy(THREE_NODE_WORKFLOW),
            {},
            make_ctx(),
            fake_tool_executor,
            AgentRegistry(),
        )
    assert exc_info.value.code == "INVALID_REQUEST"


async def test_disabled_agent_is_rejected():
    agent = StubQaAgent()
    agent.definition = {"code": "agriculture_qa", "version": "1.0.0", "enabled": False}
    with pytest.raises(AppError) as exc_info:
        await run_workflow(
            copy.deepcopy(THREE_NODE_WORKFLOW),
            {},
            make_ctx(),
            fake_tool_executor,
            make_registry(agent),
        )
    assert exc_info.value.code == "INVALID_REQUEST"


async def test_tool_not_in_allowed_tools_is_rejected():
    with pytest.raises(AppError) as exc_info:
        await run_workflow(
            copy.deepcopy(THREE_NODE_WORKFLOW),
            {},
            make_ctx(allowed_tools=[]),
            fake_tool_executor,
            make_registry(),
        )
    assert exc_info.value.code == "TOOL_FORBIDDEN"
    assert exc_info.value.http_status == 403


async def test_validation_failure_is_rejected():
    bad_output = {"answer": "缺字段的输出"}
    registry = make_registry(StubQaAgent(output=bad_output))
    with pytest.raises(AppError) as exc_info:
        await run_workflow(
            copy.deepcopy(THREE_NODE_WORKFLOW), {}, make_ctx(), fake_tool_executor, registry
        )
    assert exc_info.value.code == "INVALID_REQUEST"


async def test_max_steps_is_enforced():
    wf = copy.deepcopy(THREE_NODE_WORKFLOW)
    wf["limits"]["max_steps"] = 2
    with pytest.raises(AppError) as exc_info:
        await run_workflow(wf, {}, make_ctx(), fake_tool_executor, make_registry())
    assert exc_info.value.code == "INVALID_REQUEST"


def test_load_workflow_accepts_valid_file(tmp_path):
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(THREE_NODE_WORKFLOW), encoding="utf-8")
    definition = load_workflow(path)
    assert definition["workflow_code"] == "knowledge_qa_v1"


def test_load_workflow_rejects_invalid_definition(tmp_path):
    bad = copy.deepcopy(THREE_NODE_WORKFLOW)
    del bad["limits"]
    path = tmp_path / "wf.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(AppError) as exc_info:
        load_workflow(path)
    assert exc_info.value.code == "INVALID_REQUEST"


def test_load_workflow_rejects_bad_json(tmp_path):
    path = tmp_path / "wf.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(AppError):
        load_workflow(path)


async def test_orchestrator_summarizes_agent_result():
    registry = make_registry()
    orchestrator = OrchestratorAgent(tool_executor=fake_tool_executor, registry=registry)
    result = await orchestrator.run({"question": "水稻分蘖期观察什么？"}, make_ctx())
    assert isinstance(result, AgentResult)
    assert result.status == "succeeded"
    assert result.output == VALID_QA_OUTPUT
    assert result.confidence == 0.8
    assert result.citations == [{"document_id": "doc-1", "chunk_id": "c-1"}]


async def test_orchestrator_reports_failure_when_agent_missing():
    orchestrator = OrchestratorAgent(tool_executor=fake_tool_executor, registry=AgentRegistry())
    result = await orchestrator.run({"question": "x"}, make_ctx())
    assert result.status == "failed"
    assert result.output["error_code"] == "INVALID_REQUEST"


def test_orchestrator_defaults_to_global_registry_with_professional_agents():
    """未注入 registry 时，总控必须使用全局注册表，否则 answer 节点必然 miss。"""
    from app.modules.agents import get_global_registry
    from app.modules.agents.router import _ensure_imported

    _ensure_imported("orchestrator")
    registry = OrchestratorAgent()._get_registry()
    assert registry is get_global_registry()
    assert "agriculture_qa" in registry
