"""C1 Skill 自动总结（v1.3-B 自生长闭环）：近 7 天证据聚合 → LLM 技能候选卡 → 审核队列。

诚实口径（DESIGN-v3 C1）：
- 窗口内去重运行数 < 5 → ``EVOLUTION_INSUFFICIENT_EVIDENCE`` ``(409)``，不硬凑；
- LLM 不可用 / 输出不可解析 → ``EVOLUTION_LLM_UNAVAILABLE`` (502)，不落半成品；
- 只产 ``status=pending_review`` 草稿（created_from=``auto:suggest``），
  发布永远走既有 skill review 队列——自动化止步于建议，上架永远经人；
- 审计 action=``skill_suggested``：只记数量与 skill_code，不存候选正文全文。

证据源：``agent_run_steps`` 台账（近 7 天）——运行数/成功率、任务样本
（首个 LLM 步 input_json 的运行入参）、失败错误类别、高频工具与工具序列。
"""

import hashlib
import json
import re
import uuid
from collections import Counter, OrderedDict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents.run_steps import (
    STEP_FINAL,
    STEP_LLM,
    STEP_OK,
    STEP_TOOL,
    AgentRunStep,
)
from app.modules.audit.service import log_audit
from app.modules.evolution.models import Skill
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging

log = get_logger(__name__)

#: 证据窗口（天）与最低运行数（不足诚实拒绝）
WINDOW_DAYS = 7
MIN_RUNS = 5
#: 每次最多产出的候选卡数（DESIGN C1：1-3 张）
MAX_CARDS = 3
_MAX_PROCEDURE_STEPS = 8
_MAX_EXAMPLES = 3

#: 可触发 AI 总结的角色（platform_admin 在路由层 require_roles 直通）
_SUGGEST_ROLES = {"yard_admin", "yard_owner", "platform_admin"}

# 触发词样式：优先运行入参字段，其次 messages 里最后一条 user 消息
_QUESTION_KEYS = ("question", "query", "input", "message")


def _sha12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _require_suggest_permission(ctx: YardContext) -> None:
    if not set(ctx.roles or []) & _SUGGEST_ROLES:
        raise AppError("FORBIDDEN", "仅小院管理员可触发 AI 技能总结。", http_status=403)


def _extract_question(input_json: dict[str, Any] | None) -> str | None:
    """从 LLM 步 input_json 提取可读任务样本（诚实：取不到就 None，不编造）。"""
    if not isinstance(input_json, dict):
        return None
    for key in _QUESTION_KEYS:
        value = input_json.get(key)
        if isinstance(value, str) and value.strip():
            return str(value).strip()[:120]
    messages = input_json.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()[:120]
    return None


def _collect_evidence(db: Session, ctx: YardContext) -> dict[str, Any]:
    """聚合近 7 天运行证据。返回 dict（含全部运行原始桶，供 prompt 与卡内证据复用）。"""
    cutoff = datetime.now(UTC) - timedelta(days=WINDOW_DAYS)
    rows = (
        db.query(AgentRunStep)
        .filter(
            AgentRunStep.yard_id == ctx.yard_id,
            AgentRunStep.created_at >= cutoff,
        )
        .order_by(AgentRunStep.run_id.asc(), AgentRunStep.step_index.asc())
        .all()
    )
    runs: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for row in rows:
        bucket = runs.setdefault(
            row.run_id,
            {"tools": [], "has_error": False, "final_ok": False, "question": None},
        )
        if row.step_type == STEP_TOOL and row.tool_code and row.status == STEP_OK:
            bucket["tools"].append(str(row.tool_code))
        elif row.step_type == STEP_LLM and bucket["question"] is None:
            bucket["question"] = _extract_question(row.input_json)
        if row.status == "error":
            bucket["has_error"] = True
        if row.step_type == STEP_FINAL and row.status == STEP_OK:
            bucket["final_ok"] = True

    run_count = len(runs)
    success_runs = sum(1 for b in runs.values() if b["final_ok"] and not b["has_error"])
    tool_counter: Counter[str] = Counter()
    error_counter: Counter[str] = Counter()
    for row in rows:
        if row.status == "error":
            text = row.error_text or row.step_type
            error_counter[str(text).split(":", 1)[0].strip()[:60] or "unknown"] += 1
    for bucket in runs.values():
        tool_counter.update(set(bucket["tools"]))  # 一个运行里同一工具只计一次频率
    samples: list[str] = []
    for bucket in runs.values():
        if bucket["question"] and bucket["question"] not in samples:
            samples.append(str(bucket["question"]))
        if len(samples) >= 5:
            break

    return {
        "run_count": run_count,
        "success_rate": round(success_runs / run_count, 4) if run_count else 0.0,
        "top_tools": [code for code, _ in tool_counter.most_common(5)],
        "sample_tasks": samples,
        "error_categories": [
            {"category": cat, "count": n} for cat, n in error_counter.most_common(5)
        ],
        # 相邻工具对频率（喂给 LLM 的序列信号，不落 definition_json）
        "tool_pairs": [
            {"sequence": f"{a} → {b}", "count": n}
            for (a, b), n in Counter(
                (seq[i], seq[i + 1])
                for bucket in runs.values()
                for seq in [bucket["tools"]]
                for i in range(len(seq) - 1)
            ).most_common(3)
        ],
    }


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_cards(content: str) -> list[dict[str, Any]]:
    """从模型输出提取候选卡数组：容忍 markdown 围栏与外层 {"cards": [...]}。"""
    text = _FENCE_RE.sub("", (content or "").strip()).strip()
    payloads: list[str] = [text]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        payloads.append(text[start : end + 1])
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        payloads.append(text[start : end + 1])
    for payload in payloads:
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            data = data.get("cards")
        if isinstance(data, list):
            cards = [c for c in data if isinstance(c, dict) and str(c.get("title") or "").strip()]
            if cards:
                return cards
    raise ValueError("模型输出中未找到合法的技能候选卡 JSON")


def _next_version(db: Session, ctx: YardContext, skill_code: str) -> str:
    """同 code 版本递增（1.0.0 → 1.0.1…），保证 (yard, code, version) 唯一。"""
    existing = {
        version
        for (version,) in db.query(Skill.version)
        .filter(Skill.yard_id == ctx.yard_id, Skill.skill_code == skill_code)
        .all()
    }
    index = 0
    while f"1.0.{index}" in existing:
        index += 1
    return f"1.0.{index}"


def _build_messages(evidence: dict[str, Any], focus_hint: str | None) -> list[ChatMessage]:
    hint = f"\n用户补充关注点（可参考，不可编造）：{focus_hint.strip()[:200]}" if focus_hint else ""
    evidence_view = {k: evidence[k] for k in evidence}
    return [
        ChatMessage(
            role="system",
            content=(
                "你是农业科教平台的技能策展助手。基于给定运行证据，归纳 1-3 个值得沉淀为"
                "Skill 的可复用技能候选。只输出 JSON 数组（不要解释、不要代码块围栏），"
                '每项形如 {"title":str,"when_to_use":str,"procedure":[str],"examples":[str]}，'
                "全部中文；证据不足以支撑的技能不要硬凑，宁可少给。"
            ),
        ),
        ChatMessage(
            role="user",
            content=(
                "近 7 天运行证据（JSON）：\n" + json.dumps(evidence_view, ensure_ascii=False) + hint
            ),
        ),
    ]


async def suggest_skills(
    db: Session,
    ctx: YardContext,
    focus_hint: str | None = None,
    request_id: str = "",
) -> list[Skill]:
    """证据聚合 → LLM 候选卡 → 落 pending_review 草稿。返回新建的 Skill 行。"""
    _require_suggest_permission(ctx)
    evidence = _collect_evidence(db, ctx)
    if evidence["run_count"] < MIN_RUNS:
        raise AppError(
            "EVOLUTION_INSUFFICIENT_EVIDENCE",
            f"近 {WINDOW_DAYS} 天运行样本不足（{evidence['run_count']}/{MIN_RUNS} 次），"
            "暂不生成技能建议。",
            http_status=409,
            details={"run_count": evidence["run_count"], "min_runs": MIN_RUNS},
        )

    try:
        resp = await complete_with_logging(
            db,
            LLMRequest(
                messages=_build_messages(evidence, focus_hint),
                metadata=LLMMetadata(
                    yard_id=ctx.yard_id,
                    task_id=f"sug-{uuid.uuid4().hex[:12]}",
                    request_id=request_id or ctx.request_id,
                    purpose="skill_suggest",
                    agent_ref="evolution.suggest",
                ),
            ),
        )
    except AppError as e:
        raise AppError(
            "EVOLUTION_LLM_UNAVAILABLE",
            "模型当前不可用，未生成技能建议。",
            http_status=502,
            details={"reason": e.code},
        ) from e
    except Exception as e:  # pragma: no cover - 防御分支：非业务异常同样不产半成品
        log.warning("skill suggest llm call failed: %s", e)
        raise AppError(
            "EVOLUTION_LLM_UNAVAILABLE",
            "模型当前不可用，未生成技能建议。",
            http_status=502,
            details={"reason": type(e).__name__},
        ) from e

    try:
        cards = _parse_cards(resp.content or "")
    except ValueError as e:
        raise AppError(
            "EVOLUTION_LLM_UNAVAILABLE",
            "模型输出无法解析为技能候选卡，未生成技能建议。",
            http_status=502,
            details={"reason": "parse_failed"},
        ) from e

    # 定义正文按契约裁剪；证据节只保留契约字段（不含 error_categories 等内部信号）
    definition_evidence = {
        "run_count": evidence["run_count"],
        "success_rate": evidence["success_rate"],
        "top_tools": evidence["top_tools"],
        "sample_tasks": evidence["sample_tasks"],
    }
    created: list[Skill] = []
    for card in cards[:MAX_CARDS]:
        title = str(card.get("title") or "").strip()[:200]
        when_to_use = str(card.get("when_to_use") or "").strip()[:500]
        procedure = [
            str(step).strip()[:500] for step in (card.get("procedure") or []) if str(step).strip()
        ][:_MAX_PROCEDURE_STEPS]
        examples = [str(x).strip()[:300] for x in (card.get("examples") or []) if str(x).strip()][
            :_MAX_EXAMPLES
        ]
        if not title or not procedure:
            continue  # 空壳卡直接丢弃，不落半成品
        skill_code = "auto-" + _sha12("|".join([title, *procedure]))
        skill = Skill(
            yard_id=ctx.yard_id,
            skill_code=skill_code,
            version=_next_version(db, ctx, skill_code),
            definition_json={
                "title": title,
                "when_to_use": when_to_use,
                "procedure": procedure,
                "examples": examples,
                "evidence": definition_evidence,
            },
            status="pending_review",
            created_from="auto:suggest",
        )
        db.add(skill)
        db.flush()
        created.append(skill)

    if not created:
        # 全部候选卡不合规：等同于 LLM 未产出，不落半成品、诚实报错
        raise AppError(
            "EVOLUTION_LLM_UNAVAILABLE",
            "模型输出无法解析为技能候选卡，未生成技能建议。",
            http_status=502,
            details={"reason": "empty_cards"},
        )

    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="skill_suggested",
        resource_type="skill",
        resource_id=created[0].id,
        result="success",
        request_id=request_id or ctx.request_id,
        # 审计不存正文：只记数量、skill_code 与运行数
        metadata={
            "count": len(created),
            "skill_codes": [s.skill_code for s in created],
            "run_count": evidence["run_count"],
        },
    )
    return created
