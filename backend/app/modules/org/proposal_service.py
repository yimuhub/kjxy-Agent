"""知识库 → 组织架构提案（v3.58）。

流程：检索知识块 → 模型产出结构化建议 → 校验/归一化 → 用户预览差异 →
确认后合并写入 org_units/org_positions。提案未应用前不改动真实组织数据。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.knowledge import service as knowledge_service
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.org import service as org_service
from app.modules.org.models import (
    PROPOSAL_STATUS_APPLIED,
    PROPOSAL_STATUS_DISCARDED,
    PROPOSAL_STATUS_FAILED,
    PROPOSAL_STATUS_READY,
    SOURCE_KNOWLEDGE,
    UNIT_KINDS,
    UNIT_STATUS_ACTIVE,
    OrgPosition,
    OrgProposal,
    OrgUnit,
)

PROMPT_VERSION = "org-proposal-v1"
TASK_TYPE = "org_generate"
MAX_UNITS = 20
MAX_POSITIONS = 10
MAX_EVIDENCE = 8

_JSON_RE = re.compile(r"\{.*\}", re.S)

SYSTEM_PROMPT = """你是农业科技小院 / 合作社 / 农业企业的组织设计顾问。
请只依据提供的知识库证据，设计可落地的组织架构，并严格输出一个 JSON 对象（不要 Markdown）。

JSON 结构：
{
  "title": "提案标题（<=40字）",
  "summary": "一段话说明推导依据与总体结构（<=200字）",
  "units": [
    {
      "name": "单元名称",
      "kind": "company|coop|department|team",
      "description": "职责说明（<=120字）",
      "parent_name": null 或 其他单元名称,
      "positions": [
        {"title": "岗位名称", "responsibilities": "职责", "headcount": 1}
      ],
      "agent_suggestion": {
        "name": "建议的 Agent 名称",
        "mission": "这个 Agent 的使命与工作方式",
        "reason": "为什么需要它（引用证据要点）"
      } 或 null,
      "evidence_ids": ["证据块 id"]
    }
  ]
}

规则：
1. 单元数量 4–12 个；positions 每个单元 1–5 个；必须形成一棵树（parent_name 指向本提案中的单元）。
2. kind 只能是 company/coop/department/team；顶层通常 1 个 company 或 coop。
3. 只写证据能支持的内容；证据不足时在 summary 里说明，不得编造人员、数字、资质。
4. agent_suggestion 只在确有独立工作职责时给出，避免为每个岗位都造 Agent。
5. evidence_ids 只能使用输入中给出的 id。"""


class _Stage:
    def __init__(self, callback: Callable[[str, str, float | None], None] | None):
        self._callback = callback

    def __call__(self, stage: str, message: str, progress: float | None = None) -> None:
        if self._callback is not None:
            self._callback(stage, message, progress)


def get_proposal(db: Session, yard_id: str, proposal_id: str) -> OrgProposal:
    row = db.get(OrgProposal, proposal_id)
    if row is None or row.yard_id != yard_id:
        raise AppError(
            "ORG_PROPOSAL_NOT_FOUND",
            "组织架构提案不存在或无权访问。",
            http_status=404,
            details={"proposal_id": proposal_id},
        )
    return row


def _retrieve(db: Session, ctx: YardContext, instruction: str) -> list[dict[str, Any]]:
    query = (instruction or "").strip() or "组织架构 部门 岗位 职责 合作社 公司 业务"
    hits = knowledge_service.search(db, ctx, query, top_k=MAX_EVIDENCE)
    evidence: list[dict[str, Any]] = []
    for hit in hits:
        quote = str(getattr(hit, "quote", "") or "").strip()
        if not quote:
            continue
        evidence.append(
            {
                "chunk_id": str(getattr(hit, "chunk_id", "")),
                "document_id": str(getattr(hit, "document_id", "")),
                "title": str(getattr(hit, "title", "") or ""),
                "page_number": getattr(hit, "page_number", None),
                "quote": quote[:600],
                "score": float(getattr(hit, "score", 0.0) or 0.0),
            }
        )
    return evidence


def _evidence_text(evidence: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in evidence:
        page = f" 第{item['page_number']}页" if item.get("page_number") else ""
        lines.append(
            f"[{item['chunk_id']}] 《{item['title'] or '未命名文档'}》{page}\n{item['quote']}"
        )
    return "\n\n".join(lines)


def build_prompt(instruction: str, evidence: list[dict[str, Any]]) -> list[ChatMessage]:
    user = (
        f"用户要求：{instruction.strip() or '根据知识库资料，推导适合本小院的组织架构。'}\n\n"
        f"知识库证据：\n{_evidence_text(evidence)}\n\n"
        "请输出 JSON。"
    )
    return [
        ChatMessage(role="system", content=SYSTEM_PROMPT),
        ChatMessage(role="user", content=user),
    ]


def parse_json_object(content: str) -> dict[str, Any] | None:
    match = _JSON_RE.search(content or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _flatten_units(
    raw_units: Any,
    *,
    parent_name: str | None = None,
    out: list[dict[str, Any]],
) -> None:
    if not isinstance(raw_units, list):
        return
    for raw in raw_units:
        if not isinstance(raw, dict):
            continue
        name = _text(raw.get("name"), 80)
        if not name:
            continue
        kind = str(raw.get("kind") or "department")
        if kind not in UNIT_KINDS:
            kind = "department"
        positions_raw = raw.get("positions")
        positions: list[dict[str, Any]] = []
        if isinstance(positions_raw, list):
            for pos in positions_raw[:MAX_POSITIONS]:
                if not isinstance(pos, dict):
                    continue
                title = _text(pos.get("title"), 80)
                if not title:
                    continue
                try:
                    headcount = int(pos.get("headcount") or 1)
                except (TypeError, ValueError):
                    headcount = 1
                positions.append(
                    {
                        "title": title,
                        "responsibilities": _text(pos.get("responsibilities"), 1000),
                        "headcount": max(1, min(headcount, 99)),
                    }
                )
        suggestion_raw = raw.get("agent_suggestion")
        suggestion = None
        if isinstance(suggestion_raw, dict) and _text(suggestion_raw.get("name"), 80):
            suggestion = {
                "name": _text(suggestion_raw.get("name"), 80),
                "mission": _text(suggestion_raw.get("mission"), 500),
                "reason": _text(suggestion_raw.get("reason"), 500),
            }
        evidence_ids = raw.get("evidence_ids")
        out.append(
            {
                "name": name,
                "kind": kind,
                "description": _text(raw.get("description"), 1000),
                "parent_name": _text(raw.get("parent_name"), 80) or parent_name,
                "positions": positions,
                "agent_suggestion": suggestion,
                "evidence_ids": [str(i) for i in evidence_ids[:6]]
                if isinstance(evidence_ids, list)
                else [],
            }
        )
        _flatten_units(raw.get("children"), parent_name=name, out=out)
        if len(out) >= MAX_UNITS:
            return


def normalize(data: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    flattened: list[dict[str, Any]] = []
    _flatten_units(data.get("units"), out=flattened)

    # 全局按名称去重（同名单元合并岗位），保证提案是一棵可落库的树
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for unit in flattened:
        key = unit["name"].casefold()
        if key in merged:
            merged[key]["positions"].extend(unit["positions"])
            if not merged[key]["agent_suggestion"] and unit["agent_suggestion"]:
                merged[key]["agent_suggestion"] = unit["agent_suggestion"]
            continue
        merged[key] = unit
        order.append(key)

    names = {merged[key]["name"] for key in order}
    units: list[dict[str, Any]] = []
    suggestions: list[dict[str, Any]] = []
    for key in order:
        unit = merged[key]
        parent = unit["parent_name"]
        if parent and parent not in names:
            parent = None
        # 去掉自身作为父级/重复名称
        if parent == unit["name"]:
            parent = None
        positions = unit["positions"][:MAX_POSITIONS]
        units.append({**unit, "parent_name": parent, "positions": positions})
        if unit["agent_suggestion"]:
            suggestions.append(
                {
                    "name": unit["agent_suggestion"]["name"],
                    "department": unit["name"],
                    "mission": unit["agent_suggestion"]["mission"],
                    "reason": unit["agent_suggestion"]["reason"],
                }
            )
    if not units:
        raise AppError(
            "ORG_PROPOSAL_EMPTY",
            "模型没有产出可用的组织单元，请补充资料或调整要求后重试。",
            http_status=502,
        )
    units = _topological(units)
    return {
        "title": _text(data.get("title"), 120) or "组织架构建议",
        "summary": _text(data.get("summary"), 1000),
        "units": units,
        "agent_suggestions": suggestions,
        "evidence_count": len(evidence),
    }


def _topological(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """父单元必须排在子单元之前；无法解析/循环的父级降为顶层。"""
    by_key = {str(unit["name"]).casefold(): unit for unit in units}
    for unit in units:
        parent_key = str(unit.get("parent_name") or "").casefold()
        if parent_key and (parent_key not in by_key or parent_key == str(unit["name"]).casefold()):
            unit["parent_name"] = None
    children: dict[str | None, list[dict[str, Any]]] = {}
    for unit in units:
        parent_ref: str | None = str(unit.get("parent_name") or "").casefold() or None
        children.setdefault(parent_ref, []).append(unit)
    ordered: list[dict[str, Any]] = []
    visited: set[str] = set()

    def walk(parent: str | None) -> None:
        for unit in children.get(parent, []):
            key = str(unit["name"]).casefold()
            if key in visited:
                continue
            visited.add(key)
            ordered.append(unit)
            walk(key)

    walk(None)
    # 循环引用：剩余节点全部降为顶层，保证可落库
    for unit in units:
        key = str(unit["name"]).casefold()
        if key not in visited:
            unit["parent_name"] = None
            visited.add(key)
            ordered.append(unit)
    return ordered


def _existing_children(db: Session, yard_id: str, parent_id: str | None) -> dict[str, OrgUnit]:
    stmt = select(OrgUnit).where(
        OrgUnit.yard_id == yard_id,
        OrgUnit.status == UNIT_STATUS_ACTIVE,
    )
    if parent_id is None:
        stmt = stmt.where(OrgUnit.parent_id.is_(None))
    else:
        stmt = stmt.where(OrgUnit.parent_id == parent_id)
    return {row.name.casefold(): row for row in db.execute(stmt).scalars().all()}


def _positions_by_title(db: Session, yard_id: str, unit_id: str) -> dict[str, OrgPosition]:
    rows = (
        db.execute(
            select(OrgPosition).where(
                OrgPosition.yard_id == yard_id,
                OrgPosition.org_unit_id == unit_id,
                OrgPosition.status == UNIT_STATUS_ACTIVE,
            )
        )
        .scalars()
        .all()
    )
    return {row.title.casefold(): row for row in rows}


def build_plan(
    db: Session,
    yard_id: str,
    payload: dict[str, Any],
    *,
    parent_id: str | None,
) -> dict[str, Any]:
    """预览差异：不写库，只判断每个单元/岗位是新建还是已存在。"""
    if parent_id:
        org_service.unit_detail(db, yard_id, parent_id)
    units = payload.get("units") or []
    planned: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    counts = {
        "create_units": 0,
        "existing_units": 0,
        "create_positions": 0,
        "existing_positions": 0,
    }
    for unit in units:
        key = str(unit["name"]).casefold()
        parent_key = str(unit.get("parent_name") or "").casefold()
        effective_parent_id = parent_id
        if parent_key and parent_key in planned:
            effective_parent_id = planned[parent_key]["id"]
        existing = _existing_children(db, yard_id, effective_parent_id)
        hit = existing.get(key)
        action = "existing" if hit is not None else "create"
        if hit is not None:
            counts["existing_units"] += 1
        else:
            counts["create_units"] += 1
        planned[key] = {"id": hit.id if hit else f"new:{key}"}
        pos_hit = _positions_by_title(db, yard_id, hit.id) if hit else {}
        positions: list[dict[str, Any]] = []
        for pos in unit.get("positions") or []:
            pos_key = str(pos["title"]).casefold()
            pos_action = "existing" if pos_key in pos_hit else "create"
            counts["existing_positions" if pos_action == "existing" else "create_positions"] += 1
            positions.append({**pos, "action": pos_action})
        items.append(
            {
                "name": unit["name"],
                "kind": unit["kind"],
                "parent_name": unit.get("parent_name"),
                "description": unit.get("description") or "",
                "action": action,
                "positions": positions,
                "agent_suggestion": unit.get("agent_suggestion"),
            }
        )
    return {"items": items, "counts": counts}


async def generate(
    db: Session,
    *,
    ctx: YardContext,
    proposal_id: str,
    instruction: str,
    on_stage: Callable[[str, str, float | None], None] | None = None,
) -> dict[str, Any]:
    stage = _Stage(on_stage)
    proposal = get_proposal(db, ctx.yard_id, proposal_id)
    stage("retrieving", "正在检索知识库资料…", 0.15)
    evidence = _retrieve(db, ctx, instruction)
    if not evidence:
        raise AppError(
            "ORG_KNOWLEDGE_EMPTY",
            "知识库里还没有可用于推导的资料，请先上传制度、介绍或岗位说明。",
            http_status=400,
        )
    stage("designing", f"已找到 {len(evidence)} 条依据，正在设计组织架构…", 0.45)

    messages = build_prompt(instruction, evidence)
    response = await complete_with_logging(
        db,
        LLMRequest(
            messages=messages,
            max_tokens=2200,
            temperature=0.2,
            timeout_seconds=120,
            metadata=LLMMetadata(
                yard_id=ctx.yard_id,
                user_id=ctx.user_id,
                purpose="org_generation",
                request_id=ctx.request_id,
            ),
        ),
    )
    data = parse_json_object(response.content)
    if data is None:
        # 一次纠正重试：把模型原文回灌，明确要求只输出 JSON
        retry_messages = messages + [
            ChatMessage(role="assistant", content=response.content[:4000]),
            ChatMessage(
                role="user",
                content="上一条不是合法 JSON。请只输出一个 JSON 对象，不要 Markdown、不要解释。",
            ),
        ]
        response = await complete_with_logging(
            db,
            LLMRequest(
                messages=retry_messages,
                max_tokens=2200,
                temperature=0.1,
                timeout_seconds=120,
                metadata=LLMMetadata(
                    yard_id=ctx.yard_id,
                    user_id=ctx.user_id,
                    purpose="org_generation",
                    request_id=ctx.request_id,
                ),
            ),
        )
        data = parse_json_object(response.content)
    if data is None:
        raise AppError(
            "ORG_PROPOSAL_INVALID",
            "模型返回的内容不是合法 JSON，请稍后重试。",
            http_status=502,
        )

    normalized = normalize(data, evidence)
    proposal.title = normalized["title"]
    proposal.proposal_json = normalized
    proposal.evidence_json = evidence
    proposal.token_usage_json = {
        "input_tokens": int(response.input_tokens or 0),
        "output_tokens": int(response.output_tokens or 0),
    }
    proposal.model = response.model
    proposal.prompt_version = PROMPT_VERSION
    proposal.status = PROPOSAL_STATUS_READY
    proposal.error_message = ""
    db.flush()
    stage("ready", f"已生成 {len(normalized['units'])} 个组织单元，等待确认。", 1.0)
    return {
        "proposal_id": proposal.id,
        "title": proposal.title,
        "units": len(normalized["units"]),
        "agents": len(normalized["agent_suggestions"]),
        "evidence_count": len(evidence),
    }


def apply_proposal(
    db: Session,
    *,
    ctx: YardContext,
    proposal_id: str,
    parent_id: str | None,
) -> dict[str, Any]:
    proposal = get_proposal(db, ctx.yard_id, proposal_id)
    if proposal.status == PROPOSAL_STATUS_APPLIED:
        return {
            "already_applied": True,
            "proposal_id": proposal.id,
            "result": proposal.applied_result_json or {},
        }
    if proposal.status != PROPOSAL_STATUS_READY or not proposal.proposal_json:
        raise AppError(
            "ORG_PROPOSAL_NOT_READY",
            "该提案当前不可应用（仅已生成的提案可应用）。",
            http_status=409,
            details={"status": proposal.status},
        )
    if parent_id:
        org_service.unit_detail(db, ctx.yard_id, parent_id)

    units = proposal.proposal_json.get("units") or []
    planned: dict[str, str] = {}
    created_units = 0
    merged_units = 0
    created_positions = 0
    merged_positions = 0
    for index, unit in enumerate(units):
        key = str(unit["name"]).casefold()
        parent_key = str(unit.get("parent_name") or "").casefold()
        effective_parent_id = planned.get(parent_key, parent_id) if parent_key else parent_id
        existing = _existing_children(db, ctx.yard_id, effective_parent_id).get(key)
        if existing is not None:
            row = existing
            merged_units += 1
        else:
            row = org_service.create_unit(
                db,
                yard_id=ctx.yard_id,
                actor_id=ctx.user_id,
                name=str(unit["name"]),
                parent_id=effective_parent_id,
                code=None,
                kind=str(unit.get("kind") or "department"),
                description=unit.get("description") or "",
                sort_order=index,
            )
            row.source = SOURCE_KNOWLEDGE
            created_units += 1
        planned[key] = row.id
        pos_hit = _positions_by_title(db, ctx.yard_id, row.id)
        for pos_index, pos in enumerate(unit.get("positions") or []):
            pos_key = str(pos["title"]).casefold()
            if pos_key in pos_hit:
                merged_positions += 1
                continue
            org_service.create_position(
                db,
                yard_id=ctx.yard_id,
                actor_id=ctx.user_id,
                org_unit_id=row.id,
                title=str(pos["title"]),
                responsibilities=pos.get("responsibilities") or "",
                headcount=int(pos.get("headcount") or 1),
                agent_id=None,
                sort_order=pos_index,
            )
            created_positions += 1

    result = {
        "created_units": created_units,
        "merged_units": merged_units,
        "created_positions": created_positions,
        "merged_positions": merged_positions,
    }
    proposal.status = PROPOSAL_STATUS_APPLIED
    proposal.applied_by = ctx.user_id
    proposal.applied_at = datetime.now(UTC)
    proposal.applied_result_json = result
    db.flush()
    return {"already_applied": False, "proposal_id": proposal.id, "result": result}


def discard_proposal(db: Session, *, ctx: YardContext, proposal_id: str) -> OrgProposal:
    proposal = get_proposal(db, ctx.yard_id, proposal_id)
    if proposal.status == PROPOSAL_STATUS_APPLIED:
        raise AppError(
            "ORG_PROPOSAL_APPLIED",
            "已应用的提案不能丢弃，请直接调整组织架构。",
            http_status=409,
        )
    proposal.status = PROPOSAL_STATUS_DISCARDED
    db.flush()
    return proposal


def mark_failed(db: Session, proposal_id: str, message: str) -> None:
    proposal = db.get(OrgProposal, proposal_id)
    if proposal is None:
        return
    proposal.status = PROPOSAL_STATUS_FAILED
    proposal.error_message = message[:2000]
    db.flush()


def serialize(
    db: Session,
    proposal: OrgProposal,
    *,
    parent_id: str | None = None,
) -> dict[str, Any]:
    payload = proposal.proposal_json or {}
    diff = (
        build_plan(db, proposal.yard_id, payload, parent_id=parent_id)
        if payload and proposal.status in (PROPOSAL_STATUS_READY, PROPOSAL_STATUS_APPLIED)
        else {"items": [], "counts": {}}
    )
    return {
        "id": proposal.id,
        "title": proposal.title,
        "status": proposal.status,
        "instruction": proposal.instruction,
        "summary": payload.get("summary", ""),
        "units": payload.get("units", []),
        "agent_suggestions": payload.get("agent_suggestions", []),
        "evidence": proposal.evidence_json or [],
        "evidence_count": len(proposal.evidence_json or []),
        "model": proposal.model,
        "prompt_version": proposal.prompt_version,
        "token_usage": proposal.token_usage_json or {},
        "diff": diff,
        "applied_result": proposal.applied_result_json,
        "error_message": proposal.error_message,
        "task_id": proposal.task_id,
        "created_at": proposal.created_at,
        "applied_at": proposal.applied_at,
    }
