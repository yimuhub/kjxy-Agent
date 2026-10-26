"""C2 声明式插件（v1.3-B 自生长闭环）：workflow 型 manifest 的注册校验 / 审核 /
解释执行 / 重复序列挖掘。

安全双闸（DESIGN-v3 C2，铁律）：
- 发布闸：register 只产 ``status=pending_review`` 草稿，``approve`` 才能 ``enabled`` 运行；
- 运行闸：steps 里的 tool 一律走 ``tool_registry.execute`` 全链路
  （Schema 校验 → 权限 → 风险闸/审批 → 超时 → 审计），绝不绕过逐次审批。

manifest_json（workflow 型）::

    {"name": "...", "description": "...", "params": [{"key","label","required"}],
     "steps": [{"kind":"tool","tool":"sensor.query","args":{},"save_as":"r1"},
               {"kind":"llm","prompt":"基于 {{r1}} 给出建议 {{question}}"}]}

模板插值：字符串中的 ``{{var}}`` 以 运行参数 ∪ save_as 产物 渲染（渲染值先文本化）。
suggest 挖掘：近 7 天 ``agent_run_steps`` 中重复出现 ≥3 次、长度 ≥2 的相邻工具序列，
只产 pending_review 草稿（manifest._auto 记录 pattern/occurrences/source_runs sha12），
绝不自动启用——自动化止步于建议，启用永远经人。
"""

import hashlib
import json
import re
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents.run_steps import STEP_OK, STEP_TOOL, AgentRunStep
from app.modules.audit.service import log_audit
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.plugins.models import PluginManifest
from app.modules.plugins.service import (
    STATUS_ENABLED,
    STATUS_PENDING_REVIEW,
    STATUS_REJECTED,
    _find_record,
    compute_checksum,
)
from app.modules.tools.models import ToolDefinition
from app.modules.tools.registry import tool_registry

log = get_logger(__name__)

#: workflow 专用状态（与文件型插件 enabled/disabled 并行共存于 status 列）
WF_PENDING = STATUS_PENDING_REVIEW
WF_REJECTED = STATUS_REJECTED

MAX_STEPS = 8
_WINDOW_DAYS = 7
_MIN_OCCURRENCES = 3
_MIN_SEQ_LEN = 2
_MAX_SEQ_LEN = 4
_MAX_DRAFTS = 3

_SAVE_AS_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,49}$")
_TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _sha12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 注册校验
def _invalid(reason: str, details: dict | None = None) -> AppError:
    return AppError(
        "PLUGIN_MANIFEST_INVALID",
        f"workflow manifest 不合法：{reason}",
        http_status=400,
        details=details or {},
    )


def _check_tool_available(db: Session, code: str) -> None:
    """步骤工具必须已在注册表且未被下线；mcp__ 前缀直接拒绝（DESIGN C2）。"""
    if code.startswith("mcp__"):
        raise _invalid(f"步骤不允许引用 MCP 动态工具：{code}（请在注册表中显式声明）")
    try:
        tool_registry.get(code)
    except AppError as e:
        raise _invalid(f"步骤引用了未知或未启用工具：{code}", {"tool": code}) from e
    # DB 元数据行存在且被显式下线 → 视为小院不可用（与 GET /tools 同口径）
    row = db.query(ToolDefinition).filter(ToolDefinition.code == code).first()
    if row is not None and not row.enabled:
        raise _invalid(f"步骤引用的工具已被下线：{code}", {"tool": code})


def validate_workflow_manifest(db: Session, manifest: dict) -> dict:
    """注册路径专用校验（400）。返回规范化后的 manifest（只保留已知字段 + 默认值）。"""
    if not isinstance(manifest, dict):
        raise _invalid("manifest 必须是 JSON 对象")
    name = str(manifest.get("name") or "").strip()
    if not name:
        raise _invalid("name 不能为空")
    description = str(manifest.get("description") or "").strip()[:1000]

    params_raw = manifest.get("params") or []
    if not isinstance(params_raw, list):
        raise _invalid("params 必须是数组")
    params: list[dict[str, Any]] = []
    for item in params_raw:
        if not isinstance(item, dict) or not str(item.get("key") or "").strip():
            raise _invalid("params 每项必须含非空 key")
        params.append(
            {
                "key": str(item["key"]).strip(),
                "label": str(item.get("label") or item["key"]).strip()[:100],
                "required": bool(item.get("required")),
            }
        )

    steps_raw = manifest.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise _invalid("steps 必须是非空数组")
    if len(steps_raw) > MAX_STEPS:
        raise _invalid(f"steps 数量超限（{len(steps_raw)} > {MAX_STEPS}）")

    steps: list[dict[str, Any]] = []
    for i, step in enumerate(steps_raw):
        if not isinstance(step, dict):
            raise _invalid(f"第 {i + 1} 步必须是对象")
        kind = step.get("kind")
        if kind not in ("tool", "llm"):
            raise _invalid(f"第 {i + 1} 步 kind 只能是 tool 或 llm")
        save_as = step.get("save_as")
        if save_as is not None and not _SAVE_AS_RE.match(str(save_as)):
            raise _invalid(f"第 {i + 1} 步 save_as 命名非法（需标识符样式）")
        if kind == "tool":
            tool_code = str(step.get("tool") or "").strip()
            if not tool_code:
                raise _invalid(f"第 {i + 1} 步缺少 tool")
            _check_tool_available(db, tool_code)
            args = step.get("args") or {}
            if not isinstance(args, dict):
                raise _invalid(f"第 {i + 1} 步 args 必须是对象")
            steps.append({"kind": "tool", "tool": tool_code, "args": args, "save_as": save_as})
        else:
            prompt = str(step.get("prompt") or "").strip()
            if not prompt:
                raise _invalid(f"第 {i + 1} 步缺少 prompt")
            steps.append({"kind": "llm", "prompt": prompt, "save_as": save_as})

    normalized = {
        "name": name[:200],
        "description": description,
        "params": params,
        "steps": steps,
    }
    if manifest.get("plugin_id"):
        normalized["plugin_id"] = str(manifest["plugin_id"]).strip()[:100]
    if manifest.get("version"):
        normalized["version"] = str(manifest["version"]).strip()[:50]
    return normalized


# ---------------------------------------------------------------- 注册 / 审核
def register_workflow(db: Session, ctx: YardContext, manifest: dict) -> PluginManifest:
    """注册声明式 workflow：校验 → 落小院级 pending_review 草稿。

    同 plugin_id 重注册视为重新编辑：更新 manifest 并回到 pending_review
    （改过的 workflow 必须重新过发布闸，诚实优先于便利）。
    """
    normalized = validate_workflow_manifest(db, manifest)
    plugin_id = (
        normalized.get("plugin_id")
        or f"wf-{_sha12(json.dumps(normalized, sort_keys=True, ensure_ascii=False))}"
    )
    version = normalized.get("version") or "1.0.0"

    record = _find_record(db, plugin_id, ctx.yard_id)
    if record is None:
        record = PluginManifest(
            plugin_id=plugin_id,
            yard_id=ctx.yard_id,
            version=version,
            type="workflow",
            status=WF_PENDING,
            manifest_json=normalized,
            checksum=compute_checksum(normalized),
            checksum_ok=True,
            installed_at=datetime.now(UTC),
        )
        db.add(record)
    else:
        if record.type != "workflow":
            raise AppError(
                "PLUGIN_CONFLICT",
                f"plugin_id {plugin_id} 已被非 workflow 插件占用。",
                http_status=409,
            )
        record.version = version
        record.manifest_json = normalized
        record.checksum = compute_checksum(normalized)
        record.checksum_ok = True
        record.status = WF_PENDING  # 重新编辑必须重新过审核
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="plugin_registered",
        resource_type="plugin",
        resource_id=record.id,
        result="success",
        request_id=ctx.request_id,
        # 审计不存正文：只记标识与结构摘要
        metadata={
            "plugin_id": plugin_id,
            "step_count": len(normalized["steps"]),
            "tools": sorted({s["tool"] for s in normalized["steps"] if s["kind"] == "tool"}),
        },
    )
    return record


def find_yard_plugin_or_none(db: Session, yard_id: str, plugin_id: str) -> PluginManifest | None:
    """(plugin_id, yard) 定位 workflow 记录；不存在/非 workflow 返回 None。"""
    record = _find_record(db, plugin_id, yard_id)
    return record if record is not None and record.type == "workflow" else None


def get_yard_plugin(db: Session, ctx: YardContext, plugin_id: str) -> PluginManifest:
    """按 (plugin_id, 当前小院) 取 workflow 插件；跨小院与不存在同样 404（防枚举）。"""
    record = _find_record(db, plugin_id, ctx.yard_id)
    if record is None or record.type != "workflow":
        raise AppError(
            "PLUGIN_NOT_FOUND",
            f"workflow 插件不存在或无权访问：{plugin_id}",
            http_status=404,
            details={"plugin_id": plugin_id},
        )
    return record


def review_workflow(db: Session, ctx: YardContext, plugin_id: str, decision: str) -> PluginManifest:
    """发布闸：approve → enabled；reject → rejected。只有草稿可审。"""
    if decision not in ("approve", "reject"):
        raise AppError(
            "INVALID_REVIEW_ACTION", "审核动作只能为 approve 或 reject。", http_status=400
        )
    record = get_yard_plugin(db, ctx, plugin_id)
    if record.status != WF_PENDING:
        raise AppError(
            "INVALID_PLUGIN_TRANSITION",
            f"插件当前状态 {record.status} 不可审核（仅 pending_review 可审）。",
            http_status=409,
        )
    record.status = STATUS_ENABLED if decision == "approve" else WF_REJECTED
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="plugin_approved" if decision == "approve" else "plugin_rejected",
        resource_type="plugin",
        resource_id=record.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"plugin_id": plugin_id, "checksum_sha12": _sha12(record.checksum)},
    )
    return record


# ---------------------------------------------------------------- 解释执行
def _to_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):  # pragma: no cover - 防御分支
        return str(value)


def _render(value: Any, variables: dict[str, Any]) -> Any:
    """递归渲染 ``{{var}}``：未定义变量保留原文（缺参可见，不静默清空）。"""
    if isinstance(value, str):

        def _sub(m: re.Match[str]) -> str:
            key = m.group(1)
            return _to_text(variables[key]) if key in variables else m.group(0)

        return _TEMPLATE_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _render(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, variables) for v in value]
    return value


async def run_workflow(
    db: Session, ctx: YardContext, plugin_id: str, params: dict | None
) -> dict[str, Any]:
    """顺序解释执行 steps（同步执行）。返回 {outputs, final}。

    - tool 步：``tool_registry.execute`` 全链路（风险闸/审批/审计零绕过）；
      高风险工具未获批时 TOOL_RISK_BLOCKED 原样抛出（运行闸生效）；
    - llm 步：``complete_with_logging``（purpose=plugin，进用量与 traces）；
    - save_as 入上下文并计入 outputs；final = 末步文本化结果。
    """
    record = get_yard_plugin(db, ctx, plugin_id)
    if record.status != STATUS_ENABLED:
        raise AppError(
            "PLUGIN_NOT_ENABLED",
            "插件未通过审核启用，无法运行。",
            http_status=409,
            details={"plugin_id": plugin_id, "status": record.status},
        )
    manifest = record.manifest_json or {}
    steps = manifest.get("steps") or []
    if not steps:  # pragma: no cover - 注册校验保证非空；防脏数据
        raise AppError("PLUGIN_MANIFEST_INVALID", "插件 manifest 缺少 steps。", http_status=409)

    variables: dict[str, Any] = dict(params or {})
    missing = [
        p["key"]
        for p in (manifest.get("params") or [])
        if p.get("required") and not str(variables.get(p["key"]) or "").strip()
    ]
    if missing:
        raise AppError(
            "INVALID_REQUEST",
            "缺少必填运行参数：" + "、".join(missing),
            http_status=400,
            details={"missing": missing},
        )

    outputs: dict[str, Any] = {}
    final_text = ""
    for i, step in enumerate(steps):
        if step.get("kind") == "tool":
            args = _render(step.get("args") or {}, variables)
            if not isinstance(args, dict):  # pragma: no cover - 注册校验保证对象
                raise _invalid(f"第 {i + 1} 步 args 渲染结果不是对象")
            result = await tool_registry.execute(db, str(step["tool"]), args, ctx, ctx.request_id)
            final_text = _to_text(result)
            save_as = step.get("save_as")
            if save_as:
                outputs[str(save_as)] = result
                variables[str(save_as)] = result
        else:
            prompt = _render(str(step.get("prompt") or ""), variables)
            resp = await complete_with_logging(
                db,
                LLMRequest(
                    messages=[ChatMessage(role="user", content=str(prompt))],
                    metadata=LLMMetadata(
                        yard_id=ctx.yard_id,
                        task_id=f"wf-{_sha12(plugin_id)}",
                        request_id=ctx.request_id,
                        purpose="plugin",
                        agent_ref=str(plugin_id),
                    ),
                ),
            )
            text = resp.content or ""
            final_text = text
            save_as = step.get("save_as")
            if save_as:
                outputs[str(save_as)] = text
                variables[str(save_as)] = text

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="plugin_run",
        resource_type="plugin",
        resource_id=record.id,
        result="success",
        request_id=ctx.request_id,
        # 审计不存正文全文：final 只记 sha12+长度
        metadata={
            "plugin_id": plugin_id,
            "step_count": len(steps),
            "output_keys": sorted(outputs.keys()),
            "final_sha12": _sha12(final_text),
            "final_len": len(final_text),
        },
    )
    return {"outputs": outputs, "final": final_text}


# ---------------------------------------------------------------- 重复序列挖掘
def _recent_tool_sequences(db: Session, ctx: YardContext) -> "OrderedDict[str, list[str]]":
    """近 7 天本小院每个 run 的成功工具调用序列（run_id → 顺序 tool_code 列表）。"""
    cutoff = datetime.now(UTC) - timedelta(days=_WINDOW_DAYS)
    rows = (
        db.query(AgentRunStep)
        .filter(
            AgentRunStep.yard_id == ctx.yard_id,
            AgentRunStep.step_type == STEP_TOOL,
            AgentRunStep.status == STEP_OK,
            AgentRunStep.created_at >= cutoff,
        )
        .order_by(AgentRunStep.run_id.asc(), AgentRunStep.step_index.asc())
        .all()
    )
    sequences: OrderedDict[str, list[str]] = OrderedDict()
    for row in rows:
        if row.tool_code:
            sequences.setdefault(row.run_id, []).append(str(row.tool_code))
    return sequences


def _mine_patterns(
    sequences: "OrderedDict[str, list[str]]",
) -> list[tuple[tuple[str, ...], set[str]]]:
    """挖掘重复 ≥3 个 run、长度 2..4 的相邻工具序列；长序列吞并其短子序列。"""
    windows: dict[tuple[str, ...], set[str]] = {}
    for run_id, seq in sequences.items():
        seen: set[tuple[str, ...]] = set()  # 同一 run 内同一子序列只计一次
        for length in range(_MIN_SEQ_LEN, min(_MAX_SEQ_LEN, len(seq)) + 1):
            for i in range(len(seq) - length + 1):
                sub = tuple(seq[i : i + length])
                if sub not in seen:
                    seen.add(sub)
                    windows.setdefault(sub, set()).add(run_id)
    candidates = sorted(
        (p for p, r in windows.items() if len(r) >= _MIN_OCCURRENCES),
        key=lambda p: (-len(p), -len(windows[p]), p),
    )
    accepted: list[tuple[tuple[str, ...], set[str]]] = []
    for pattern in candidates:
        runs = windows[pattern]
        covered = any(
            len(q) > len(pattern) and q_runs >= runs and _is_contained(pattern, q)
            for (q, q_runs) in accepted
        )
        if not covered:
            accepted.append((pattern, runs))
    accepted.sort(key=lambda item: (-len(item[1]), -len(item[0]), item[0]))
    return accepted[:_MAX_DRAFTS]


def _is_contained(short: tuple[str, ...], long: tuple[str, ...]) -> bool:
    if len(short) > len(long):
        return False
    for i in range(len(long) - len(short) + 1):
        if long[i : i + len(short)] == short:
            return True
    return False


def suggest_workflows(db: Session, ctx: YardContext) -> list[PluginManifest]:
    """挖掘重复工具序列 → 只产 pending_review 草稿（绝不自动启用）。幂等：同 pattern 不重复建。"""
    sequences = _recent_tool_sequences(db, ctx)
    created: list[PluginManifest] = []
    for pattern, run_ids in _mine_patterns(sequences):
        tools = list(pattern)
        # 工具已下线/为 MCP 动态工具的模式跳过（草稿也必须是可注册的形状）
        try:
            for code in tools:
                _check_tool_available(db, code)
        except AppError:
            continue

        plugin_id = "auto-wf-" + _sha12(">".join(tools))
        if _find_record(db, plugin_id, ctx.yard_id) is not None:
            continue  # 同 pattern 已有草稿/正式版：不重复生成（幂等）

        manifest: dict[str, Any] = {
            "name": f"自动工作流：{' → '.join(tools)}",
            "description": (
                f"由近 {_WINDOW_DAYS} 天 {len(run_ids)} 次重复执行自动总结生成，待人工审核"
            ),
            "params": [],
            "steps": [
                {"kind": "tool", "tool": code, "args": {}, "save_as": f"r{i + 1}"}
                for i, code in enumerate(tools)
            ],
            # 自动生成溯源（审计友好）：source_runs 用 sha12，不落 run_id 原文
            "_auto": {
                "pattern": tools,
                "occurrences": len(run_ids),
                "source_runs": sorted(_sha12(run_id) for run_id in run_ids)[:10],
            },
        }
        record = PluginManifest(
            plugin_id=plugin_id,
            yard_id=ctx.yard_id,
            version="1.0.0",
            type="workflow",
            status=WF_PENDING,
            manifest_json=manifest,
            checksum=compute_checksum(manifest),
            checksum_ok=True,
            installed_at=datetime.now(UTC),
        )
        db.add(record)
        db.flush()
        created.append(record)

    if created:
        log_audit(
            db,
            yard_id=ctx.yard_id,
            actor_id=ctx.user_id,
            action="plugin_suggested",
            resource_type="plugin",
            resource_id=created[0].id,
            result="success",
            request_id=ctx.request_id,
            # 只记数量与 plugin_id / 模式长度，不存正文
            metadata={
                "count": len(created),
                "plugin_ids": [r.plugin_id for r in created],
                "pattern_lengths": [len(r.manifest_json["_auto"]["pattern"]) for r in created],
            },
        )
    return created
