"""一句话技能链路（v3.2）：检索匹配 → 联网找资料 → 模型产出规格 → 私有草稿。

四条入口（前端 ``skillLabApi`` 与对话卡片共用同一套实现）：

- ``request_skill``   → ``POST /market/agent/request``：检索本地内置/缓存/GitHub 技能；
- ``generate_skill``  → ``POST /market/agent/generate``：生成技能并落为私有草稿；
- ``install_skill``   → ``POST /market/agent/install``：安装检索到的条目；
- ``handle_skill_request``：对话链路入口（``chat/service.py`` 调用），一次完成
  "先检索、无匹配再生成"。

安全边界（沿用既有权限与审核口径，不开旁路）：

- 生成结果一律落为**私有草稿**（``visibility=private`` + ``owner_user_id``=当前用户），
  必须经人工审核（``submit_public``）才能进小院公共库；
- 规格必须通过 ``skill_template.validate_skill_spec``：未知字段拒绝、工具必须在
  注册表内、引用副作用工具强制 ``risk_level=high`` + ``requires_review``；
- 工具白名单取自工具注册表快照，模型不得凭空发明工具；
- 日配额取小院设置 ``skill_lab.daily_limit``，超限直接拒绝；
- 全链路留审计 ``skill_generated``（模型/来源数/是否需复核）。
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.progress import (
    PHASE_GENERATING,
    PHASE_RETRIEVING,
    STEP_DONE,
    STEP_FAILED,
    STEP_RUNNING,
)
from app.modules.agents.bindable import list_bindable_extensions
from app.modules.audit.service import log_audit
from app.modules.evolution.models import Skill
from app.modules.evolution.service import create_skill
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging
from app.modules.market import installer
from app.modules.market.github_source import search_github_skills
from app.modules.market.skill_template import SkillSpec, dump_markdown, validate_skill_spec
from app.modules.search.service import search_web
from app.modules.yards import redis_quota
from app.modules.yards.settings_service import get_setting

log = get_logger(__name__)

#: 卡片类型与状态（与 frontend/src/api/chat.ts::ChatSkillCard 一致）
CARD_TYPE = "skill_card"
STATE_MATCHED = "matched"
STATE_CREATED = "created"
STATE_FAILED = "failed"

#: 一次生成的模型调用上限（首次 + 2 次纠错重试）
MAX_LLM_ATTEMPTS = 3
#: 候选技能条数上限
MAX_MATCHES = 8
#: 联网检索参考来源条数上限
MAX_SOURCES = 6
#: 生成技能的默认许可
DEFAULT_LICENSE = "MIT"
#: 生成技能的默认分类
DEFAULT_CATEGORY = "other"

_JSON_RE = re.compile(r"\{.*\}", re.S)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_.+-]*")

#: 中文口语填充词（长词优先）：清洗检索词，避免整句进检索导致零命中
_FILLERS: tuple[str, ...] = (
    "帮我设计一个",
    "帮我生成一个",
    "帮我创建一个",
    "帮我做一个",
    "帮我建一个",
    "我想要一个",
    "我需要一个",
    "给我来一个",
    "我需要",
    "我要一个",
    "请问",
    "有没有",
    "帮我",
    "帮忙",
    "来个",
    "做一个",
    "建一个",
    "建个",
    "生成一个",
    "生成个",
    "创建一个",
    "生成",
    "创建",
    "设计",
    "搭建",
    "技能包",
    "技能",
    "skill",
    "智能体",
    "助手",
    "agent",
    "插件",
    "plugin",
    "一个",
    "的",
    "了",
)


# ---------------------------------------------------------------------------
# 检索词清洗与打分
# ---------------------------------------------------------------------------


def _clean_text(text: str) -> str:
    """去掉口语填充词后的检索词（中文按空格切分，英文保留原词）。"""
    raw = (text or "").strip().lower()
    for filler in _FILLERS:
        raw = raw.replace(filler, " ")
    return " ".join(raw.split())


def _keywords(text: str) -> list[str]:
    """检索关键词：英文词 + 中文连续串 + 中文 2/3-gram（中文无空格分词）。"""
    cleaned = _clean_text(text)
    tokens: list[str] = []
    for match in _WORD_RE.finditer(cleaned):
        if len(match.group(0)) >= 2:
            tokens.append(match.group(0))
    for match in _CJK_RE.finditer(cleaned):
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


def _score(item: dict[str, Any], keywords: list[str]) -> float:
    """候选打分：命中关键词的字符数之和（长词权重更高）。"""
    hay = " ".join(str(item.get(key) or "") for key in ("name", "description", "code")).lower()
    if not hay.strip():
        return 0.0
    return float(sum(len(kw) for kw in keywords if kw in hay))


def _normalize(scored: list[tuple[float, dict[str, Any]]]) -> list[tuple[float, dict[str, Any]]]:
    """把打分归一化到 (0, 1]，最高分记 100%（前端按百分比展示匹配度）。"""
    if not scored:
        return []
    top = max(score for score, _ in scored)
    if top <= 0:
        return scored
    return [(score / top, item) for score, item in scored]


def _match_payload(item: dict[str, Any], score: float) -> dict[str, Any]:
    """候选条目 → 前端 SkillMatch 形状。"""
    return {
        "item_key": str(item.get("item_key") or ""),
        "name": str(item.get("name") or item.get("code") or ""),
        "summary": str(item.get("description") or ""),
        "source": str(item.get("source") or ""),
        "license": item.get("license"),
        "risk_level": str(item.get("risk_level") or ""),
        "installed": bool(item.get("installed")),
        "score": round(score, 3),
    }


# ---------------------------------------------------------------------------
# 小院设置与配额
# ---------------------------------------------------------------------------

#: 进程内日配额计数（小院 × 日期），与搜索配额同口径
_quota: dict[tuple[str, str], int] = {}


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _lab_settings(db: Session, yard_id: str) -> dict[str, Any]:
    try:
        cfg = get_setting(db, yard_id, "skill_lab")
    except Exception:  # pragma: no cover - 设置表不可用时按默认放行
        return {}
    return cfg if isinstance(cfg, dict) else {}


def _daily_limit(db: Session, yard_id: str) -> int:
    """每日生成上限（0 = 不限）。"""
    try:
        return max(0, int(_lab_settings(db, yard_id).get("daily_limit") or 0))
    except (TypeError, ValueError):
        return 0


def _max_sources(settings: dict[str, Any]) -> int:
    try:
        return max(0, min(int(settings.get("max_sources") or MAX_SOURCES), MAX_SOURCES))
    except (TypeError, ValueError):
        return MAX_SOURCES


def quota_snapshot(db: Session, yard_id: str) -> dict[str, Any]:
    """今日生成配额快照（Redis 跨进程计数优先，回落进程内 dict）。"""
    limit = _daily_limit(db, yard_id)
    redis_used = redis_quota.get_daily(f"skill_gen:{yard_id}")
    used = redis_used if redis_used is not None else _quota.get((yard_id, _today()), 0)
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


def _ensure_quota(yard_id: str, limit: int) -> None:
    redis_used = redis_quota.get_daily(f"skill_gen:{yard_id}")
    used = redis_used if redis_used is not None else _quota.get((yard_id, _today()), 0)
    if limit > 0 and used >= limit:
        raise AppError(
            "SKILL_QUOTA_EXCEEDED",
            "今日技能生成配额已用尽。",
            http_status=429,
            details={"limit": limit, "used": used, "date": _today()},
        )


def _consume_quota(yard_id: str) -> None:
    key = (yard_id, _today())
    redis_value = redis_quota.incr_daily(f"skill_gen:{yard_id}")
    if redis_value is not None:
        _quota[key] = redis_value
        return
    _quota[key] = _quota.get(key, 0) + 1


# ---------------------------------------------------------------------------
# 工具注册表快照
# ---------------------------------------------------------------------------


def _available_tools() -> dict[str, bool]:
    """工具注册表快照：code → 是否只读（副作用工具为 False）。"""
    try:
        from app.modules.tools.registry import tool_registry
    except Exception as e:  # pragma: no cover - 注册表不可用时只允许纯提示词技能
        log.warning("工具注册表不可用，技能生成仅允许纯提示词：%s", e)
        return {}
    snapshot: dict[str, bool] = {}
    for tool in tool_registry.list_tools():
        snapshot[tool.code] = tool.risk_level == "read_only" and not tool.side_effect
    return snapshot


# ---------------------------------------------------------------------------
# 候选检索
# ---------------------------------------------------------------------------


def _local_scored(
    db: Session, ctx: YardContext, keywords: list[str]
) -> list[tuple[float, dict[str, Any]]]:
    """本地内置 + 市场缓存条目的打分结果。"""
    if not keywords:
        return []
    result = installer.search_items(db, ctx, query="", item_type="skill", limit=50)
    scored = [(_score(item, keywords), item) for item in (result.get("items") or [])]
    return [(score, item) for score, item in scored if score > 0]


def select_relevant_skill_codes(
    db: Session, *, ctx: YardContext, text: str, limit: int = 3
) -> list[dict[str, str]]:
    """在本小院**当前可用**的技能里按关键词挑几条（只查本地库，不联网、不装新技能）。

    返回的 code 就是绑定编码契约（``skill:<id>``），可直接交给
    ``declarative.skills_prompt_for`` 生成不可信包裹的 SOP 指令块。
    """
    keywords = _keywords(text)
    if not keywords:
        return []
    entries = list_bindable_extensions(db, ctx.yard_id).get("skills") or []
    scored: list[tuple[float, dict[str, Any]]] = []
    for entry in entries:
        haystack = f"{entry.get('name') or ''} {entry.get('summary') or ''}".lower()
        score = float(sum(len(kw) for kw in keywords if kw in haystack))
        if score > 0:
            scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("name") or "")))
    return [
        {
            "code": str(entry.get("code") or ""),
            "name": str(entry.get("name") or entry.get("code") or ""),
        }
        for _, entry in scored[:limit]
    ]


def _find_matches(
    db: Session, ctx: YardContext, text: str, limit: int = MAX_MATCHES
) -> tuple[list[dict[str, Any]], bool, str | None]:
    """检索可安装技能：本地优先，无命中再试 GitHub（联网失败即离线降级）。"""
    keywords = _keywords(text)
    scored = _local_scored(db, ctx, keywords)
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("name") or "")))
    if scored:
        top = [_match_payload(item, score) for score, item in _normalize(scored)[:limit]]
        return top, False, None

    query = _clean_text(text)
    if not query:
        return [], False, None
    try:
        rows = installer.cache_github_candidates(db, search_github_skills(query, limit=5))
    except AppError as e:
        return [], True, f"联网检索暂不可用，已回退本地技能库：{e.message}"
    except Exception as e:  # 防御：网络层异常不得影响本地检索
        log.warning("github skill search failed: %s", e)
        return [], True, "联网检索暂不可用，已回退本地技能库。"
    remote = [
        (_score(item, keywords), item) for item in (installer.serialize_item(row) for row in rows)
    ]
    remote = [(score, item) for score, item in remote if score > 0]
    remote.sort(key=lambda pair: (-pair[0], str(pair[1].get("name") or "")))
    return [_match_payload(item, score) for score, item in _normalize(remote)[:limit]], False, None


# ---------------------------------------------------------------------------
# 参考来源
# ---------------------------------------------------------------------------


async def _collect_sources(
    db: Session, ctx: YardContext, query: str, limit: int
) -> list[dict[str, str]]:
    """联网检索参考来源；失败降级为空（离线不阻断生成）。"""
    if not query or limit <= 0:
        return []
    try:
        data = await search_web(db, ctx.yard_id, query, count=limit)
    except AppError as e:
        log.info("技能生成参考来源检索失败（降级为空）：%s", e.message)
        return []
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for hit in data.get("results") or []:
        url = str(hit.get("url") or "").strip()
        if not url.startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        rows.append({"title": str(hit.get("title") or url)[:200], "url": url[:500]})
    return rows


# ---------------------------------------------------------------------------
# 规格生成
# ---------------------------------------------------------------------------


def _parse_json_object(content: str) -> dict[str, Any] | None:
    """从模型输出里抠出 JSON 对象；不是合法对象返回 None。"""
    match = _JSON_RE.search(content or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _slug_code(text: str) -> str:
    """把任意文本压成合法技能编码（^[a-z][a-z0-9_]{2,60}$）；无 ASCII 时兜底。"""
    slug = re.sub(r"[^a-z0-9_]+", "_", (text or "").lower()).strip("_")
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,60}", slug):
        return f"skill_{uuid.uuid4().hex[:8]}"
    return slug


def _fallback_spec(
    utterance: str,
    sources: list[dict[str, str]],
    available_tools: dict[str, bool],
) -> dict[str, Any]:
    """模型不可用/输出不合规时的确定性兜底规格（内容保守，可人工修订）。"""
    title = " ".join((utterance or "").split())[:40] or "新建技能"
    tools = [code for code in ("knowledge.search",) if code in available_tools]
    return {
        "code": _slug_code(title),
        "name": title,
        "version": "1.0.0",
        "category": DEFAULT_CATEGORY,
        "summary": f"面向「{title}」的农业技能草稿。",
        "when_to_use": f"用户提出与「{title}」相关的需求时使用。",
        "inputs": [],
        "steps": [
            {
                "kind": "prompt",
                "title": "澄清需求",
                "text": f"先确认与「{title}」相关的地块、生育期与现场信息。",
            },
            {
                "kind": "llm",
                "title": "给出建议",
                "text": "结合小院知识库证据给出可执行建议，并标注依据来源。",
            },
        ],
        "allowed_tools": tools,
        "knowledge_scopes": ["yard"],
        "prompt": (
            f"你是「{title}」技能助手。先澄清关键信息，再结合小院知识库证据给出"
            "结论先行、可执行的专业建议；资料不足时明确说明缺哪些数据以及如何补测，"
            "不编造数值。"
        ),
        "examples": [],
        "eval_cases": [
            {"input": f"请给出{title}的处理建议", "expect": "结论先行、给出依据与下一步动作"},
            {"input": "所需资料不足时如何回答", "expect": "明确列出缺失数据与补测方法，不编造数值"},
        ],
        "guardrails": {
            "risk_level": "low",
            "max_steps": 5,
            "require_citation": bool(sources),
        },
        "output_format": "markdown",
        "sources": list(sources),
        "tags": [],
        "license": DEFAULT_LICENSE,
    }


def _spec_prompt(
    utterance: str, available_tools: dict[str, bool], sources: list[dict[str, str]]
) -> tuple[str, str]:
    """生成提示词：(system, user)。"""
    tool_hint = (
        "、".join(sorted(available_tools)) or "（当前没有可用工具，allowed_tools 必须留空数组）"
    )
    source_block = (
        "\n".join(f"- {s['title']} {s['url']}" for s in sources)
        if sources
        else "（没有可用参考来源）"
    )
    system = (
        "你是农业技能设计器。把用户需求转成一个技能规格，只输出一个 JSON 对象，"
        "不要输出 Markdown 代码块围栏，也不要输出任何解释文字。\n"
        "字段（缺一不可，出现未知字段会被服务端拒绝）：\n"
        '{"code":"小写英文标识，^[a-z][a-z0-9_]{2,60}$，不得以 builtin/system/admin 开头",'
        '"name":"中文技能名，1-80 字","version":"1.0.0",'
        '"category":"plant_protection|pest_control|cultivation|water_fertilizer|machinery|market|other",'
        '"summary":"一句话摘要，1-300 字","when_to_use":"适用时机，1-500 字",'
        '"inputs":[{"key":"plot_id","label":"地块编号","required":true,"type":"string"}],'
        '"steps":[{"kind":"prompt","title":"澄清症状","text":"..."}],'
        '"allowed_tools":[],"knowledge_scopes":["yard"],'
        '"prompt":"系统提示词，1-4000 字",'
        '"examples":[{"question":"...","answer_outline":"..."}],'
        '"eval_cases":[{"input":"...","expect":"..."}],'
        '"guardrails":{"risk_level":"low","max_steps":5,"require_citation":false},'
        '"output_format":"markdown","sources":[{"title":"...","url":"https://..."}],'
        '"tags":[],"license":"MIT"}\n'
        "硬约束：steps 1-10 条（kind=tool 必须带 tool 且该工具真实存在，"
        "kind=prompt|llm 必须带 text）；eval_cases 至少 2 条；allowed_tools 只能取自 "
        f"{tool_hint}；sources 为空时 guardrails.require_citation 必须为 false。"
    )
    user = f"用户需求：{(utterance or '').strip()[:1000]}\n" f"可参考的公开资料：\n{source_block}"
    return system, user


async def _build_spec(
    db: Session,
    ctx: YardContext,
    utterance: str,
    sources: list[dict[str, str]],
    available_tools: dict[str, bool],
) -> tuple[SkillSpec, dict[str, Any], str, str]:
    """产出技能规格：(spec, token_usage, model, note)。

    模型输出不合规时最多纠错 2 次；仍不合规则降级到确定性兜底模板，
    保证功能可用（内容保守，提示人工复核）。
    """
    system, user = _spec_prompt(utterance, available_tools, sources)
    messages = [
        ChatMessage(role="system", content=system),
        ChatMessage(role="user", content=user),
    ]
    usage: dict[str, Any] = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "model": "skill_generator",
    }
    model_name = ""
    last_error = ""

    for attempt in range(MAX_LLM_ATTEMPTS):
        try:
            resp = await complete_with_logging(
                db,
                LLMRequest(
                    messages=messages,
                    metadata=LLMMetadata(
                        yard_id=ctx.yard_id,
                        task_id=f"skill-gen-{uuid.uuid4().hex[:12]}",
                        request_id=ctx.request_id,
                        purpose="skill_generation",
                        agent_ref="skill_agent",
                    ),
                ),
            )
        except AppError as e:
            log.warning("技能规格生成失败（第 %d 次）：%s", attempt + 1, e.message)
            last_error = e.message
            break
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
            spec = validate_skill_spec(parsed, available_tools=available_tools)
        except AppError as e:
            last_error = e.message
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=json.dumps(parsed, ensure_ascii=False)[:2000],
                )
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
    if last_error:
        log.info("技能规格生成降级为兜底模板：%s", last_error)
    fallback = _fallback_spec(utterance, sources, available_tools)
    try:
        spec = validate_skill_spec(fallback, available_tools=available_tools)
    except AppError as e:  # pragma: no cover - 兜底规格恒合法，防御性保留
        log.error("兜底规格校验失败：%s", e.message)
        raise
    return (
        spec,
        usage,
        model_name,
        "模型未产出合规规格，已按保守模板生成草稿，请人工复核后再提交审核。",
    )


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------


def _unique_code(db: Session, yard_id: str, base: str) -> str:
    """小院内技能编码唯一（生成场景用下划线后缀）。"""
    candidate = base
    index = 1
    while True:
        exists = (
            db.query(Skill.id)
            .filter(Skill.yard_id == yard_id, Skill.skill_code == candidate)
            .first()
        )
        if exists is None:
            return candidate
        index += 1
        candidate = f"{base}_{index}"
        if index > 50:
            return f"{base}_{uuid.uuid4().hex[:8]}"


def _definition(spec: SkillSpec) -> dict[str, Any]:
    """技能定义：运行时读 description/prompt/steps；其余字段保留以备导出与复核。"""
    data = spec.model_dump()
    raw_steps = [s for s in (data.get("steps") or []) if isinstance(s, dict)]
    definition: dict[str, Any] = {
        "name": data.get("name"),
        "description": data.get("summary"),
        "summary": data.get("summary"),
        "when_to_use": data.get("when_to_use"),
        "prompt": data.get("prompt"),
        # 运行时按字符串列表读取步骤
        "steps": [str(s.get("title") or s.get("text") or "") for s in raw_steps],
        "step_details": raw_steps,
        "allowed_tools": list(data.get("allowed_tools") or []),
        "knowledge_scopes": list(data.get("knowledge_scopes") or []),
        "category": data.get("category"),
        "output_format": data.get("output_format"),
        "guardrails": data.get("guardrails") or {},
        "sources": list(data.get("sources") or []),
        "tags": list(data.get("tags") or []),
        "license": data.get("license"),
        "requires_review": bool(data.get("requires_review")),
        "template": "skill-template/v3.2",
        "markdown": dump_markdown(spec),
    }
    return definition


def _brief(skill: Skill) -> dict[str, Any]:
    """技能摘要（前端 GeneratedSkillBrief 形状）。"""
    definition = skill.definition_json if isinstance(skill.definition_json, dict) else {}
    return {
        "id": skill.id,
        "code": skill.skill_code,
        "name": str(definition.get("name") or skill.skill_code),
        "version": skill.version,
        "visibility": skill.visibility or "private",
        "status": skill.status,
    }


def _create_draft(db: Session, ctx: YardContext, spec: SkillSpec) -> Skill:
    """把规格落成**私有草稿**技能（owner=当前用户，visibility=private）。"""
    code = _unique_code(db, ctx.yard_id, spec.code)
    definition = _definition(spec)
    skill = create_skill(
        db,
        ctx,
        code,
        spec.version,
        definition,
        created_from="user_generated",
        request_id=ctx.request_id,
        visibility="private",
        owner_user_id=ctx.user_id,
        origin="skill_agent",
    )
    # 生成即自检（v3.56）：静态检查报告写进草稿定义，审核者直接看到分数，
    # 不用等到上架门禁才发现结构问题。纯静态、零 LLM 成本；失败不阻断生成。
    try:
        from app.modules.evolution import prepublish

        report = prepublish.static_report(skill)
        definition["auto_eval"] = {
            "kind": "prepublish",
            "score": report.get("score"),
            "errors": report.get("errors"),
            "warnings": report.get("warnings"),
            "generated_at": report.get("generated_at"),
        }
        skill.definition_json = definition
        db.flush()
    except Exception as e:  # noqa: BLE001 - 自检失败不影响草稿生成
        log.warning("技能草稿自检失败 code=%s: %s", code, e)
    return skill


# ---------------------------------------------------------------------------
# 对外服务
# ---------------------------------------------------------------------------


def request_skill(db: Session, *, ctx: YardContext, utterance: str) -> dict[str, Any]:
    """一句话检索：本地内置/缓存 → （无命中时）GitHub 开源技能。"""
    text = (utterance or "").strip()
    if not text:
        raise AppError("INVALID_REQUEST", "请描述你需要的技能。", http_status=400)
    matches, offline, warning = _find_matches(db, ctx, text)
    if matches:
        names = "、".join(str(m.get("name") or "") for m in matches[:3])
        note = f"找到 {len(matches)} 个匹配技能：{names}。"
    elif offline:
        note = warning or "当前处于离线状态，仅检索了本地技能库。"
    else:
        note = "没有找到可安装的现成技能，可以直接按你的描述生成一个新技能。"
    return {
        "utterance": text,
        "query": _clean_text(text),
        "recommend": "install" if matches else "generate",
        "matches": matches,
        "note": note,
        "offline": offline,
    }


async def generate_skill(
    db: Session, *, ctx: YardContext, utterance: str, use_web: bool = True
) -> dict[str, Any]:
    """按一句话需求生成技能，落为私有草稿并返回生成结果。"""
    text = (utterance or "").strip()
    if not text:
        raise AppError("INVALID_REQUEST", "请描述你需要的技能。", http_status=400)
    settings = _lab_settings(db, ctx.yard_id)
    if settings.get("enabled") is False:
        raise AppError("SKILL_LAB_DISABLED", "本小院已关闭技能生成服务。", http_status=403)
    limit = _daily_limit(db, ctx.yard_id)
    _ensure_quota(ctx.yard_id, limit)

    query = _clean_text(text) or text
    sources: list[dict[str, str]] = []
    if use_web and settings.get("use_web", True):
        sources = await _collect_sources(db, ctx, query, _max_sources(settings))

    available = _available_tools()
    spec, usage, model_name, note = await _build_spec(db, ctx, text, sources, available)
    skill = _create_draft(db, ctx, spec)
    _consume_quota(ctx.yard_id)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="skill_generated",
        resource_type="skill",
        resource_id=skill.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "skill_code": skill.skill_code,
            "model": model_name,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "source_count": len(sources),
            "requires_review": bool(spec.requires_review),
        },
    )
    db.flush()
    return {
        "skill": _brief(skill),
        "spec": spec.model_dump(),
        "sources": sources,
        "requires_review": bool(spec.requires_review),
        "note": note,
        "token_usage": usage,
    }


def install_skill(db: Session, *, ctx: YardContext, payload: dict[str, Any]) -> dict[str, Any]:
    """安装技能：按 item_key 装市场条目，或按 skill_id 把自己的私有草稿送审。"""
    item_key = str(payload.get("item_key") or "").strip()
    skill_id = str(payload.get("skill_id") or "").strip()
    if not item_key and not skill_id:
        raise AppError("INVALID_REQUEST", "请提供 item_key 或 skill_id。", http_status=400)
    if item_key:
        result = installer.install_item(db, ctx, item_key, actor_role=list(ctx.roles or []))
        needs_review = str(result.get("review_status") or "") == "pending_review"
        return {**result, "needs_review": needs_review}
    return _submit_draft(db, ctx, skill_id)


def _submit_draft(db: Session, ctx: YardContext, skill_id: str) -> dict[str, Any]:
    """把私有草稿送入小院待审（visibility=yard + status=pending_review）。"""
    skill = db.query(Skill).filter(Skill.id == skill_id, Skill.yard_id == ctx.yard_id).first()
    if skill is None:
        raise AppError("SKILL_NOT_FOUND", "技能不存在或不属于当前小院。", http_status=404)
    if skill.owner_user_id and skill.owner_user_id != ctx.user_id:
        raise AppError("FORBIDDEN", "只能安装自己创建的技能草稿。", http_status=403)
    if skill.status == "published":
        return {
            "skill_id": skill.id,
            "installed_as": skill.skill_code,
            "skill_status": skill.status,
            "review_status": "published",
            "needs_review": False,
        }
    skill.visibility = "yard"
    skill.status = "pending_review"
    skill.reviewed_at = None
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="skill_submitted_for_review",
        resource_type="skill",
        resource_id=skill.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"skill_code": skill.skill_code},
    )
    db.flush()
    return {
        "skill_id": skill.id,
        "installed_as": skill.skill_code,
        "skill_status": skill.status,
        "review_status": "pending_review",
        "needs_review": True,
    }


# ---------------------------------------------------------------------------
# 对话链路
# ---------------------------------------------------------------------------


def _card(
    state: str,
    utterance: str,
    *,
    skill: dict[str, Any] | None = None,
    matches: list[dict[str, Any]] | None = None,
    sources: list[dict[str, str]] | None = None,
    note: str = "",
    token_usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """技能卡片载荷（前端 ChatSkillCard 契约）。"""
    card: dict[str, Any] = {
        "type": CARD_TYPE,
        "state": state,
        "utterance": utterance,
        "skill": skill,
        "matches": list(matches or []),
        "sources": list(sources or []),
        "note": note,
    }
    if token_usage:
        card["token_usage"] = token_usage
    return card


async def handle_skill_request(
    db: Session, *, ctx: YardContext, utterance: str, on_step: Any = None
) -> dict[str, Any]:
    """对话链路：先检索匹配，无匹配再生成私有草稿，返回技能卡片。"""
    text = (utterance or "").strip()
    if not text:
        raise AppError("INVALID_REQUEST", "请描述你需要的技能。", http_status=400)

    matches, _offline, _warning = _find_matches(db, ctx, text)
    if matches:
        if on_step:
            on_step(PHASE_RETRIEVING, f"找到 {len(matches)} 个匹配技能", STEP_DONE)
        names = "、".join(str(m.get("name") or "") for m in matches[:3])
        return _card(
            STATE_MATCHED,
            text,
            matches=matches,
            note=f"找到 {len(matches)} 个匹配技能：{names}。可在卡片中直接安装。",
        )

    if on_step:
        on_step(PHASE_GENERATING, "未找到匹配技能，正在联网查找资料并生成", STEP_RUNNING)
    try:
        result = await generate_skill(db, ctx=ctx, utterance=text, use_web=True)
    except AppError as e:
        if on_step:
            on_step(PHASE_GENERATING, f"技能生成失败：{e.message}", STEP_FAILED)
        return _card(STATE_FAILED, text, note=e.message)

    skill = result.get("skill") or {}
    note = str(result.get("note") or "")
    if not note:
        note = f"已生成私有技能草稿「{skill.get('name')}」，提交审核后可在小院共享。"
    return _card(
        STATE_CREATED,
        text,
        skill=skill,
        sources=result.get("sources") or [],
        note=note,
        token_usage=result.get("token_usage"),
    )


__all__ = [
    "CARD_TYPE",
    "STATE_CREATED",
    "STATE_FAILED",
    "STATE_MATCHED",
    "generate_skill",
    "handle_skill_request",
    "install_skill",
    "quota_snapshot",
    "request_skill",
]
