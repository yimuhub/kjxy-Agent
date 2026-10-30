"""一句话生成智能体：检索 → 定框架 → 校验 → 匹配技能 → 落草稿（v3.5）。

与技能/插件生成同构，但产出的是**可运行的智能体草稿**（``yard_agents``，
status=draft，不自动发布），并额外做两件事：

1. 固定框架校验（``agent_spec``）：六段结构、工具白名单、服务端复算风险；
2. 技能匹配：把模型声明的 ``skill_needs`` 与本院可绑定技能/插件做关键词打分，
   命中项自动写进 ``bound_skills``，未命中项落 ``skill_requirements``（待补清单），
   前端可见、可人工补齐——「配不上就留空」在这里落地。

异步执行由 ``agent_generate_tasks`` 承担：请求只建任务并立即返回，里程碑写
task_events，前端用现成的 /tasks/{id}/events/stream 追进度。
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import agent_spec as spec_mod
from app.modules.agents import bindable, declarative
from app.modules.agents.declarative import DeclarativeAgentCreate
from app.modules.agents.models import YardAgent
from app.modules.audit.service import log_audit
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.search.service import search_web
from app.modules.tasks.models import Task
from app.modules.yards.settings_service import get_setting

log = get_logger(__name__)

#: 小院设置键（配额/开关/联网）
SETTING_KEY = "agent_generate"
#: 任务类型（tasks 模块注册键；配额按它计数、任务中心按它识别）
TASK_TYPE = "agent_generate"

MAX_LLM_ATTEMPTS = 3
MAX_SOURCES = 5
MAX_MATCHES = 5
#: 关键词命中分数下限（一次 3 字命中，或两次 2 字命中）
MATCH_THRESHOLD = 3

#: 生成阶段（task_events 里程碑 + 前端进度条共用）
STAGE_RETRIEVING = "retrieving"
STAGE_DESIGNING = "designing"
STAGE_MATCHING = "matching"
STAGE_PERSISTING = "persisting"

StageCallback = Callable[[str, str, float], None]

_WORD_RE = re.compile(r"[a-z0-9_]{2,}")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_JSON_RE = re.compile(r"\{.*\}", re.S)


# ---------------------------------------------------------------------------
# 设置与配额
# ---------------------------------------------------------------------------


def _settings(db: Session, yard_id: str) -> dict[str, Any]:
    try:
        cfg = get_setting(db, yard_id, SETTING_KEY)
    except Exception:  # pragma: no cover - 设置表不可用时按默认放行
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _daily_limit(settings: dict[str, Any]) -> int:
    try:
        return max(0, int(settings.get("daily_limit") or 0))
    except (TypeError, ValueError):
        return 0


def _day_start() -> datetime:
    now = datetime.now(UTC)
    return datetime.combine(now.date(), time.min, tzinfo=UTC)


def used_today(db: Session, yard_id: str, user_id: str) -> int:
    """今日该成员已发起的生成次数（按 tasks 行计数，跨进程一致）。"""
    return int(
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(
                Task.yard_id == yard_id,
                Task.created_by == user_id,
                Task.task_type == TASK_TYPE,
                Task.created_at >= _day_start(),
            )
        )
        or 0
    )


def quota_snapshot(db: Session, yard_id: str, user_id: str) -> dict[str, Any]:
    settings = _settings(db, yard_id)
    limit = _daily_limit(settings)
    used = used_today(db, yard_id, user_id)
    return {
        "enabled": settings.get("enabled", True) is not False,
        "use_web": settings.get("use_web", True) is not False,
        "used": used,
        "limit": limit,
        "remaining": max(0, limit - used) if limit else -1,
    }


def ensure_daily_quota(db: Session, yard_id: str, user_id: str) -> dict[str, Any]:
    """每日每人生成上限（0 = 不限）；超限 429。"""
    snapshot = quota_snapshot(db, yard_id, user_id)
    if not snapshot["enabled"]:
        raise AppError(
            "AGENT_GENERATE_DISABLED",
            "本小院已关闭一句话生成智能体服务。",
            http_status=403,
        )
    limit = snapshot["limit"]
    if limit and snapshot["used"] >= limit:
        raise AppError(
            "AGENT_GENERATE_QUOTA",
            f"今日智能体生成次数已用尽（{snapshot['used']}/{limit}）。",
            http_status=429,
            details={"limit": limit, "used": snapshot["used"]},
        )
    return snapshot


# ---------------------------------------------------------------------------
# 检索与匹配
# ---------------------------------------------------------------------------


def _keywords(text: str) -> list[str]:
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
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


#: 领域同义词：用户说「农机维修」，技能名叫「农机保养」也要能命中。
#: 只做“词 → 同领域词”的确定性扩展，不引入模型判断，结果可复核。
DOMAIN_SYNONYMS: dict[str, tuple[str, ...]] = {
    "维修": ("保养", "检修", "故障", "易损件"),
    "检修": ("保养", "维修", "故障"),
    "故障": ("维修", "保养", "检修"),
    "拖拉机": ("农机", "保养"),
    "收割机": ("农机", "保养"),
    "插秧机": ("农机", "保养"),
    "播种机": ("农机", "保养"),
    "旋耕机": ("农机", "保养"),
    "水泵": ("灌溉", "农机"),
    "滴灌": ("灌溉", "节水"),
    "喷灌": ("灌溉", "节水"),
    "浇水": ("灌溉", "墒情"),
    "打药": ("农药", "病虫害", "防治"),
    "喷药": ("农药", "病虫害"),
    "施肥": ("营养", "缺素"),
    "缺素": ("营养", "施肥"),
    "病害": ("病虫害", "诊断"),
    "虫害": ("病虫害", "防治"),
    "稻瘟病": ("病虫害", "诊断"),
    "纹枯病": ("病虫害", "诊断"),
    "稻飞虱": ("病虫害", "诊断"),
    "防冻": ("气候", "低温", "灾害"),
    "台风": ("气候", "大风", "灾害"),
    "冰雹": ("气候", "灾害"),
    "干旱": ("气候", "灌溉", "灾害"),
    "渍涝": ("气候", "排水", "灾害"),
    "采摘": ("采收", "保鲜"),
    "储藏": ("贮运", "保鲜"),
    "冷链": ("贮运", "保鲜"),
    "行情": ("市场", "询价"),
    "收购": ("收购", "市场", "询价"),
    "补贴": ("政策", "申报"),
    "惠农": ("政策", "申报"),
    "台账": ("记录", "可追溯"),
    "巡田": ("田间", "记录"),
    "农时": ("农事", "日历"),
}


def _expand_keywords(keywords: list[str]) -> list[str]:
    """按领域同义词扩展关键词（保序去重），让「维修」能命中「保养」类技能。"""
    out: list[str] = []
    seen: set[str] = set()
    for kw in keywords:
        for token in (kw, *DOMAIN_SYNONYMS.get(kw, ())):
            if token and token not in seen:
                seen.add(token)
                out.append(token)
    return out


def _hay(entry: dict[str, Any]) -> str:
    tags = entry.get("tags") or []
    tag_text = " ".join(str(tag) for tag in tags) if isinstance(tags, list) else ""
    return f"{entry.get('name') or ''} {entry.get('summary') or ''} {tag_text}".lower()


def _score(entry: dict[str, Any], keywords: list[str]) -> int:
    hay = _hay(entry)
    return sum(len(kw) for kw in keywords if kw in hay)


def _tool_snapshot() -> dict[str, bool]:
    """工具注册表快照：code → 是否只读（只用于校验与提示词白名单）。"""
    try:
        from app.modules.tools.registry import tool_registry
    except Exception as e:  # pragma: no cover - 注册表不可用时只允许纯提示词
        log.warning("工具注册表不可用，生成仅允许纯提示词智能体：%s", e)
        return {}
    snapshot: dict[str, bool] = {}
    for tool in tool_registry.list_tools():
        snapshot[tool.code] = tool.risk_level == "read_only" and not tool.side_effect
    return snapshot


def _catalog_text(extensions: dict[str, list[dict[str, Any]]]) -> str:
    lines: list[str] = []
    for kind, label in (("skills", "技能"), ("plugins", "插件")):
        for entry in extensions.get(kind) or []:
            summary = str(entry.get("summary") or "")[:60]
            lines.append(f"- {label}：{entry.get('name')}（{summary}）")
    return "\n".join(lines[:40])


def _pool(extensions: dict[str, list[dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    return [("skill", e) for e in extensions.get("skills") or []] + [
        ("plugin", e) for e in extensions.get("plugins") or []
    ]


def _need_list(spec: spec_mod.AgentSpec, utterance: str) -> list[dict[str, Any]]:
    """模型没给 skill_needs 时，用原始需求兜一个「通用能力」去匹配，避免完全空手。"""
    if spec.skill_needs:
        return [need.model_dump() for need in spec.skill_needs]
    keywords = _keywords(utterance)[:6]
    if not keywords:
        return []
    return [{"need": "与需求直接相关的能力", "keywords": keywords, "reason": "按需求关键词匹配"}]


def match_skills(
    extensions: dict[str, list[dict[str, Any]]],
    needs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按需求逐条匹配可绑定技能/插件；命中自动绑定，未命中进待补清单。"""
    pool = _pool(extensions)
    matched: list[dict[str, Any]] = []
    requirements: list[dict[str, Any]] = []
    used: set[str] = set()
    for need in needs:
        keywords = _expand_keywords(
            [str(kw).lower() for kw in (need.get("keywords") or []) if len(str(kw)) >= 2]
        )
        best: tuple[int, str, dict[str, Any]] | None = None
        for kind, entry in pool:
            code = str(entry.get("code") or "")
            if not code or code in used:
                continue
            score = _score(entry, keywords)
            if best is None or score > best[0]:
                best = (score, kind, entry)
        if best is not None and best[0] >= MATCH_THRESHOLD and len(matched) < MAX_MATCHES:
            score, kind, entry = best
            code = str(entry.get("code"))
            used.add(code)
            matched.append(
                {
                    "need": str(need.get("need") or ""),
                    "code": code,
                    "name": str(entry.get("name") or ""),
                    "kind": kind,
                    "summary": str(entry.get("summary") or ""),
                    "source": str(entry.get("source") or ""),
                    "score": score,
                    "reason": str(need.get("reason") or ""),
                }
            )
        else:
            requirements.append(
                {
                    "need": str(need.get("need") or ""),
                    "keywords": [str(kw) for kw in (need.get("keywords") or [])],
                    "reason": str(need.get("reason") or ""),
                    "status": "missing",
                }
            )
    return matched, requirements


def request_agent(db: Session, *, ctx: YardContext, utterance: str) -> dict[str, Any]:
    """一句话预览（不调 LLM）：意图关键词 + 现有技能候选 + 配额快照。"""
    text = (utterance or "").strip()
    if len(text) < 4:
        raise AppError("INVALID_REQUEST", "请用一句话描述你需要的智能体。", http_status=400)

    extensions = bindable.list_bindable_extensions(db, ctx.yard_id)
    keywords = _expand_keywords(_keywords(text))
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for kind, entry in _pool(extensions):
        score = _score(entry, keywords)
        if score > 0:
            scored.append((score, kind, entry))
    scored.sort(key=lambda row: (-row[0], str(row[2].get("name") or "")))
    matches = [
        {
            "code": str(entry.get("code")),
            "name": str(entry.get("name") or ""),
            "kind": kind,
            "summary": str(entry.get("summary") or ""),
            "source": str(entry.get("source") or ""),
            "score": score,
        }
        for score, kind, entry in scored[:MAX_MATCHES]
    ]
    note = (
        f"平台现有 {len(matches)} 个可能相关的技能/插件，生成时会按职责自动匹配。"
        if matches
        else "平台暂未发现直接相关的技能，生成后可在「待补技能」里人工补充。"
    )
    return {
        "utterance": text,
        "keywords": keywords[:12],
        "matches": matches,
        # 前端按同一阈值判断“可直接自动绑定”，避免两边口径漂移
        "threshold": MATCH_THRESHOLD,
        "note": note,
        "policy": quota_snapshot(db, ctx.yard_id, ctx.user_id),
    }


# ---------------------------------------------------------------------------
# 取证与规格生成
# ---------------------------------------------------------------------------


async def _collect_sources(
    db: Session, yard_id: str, query: str, limit: int
) -> tuple[list[dict[str, str]], bool]:
    """联网取证；失败降级为（空来源, offline=True），不阻断生成。"""
    if not query or limit <= 0:
        return [], False
    try:
        data = await search_web(db, yard_id, query, count=limit)
    except AppError as e:
        log.info("智能体生成参考来源检索失败（降级离线）：%s", e.message)
        return [], True
    except Exception as e:  # 防御：网络层异常不影响生成
        log.warning("agent source search failed: %s", e)
        return [], True
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for hit in data.get("results") or []:
        url = str(hit.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        rows.append({"title": str(hit.get("title") or url)[:200], "url": url[:500]})
        if len(rows) >= limit:
            break
    return rows, False


def _parse_json_object(content: str) -> dict[str, Any] | None:
    match = _JSON_RE.search(content or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def _build_spec(
    db: Session,
    ctx: YardContext,
    utterance: str,
    sources: list[dict[str, str]],
    tools: dict[str, bool],
    catalog: str,
) -> tuple[spec_mod.AgentSpec, dict[str, Any], str, str]:
    """产出 (规格, token 用量, 模型, 备注)。

    - 模型有响应但连续不合规：落确定性兜底模板（备注提示人工复核）；
    - 模型完全不可用（一次都没成功）：抛 502，不产出无意义的通用草稿。
    """
    system, user = spec_mod.build_spec_prompt(utterance, tools, sources, catalog)
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]
    usage: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model": "agent_generator",
    }
    model_name = ""
    last_error = ""
    responded = False

    for attempt in range(MAX_LLM_ATTEMPTS):
        try:
            resp = await complete_with_logging(
                db,
                LLMRequest(
                    messages=messages,
                    metadata=LLMMetadata(
                        yard_id=ctx.yard_id,
                        task_id=f"agent-gen-{uuid.uuid4().hex[:12]}",
                        request_id=ctx.request_id,
                        purpose="agent_generation",
                        agent_ref="agent_builder",
                    ),
                ),
            )
        except AppError as e:
            log.warning("智能体规格生成失败（第 %d 次）：%s", attempt + 1, e.message)
            last_error = e.message
            break
        responded = True
        usage["input_tokens"] += int(resp.input_tokens or 0)
        usage["output_tokens"] += int(resp.output_tokens or 0)
        model_name = resp.model or model_name
        usage["model"] = model_name or usage["model"]

        parsed = _parse_json_object(resp.content or "")
        if parsed is None:
            last_error = "模型输出不是合法 JSON 对象。"
            messages.append(ChatMessage(role="assistant", content=(resp.content or "")[:2000]))
            messages.append(
                ChatMessage(
                    role="user",
                    content=f"上一条输出无法解析：{last_error}。请只输出合法 JSON 对象。",
                )
            )
            continue
        try:
            spec = spec_mod.validate_agent_spec(parsed, available_tools=tools)
        except AppError as e:
            last_error = e.message
            messages.append(
                ChatMessage(role="assistant", content=json.dumps(parsed, ensure_ascii=False)[:2000])
            )
            messages.append(
                ChatMessage(
                    role="user",
                    content=f"上一条规格未通过校验：{last_error}。请修正后只输出 JSON 对象。",
                )
            )
            continue
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        return spec, usage, model_name, ""

    usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    if not responded:
        raise AppError(
            "AGENT_GENERATE_LLM_UNAVAILABLE",
            f"模型调用失败，未生成任何草稿：{last_error or '供应商不可用'}",
            http_status=502,
            details={"reason": last_error},
        )
    log.info("智能体规格连续不合规，降级为兜底模板：%s", last_error)
    fallback = spec_mod.fallback_spec(utterance, available_tools=tools)
    return (
        fallback,
        usage,
        model_name,
        "模型未产出合规规格，已按保守模板生成草稿，请人工复核后再发布。",
    )


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------


def _unique_code(db: Session, yard_id: str, base: str) -> str:
    candidate = base
    index = 1
    while True:
        exists = (
            db.query(YardAgent.id)
            .filter(YardAgent.yard_id == yard_id)
            .filter(YardAgent.agent_code == candidate)
            .first()
        )
        if exists is None:
            return candidate
        index += 1
        candidate = f"{base}_{index}"
        if index > 50:
            return f"{base}_{uuid.uuid4().hex[:6]}"


def _unique_name(db: Session, yard_id: str, name: str) -> str:
    candidate = name[:100]
    index = 2
    while True:
        exists = (
            db.query(YardAgent.id)
            .filter(YardAgent.yard_id == yard_id)
            .filter(YardAgent.name == candidate)
            .first()
        )
        if exists is None:
            return candidate
        suffix = f"-{index}"
        candidate = f"{name[: 100 - len(suffix)]}{suffix}"
        index += 1
        if index > 50:
            return f"{name[:90]}-{uuid.uuid4().hex[:4]}"


def ensure_visibility_allowed(*, visibility: str | None, roles: list[str]) -> str:
    """入队前判权：管理员可建 public，成员只能建 private（与手工创建口径一致）。"""
    is_admin = bool({"platform_admin", "yard_admin"} & set(roles))
    if visibility == "public" and not is_admin:
        raise AppError(
            "FORBIDDEN",
            "将智能体设为公开需要小院管理员权限；普通成员只能生成私有智能体。",
            http_status=403,
        )
    if visibility == "private":
        return "private"
    return "public" if is_admin else "private"


async def generate_agent(
    db: Session,
    *,
    ctx: YardContext,
    utterance: str,
    visibility: str,
    use_web: bool = True,
    supplier_id: str | None = None,
    on_stage: StageCallback | None = None,
) -> dict[str, Any]:
    """一句话生成智能体草稿（不发布）；返回预览数据供任务结果与前端展示。"""
    text = (utterance or "").strip()
    if len(text) < 4:
        raise AppError("INVALID_REQUEST", "请用一句话描述你需要的智能体。", http_status=400)

    settings = _settings(db, ctx.yard_id)
    if settings.get("enabled", True) is False:
        raise AppError(
            "AGENT_GENERATE_DISABLED", "本小院已关闭一句话生成智能体服务。", http_status=403
        )
    daily_limit = _daily_limit(settings)
    used = used_today(db, ctx.yard_id, ctx.user_id)
    if daily_limit and used >= daily_limit:
        raise AppError(
            "AGENT_GENERATE_QUOTA",
            f"今日智能体生成次数已用尽（{used}/{daily_limit}）。",
            http_status=429,
            details={"limit": daily_limit, "used": used},
        )

    _emit(on_stage, STAGE_RETRIEVING, "正在检索公开资料", 0.15)
    query = " ".join(_keywords(text)[:8]) or text
    web_enabled = use_web and settings.get("use_web", True) is not False
    sources, offline = await _collect_sources(db, ctx.yard_id, query, MAX_SOURCES)
    if not web_enabled:
        offline = True

    extensions = bindable.list_bindable_extensions(db, ctx.yard_id)
    tools = _tool_snapshot()
    _emit(on_stage, STAGE_DESIGNING, "正在按固定框架设计职责与工作流", 0.35)
    spec, usage, model_name, note = await _build_spec(
        db, ctx, text, sources, tools, _catalog_text(extensions)
    )

    _emit(on_stage, STAGE_MATCHING, "正在匹配本院已有技能", 0.7)
    needs = _need_list(spec, text)
    matched, requirements = match_skills(extensions, needs)

    _emit(on_stage, STAGE_PERSISTING, "正在保存草稿", 0.9)
    agent = declarative.create_agent(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        req=DeclarativeAgentCreate(
            agent_code=_unique_code(db, ctx.yard_id, spec.agent_code),
            name=_unique_name(db, ctx.yard_id, spec.name),
            description=spec.description,
            emoji=spec.emoji,
            persona_prompt=spec.persona_prompt,
            task_prompt_template=spec.task_prompt_template,
            allowed_tools=list(spec.allowed_tools),
            bound_skills=[m["code"] for m in matched],
            knowledge_scopes=list(spec.knowledge_scopes),
            supplier_id=supplier_id,
            created_from="generated",
            visibility=visibility,
        ),
        request_id=ctx.request_id,
        # 入队前已按 roles 判过 visibility；此处显式放行，避免二次误判
        roles=None,
    )
    agent.blueprint_json = spec_mod.blueprint_payload(
        spec,
        sources=sources,
        offline=offline,
        model=model_name,
        note=note,
    )
    agent.skill_requirements = requirements
    db.flush()

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="agent_generated",
        resource_type="agent",
        resource_id=agent.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "agent_code": agent.agent_code,
            "model": model_name,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "source_count": len(sources),
            "matched_skills": len(matched),
            "missing_skills": len(requirements),
            "offline": offline,
        },
    )
    return {
        "agent": declarative.to_out(agent, db),
        "spec": spec.model_dump(),
        "matched": matched,
        "skill_requirements": requirements,
        "sources": sources,
        "offline": offline,
        "note": note,
        "token_usage": usage,
    }


def _emit(cb: StageCallback | None, stage: str, message: str, progress: float) -> None:
    if cb is None:
        return
    try:
        cb(stage, message, progress)
    except Exception as e:  # pragma: no cover - 进度回调失败不得影响生成
        log.warning("agent generate stage callback failed: %s", e)
