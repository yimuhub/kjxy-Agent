"""总控问答入口（supervisor）：把一个问题路由到本租户最合适的助手或班组。

三级路由（诚实可审计，routed_by 必须如实反映实际路径）：
1. rules   —— 农业域关键词计分：问题命中词表 且 候选文本也含该词即计分，最高分>0 者当选；
2. llm     —— 一次纯文本分类调用（llm_traces purpose="supervisor"），要求只输出
              JSON {"id","reason"}；解析失败/超时/选中不在候选集 → 落入兜底；
3. fallback—— 近 7 天调用次数最多的候选（助手按 llm_traces.agent_ref，班组按 team_runs），
              再没有则第一个候选。兜底必须带可见标记，绝不伪装成智能选择。

审计：action=supervisor_route，metadata 记 routed_by/choice/reason 与问题的
sha12 摘要——**不存问题全文**（与 llm_traces 隐私口径一致）。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import declarative
from app.modules.agents.models import TenantAgent
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


def _question_digest(question: str) -> str:
    return hashlib.sha256(question.encode("utf-8")).hexdigest()[:12]


def list_candidates(db: Session, tenant_id: str) -> list[dict[str, Any]]:
    """候选集 = 本租户 published 助手（同码取最新版本）+ enabled 班组。"""
    cands: list[dict[str, Any]] = []
    seen: set[str] = set()
    rows = db.scalars(
        select(TenantAgent)
        .where(TenantAgent.tenant_id == tenant_id, TenantAgent.status == "published")
        .order_by(TenantAgent.agent_code, TenantAgent.version.desc())
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
        select(AgentTeam).where(AgentTeam.tenant_id == tenant_id, AgentTeam.enabled.is_(True))
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
    """规则计分：问题命中关键词 且 候选文本含同词 → +1；最高分且>0 当选。"""
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
    """从 LLM 回复中提取 {"id","reason"}；id 必须在候选集内，否则视为解析失败。"""
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
    db: Session, ctx: TenantContext, question: str, cands: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """LLM 分类；任何异常（网络/配额/格式）返回 None 由调用方落入兜底。"""

    try:
        resp = await complete_with_logging(
            db,
            LLMRequest(
                messages=_llm_prompt(question, cands),
                metadata=LLMMetadata(
                    tenant_id=ctx.tenant_id,
                    task_id=f"sup-{uuid.uuid4().hex[:12]}",
                    request_id=ctx.request_id,
                    purpose="supervisor",
                    agent_ref="supervisor",
                ),
            ),
        )
        return parse_llm_choice(resp.content or "", cands)
    except Exception as e:  # noqa: BLE001 —— 分类失败走兜底，不打断问答
        log.warning("supervisor llm classify failed (fallback): %s", e)
        return None


def _recent_call_counts(db: Session, tenant_id: str, cands: list[dict[str, Any]]) -> dict[str, int]:
    """近 7 天调用数：助手按 llm_traces.agent_ref，班组按 team_runs。"""
    cutoff = datetime.now(UTC) - timedelta(days=7)
    counts: dict[str, int] = {}
    codes = [c["code"] for c in cands if c["code"]]
    if codes:
        rows = db.execute(
            select(LLMTrace.agent_ref, func.count())
            .where(
                LLMTrace.tenant_id == tenant_id,
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
                TeamRun.tenant_id == tenant_id,
                TeamRun.team_id.in_(team_ids),
                TeamRun.started_at >= cutoff,
            )
            .group_by(TeamRun.team_id)
        ).all()
        counts.update({str(r[0]): int(r[1]) for r in team_rows})
    return counts


def pick_fallback(db: Session, tenant_id: str, cands: list[dict[str, Any]]) -> dict[str, Any]:
    counts = _recent_call_counts(db, tenant_id, cands)
    return max(cands, key=lambda c: (counts.get(c["code"] or c["id"], 0),))


async def execute_choice(
    db: Session, ctx: TenantContext, choice: dict[str, Any], question: str
) -> dict[str, Any]:
    """执行选中的候选：助手走声明式同步运行，班组走接力运行。"""
    if choice["kind"] == "team":
        team = team_service.get_team(db, tenant_id=ctx.tenant_id, team_id=choice["id"])
        run = team_service.create_team_run(db, team=team, user_input=question, ctx=ctx)
        run = await team_service.continue_team_run(db, run=run, ctx=ctx)
        return {
            "kind": "team",
            "run_id": run.id,
            "status": run.status,
            "answer": run.answer or "",
        }
    decl = declarative.get_published_by_code(
        db, tenant_id=ctx.tenant_id, agent_code=choice["code"] or ""
    )
    if decl is None:
        raise AppError("AGENT_NOT_FOUND", f"未知的 Agent：{choice['code']}。", http_status=404)
    result = await declarative.run_declarative_agent(
        db,
        agent=decl,
        input_data={"question": question},
        ctx=ctx,
        task_id=f"sup-{uuid.uuid4().hex[:12]}",
    )
    payload = result.model_dump() if hasattr(result, "model_dump") else dict(result)
    answer = ""
    if isinstance(payload.get("output"), dict):
        answer = str(payload["output"].get("answer") or "")
    return {"kind": "agent", "answer": answer, "raw": payload}


async def ask(db: Session, *, ctx: TenantContext, question: str) -> dict[str, Any]:
    """总控问答主入口：三级路由 → 执行 → 审计（问题只存摘要）。"""
    question = (question or "").strip()
    if not question:
        raise AppError("INVALID_REQUEST", "问题不能为空。", http_status=400)
    cands = list_candidates(db, ctx.tenant_id)
    if not cands:
        raise AppError(
            "NOT_FOUND",
            "本租户还没有可用的已发布助手或班组——请先在 Agent 市场装载或创建助手。",
            http_status=404,
        )
    reason = ""
    chosen = score_by_rules(question, cands)
    routed_by = "rules"
    if chosen is None:
        picked = await classify_by_llm(db, ctx, question, cands)
        if picked:
            chosen, reason = picked["choice"], picked["reason"]
            routed_by = "llm"
    if chosen is None:
        chosen = pick_fallback(db, ctx.tenant_id, cands)
        routed_by = "fallback"
        reason = "规则与智能分类均未命中，按近 7 天使用频率兜底"
    result = await execute_choice(db, ctx, chosen, question)
    log_audit(
        db,
        tenant_id=ctx.tenant_id,
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


__all__ = ["ask", "list_candidates", "score_by_rules", "parse_llm_choice"]


async def ask_directed(
    db: Session,
    *,
    ctx: TenantContext,
    question: str,
    target: str | None,
    prefer_team: bool = False,
) -> dict:
    """定向执行：跳过三级路由，直达指定助手/班组（机器人绑定与 /use 命令用）。

    target 可为候选 id / agent_code / 名称（模糊精确匹配，先 id 再 code 再名称）；
    prefer_team=True 时同名冲突优先取班组。找不到 → NOT_FOUND 404（附可选清单提示）。
    返回与 ask 同构：{choice, routed_by='bound', reason, result}。
    """
    question = (question or "").strip()
    if not question:
        raise AppError("INVALID_REQUEST", "问题不能为空。", http_status=400)
    cands = list_candidates(db, ctx.tenant_id)
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
        tenant_id=ctx.tenant_id,
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
