"""版本化工作流执行引擎（开发文档 §8.4）。

- 工作流定义为 JSON，先用 contracts/json-schema/workflow.schema.json 校验。
- 执行前验证：节点类型白名单、引用的工具/Agent 存在且启用、无非法循环。
- 运行时强制 limits.max_steps 与 limits.timeout_seconds。
"""

import asyncio
import inspect
import json
import time
from pathlib import Path
from typing import Any

import jsonschema

from app.core.errors import AppError
from app.modules.agents.schemas import AgentContext, AgentResult

REPO_ROOT = Path(__file__).resolve().parents[4]
SCHEMA_DIR = REPO_ROOT / "contracts" / "json-schema"

NODE_TYPES = ("tool", "agent", "validation")


def _load_schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def load_workflow(path: str | Path) -> dict:
    """加载并校验工作流定义文件。校验失败抛 INVALID_REQUEST(400)。"""
    raw = Path(path).read_text(encoding="utf-8")
    try:
        definition = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AppError("INVALID_REQUEST", f"工作流定义不是合法 JSON：{e}", http_status=400) from e
    try:
        jsonschema.validate(definition, _load_schema("workflow.schema.json"))
    except jsonschema.ValidationError as e:
        raise AppError(
            "INVALID_REQUEST",
            f"工作流定义校验失败：{e.message}",
            http_status=400,
        ) from e
    return definition


def _execution_order(nodes: dict[str, dict], edges: list[dict]) -> list[dict]:
    """按 edges 拓扑排序得到执行顺序；存在非法循环时抛错。"""
    incoming: dict[str, int] = {nid: 0 for nid in nodes}
    outgoing: dict[str, list[str]] = {nid: [] for nid in nodes}
    for edge in edges:
        src, dst = edge["from"], edge["to"]
        if src not in nodes or dst not in nodes:
            raise AppError(
                "INVALID_REQUEST",
                f"工作流 edge 引用了不存在的节点：{src} → {dst}。",
                http_status=400,
            )
        outgoing[src].append(dst)
        incoming[dst] += 1
    # 稳定拓扑排序：优先 edges 中先出现的源节点
    ready = [nid for nid in nodes if incoming[nid] == 0]
    order: list[str] = []
    while ready:
        nid = ready.pop(0)
        order.append(nid)
        for dst in outgoing[nid]:
            incoming[dst] -= 1
            if incoming[dst] == 0:
                ready.append(dst)
    if len(order) != len(nodes):
        raise AppError("INVALID_REQUEST", "工作流存在非法循环，拒绝执行。", http_status=400)
    return [nodes[nid] for nid in order]


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def run_workflow(
    definition: dict[str, Any],
    input_data: dict[str, Any],
    ctx: AgentContext,
    tool_executor,
    agent_registry,
) -> dict[str, Any]:
    """执行工作流定义，返回 {"status", "trace", "outputs"}。

    - tool 节点：调用 tool_executor(name, params)；params 缺省时透传 input_data。
    - agent 节点：registry.get(name).run(input_data, ctx)，须返回 AgentResult。
    - validation 节点：用 qa-output schema 校验上一步输出。
    """
    nodes_list = definition.get("nodes") or []
    edges = definition.get("edges") or []
    limits = definition.get("limits") or {}
    max_steps = limits.get("max_steps")
    timeout_seconds = limits.get("timeout_seconds")
    if not isinstance(max_steps, int) or max_steps < 1:
        raise AppError("INVALID_REQUEST", "工作流 limits.max_steps 非法。", http_status=400)
    if not isinstance(timeout_seconds, int | float) or timeout_seconds < 1:
        raise AppError("INVALID_REQUEST", "工作流 limits.timeout_seconds 非法。", http_status=400)

    nodes = {n["id"]: n for n in nodes_list}
    for node in nodes.values():
        if node.get("type") not in NODE_TYPES:
            raise AppError(
                "INVALID_REQUEST",
                f"非法节点类型：{node.get('type')}（节点 {node.get('id')}）。",
                http_status=400,
            )
        if node["type"] == "agent":
            agent = agent_registry.get(node["name"])
            definition_meta = getattr(agent, "definition", None)
            enabled = (
                definition_meta.get("enabled", True) if isinstance(definition_meta, dict) else True
            )
            if not enabled:
                raise AppError(
                    "INVALID_REQUEST",
                    f"Agent 未启用：{node['name']}。",
                    http_status=400,
                )
        if node["type"] == "tool" and node["name"] not in (ctx.allowed_tools or []):
            raise AppError(
                "TOOL_FORBIDDEN",
                f"无工具调用权限：{node['name']}。",
                http_status=403,
            )

    ordered = _execution_order(nodes, edges)
    if len(ordered) > max_steps:
        raise AppError(
            "INVALID_REQUEST",
            f"工作流步骤数（{len(ordered)}）超过 limits.max_steps（{max_steps}）。",
            http_status=400,
        )

    qa_output_schema = _load_schema("qa-output.schema.json")
    trace: list[dict[str, Any]] = []
    outputs: dict[str, Any] = {}
    last_output: Any = None
    started = time.monotonic()

    for node in ordered:
        elapsed = time.monotonic() - started
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            raise AppError(
                "INTERNAL_ERROR",
                f"工作流执行超时（>{timeout_seconds}s），已终止。",
                http_status=500,
            )
        step_started = time.monotonic()
        entry: dict[str, Any] = {
            "node_id": node["id"],
            "type": node["type"],
            "name": node["name"],
        }
        try:

            async def _run_node(node: dict[str, Any], prev_output: Any):
                if node["type"] == "tool":
                    params = node.get("params") or dict(input_data)
                    result = await _maybe_await(tool_executor(node["name"], params))
                    return {"tool_result": result}, result
                if node["type"] == "agent":
                    agent = agent_registry.get(node["name"])
                    result = await agent.run(dict(input_data), ctx)
                    if not isinstance(result, AgentResult):
                        raise AppError(
                            "INTERNAL_ERROR",
                            f"Agent {node['name']} 未返回合法的 AgentResult。",
                            http_status=500,
                        )
                    return {"agent_result": result.model_dump(mode="json")}, result.output
                # validation
                if node["name"] == "qa_output_schema":
                    try:
                        jsonschema.validate(prev_output, qa_output_schema)
                    except jsonschema.ValidationError as e:
                        raise AppError(
                            "INVALID_REQUEST",
                            f"Agent 输出未通过 qa-output 校验：{e.message}",
                            http_status=400,
                        ) from e
                    return {"validated": True}, prev_output
                raise AppError(
                    "INVALID_REQUEST",
                    f"未知校验节点：{node['name']}。",
                    http_status=400,
                )

            node_output, last_output = await asyncio.wait_for(
                _run_node(node, last_output), timeout=remaining
            )
            outputs[node["id"]] = node_output
            entry["status"] = "succeeded"
        except AppError as e:
            entry["status"] = "failed"
            entry["error"] = {"code": e.code, "message": e.message}
            entry["duration_ms"] = int((time.monotonic() - step_started) * 1000)
            trace.append(entry)
            raise
        except TimeoutError:
            entry["status"] = "failed"
            entry["error"] = {"code": "INTERNAL_ERROR", "message": "节点执行超时。"}
            entry["duration_ms"] = int((time.monotonic() - step_started) * 1000)
            trace.append(entry)
            raise AppError(
                "INTERNAL_ERROR",
                f"工作流执行超时（>{timeout_seconds}s），已终止。",
                http_status=500,
            ) from None
        except Exception as e:  # noqa: BLE001
            entry["status"] = "failed"
            entry["error"] = {"code": "INTERNAL_ERROR", "message": "节点执行异常。"}
            entry["duration_ms"] = int((time.monotonic() - step_started) * 1000)
            trace.append(entry)
            raise AppError("INTERNAL_ERROR", "工作流节点执行异常。", http_status=500) from e
        entry["duration_ms"] = int((time.monotonic() - step_started) * 1000)
        trace.append(entry)

    return {"status": "succeeded", "trace": trace, "outputs": outputs}
