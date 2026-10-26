"""总控问答入口（supervisor）：把一个问题路由到本小院最合适的助手或班组（含 Token 消耗透传）。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.progress import (
    PHASE_GENERATING,
    PHASE_ROUTING,
    STEP_DONE,
    STEP_FAILED,
    STEP_RUNNING,
    StepCallback,
)
from app.modules.agents import declarative
from app.modules.agents.models import YardAgent
from app.modules.agents.teams import service as team_service
from app.modules.agents.teams.models import AgentTeam, TeamRun
from app.modules.audit.service import log_audit
from app.modules.llm.models import LLMTrace
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging

log = get_logger(__name__)

#: 农业域关键词表：命中问题 + 候选文本同时含该词才计分（避免跨域误路由）
AGRI_KEYWORDS: dict[str, list[str]] = {
    "棚室类": ["棚室", "通风", "保温", "光照", "湿度", "加温", "卷帘"],
    "灌溉类": ["灌溉", "浇水", "墒情", "水分", "滴灌", "水肥"],
    "植保类": ["虫害", "病害", "农药", "打药", "霉", "病斑", "虫害防治", "虫害"],
    "苗情类": ["苗情", "长势", "出苗", "壮苗", "测苗"],
    "政策类": ["政策", "补贴", "申报", "立项"],
    "市场类": ["价格", "行情", "市场", "卖货", "批发"],
}

_JSON_RE = re.compile(r"\{.*\}", re.S)

#: 路由来源的可读标签（步骤条与前端徽章共用同一口径）
_ROUTED_LABEL: dict[str, str] = {
    "rules": "规则命中",
    "llm": "智能分类",
    "fallback": "频率兜底",
    "bound": "定向执行",
    "local": "对话直办",
    "group": "群聊说明",
}

#: 徽章色调（前端按此取标签样式，词表同样只在这里维护）
_ROUTED_TONE: dict[str, str] = {
    "rules": "success",
    "llm": "primary",
    "fallback": "warning",
    "bound": "info",
    "local": "info",
    "group": "primary",
    "error": "danger",
}

ROUTED_LABEL_UNKNOWN = "未标注来源"
ROUTED_TONE_UNKNOWN = "info"


def routed_label(routed_by: str | None) -> str:
    if not routed_by:
        return ""
    return _ROUTED_LABEL.get(routed_by, ROUTED_LABEL_UNKNOWN)


def routed_tone(routed_by: str | None) -> str:
    if not routed_by:
        return ""
    return _ROUTED_TONE.get(routed_by, ROUTED_TONE_UNKNOWN)


def _question_digest(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:12]


def list_candidates(
    db: Session, yard_id: str, *, user_id: str | None = None
) -> list[dict[str, Any]]:
    visibility_filter = (
        YardAgent.visibility == "public"
        if user_id is None
        else or_(YardAgent.visibility == "public", YardAgent.created_by == user_id)
    )
    cands: list[dict[str, Any]] = []
    seen: set[str] = set()
    rows = db.scalars(
        select(YardAgent)
        .where(
            YardAgent.yard_id == yard_id,
            YardAgent.status == "published",
            visibility_filter,
        )
        .order_by(YardAgent.agent_code, YardAgent.version.desc())
    )
    for a in rows:
        if a.agent_code in seen:
            continue
        seen.add(a.agent_code)
        cands.append(
            {
                "kind": "agent",
                "id": a.id,
                "code": a.agent_code,
                "name": a.name,
                "blurb": (a.description or "")[:120],
                "text": f"{a.name} {a.description or ''}",
            }
        )
    for t in db.scalars(
        select(AgentTeam).where(AgentTeam.yard_id == yard_id, AgentTeam.enabled.is_(True))
    ):
        cands.append(
            {
                "kind": "team",
                "id": t.id,
                "code": None,
                "name": t.name,
                "blurb": (t.description or "")[:120],
                "text": f"{t.name} {t.description or ''} {t.model_hint or ''}",
            }
        )
    return cands


def score_by_rules(question: str, cands: list[dict[str, Any]]) -> dict[str, Any] | None:
    kws = [k for words in AGRI_KEYWORDS.values() for k in words if k in question]
    if not kws:
        return None
    best: tuple[int, dict[str, Any]] | None = None
    for c in cands:
        score = sum(1 for k in kws if k in c["text"])
        if score > 0 and (best is None or score > best[0]):
            best = (score, c)
    return best[1] if best else None


def parse_llm_choice(raw: str, cands: list[dict[str, Any]]) -> dict[str, Any] | None:
    m = _JSON_RE.search(raw or "")
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    cid = str(data.get("id") or "")
    for c in cands:
        if c["id"] == cid or (c["code"] and c["code"] == cid) or c["name"] == cid:
            return {"choice": c, "reason": str(data.get("reason") or "")[:200]}
    return None


def _llm_prompt(question: str, cands: list[dict[str, Any]]) -> list[ChatMessage]:
    lines = [
        f"- id={c['id']} 类型={c['kind']} 名称={c['name']} 简介={c['blurb']}" for c in cands[:20]
    ]
    return [
        ChatMessage(
            role="system",
            content=(
                "你是总控路由：从候选中为问题选择唯一最合适的助手或班组。"
                '只输出 JSON：{"id":"<选中候选的id>","reason":"<30字内理由>"}，不要输出其他内容。'
            ),
        ),
        ChatMessage(
            role="user",
            content="候选列表：\n" + "\n".join(lines) + f"\n\n问题：{question[:500]}",
        ),
    ]


async def classify_by_llm(
    db: Session, ctx: YardContext, question: str, cands: list[dict[str, Any]]
) -> dict[str, Any] | None:
    try:
        resp = await complete_with_logging(
            db,
            LLMRequest(
                messages=_llm_prompt(question, cands),
                metadata=LLMMetadata(
                    yard_id=ctx.yard_id,
                    task_id=f"sup-{uuid.uuid4().hex[:12]}",
                    request_id=ctx.request_id,
                    purpose="supervisor",
                    agent_ref="supervisor",
                ),
            ),
        )
        parsed = parse_llm_choice(resp.content or "", cands)
        if parsed:
            parsed["gate_tokens"] = {
                "input_tokens": int(resp.input_tokens or 0),
                "output_tokens": int(resp.output_tokens or 0),
            }
        return parsed
    except Exception as e:  # noqa: BLE001
        log.warning("supervisor llm classify failed (fallback): %s", e)
        return None


def _recent_call_counts(db: Session, yard_id: str, cands: list[dict[str, Any]]) -> dict[str, int]:
    cutoff = datetime.now(UTC) - timedelta(days=7)
    counts: dict[str, int] = {}
    codes = [c["code"] for c in cands if c["code"]]
    if codes:
        rows = db.execute(
            select(LLMTrace.agent_ref, func.count())
            .where(
                LLMTrace.yard_id == yard_id,
                LLMTrace.created_at >= cutoff,
                LLMTrace.agent_ref.in_(codes),
            )
            .group_by(LLMTrace.agent_ref)
        ).all()
        counts.update({str(r[0]): int(r[1]) for r in rows})
    team_ids = [c["id"] for c in cands if c["kind"] == "team"]
    if team_ids:
        team_rows = db.execute(
            select(TeamRun.team_id, func.count())
            .where(
                TeamRun.yard_id == yard_id,
                TeamRun.team_id.in_(team_ids),
                TeamRun.started_at >= cutoff,
            )
            .group_by(TeamRun.team_id)
        ).all()
        counts.update({str(r[0]): int(r[1]) for r in team_rows})
    return counts


def pick_fallback(db: Session, yard_id: str, cands: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _recent_call_counts(db, yard_id, cands)
    return max(cands, key=lambda c: (counts.get(c["code"] or c["id"], 0),))


async def execute_choice(
    db: Session,
    ctx: YardContext,
    choice: dict[str, Any],
    question: str,
    on_step: StepCallback | None = None,
    context: str = "",
) -> dict[str, Any]:
    """执行选中的候选：助手走声明式同步运行，班组走接力运行，并提取 token_usage。"""
    if choice["kind"] == "team":
        if on_step:
            on_step(PHASE_GENERATING, f"正在执行班组「{choice['name']}」", STEP_RUNNING)
        team = team_service.get_team(db, yard_id=ctx.yard_id, team_id=choice["id"])
        run = team_service.create_team_run(
            db, team=team, user_input=f"{question}\n\n{context}" if context else question, ctx=ctx
        )
        run = await team_service.continue_team_run(db, run=run, ctx=ctx)
        if on_step:
            on_step(PHASE_GENERATING, f"班组「{choice['name']}」执行完成", STEP_DONE)
        members = team_service.members_of_run(db, run)
        run_detail = team_service.run_out(run, members, db=db)
        return {
            "kind": "team",
            "run_id": run.id,
            "status": run.status,
            "answer": run.answer or "",
            "token_usage": run_detail.get("token_usage"),
        }
    decl = declarative.get_published_by_code(
        db, yard_id=ctx.yard_id, agent_code=choice["code"] or "", user_id=ctx.user_id
    )
    if decl is None:
        raise AppError("AGENT_NOT_FOUND", f"未知的 Agent：{choice['code']}。", http_status=404)
    result = await declarative.run_declarative_agent(
        db,
        agent=decl,
        input_data={"question": question},
        ctx=ctx,
        task_id=f"sup-{uuid.uuid4().hex[:12]}",
        on_step=on_step,
        context=context,
    )
    payload = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    answer = ""
    token_usage = None
    if isinstance(payload.get("output"), dict):
        answer = str(payload["output"].get("answer") or "")
        token_usage = payload["output"].get("token_usage")
    return {"kind": "agent", "answer": answer, "raw": payload, "token_usage": token_usage}


async def ask(
    db: Session,
    *,
    ctx: YardContext,
    question: str,
    on_step: StepCallback | None = None,
    context: str = "",
) -> dict[str, Any]:
    """总控问答主入口：三级路由 → 执行 → 审计（问题只存摘要）。"""
    question = (question or "").strip()
    if not question:
        raise AppError("INVALID_REQUEST", "问题不能为空。", http_status=400)
    cands = list_candidates(db, ctx.yard_id, user_id=ctx.user_id)
    if not cands:
        raise AppError(
            "NOT_FOUND",
            "本小院还没有可用的已发布助手或班组——请先在 Agent 市场装载或创建助手。",
            http_status=404,
        )
    if on_step:
        on_step(PHASE_ROUTING, "按规则与智能分类挑选最合适的助手", STEP_RUNNING)
    reason = ""
    chosen = score_by_rules(question, cands)
    routed_by = "rules"
    gate_tokens: dict[str, int] | None = None
    if chosen is None:
        picked = await classify_by_llm(db, ctx, question, cands)
        if picked:
            chosen, reason = picked["choice"], picked["reason"]
            routed_by = "llm"
            gate_tokens = picked.get("gate_tokens")
    if chosen is None:
        chosen = pick_fallback(db, ctx.yard_id, cands)
        routed_by = "fallback"
        reason = "规则与智能分类均未命中，按近 7 天使用频率兜底"
    if on_step:
        on_step(
            PHASE_ROUTING,
            f"已选定「{chosen['name']}」（{_ROUTED_LABEL.get(routed_by, routed_by)}）",
            STEP_DONE,
        )
    try:
        result = await execute_choice(db, ctx, chosen, question, on_step=on_step, context=context)
    except AppError:
        if on_step:
            on_step(PHASE_ROUTING, "执行失败", STEP_FAILED)
        raise

    if gate_tokens and isinstance(result.get("token_usage"), dict):
        tu = dict(result["token_usage"])
        tu["input_tokens"] = int(tu.get("input_tokens") or 0) + gate_tokens["input_tokens"]
        tu["output_tokens"] = int(tu.get("output_tokens") or 0) + gate_tokens["output_tokens"]
        tu["total_tokens"] = tu["input_tokens"] + tu["output_tokens"]
        result["token_usage"] = tu

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="supervisor_route",
        resource_type="supervisor",
        resource_id=str(chosen.get("code") or chosen["id"]),
        result="success",
        request_id=ctx.request_id,
        metadata={
            "routed_by": routed_by,
            "kind": chosen["kind"],
            "name": chosen["name"],
            "reason": reason[:200],
            "question_digest": _question_digest(question),
        },
    )
    db.commit()
    return {
        "choice": {k: chosen[k] for k in ("kind", "id", "code", "name")},
        "routed_by": routed_by,
        "reason": reason,
        "result": result,
    }


__all__ = [
    "ask",
    "ask_directed",
    "list_candidates",
    "parse_llm_choice",
    "routed_label",
    "routed_tone",
    "score_by_rules",
]


async def ask_directed(
    db: Session,
    *,
    ctx: YardContext,
    question: str,
    target: str | None,
    prefer_team: bool = False,
) -> dict:
    """定向执行：跳过三级路由，直达指定助手/班组。"""
    question = (question or "").strip()
    if not question:
        raise AppError("INVALID_REQUEST", "问题不能为空。", http_status=400)
    cands = list_candidates(db, ctx.yard_id, user_id=ctx.user_id)
    chosen = None
    for c in cands:
        if c["id"] == target:
            chosen = c
            break
    if chosen is None:
        want_team = cands if not prefer_team else [c for c in cands if c["kind"] == "team"]
        rest = [c for c in cands if c["kind"] != "team"]
        for pool in (want_team, rest, cands):
            for c in pool:
                if c.get("code") == target or c.get("name") == target:
                    chosen = c
                    break
            if chosen:
                break
    if chosen is None:
        names = "、".join(c["name"] for c in cands[:10]) or "无"
        raise AppError(
            "NOT_FOUND",
            f"没有找到名为「{target}」的已发布助手或班组。可选：{names}",
            http_status=404,
        )
    result = await execute_choice(db, ctx, chosen, question)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="supervisor_directed",
        resource_type="supervisor",
        resource_id=chosen["id"],
        result="success",
        request_id=ctx.request_id,
        metadata={
            "kind": chosen["kind"],
            "name": chosen["name"],
            "question_digest": _question_digest(question),
        },
    )
    db.commit()
    return {
        "choice": {k: chosen[k] for k in ("kind", "id", "code", "name")},
        "routed_by": "bound",
        "reason": f"定向执行：{chosen['name']}",
        "result": result,
    }
