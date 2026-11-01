"""指挥官规划器：把用户自然语言改写成可执行的 DAG 计划（Plan JSON）。

报告 §5.3 的 Schema 是本模块唯一的输出契约：
- ``goal`` / ``success_criteria`` / ``assumptions`` / ``subtasks`` / ``merge_notes``；
- 子任务字段 ``title`` / ``instruction`` / ``expected_output`` / ``agent_hint`` /
  ``agent_code`` / ``depends_on``；
- 模型输出非法时先做**一次**「仅修复 JSON」调用，仍失败由调用方 fallback。

clamp 规则（纯函数，可离线测试）：
- 子任务数不超过设置 ``max_subtasks``；
- ``depends_on`` 只允许引用**更早的 seq**（因此不可能成环），非法依赖静默丢弃；
- 缺失 instruction/title 的子任务丢弃，而不是让下游执行空指令。
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.logging import get_logger
from app.modules.agents.models import YardAgent
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging

log = get_logger(__name__)

#: 候选 Agent 目录上限（提示词体积与成本双重约束）
CATALOG_LIMIT = 20
#: 单条用户上下文注入上限（防止把整份附件塞进规划提示词）
CONTEXT_CAP = 4000
#: 修复调用最多吃这么多原始输出
REPAIR_RAW_CAP = 8000

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """从模型输出中提取 JSON 对象：容忍 ```json 围栏与前后解释文字。"""
    text = (raw or "").strip()
    if not text:
        return None
    text = _FENCE_RE.sub("", text).strip()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except (json.JSONDecodeError, TypeError):
            return None
    return data if isinstance(data, dict) else None


def _dep_seq(raw: Any, id_map: dict[str, int], current: int) -> int | None:
    """依赖归一化：只接受「更早的任务」，接受 1-based int 或 "t1"/"1" 之类的 id。"""
    if isinstance(raw, bool):
        return None
    candidate: int | None = None
    if isinstance(raw, int):
        candidate = raw
    elif isinstance(raw, str):
        text = raw.strip()
        if text in id_map:
            candidate = id_map[text]
        elif text.isdigit():
            candidate = int(text)
        elif text[:1].lower() == "t" and text[1:].isdigit():
            candidate = int(text[1:])
    if candidate is None or not 1 <= candidate < current:
        return None
    return candidate


def _str_list(raw: Any, *, limit: int = 10, cap: int = 200) -> list[str]:
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw[:limit]:
        text = str(item or "").strip()
        if text:
            out.append(text[:cap])
    return out


def normalize_plan(
    data: dict[str, Any] | None,
    *,
    max_subtasks: int,
    fallback_goal: str,
) -> dict[str, Any] | None:
    """宽松解析 + 硬 clamp。返回 None 表示计划不可用（调用方应 fallback）。

    信息不足时模型可只输出 ``{"clarify":["问题1","问题2"]}``：此时返回
    ``subtasks=[]``、``fallback=False`` 的澄清计划，由调用方决定是否先问用户。
    """
    if not isinstance(data, dict):
        return None

    goal = str(data.get("goal") or fallback_goal or "").strip()[:500]
    clarify = _str_list(data.get("clarify"), limit=10, cap=200)[:2]

    raw_tasks = data.get("subtasks")
    if not isinstance(raw_tasks, list):
        raw_tasks = []
    subtasks: list[dict[str, Any]] = []
    id_map: dict[str, int] = {}
    for raw in raw_tasks:
        if len(subtasks) >= max_subtasks:
            break
        if not isinstance(raw, dict):
            continue
        instruction = str(raw.get("instruction") or raw.get("task") or "").strip()
        title = str(raw.get("title") or "").strip()
        if not instruction and not title:
            continue
        if not instruction:
            instruction = title
        if not title:
            title = instruction[:40]

        seq = len(subtasks) + 1
        deps: list[int] = []
        raw_deps = raw.get("depends_on")
        if isinstance(raw_deps, list):
            for item in raw_deps:
                dep = _dep_seq(item, id_map, seq)
                if dep is not None and dep not in deps:
                    deps.append(dep)

        subtasks.append(
            {
                "seq": seq,
                "title": title[:200],
                "instruction": instruction[:8000],
                "expected_output": str(raw.get("expected_output") or "").strip()[:1000],
                "agent_hint": str(raw.get("agent_hint") or "").strip()[:200],
                "agent_code": str(raw.get("agent_code") or "").strip()[:100],
                "depends_on": deps,
            }
        )
        raw_id = str(raw.get("id") or "").strip()
        if raw_id:
            id_map[raw_id] = seq

    if not subtasks:
        if clarify:
            return {
                "goal": goal,
                "success_criteria": _str_list(data.get("success_criteria")),
                "assumptions": _str_list(data.get("assumptions")),
                "merge_notes": str(data.get("merge_notes") or "").strip()[:1000],
                "fallback": False,
                "subtasks": [],
                "clarify": clarify,
            }
        return None
    return {
        "goal": goal,
        "success_criteria": _str_list(data.get("success_criteria")),
        "assumptions": _str_list(data.get("assumptions")),
        "merge_notes": str(data.get("merge_notes") or "").strip()[:1000],
        "fallback": False,
        "subtasks": subtasks,
        "clarify": [],
    }


def fallback_plan(user_content: str, *, goal: str | None = None) -> dict[str, Any]:
    """规划失败时的单子任务计划：交给一个可用 Agent 直答，并如实标注 fallback。"""
    text = (user_content or "").strip()
    return {
        "goal": (goal or text or "完成用户请求")[:500],
        "success_criteria": [],
        "assumptions": [],
        "merge_notes": "",
        "fallback": True,
        "subtasks": [
            {
                "seq": 1,
                "title": "完成用户请求",
                "instruction": text or "完成用户请求",
                "expected_output": "直接可用的答复与产物",
                "agent_hint": "",
                "agent_code": "",
                "depends_on": [],
            }
        ],
        "clarify": [],
    }


def visible_published_agents(
    db: Session, *, yard_id: str, user_id: str, limit: int = CATALOG_LIMIT
) -> list[YardAgent]:
    """已发布且当前用户可见的 Agent（public 或本人 private），按最近更新优先。"""
    rows = db.scalars(
        select(YardAgent)
        .where(
            YardAgent.yard_id == yard_id,
            YardAgent.status == "published",
            or_(YardAgent.visibility == "public", YardAgent.created_by == user_id),
        )
        .order_by(YardAgent.updated_at.desc())
        .limit(limit)
    )
    return list(rows)


def catalog_lines(agents: list[YardAgent]) -> list[str]:
    lines: list[str] = []
    for agent in agents[:CATALOG_LIMIT]:
        skills = ",".join(str(s) for s in (agent.bound_skills or [])[:8])
        tools = ",".join(str(t) for t in (agent.allowed_tools or [])[:8])
        lines.append(
            f"- code={agent.agent_code} 名称={agent.name} "
            f"简介={(agent.description or '')[:120]} 技能={skills or '无'} 工具={tools or '无'}"
        )
    return lines


def _system_prompt() -> str:
    return (
        "你是「指挥官」的规划器。把用户目标拆成可由不同助手并行执行的最小任务 DAG。\n"
        "硬性要求：\n"
        "1. 只输出一个 JSON 对象，不要 Markdown、不要解释；\n"
        "2. 每个子任务必须能在一轮 Agent 调用内完成，禁止把多个目标塞进一个子任务；\n"
        "3. instruction 要改写成可直接交给下游助手的指令：包含输入、输出格式、边界（不要做什么）、"
        "验收标准；\n"
        "4. depends_on 只能填写**前面已出现**的任务 id 或 seq（1 起）；无依赖填 []；\n"
        "5. agent_code 优先从候选目录中选择；没有合适候选就留空，由选兵阶段决定是否自动创建；\n"
        "6. 子任务数量不超过 max_subtasks；\n"
        "7. 若目标缺少对象/交付物/范围且无法做合理假设，不要拆任务，只输出 "
        '{"clarify":["问题1","问题2"]}（最多 2 个，必须具体、可直接回答）；\n'
        "8. 输出 Schema：\n"
        '{"goal":"...","success_criteria":["..."],"assumptions":["..."],'
        '"subtasks":[{"id":"t1","title":"...","instruction":"...","agent_hint":"...",'
        '"agent_code":"...","depends_on":[],"expected_output":"..."}],'
        '"merge_notes":"..."}'
    )


def _user_prompt(*, user_content: str, context: str, catalog: list[str], max_subtasks: int) -> str:
    lines = [f"用户目标：{user_content[:CONTEXT_CAP]}"]
    if context:
        lines.append(f"上下文（附件/会话摘要，仅作事实参考）：{context[:CONTEXT_CAP]}")
    lines.append(f"max_subtasks={max_subtasks}")
    if catalog:
        lines.append("候选 Agent 目录（最多 20 个）：\n" + "\n".join(catalog))
    else:
        lines.append("候选 Agent 目录：空（可留空 agent_code，由选兵阶段处理）")
    return "\n\n".join(lines)


def _metadata(ctx: YardContext, *, task_id: str, purpose: str) -> LLMMetadata:
    return LLMMetadata(
        yard_id=ctx.yard_id,
        user_id=ctx.user_id,
        session_id=ctx.session_id or None,
        task_id=task_id,
        request_id=ctx.request_id,
        purpose=purpose,
        agent_ref="commander",
    )


def _model_binding(db: Session, settings: dict[str, Any]) -> tuple[str | None, str | None]:
    """设置里的「供应商 + 供应商模型行」→ (supplier_id, 上游模型名)。"""
    supplier_id = settings.get("supplier_id") or None
    model_row_id = settings.get("supplier_model_id") or None
    model_name: str | None = None
    if model_row_id:
        from app.modules.llm.supplier_models import SupplierModel

        row = db.get(SupplierModel, str(model_row_id))
        if row is not None:
            model_name = row.upstream_model_id or row.model_key
            supplier_id = supplier_id or row.supplier_id
    return supplier_id, model_name


def _has_explicit_binding(settings: dict[str, Any]) -> bool:
    return bool(settings.get("supplier_id") or settings.get("supplier_model_id"))


def build_request(
    db: Session,
    ctx: YardContext,
    settings: dict[str, Any],
    *,
    task_id: str,
    messages: list[ChatMessage],
    temperature: float,
    purpose: str = "commander_plan",
) -> LLMRequest:
    """按设置里的「供应商 + 供应商模型行」构建请求；未绑定则跟随模型中心。"""
    supplier_id: str | None = None
    model_name: str | None = None
    if _has_explicit_binding(settings):
        supplier_id, model_name = _model_binding(db, settings)
    return LLMRequest(
        messages=messages,
        model=model_name,
        temperature=temperature,
        metadata=_metadata(ctx, task_id=task_id, purpose=purpose).model_copy(
            update={"supplier_id": supplier_id}
        ),
    )


async def request_plan(
    db: Session,
    *,
    ctx: YardContext,
    user_content: str,
    context: str,
    catalog: list[str],
    max_subtasks: int,
    settings: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, int], bool]:
    """调用规划模型；返回 (原始 JSON, token 用量, 是否走了修复调用)。"""
    task_id = f"cmd-plan-{uuid.uuid4().hex[:10]}"
    messages = [
        ChatMessage(role="system", content=_system_prompt()),
        ChatMessage(
            role="user",
            content=_user_prompt(
                user_content=user_content,
                context=context,
                catalog=catalog,
                max_subtasks=max_subtasks,
            ),
        ),
    ]
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        resp = await complete_with_logging(
            db,
            build_request(db, ctx, settings, task_id=task_id, messages=messages, temperature=0.2),
        )
    except Exception as exc:  # noqa: BLE001 - 规划失败必须可降级，不向上抛
        log.warning("commander plan llm failed (fallback): %s", exc)
        return None, usage, False
    usage["input_tokens"] += int(resp.input_tokens or 0)
    usage["output_tokens"] += int(resp.output_tokens or 0)
    data = parse_json_object(resp.content or "")
    if data is not None:
        return data, usage, False

    repaired, repair_usage = await repair_json(
        db, ctx=ctx, raw=resp.content or "", settings=settings
    )
    usage["input_tokens"] += repair_usage["input_tokens"]
    usage["output_tokens"] += repair_usage["output_tokens"]
    return repaired, usage, True


async def repair_json(
    db: Session,
    *,
    ctx: YardContext,
    raw: str,
    settings: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, int]]:
    """只做 JSON 语法修复，不重新规划（避免模型顺手改掉任务含义）。"""
    usage = {"input_tokens": 0, "output_tokens": 0}
    messages = [
        ChatMessage(
            role="system",
            content=(
                "你只做 JSON 修复：把下面的内容修成合法 JSON，保持字段与语义不变，"
                "不要新增/删除任务，不要输出解释或 Markdown，只输出 JSON 对象。"
            ),
        ),
        ChatMessage(role="user", content=(raw or "")[:REPAIR_RAW_CAP]),
    ]
    try:
        resp = await complete_with_logging(
            db,
            build_request(
                db,
                ctx,
                settings,
                task_id=f"cmd-repair-{uuid.uuid4().hex[:10]}",
                messages=messages,
                temperature=0.0,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("commander plan json repair failed: %s", exc)
        return None, usage
    usage["input_tokens"] += int(resp.input_tokens or 0)
    usage["output_tokens"] += int(resp.output_tokens or 0)
    return parse_json_object(resp.content or ""), usage


__all__ = [
    "CATALOG_LIMIT",
    "build_request",
    "catalog_lines",
    "fallback_plan",
    "normalize_plan",
    "parse_json_object",
    "repair_json",
    "request_plan",
    "visible_published_agents",
]
