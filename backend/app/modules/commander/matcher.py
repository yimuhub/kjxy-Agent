"""指挥官选兵：已发布可见 Agent → 规则打分 → LLM 重排 → 去重复用 → 自动创建草稿。

选兵顺序（报告 §5.4，便宜到贵）：

1. 计划里给了有效 ``agent_code`` 且该 Agent 已发布且可见 → 直接用；
2. 规则打分（子任务关键词 × 名称/描述/技能/工具，叠加近 30 天成功率/频次/负载）；
3. 规则最高分交给 LLM 复核（前 8 个候选），低置信度/失败仍回退规则最高分；
4. 无候选且允许自动创建 → 先找近 90 天同源（commander）的相似 Agent 复用；
5. 仍未命中且未达每日创建上限 → ``agent_builder.generate_agent`` 创建草稿
   （``visibility="public"``，``use_web=False``），来源改为 ``commander``；
6. 创建失败或达上限 → 回退最接近的已发布候选；仍无 → ``commander`` 代执行。

自动发布是保护型开关：只有 ``auto_publish_agent=true`` 且发起人拥有
``agents:manage`` 时才调用既有发布链路，并写 ``commander_agent_published`` 审计。
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core import timezone as business_time
from app.core.deps import YardContext
from app.core.logging import get_logger
from app.modules.agents import agent_builder, declarative
from app.modules.agents.models import CREATED_FROM_COMMANDER, YardAgent
from app.modules.audit.service import log_audit
from app.modules.commander import deliverables, feasibility
from app.modules.commander.models import (
    SOURCE_COMMANDER,
    SOURCE_EXISTING,
    SOURCE_GENERATED,
    SUB_OK,
    SUB_RUNNING,
    CommanderSubtask,
)
from app.modules.llm.schemas import ChatMessage
from app.modules.llm.service import complete_with_logging

log = get_logger(__name__)

#: 相似复用阈值：关键词集合的「较小集合覆盖率」达到该值即视为同一意图
REUSE_THRESHOLD = 0.5
#: 至少要有这么多共同关键词，避免一两个通用词误命中
REUSE_MIN_OVERLAP = 2
#: 参与相似度比较的候选上限
REUSE_SCAN_LIMIT = 100
#: 去重复用的时间窗：只复用近 90 天由指挥官自动创建的 Agent
REUSE_WINDOW_DAYS = 90
#: 成功率统计窗口
STATS_WINDOW_DAYS = 30
#: 成功率样本下限：少于该值的 Agent 不参与成功率加分
STATS_MIN_SAMPLES = 2
#: LLM 重排候选数上限与置信度阈值
LLM_RERANK_LIMIT = 8
LLM_RERANK_MIN_CONFIDENCE = 0.6
#: 每日自动创建上限的兜底值（设置里已声明，读取失败时用它）
DEFAULT_DAILY_AGENT_LIMIT = 10
#: 交付格式 → 同族技能编码前缀（规划期检索到的技能按族并入责任人绑定）
_PLAN_SKILL_PREFIX: dict[str, str] = {
    "pptx": "office_ppt_",
    "docx": "office_",
    "pdf": "office_",
    "xlsx": "office_excel",
}

_WORD_RE = re.compile(r"[a-z0-9_]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_STOPWORDS = frozenset(
    {
        "生成",
        "输出",
        "使用",
        "需要",
        "进行",
        "完成",
        "基于",
        "根据",
        "一个",
        "用户",
        "任务",
        "内容",
        "结果",
        "要求",
        "以及",
        "并且",
        "可以",
        "不要",
    }
)


def keywords(text: str) -> list[str]:
    """检索关键词：英文词 + 中文连续串 + 中文 2/3-gram（中文无空格分词）。"""
    lowered = (text or "").lower()
    tokens: list[str] = [m.group(0) for m in _WORD_RE.finditer(lowered)]
    for match in _CJK_RE.finditer(lowered):
        run = match.group(0)
        if len(run) >= 2:
            tokens.append(run)
        for size in (2, 3):
            if len(run) >= size:
                tokens.extend(run[i : i + size] for i in range(len(run) - size + 1))
    seen: set[str] = set()
    out: list[str] = []
    for token in tokens:
        if token and token not in _STOPWORDS and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def agent_text(agent: YardAgent) -> str:
    skills = " ".join(str(s) for s in (agent.bound_skills or []))
    tools = " ".join(str(t) for t in (agent.allowed_tools or []))
    return " ".join(
        part
        for part in (
            agent.name or "",
            agent.description or "",
            agent.group_name or "",
            skills,
            tools,
        )
        if part
    )


def subtask_text(subtask: dict[str, Any]) -> str:
    return " ".join(
        str(subtask.get(key) or "")
        for key in ("title", "instruction", "expected_output", "agent_hint")
    )


def agent_stats(db: Session, yard_id: str) -> dict[str, dict[str, int]]:
    """近 30 天子任务表现：{agent_id: {total, ok, running}}。

    total/ok 只统计窗口内已绑定 Agent 的子任务；running 是**当前**仍在跑的负载，
    用于同分时避免继续压给已经很忙的 Agent。
    """
    cutoff = datetime.now(UTC) - timedelta(days=STATS_WINDOW_DAYS)
    rows = db.execute(
        select(
            CommanderSubtask.agent_id,
            func.count(CommanderSubtask.id).label("total"),
            func.sum(case((CommanderSubtask.status == SUB_OK, 1), else_=0)).label("ok"),
        )
        .where(
            CommanderSubtask.yard_id == yard_id,
            CommanderSubtask.agent_id.is_not(None),
            CommanderSubtask.created_at >= cutoff,
        )
        .group_by(CommanderSubtask.agent_id)
    ).all()
    stats: dict[str, dict[str, int]] = {}
    for row in rows:
        stats[str(row.agent_id)] = {
            "total": int(row.total or 0),
            "ok": int(row.ok or 0),
            "running": 0,
        }
    running_rows = db.execute(
        select(
            CommanderSubtask.agent_id,
            func.count(CommanderSubtask.id).label("running"),
        )
        .where(
            CommanderSubtask.yard_id == yard_id,
            CommanderSubtask.agent_id.is_not(None),
            CommanderSubtask.status == SUB_RUNNING,
        )
        .group_by(CommanderSubtask.agent_id)
    ).all()
    for row in running_rows:
        item = stats.setdefault(str(row.agent_id), {"total": 0, "ok": 0, "running": 0})
        item["running"] = int(row.running or 0)
    return stats


def _stat_for(stats: dict[str, dict[str, int]] | None, agent: YardAgent) -> dict[str, int] | None:
    if not stats or not agent.id:
        return None
    return stats.get(str(agent.id))


def _running_for(stats: dict[str, dict[str, int]] | None, agent: YardAgent) -> int:
    stat = _stat_for(stats, agent)
    return int(stat.get("running") or 0) if stat else 0


def score_agent(text: str, agent: YardAgent, stats: dict[str, dict[str, int]] | None = None) -> int:
    """规则分：命中关键词按长度加权（长词更具体，权重更高）。

    有 ``stats`` 时叠加近 30 天成功率（+30 * 成功率，样本 <2 不加）与使用频次
    （min(total, 10) 的小权重）；关键词零命中的 Agent 不加统计分，否则高频但
    不相关的 Agent 会挤掉「无候选 → 自动创建」路径。
    """
    kws = keywords(text)
    hay = agent_text(agent).lower()
    if not kws or not hay:
        return 0
    base = sum(len(kw) for kw in kws if kw in hay)
    if base <= 0 or stats is None:
        return base
    stat = _stat_for(stats, agent)
    if stat is None:
        return base
    total = int(stat.get("total") or 0)
    ok = int(stat.get("ok") or 0)
    bonus = min(total, 10)
    if total >= STATS_MIN_SAMPLES:
        bonus += round(30 * (ok / total))
    return base + bonus


def similarity(a_text: str, b_text: str) -> float:
    """较小关键词集合被覆盖的比例；用于「同一意图不重复建 Agent」。"""
    a = set(keywords(a_text))
    b = set(keywords(b_text))
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    if overlap < REUSE_MIN_OVERLAP:
        return 0.0
    return overlap / max(1, min(len(a), len(b)))


def _visible_filter(user_id: str):
    return or_(YardAgent.visibility == "public", YardAgent.created_by == user_id)


def _not_commander():
    """指挥官身份行不参与选兵候选。

    它挂了全院技能与工具，任何子任务都会以最高分命中它——一旦成为候选，
    团队里其他助手会被架空，规划器看到的「候选目录」也只剩自己。
    它的用武之地是派单与「无合适助手时亲自执行」（见 identity.build_executor_agent）。
    """
    from app.modules.commander import identity

    return YardAgent.agent_code != identity.COMMANDER_AGENT_CODE


def match_candidates(db: Session, *, yard_id: str, user_id: str) -> list[YardAgent]:
    rows = db.scalars(
        select(YardAgent)
        .where(
            YardAgent.yard_id == yard_id,
            YardAgent.status == "published",
            _visible_filter(user_id),
            _not_commander(),
        )
        .order_by(YardAgent.updated_at.desc())
    )
    return list(rows)


def lookup_by_code(db: Session, *, yard_id: str, user_id: str, agent_code: str) -> YardAgent | None:
    if not agent_code:
        return None
    return db.scalar(
        select(YardAgent)
        .where(
            YardAgent.yard_id == yard_id,
            YardAgent.agent_code == agent_code,
            YardAgent.status == "published",
            _visible_filter(user_id),
            # 计划点名「commander」走 executor=commander 的自执行通道，不当作普通助手
            _not_commander(),
        )
        .order_by(YardAgent.updated_at.desc())
        .limit(1)
    )


def best_candidate(
    text: str,
    candidates: list[YardAgent],
    stats: dict[str, dict[str, int]] | None = None,
) -> tuple[YardAgent | None, int]:
    """最高分候选；同分时（有 stats）选当前 running 子任务更少的一个。"""
    best: tuple[YardAgent | None, int] = (None, 0)
    for agent in candidates:
        score = score_agent(text, agent, stats)
        if score > best[1]:
            best = (agent, score)
        elif (
            stats is not None
            and best[0] is not None
            and score == best[1]
            and score > 0
            and _running_for(stats, agent) < _running_for(stats, best[0])
        ):
            best = (agent, score)
    return best


def daily_created_count(db: Session, yard_id: str) -> int:
    """当天（业务时区 00:00 起）由指挥官自动创建的 Agent 数量，用于每日创建上限。"""
    start = business_time.day_bounds(business_time.business_today())[0]
    count = db.scalar(
        select(func.count(YardAgent.id)).where(
            YardAgent.yard_id == yard_id,
            YardAgent.created_from == CREATED_FROM_COMMANDER,
            YardAgent.created_at >= start,
        )
    )
    return int(count or 0)


def _rerank_messages(text: str, candidates: list[YardAgent]) -> list[ChatMessage]:
    lines = [
        f"- code={agent.agent_code} 名称={agent.name} 简介={(agent.description or '')[:120]}"
        for agent in candidates
    ]
    system = (
        "你是「指挥官」的选兵复核器。给定子任务与规则候选，判断哪个助手最适合执行。\n"
        "硬性要求：\n"
        "1. 只输出一个 JSON 对象，不要 Markdown、不要解释；\n"
        '2. Schema：{"agent_code":"...","confidence":0.0,"reason":"..."}；\n'
        "3. agent_code 只能取候选列表里的 code；没有合适候选就返回空字符串并把"
        " confidence 设为 0；\n"
        "4. confidence 为 0.0–1.0，低于 0.6 表示「不确定/不合适」。"
    )
    user = f"子任务：{text[:2000]}\n\n候选助手：\n" + "\n".join(lines) + "\n\n请只输出 JSON。"
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


async def rerank_by_llm(
    db: Session,
    ctx: YardContext,
    *,
    text: str,
    candidates: list[YardAgent],
    settings: dict[str, Any],
) -> tuple[YardAgent | None, bool]:
    """规则前 8 个候选交给模型复核。

    返回 ``(agent, decided)``：
    - ``(agent, True)``：模型给出可信选择；
    - ``(None, True)``：模型明确判定没有合适候选（置信度 < 0.6）→ 调用方应继续自动建；
    - ``(None, False)``：模型不可用/输出非法 → 调用方回退规则最高分。
    """
    top = sorted(candidates, key=lambda agent: score_agent(text, agent), reverse=True)
    top = top[:LLM_RERANK_LIMIT]
    if not top:
        return None, False
    try:
        # 延迟 import：planner 依赖 llm.service，顶层 import 会形成选兵/规划互相引用
        from app.modules.commander import planner

        request = planner.build_request(
            db,
            ctx,
            settings,
            task_id=f"cmd-match-{uuid.uuid4().hex[:10]}",
            messages=_rerank_messages(text, top),
            temperature=0.0,
            purpose="commander_match",
        )
        response = await complete_with_logging(db, request)
        data = planner.parse_json_object(response.content or "")
        if data is None:
            return None, False
        try:
            confidence = float(data.get("confidence") or 0)
        except (TypeError, ValueError):
            return None, False
        if confidence < LLM_RERANK_MIN_CONFIDENCE:
            # 明确“没有合适的”→ 交给选兵继续复用/自动建，而不是硬塞规则最高分
            return None, True
        code = str(data.get("agent_code") or "").strip()
        for agent in top:
            if agent.agent_code == code:
                return agent, True
        return None, False
    except Exception as exc:  # noqa: BLE001 - 重排失败只降级为规则分，不影响选兵
        log.warning("commander llm rerank failed (fallback to rules): %s", exc)
        return None, False


def find_similar_agent(db: Session, *, yard_id: str, user_id: str, text: str) -> YardAgent | None:
    """近 90 天指挥官自动创建且状态可用的 Agent 中，找高度相似的可复用项。

    把扫描范围限定 ``created_from=commander``，避免复用用户手工/模板创建的 Agent
    （它们的发布与命名由用户自己负责）。
    """
    cutoff = datetime.now(UTC) - timedelta(days=REUSE_WINDOW_DAYS)
    rows = db.scalars(
        select(YardAgent)
        .where(
            YardAgent.yard_id == yard_id,
            YardAgent.status.in_(("draft", "pending_platform_review", "published")),
            YardAgent.created_from == CREATED_FROM_COMMANDER,
            YardAgent.created_at >= cutoff,
            _visible_filter(user_id),
            _not_commander(),
        )
        .order_by(YardAgent.updated_at.desc())
        .limit(REUSE_SCAN_LIMIT)
    )
    best: tuple[YardAgent | None, float] = (None, 0.0)
    for agent in rows:
        value = similarity(text, agent_text(agent))
        if value > best[1]:
            best = (agent, value)
    if best[0] is None or best[1] < REUSE_THRESHOLD:
        return None
    return best[0]


def _agent_pick(agent: YardAgent, source: str) -> dict[str, Any]:
    return {
        "agent_id": agent.id,
        "agent_code": agent.agent_code,
        "agent_name": agent.name,
        "agent_source": source,
    }


def _commander_pick() -> dict[str, Any]:
    return {
        "agent_id": None,
        "agent_code": None,
        "agent_name": "指挥官",
        "agent_source": SOURCE_COMMANDER,
    }


def _can_publish(db: Session, ctx: YardContext) -> bool:
    from app.modules.yards.authz import has_permission

    return has_permission(
        db,
        yard_id=ctx.yard_id,
        user_id=ctx.user_id,
        jwt_roles=ctx.roles,
        permission="agents:manage",
    )


def _maybe_publish(
    db: Session, *, ctx: YardContext, agent: YardAgent, settings: dict[str, Any]
) -> None:
    if not settings.get("auto_publish_agent"):
        return
    if not _can_publish(db, ctx):
        log.info("commander auto publish skipped: actor lacks agents:manage")
        return
    try:
        published = declarative.publish_agent(
            db,
            yard_id=ctx.yard_id,
            actor_id=ctx.user_id,
            agent_id=agent.id,
            roles=list(ctx.roles),
            request_id=ctx.request_id,
        )
    except Exception as exc:  # noqa: BLE001 - 发布失败不影响本轮草稿执行
        log.warning("commander auto publish failed for %s: %s", agent.agent_code, exc)
        return
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_agent_published",
        resource_type="agent",
        resource_id=published.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "agent_code": published.agent_code,
            "name": published.name,
            "status": published.status,
        },
    )


async def _create_agent(
    db: Session, *, ctx: YardContext, text: str, settings: dict[str, Any]
) -> YardAgent | None:
    utterance = text.strip()
    if len(utterance) < 4:
        utterance = f"完成子任务：{utterance or '通用任务'}"
    try:
        result = await agent_builder.generate_agent(
            db,
            ctx=ctx,
            utterance=utterance[:800],
            visibility="public",
            use_web=False,
        )
    except Exception as exc:  # noqa: BLE001 - 自动创建失败必须可回退
        log.warning("commander auto create agent failed: %s", exc)
        return None
    payload = result.get("agent") if isinstance(result, dict) else None
    agent_id = str((payload or {}).get("id") or "")
    agent = db.get(YardAgent, agent_id) if agent_id else None
    if agent is None:
        log.warning("commander auto create agent returned no agent row")
        return None
    # 来源标记：agent_builder 记的是 generated（一句话生成），这里改成
    # commander，智能体库据此显示「AI 创建」，与用户自己点生成区分开
    agent.created_from = CREATED_FROM_COMMANDER
    db.add(agent)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="commander_agent_created",
        resource_type="agent",
        resource_id=agent.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "agent_code": agent.agent_code,
            "name": agent.name,
            "status": agent.status,
        },
    )
    _maybe_publish(db, ctx=ctx, agent=agent, settings=settings)
    return agent


def _owner_by_skills(
    db: Session, *, yard_id: str, user_id: str, skill_codes: list[str]
) -> YardAgent | None:
    """团队里已绑定目标技能、可见且已发布的助手（优先最近使用过的）。"""
    if not skill_codes:
        return None
    wanted = set(skill_codes)
    rows = db.scalars(
        select(YardAgent)
        .where(
            YardAgent.yard_id == yard_id,
            YardAgent.status == "published",
            or_(YardAgent.visibility == "public", YardAgent.created_by == user_id),
            _not_commander(),
        )
        .order_by(YardAgent.updated_at.desc())
        .limit(REUSE_SCAN_LIMIT)
    )
    for agent in rows:
        bound = {str(s) for s in (agent.bound_skills or [])}
        if bound & wanted:
            return agent
    return None


def _ensure_skills(db: Session, agent: YardAgent, codes: list[str]) -> list[str]:
    """补齐责任人 Agent 的技能绑定；返回实际新增的技能编码。

    绑定表里同时存在两种写法：``skill:<id>``（安装时的引用）与技能 code。
    agent_builder 自动匹配绑的是前者，所以这里要先把 id 映射回 code 再判重，
    否则同一项技能会被绑两遍，提示词里也会重复出现。
    """
    bound = [str(s) for s in (agent.bound_skills or [])]
    if not codes:
        return []
    from sqlalchemy import select as _select

    from app.modules.market.models import MarketItem

    skill_refs = {item for item in bound if item.startswith("skill:")}
    if skill_refs:
        # MarketItem 是平台级目录（无 yard_id 列），只按 code 取 id 再换算成 skill:<id>
        rows = db.execute(
            _select(MarketItem.code, MarketItem.id).where(MarketItem.code.in_(codes))
        ).all()
        already = {code for code, item_id in rows if f"skill:{item_id}" in skill_refs}
    else:
        already = set()
    added = [code for code in codes if code and code not in bound and code not in already]
    if added:
        agent.bound_skills = [*bound, *added]
        db.add(agent)
    return added


async def _capability_owner(
    db: Session,
    *,
    ctx: YardContext,
    subtask: dict[str, Any],
    settings: dict[str, Any],
    created_today: int,
    plan_skills: list[str] | None = None,
) -> tuple[dict[str, Any] | None, int]:
    """为一个能力型子任务准备常驻责任人：先复用，后创建。

    责任人只负责「这件事归谁」，实际文件生成仍由指挥官直连渲染器执行——
    这样既有明确的人负责、能沉淀复用，又不会把成功率押在某个助手会不会
    调 ``doc.generate`` 上。

    返回 (责任人摘要, 本次新增创建数)。
    """
    caps = feasibility.detect_capabilities(
        " ".join(str(subtask.get(key) or "") for key in ("title", "instruction", "expected_output"))
    )
    skills = feasibility.skill_codes_for(caps)
    # 规划阶段检索到的同族技能一并绑上（例如 PPT 的叙事/设计/图表/质检/模板）
    if plan_skills:
        fmt = deliverables.subtask_format(subtask)
        prefix = _PLAN_SKILL_PREFIX.get(fmt or "", "")
        for code in plan_skills:
            clean = str(code or "").strip()
            if clean and prefix and clean.startswith(prefix) and clean not in skills:
                skills.append(clean)
    existing = _owner_by_skills(db, yard_id=ctx.yard_id, user_id=ctx.user_id, skill_codes=skills)
    if existing is not None:
        return (
            {
                "agent_id": existing.id,
                "agent_code": existing.agent_code,
                "agent_name": existing.name,
                "created": False,
                "skills": skills,
            },
            0,
        )

    if not settings.get("auto_create_agent"):
        return None, 0
    limit = int(settings.get("daily_agent_limit") or DEFAULT_DAILY_AGENT_LIMIT)
    if created_today >= limit:
        log.warning("commander capability owner skipped: daily agent limit reached")
        return None, 0

    labels = [cap.label for code in caps if (cap := feasibility.capability(code)) is not None]
    topic = "、".join(labels) or "文档交付"
    title = str(subtask.get("title") or "").strip()
    utterance = f"{topic}专家，负责{title or topic}：按需求产出可直接交付的成品并自检质量。"
    agent = await _create_agent(db, ctx=ctx, text=utterance, settings=settings)
    if agent is None:
        return None, 0
    added = _ensure_skills(db, agent, skills)
    log.info(
        "commander capability owner created: agent=%s skills=%s added=%s",
        agent.agent_code,
        len(skills),
        added,
    )
    return (
        {
            "agent_id": agent.id,
            "agent_code": agent.agent_code,
            "agent_name": agent.name,
            "created": True,
            "skills": skills,
        },
        1,
    )


def _closest_pick(
    text: str,
    candidates: list[YardAgent],
    stats: dict[str, dict[str, int]] | None,
) -> dict[str, Any]:
    """回退到最接近的已发布候选（允许 0 分，仍比空转强）；无候选则指挥官代执行。"""
    closest, _ = best_candidate(text, candidates, stats)
    if closest is None and candidates:
        closest = candidates[0]
    return _agent_pick(closest, SOURCE_EXISTING) if closest else _commander_pick()


async def assign_agents(
    db: Session,
    *,
    ctx: YardContext,
    subtasks: list[dict[str, Any]],
    settings: dict[str, Any],
    plan_fallback: bool,
    plan_skills: list[str] | None = None,
) -> list[dict[str, Any]]:
    """为每个子任务选一个执行者；返回与 subtasks 等长的绑定列表。

    ``plan_skills``：规划阶段从技能库检索到的技能编码，用于给责任人补齐绑定，
    让「技能优先」不只停留在提示词里。
    """
    candidates = match_candidates(db, yard_id=ctx.yard_id, user_id=ctx.user_id)
    stats = agent_stats(db, ctx.yard_id)
    # 兜底规划同样允许补人：降级过的计划更需要一个明确责任人，否则永远补不上能力
    auto_create = bool(settings.get("auto_create_agent"))
    daily_limit = int(settings.get("daily_agent_limit") or DEFAULT_DAILY_AGENT_LIMIT)
    created_today = daily_created_count(db, ctx.yard_id) if auto_create else 0
    picks: list[dict[str, Any]] = []
    for subtask in subtasks:
        # 计划里显式指定指挥官执行的（能力缺口说明、交付物渲染）不再选兵
        if str(subtask.get("executor") or "") == "commander":
            picks.append(_commander_pick())
            continue
        # 文件交付任务由指挥官确定性执行（doc.generate 直连文档渲染），
        # 但会先确保团队里有对应技能的责任人：没有就建一个并绑定技能，
        # 下次同类任务直接复用这个人。责任人不承担渲染，只保证「有人负责」。
        fmt = deliverables.subtask_format(subtask)
        if fmt:
            owner, created = await _capability_owner(
                db,
                ctx=ctx,
                subtask=subtask,
                settings=settings,
                created_today=created_today,
                plan_skills=plan_skills,
            )
            created_today += created
            if owner is not None:
                subtask["owner"] = owner
            log.info(
                "commander match seq=%s deliverable=%s executor=commander owner=%s",
                subtask.get("seq"),
                fmt,
                (owner or {}).get("agent_name") or "-",
            )
            picks.append(_commander_pick())
            continue
        text = subtask_text(subtask)
        explicit = lookup_by_code(
            db,
            yard_id=ctx.yard_id,
            user_id=ctx.user_id,
            agent_code=str(subtask.get("agent_code") or ""),
        )
        if explicit is not None:
            picks.append(_agent_pick(explicit, SOURCE_EXISTING))
            continue

        best, score = best_candidate(text, candidates, stats)
        if best is not None and score > 0:
            # 只有多个候选时重排才有意义；单候选时 LLM 的「不合适」也不会改变结果，
            # 直接省掉一次模型调用（成本与延迟都不划算）
            if len(candidates) <= 1:
                picks.append(_agent_pick(best, SOURCE_EXISTING))
                continue
            reranked, decided = await rerank_by_llm(
                db,
                ctx,
                text=text,
                candidates=candidates,
                settings=settings,
            )
            if reranked is not None:
                picks.append(_agent_pick(reranked, SOURCE_EXISTING))
                continue
            if not decided or not auto_create:
                # 模型不可用，或本小院不允许自动建：沿用规则最高分
                picks.append(_agent_pick(best, SOURCE_EXISTING))
                continue
            # decided=True 且允许自动建：继续走“相似复用 → 自动创建”分支

        if not auto_create:
            # 兜底规划（规划失败）优先给一个可用 Agent，而不是让指挥官代答
            if plan_fallback and candidates:
                fallback, _ = best_candidate(text, candidates, stats)
                chosen = fallback or candidates[0]
                picks.append(_agent_pick(chosen, SOURCE_EXISTING))
            else:
                picks.append(_commander_pick())
            continue

        reused = find_similar_agent(db, yard_id=ctx.yard_id, user_id=ctx.user_id, text=text)
        if reused is not None:
            picks.append(_agent_pick(reused, SOURCE_EXISTING))
            continue

        if created_today >= daily_limit:
            log.warning(
                "commander daily agent limit reached (%s/%s), fallback to existing",
                created_today,
                daily_limit,
            )
            picks.append(_closest_pick(text, candidates, stats))
            continue

        new_agent = await _create_agent(db, ctx=ctx, text=text, settings=settings)
        if new_agent is not None:
            created_today += 1
            picks.append(_agent_pick(new_agent, SOURCE_GENERATED))
            continue

        # 自动创建失败：回退到最接近的已发布候选
        picks.append(_closest_pick(text, candidates, stats))
    return picks


async def create_remediation_agent(
    db: Session, *, ctx: YardContext, text: str, settings: dict[str, Any]
) -> YardAgent | None:
    """自主补救用：按失败子任务再生成一个可用助手；受开关与每日上限约束。"""
    if not settings.get("auto_create_agent"):
        return None
    limit = int(settings.get("daily_agent_limit") or DEFAULT_DAILY_AGENT_LIMIT)
    if daily_created_count(db, ctx.yard_id) >= limit:
        log.warning("commander remediation skipped: daily agent limit reached")
        return None
    utterance = (
        f"完成子任务：{text.strip()[:600]}。"
        "请设计一个能独立完成该任务的助手，优先绑定搜索、文档生成等可自主执行的能力。"
    )
    return await _create_agent(db, ctx=ctx, text=utterance, settings=settings)


__all__ = [
    "DEFAULT_DAILY_AGENT_LIMIT",
    "REUSE_THRESHOLD",
    "agent_stats",
    "agent_text",
    "assign_agents",
    "best_candidate",
    "create_remediation_agent",
    "daily_created_count",
    "find_similar_agent",
    "keywords",
    "lookup_by_code",
    "match_candidates",
    "rerank_by_llm",
    "score_agent",
    "similarity",
    "subtask_text",
]
