"""多助手群聊编排：固定成员名单 + 本轮应答策略（all | named | auto）+ 成员级 Token 消耗统计。"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.progress import PHASE_GENERATING, PHASE_ROUTING, STEP_DONE, STEP_FAILED, StepRecorder
from app.modules.agents import supervisor
from app.modules.audit.service import log_audit
from app.modules.chat import attachments as attachments_service
from app.modules.chat import service
from app.modules.chat.models import (
    MSG_FAILED,
    MSG_OK,
    POLICY_ALL,
    POLICY_AUTO,
    POLICY_NAMED,
    ChatParticipant,
)
from app.modules.knowledge.chunking import estimate_token_count
from app.modules.llm.schemas import ChatMessage as GateChatMessage
from app.modules.llm.schemas import LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging

log = get_logger(__name__)

GROUP_CONCURRENCY = 3
MAX_PARTICIPANTS = 8
MAX_ANSWERS_PER_TURN = 6
GATE_LLM_CALL_CAP = 6
ANSWER_THRESHOLD = 0.55
MEMBER_TIMEOUT_SECONDS = 60.0
_GATE_QUESTION_CAP = 500

_JSON_RE = re.compile(r"\{.*\}", re.S)

MEMBER_RUNNING = "running"
MEMBER_OK = "ok"
MEMBER_ERROR = "error"

ROUTED_GROUP = "group"

_session_factory: Callable[[], Session] | None = None


def _get_session_factory() -> Callable[[], Session]:
    if _session_factory is not None:
        return _session_factory
    from app.modules.chat import stream as chat_stream

    return chat_stream._get_session_factory()


def _new_turn_id() -> str:
    return uuid.uuid4().hex[:12]


# ---------------------------------------------------------------------------
# 成员名单（会话私有边界内可增删可排序）
# ---------------------------------------------------------------------------


def participant_out(p: ChatParticipant) -> dict[str, Any]:
    return {
        "id": p.id,
        "session_id": p.session_id,
        "agent_id": p.agent_id,
        "agent_code": p.agent_code,
        "agent_name": p.agent_name,
        "position": int(p.position),
    }


def list_participants(
    db: Session, *, yard_id: str, created_by: str, session_id: str
) -> list[dict[str, Any]]:
    rows = db.scalars(
        select(ChatParticipant)
        .where(
            ChatParticipant.session_id == session_id,
            ChatParticipant.yard_id == yard_id,
            ChatParticipant.created_by == created_by,
        )
        .order_by(ChatParticipant.position.asc())
    )
    return [participant_out(p) for p in rows]


def replace_participants(
    db: Session, *, ctx: YardContext, session_id: str, agent_ids: list[str]
) -> list[dict[str, Any]]:
    service.get_session(db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id)

    unique: list[str] = []
    for raw in agent_ids or []:
        aid = str(raw or "").strip()
        if aid and aid not in unique:
            unique.append(aid)
    if len(unique) > MAX_PARTICIPANTS:
        raise AppError(
            "GROUP_PARTICIPANTS_TOO_MANY",
            f"群聊成员最多 {MAX_PARTICIPANTS} 位（当前 {len(unique)} 位）。",
            http_status=400,
            details={"max": MAX_PARTICIPANTS},
        )

    agents = {
        str(c["id"]): c
        for c in supervisor.list_candidates(db, ctx.yard_id, user_id=ctx.user_id)
        if c["kind"] == "agent"
    }
    unknown = [a for a in unique if a not in agents]
    if unknown:
        raise AppError(
            "GROUP_PARTICIPANT_INVALID",
            "成员必须为本小院已发布的助手；未找到：" + "、".join(unknown[:5]),
            http_status=400,
            details={
                "unknown_agent_ids": unknown,
                "available": [{"id": c["id"], "name": c["name"]} for c in agents.values()][:20],
            },
        )

    db.execute(
        delete(ChatParticipant).where(
            ChatParticipant.session_id == session_id,
            ChatParticipant.yard_id == ctx.yard_id,
            ChatParticipant.created_by == ctx.user_id,
        )
    )
    out: list[dict[str, Any]] = []
    for idx, aid in enumerate(unique, start=1):
        cand = agents[aid]
        row = ChatParticipant(
            session_id=session_id,
            yard_id=ctx.yard_id,
            created_by=ctx.user_id,
            agent_id=aid,
            agent_code=str(cand.get("code") or "")[:100],
            agent_name=str(cand["name"])[:100],
            position=idx,
        )
        db.add(row)
        db.flush()
        out.append(participant_out(row))

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="chat_participants_updated",
        resource_type="chat_session",
        resource_id=session_id,
        result="success",
        request_id=ctx.request_id,
        metadata={"members": [p["agent_name"] for p in out], "count": len(out)},
    )
    return out


# ---------------------------------------------------------------------------
# auto 门槛：可解释的"我该不该答"
# ---------------------------------------------------------------------------


def profile_text(agent_name: str, blurb: str = "") -> str:
    return f"{agent_name} {blurb}".strip()


def keyword_confidence(question: str, text: str) -> float:
    hits = {
        kw
        for words in supervisor.AGRI_KEYWORDS.values()
        for kw in words
        if kw in question and kw in text
    }
    if not hits:
        return 0.0
    return min(1.0, 0.6 + 0.2 * (len(hits) - 1))


def gate_prompt(name: str, blurb: str, question: str) -> list[GateChatMessage]:
    return [
        GateChatMessage(
            role="system",
            content=(
                f"你是群聊中的助手「{name}」（简介：{(blurb or '')[:120] or '无'}）。"
                "只判断一件事：这个问题是否该由你回答——只有与你职责/简介确实匹配、"
                "你有把握答好的才回答，不确定或不相关就明确拒绝。"
                '只输出 JSON：{"willing":true|false,"confidence":0到1,"reason":"20字内理由"}。'
            ),
        ),
        GateChatMessage(role="user", content=f"问题：{question[:_GATE_QUESTION_CAP]}"),
    ]


def parse_gate_response(raw: str) -> dict[str, Any] | None:
    m = _JSON_RE.search(raw or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "willing" not in data:
        return None
    willing = data.get("willing")
    if isinstance(willing, str):
        willing = willing.strip().lower() in ("true", "yes", "1", "是", "可以", "能")
    raw_conf: Any = data.get("confidence")
    try:
        conf = float(raw_conf)
    except (TypeError, ValueError):
        conf = 0.0
    return {
        "willing": bool(willing),
        "confidence": max(0.0, min(1.0, conf)),
        "reason": str(data.get("reason") or "")[:120],
    }


def gate_decision(
    question: str, *, agent_name: str, blurb: str = "", llm: dict[str, Any] | None
) -> dict[str, Any]:
    kw = keyword_confidence(question, profile_text(agent_name, blurb))
    if llm is not None:
        eff = llm["confidence"] if llm["willing"] else max(0.0, 1.0 - llm["confidence"])
        confidence = round(0.7 * eff + 0.3 * kw, 3)
        basis = "助手自判"
    else:
        confidence = kw
        basis = "简介关键词兜底"
    reason = (llm or {}).get("reason") or (
        "简介与问题匹配" if confidence >= ANSWER_THRESHOLD else "简介与问题不相关"
    )
    return {
        "speak": confidence >= ANSWER_THRESHOLD,
        "confidence": confidence,
        "basis": basis,
        "reason": f"{reason}（{basis} {confidence:.2f}，门槛 {ANSWER_THRESHOLD}）",
    }


async def _ask_gate_llm(
    db: Session, ctx: YardContext, *, agent_name: str, blurb: str, question: str
) -> dict[str, Any] | None:
    try:
        resp = await complete_with_logging(
            db,
            LLMRequest(
                messages=gate_prompt(agent_name, blurb, question),
                metadata=LLMMetadata(
                    yard_id=ctx.yard_id,
                    task_id=f"grp-{uuid.uuid4().hex[:12]}",
                    request_id=ctx.request_id,
                    purpose="group_gate",
                    agent_ref=agent_name,
                ),
            ),
        )
    except Exception as e:  # noqa: BLE001
        log.warning("group gate llm failed for「%s」: %s", agent_name, e)
        return None
    parsed = parse_gate_response(resp.content or "")
    if parsed is not None:
        parsed["gate_tokens"] = {
            "input_tokens": int(resp.input_tokens or 0),
            "output_tokens": int(resp.output_tokens or 0),
        }
    return parsed


# ---------------------------------------------------------------------------
# 本轮成员选择
# ---------------------------------------------------------------------------


def resolve_named_target(participants: list[dict[str, Any]], target: str | None) -> dict[str, Any]:
    want = str(target or "").strip()
    if not want:
        raise AppError(
            "GROUP_TARGET_REQUIRED", "named 策略需要指定应答助手（target）。", http_status=400
        )
    for p in participants:
        if want in (str(p.get("agent_id")), str(p.get("agent_code")), str(p.get("agent_name"))):
            return p
    raise AppError(
        "GROUP_TARGET_NOT_PARTICIPANT",
        f"「{want}」不在本会话群聊名单里。",
        http_status=404,
        details={"target": want, "members": [p.get("agent_name") for p in participants]},
    )


def pick_members(
    policy: str, participants: list[dict[str, Any]], target: str | None
) -> list[dict[str, Any]]:
    if not participants:
        raise AppError(
            "GROUP_NO_PARTICIPANTS",
            "本会话还没有群聊成员，请先设置成员名单。",
            http_status=400,
        )
    if policy == POLICY_NAMED:
        return [resolve_named_target(participants, target)]
    if policy in (POLICY_ALL, POLICY_AUTO):
        return list(participants)
    raise AppError("INVALID_REQUEST", f"未知的应答策略：{policy}。", http_status=400)


# ---------------------------------------------------------------------------
# 一轮群聊的执行
# ---------------------------------------------------------------------------


def _member_event(
    turn_id: str,
    participant: dict[str, Any],
    status: str,
    error_text: str = "",
    token_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ev: dict[str, Any] = {
        "turn_id": turn_id,
        "member_index": int(participant.get("position") or 0),
        "role_name": str(participant.get("agent_name") or ""),
        "agent_id": str(participant.get("agent_id") or ""),
        "status": status,
        "error_text": error_text[:300] if error_text else None,
    }
    if token_usage:
        ev["token_usage"] = token_usage
    return ev


class _TurnBudget:
    def __init__(self, answer_cap: int, gate_cap: int) -> None:
        self._answer_cap = answer_cap
        self._gate_cap = gate_cap
        self._answers = 0
        self._gates = 0
        self._lock = asyncio.Lock()

    async def take_answer(self) -> bool:
        async with self._lock:
            if self._answers >= self._answer_cap:
                return False
            self._answers += 1
            return True

    async def take_gate(self) -> bool:
        async with self._lock:
            if self._gates >= self._gate_cap:
                return False
            self._gates += 1
            return True


async def _execute_one_member(
    *,
    ctx: YardContext,
    question: str,
    turn_id: str,
    participant: dict[str, Any],
    blurb: str,
    policy: str,
    budget: _TurnBudget,
    semaphore: asyncio.Semaphore,
    emit: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    name = str(participant.get("agent_name") or "")
    steps = StepRecorder()

    def _declined(reason: str) -> dict[str, Any]:
        steps.emit(PHASE_ROUTING, f"「{name}」未回答：{reason}", STEP_DONE)
        return {
            "participant": participant,
            "status": "declined",
            "reason": reason,
            "steps": steps.steps,
        }

    async with semaphore:
        mem_db = _get_session_factory()()
        try:
            gate_in_tok = 0
            gate_out_tok = 0
            if policy == POLICY_AUTO:
                gate_llm: dict[str, Any] | None = None
                gate_note = ""
                if await budget.take_gate():
                    gate_llm = await _ask_gate_llm(
                        mem_db, ctx, agent_name=name, blurb=blurb, question=question
                    )
                    if gate_llm is None:
                        gate_note = "自判模型不可用，已降级兜底"
                    elif isinstance(gate_llm.get("gate_tokens"), dict):
                        gate_in_tok = int(gate_llm["gate_tokens"].get("input_tokens") or 0)
                        gate_out_tok = int(gate_llm["gate_tokens"].get("output_tokens") or 0)
                else:
                    gate_note = f"本轮判定调用已达上限（{GATE_LLM_CALL_CAP} 次）"
                decision = gate_decision(question, agent_name=name, blurb=blurb, llm=gate_llm)
                if gate_note:
                    decision["reason"] = f"{decision['reason']}；{gate_note}"
                verdict = "回答" if decision["speak"] else "不回答"
                steps.emit(
                    PHASE_ROUTING,
                    f"「{name}」自判：{verdict}——{decision['reason']}",
                    STEP_DONE,
                )
                if not decision["speak"]:
                    return _declined(decision["reason"])

            if not await budget.take_answer():
                return _declined(f"本轮应答预算（{MAX_ANSWERS_PER_TURN} 位）已满")

            emit("member_event", _member_event(turn_id, participant, MEMBER_RUNNING))
            data = await asyncio.wait_for(
                supervisor.ask_directed(
                    mem_db, ctx=ctx, question=question, target=str(participant.get("agent_id"))
                ),
                timeout=MEMBER_TIMEOUT_SECONDS,
            )
            result = dict(data.get("result") or {})
            answer = str(result.get("answer") or "")
            if not answer.strip():
                return _declined("回答为空（模型未给出内容）")

            tu = result.get("token_usage")
            if isinstance(tu, dict):
                tu = dict(tu)
                tu["input_tokens"] = int(tu.get("input_tokens") or 0) + gate_in_tok
                tu["output_tokens"] = int(tu.get("output_tokens") or 0) + gate_out_tok
                tu["total_tokens"] = tu["input_tokens"] + tu["output_tokens"]
            else:
                in_est = estimate_token_count(question) + gate_in_tok
                out_est = estimate_token_count(answer) + gate_out_tok
                tu = {
                    "input_tokens": in_est,
                    "output_tokens": out_est,
                    "total_tokens": in_est + out_est,
                    "estimated": True,
                }

            choice = dict(data.get("choice") or {})
            choice.update(
                {
                    "id": str(participant.get("agent_id")),
                    "code": str(participant.get("agent_code") or choice.get("code") or ""),
                    "name": name,
                    "member_index": int(participant.get("position") or 0),
                    "turn_id": turn_id,
                    "policy": policy,
                }
            )
            steps.emit(PHASE_GENERATING, f"「{name}」已回答", STEP_DONE)
            emit("member_event", _member_event(turn_id, participant, MEMBER_OK, token_usage=tu))
            return {
                "participant": participant,
                "status": "ok",
                "content": answer,
                "routed_by": str(data.get("routed_by") or "bound")[:10],
                "choice": choice,
                "reason": str(data.get("reason") or "")[:200],
                "steps": steps.steps,
                "citations": service.citations_from_result(result),
                "token_usage": tu,
            }
        except TimeoutError:
            mem_db.rollback()
            error_text = f"回答超时（超过 {int(MEMBER_TIMEOUT_SECONDS)} 秒），本轮不再重试。"
            steps.emit(PHASE_GENERATING, f"「{name}」{error_text}", STEP_FAILED)
            emit("member_event", _member_event(turn_id, participant, MEMBER_ERROR, error_text))
            return {
                "participant": participant,
                "status": "error",
                "reason": error_text,
                "error_code": "GROUP_MEMBER_TIMEOUT",
                "steps": steps.steps,
            }
        except AppError as e:
            mem_db.rollback()
            steps.emit(PHASE_GENERATING, f"「{name}」未回答：{e.message}", STEP_FAILED)
            emit("member_event", _member_event(turn_id, participant, MEMBER_ERROR, e.message))
            return {
                "participant": participant,
                "status": "error",
                "reason": e.message,
                "error_code": e.code,
                "steps": steps.steps,
            }
        except Exception as e:  # noqa: BLE001
            mem_db.rollback()
            log.warning("group member「%s」failed: %s", name, e)
            error_text = "服务暂时不可用。"
            steps.emit(PHASE_GENERATING, f"「{name}」未回答：{error_text}", STEP_FAILED)
            emit("member_event", _member_event(turn_id, participant, MEMBER_ERROR, error_text))
            return {
                "participant": participant,
                "status": "error",
                "reason": error_text,
                "error_code": "GROUP_MEMBER_ERROR",
                "steps": steps.steps,
            }
        finally:
            mem_db.close()


def _notice_content(
    *, policy: str, member_count: int, answered: int, not_answered: list[dict[str, Any]]
) -> str:
    lines = [f"未回答（{len(not_answered)} 位）："]
    lines.extend(
        f"「{str(x['participant'].get('agent_name') or '')}」未回答：{x['reason']}"
        for x in not_answered
    )
    label = {POLICY_ALL: "全部应答", POLICY_AUTO: "自愿应答", POLICY_NAMED: "点名应答"}.get(
        policy, policy
    )
    head = f"本轮策略「{label}」：名单 {member_count} 位，已回答 {answered} 位。"
    return head + "\n" + "\n".join(lines)


async def run_group_turn(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachment_ids: list[str] | None,
    policy: str,
    target: str | None = None,
    participants: list[dict[str, Any]] | None = None,
    recorder: StepRecorder | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    steps = recorder or StepRecorder()
    turn_id = _new_turn_id()
    emit: Callable[[str, dict[str, Any]], None] = on_event or (lambda _k, _d: None)

    members = (
        participants
        if participants is not None
        else list_participants(
            db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
        )
    )
    picked = pick_members(policy, members, target)

    attachments = service.resolve_attachments(
        db, ctx=ctx, session_id=session_id, attachment_ids=attachment_ids or []
    )
    context = attachments_service.build_attachment_context(attachments)
    question = f"{content}\n\n{context}" if context else content

    blurbs: dict[str, str] = {
        str(c["id"]): str(c.get("blurb") or "")
        for c in supervisor.list_candidates(db, ctx.yard_id, user_id=ctx.user_id)
        if c["kind"] == "agent"
    }

    steps.emit(PHASE_ROUTING, f"群聊本轮：{len(picked)} 位成员参与", STEP_DONE)
    semaphore = asyncio.Semaphore(GROUP_CONCURRENCY)
    budget = _TurnBudget(MAX_ANSWERS_PER_TURN, GATE_LLM_CALL_CAP)
    results = await asyncio.gather(
        *(
            _execute_one_member(
                ctx=ctx,
                question=question,
                turn_id=turn_id,
                participant=p,
                blurb=blurbs.get(str(p.get("agent_id")), ""),
                policy=policy,
                budget=budget,
                semaphore=semaphore,
                emit=emit,
            )
            for p in picked
        ),
        return_exceptions=True,
    )

    outcomes: list[dict[str, Any]] = []
    for p, res in zip(picked, results, strict=True):
        if isinstance(res, BaseException):
            log.warning("group member「%s」unexpected: %s", p.get("agent_name"), res)
            outcomes.append(
                {
                    "participant": p,
                    "status": "error",
                    "reason": "服务暂时不可用。",
                    "error_code": "GROUP_MEMBER_ERROR",
                    "steps": [],
                }
            )
        else:
            outcomes.append(res)

    session_row = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    answered: list[dict[str, Any]] = []
    not_answered: list[dict[str, Any]] = []
    for res in outcomes:
        for step in res.get("steps") or []:
            steps.emit(step["phase"], str(step.get("detail") or ""), str(step["status"]))
        if res["status"] == "ok":
            msg = service.persist_assistant_message(
                db,
                ctx=ctx,
                session=session_row,
                content=res["content"],
                routed_by=res["routed_by"],
                choice=res["choice"],
                reason=res["reason"],
                steps=res.get("steps") or None,
                status=MSG_OK,
                citations=res.get("citations"),
                token_usage=res.get("token_usage"),
            )
            db.flush()
            answered.append(service.message_out(msg))
        else:
            res["error_code"] = res.get("error_code") or "GROUP_MEMBER_DECLINED"
            not_answered.append(res)

    notice_out: dict[str, Any] | None = None
    if not_answered:
        notice = service.persist_assistant_message(
            db,
            ctx=ctx,
            session=session_row,
            content=_notice_content(
                policy=policy,
                member_count=len(picked),
                answered=len(answered),
                not_answered=not_answered,
            ),
            routed_by=ROUTED_GROUP,
            choice={
                "kind": "group",
                "id": "",
                "name": "群聊",
                "turn_id": turn_id,
                "policy": policy,
                "not_answered": [
                    {
                        "member_index": int(r["participant"].get("position") or 0),
                        "name": r["participant"].get("agent_name"),
                        "reason": r["reason"],
                        "error_code": r.get("error_code"),
                    }
                    for r in not_answered
                ],
            },
            reason=f"{len(not_answered)} 位未回答，已如实说明。"[:200],
            steps=steps.steps,
            status=MSG_OK if answered else MSG_FAILED,
            error_code="" if answered else "GROUP_NO_ANSWER",
            token_usage={
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
                "model": "group",
            },
        )
        db.flush()
        notice_out = service.message_out(notice)

    steps.finish()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="chat_group_turn",
        resource_type="chat_session",
        resource_id=session_id,
        result="success" if answered else "failed",
        request_id=ctx.request_id,
        metadata={
            "turn_id": turn_id,
            "policy": policy,
            "member_count": len(picked),
            "answered": len(answered),
            "not_answered": len(not_answered),
            "attachments": len(attachments),
        },
    )
    db.commit()

    for item in answered:
        emit("message", item)
    if notice_out is not None:
        emit("message", notice_out)

    return {
        "turn_id": turn_id,
        "policy": policy,
        "member_count": len(picked),
        "messages": answered,
        "answered_names": [
            str(r["participant"].get("agent_name") or "") for r in outcomes if r["status"] == "ok"
        ],
        "not_answered": [
            {
                "member_index": int(r["participant"].get("position") or 0),
                "agent_id": r["participant"].get("agent_id"),
                "name": r["participant"].get("agent_name"),
                "status": r["status"],
                "reason": r["reason"],
                "error_code": r.get("error_code"),
            }
            for r in not_answered
        ],
        "notice": notice_out,
        "steps": steps.steps,
    }


__all__ = [
    "ANSWER_THRESHOLD",
    "GATE_LLM_CALL_CAP",
    "GROUP_CONCURRENCY",
    "MAX_ANSWERS_PER_TURN",
    "MAX_PARTICIPANTS",
    "MEMBER_ERROR",
    "MEMBER_OK",
    "MEMBER_RUNNING",
    "MEMBER_TIMEOUT_SECONDS",
    "ROUTED_GROUP",
    "gate_decision",
    "keyword_confidence",
    "list_participants",
    "parse_gate_response",
    "participant_out",
    "pick_members",
    "replace_participants",
    "resolve_named_target",
    "run_group_turn",
]
