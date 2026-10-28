"""Skill 调用埋点与自动升级（v3.21）。

口径：
- 只统计「技能参与了哪次回答」，不读取、不存储对话正文；证据只取主题级信号
  （标题截断、反馈片段截断、工具错误类别）与调用次数。
- 自动升级**只产出待审核草稿**，不自动发布；证据不足、模型不可用、冷却未到
  都如实跳过并记录原因。
- 与 3.4 的自我总结共用同一套「证据 → 模型 → 草稿」思路，但按技能收窄输入。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.chat.models import ChatSession
from app.modules.evolution.models import (
    Feedback,
    Skill,
    SkillAutoUpgrade,
    SkillUpgradeRun,
    SkillUsageEvent,
)
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest
from app.modules.llm.service import complete_with_logging

log = get_logger(__name__)

USAGE_WINDOW_DAYS = 14
MAX_FEEDBACK_SAMPLES = 5
MAX_SESSION_TITLES = 8
TITLE_CHARS = 40
FEEDBACK_CHARS = 120
MAX_OUTPUT_TOKENS = 900
LLM_ATTEMPTS = 2
MAX_STEPS = 10
MAX_TEXT = 2000

SKILL_STATUS_LABELS = {
    "draft": "草稿",
    "testing": "测试中",
    "pending_review": "待审核",
    "published": "已发布",
    "deprecated": "已下线",
    "rejected": "已驳回",
}


def status_label(status: str | None) -> str:
    return SKILL_STATUS_LABELS.get(str(status or ""), str(status or ""))


# ---------------------------------------------------------------------------
# 调用埋点与统计
# ---------------------------------------------------------------------------


def record_usage(
    db: Session,
    *,
    yard_id: str,
    codes: list[str],
    source: str,
    session_id: str | None = None,
    outcome: str = "ok",
) -> None:
    """记录一次技能参与回答；重复/空编码自动去重。"""
    seen: set[str] = set()
    for raw in codes:
        code = str(raw or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        db.add(
            SkillUsageEvent(
                yard_id=yard_id,
                skill_code=code,
                source=source,
                session_id=session_id,
                outcome=outcome,
            )
        )
    if seen:
        db.flush()


def usage_stats(db: Session, yard_id: str, code: str, *, days: int = 30) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    base = (
        select(func.count())
        .select_from(SkillUsageEvent)
        .where(
            SkillUsageEvent.yard_id == yard_id,
            SkillUsageEvent.skill_code == code,
            SkillUsageEvent.created_at >= since,
        )
    )
    calls = int(db.scalar(base) or 0)
    sessions = int(
        db.scalar(
            select(func.count(func.distinct(SkillUsageEvent.session_id))).where(
                SkillUsageEvent.yard_id == yard_id,
                SkillUsageEvent.skill_code == code,
                SkillUsageEvent.created_at >= since,
                SkillUsageEvent.session_id.is_not(None),
            )
        )
        or 0
    )
    failed = int(db.scalar(base.where(SkillUsageEvent.outcome == "failed")) or 0)
    last_at = db.scalar(
        select(func.max(SkillUsageEvent.created_at)).where(
            SkillUsageEvent.yard_id == yard_id,
            SkillUsageEvent.skill_code == code,
        )
    )
    return {
        "days": days,
        "calls": calls,
        "sessions": sessions,
        "failed": failed,
        "last_used_at": last_at.isoformat() if last_at else None,
    }


def usage_for_codes(db: Session, yard_id: str, codes: list[str]) -> dict[str, dict[str, Any]]:
    return {code: usage_stats(db, yard_id, code) for code in codes}


def usage_map(db: Session, yard_id: str, *, days: int = 30) -> dict[str, dict[str, int]]:
    """一次分组查询取全部技能调用量，供列表页避免 N+1。"""
    since = datetime.now(UTC) - timedelta(days=days)
    rows = db.execute(
        select(
            SkillUsageEvent.skill_code,
            func.count(),
            func.count(func.distinct(SkillUsageEvent.session_id)),
        )
        .where(SkillUsageEvent.yard_id == yard_id, SkillUsageEvent.created_at >= since)
        .group_by(SkillUsageEvent.skill_code)
    )
    return {
        str(code): {"calls": int(calls or 0), "sessions": int(sessions or 0)}
        for code, calls, sessions in rows
    }


# ---------------------------------------------------------------------------
# 开关与配置
# ---------------------------------------------------------------------------


def get_setting(db: Session, yard_id: str, code: str) -> SkillAutoUpgrade | None:
    return db.scalar(
        select(SkillAutoUpgrade).where(
            SkillAutoUpgrade.yard_id == yard_id,
            SkillAutoUpgrade.skill_code == code,
        )
    )


def ensure_setting(db: Session, yard_id: str, code: str, actor_id: str) -> SkillAutoUpgrade:
    row = get_setting(db, yard_id, code)
    if row is None:
        row = SkillAutoUpgrade(yard_id=yard_id, skill_code=code, created_by=actor_id)
        db.add(row)
        db.flush()
    return row


def setting_out(row: SkillAutoUpgrade | None, stats: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": bool(row.enabled) if row else False,
        "min_calls": int(row.min_calls) if row else 10,
        "cooldown_hours": int(row.cooldown_hours) if row else 168,
        "last_run_at": row.last_run_at.isoformat() if row and row.last_run_at else None,
        "last_status": row.last_status if row else None,
        "last_error": row.last_error if row else None,
        "usage": stats,
    }


# ---------------------------------------------------------------------------
# 证据与升级
# ---------------------------------------------------------------------------


def _short(text: str | None, limit: int) -> str:
    return " ".join((text or "").split())[:limit]


def collect_evidence(
    db: Session,
    *,
    yard_id: str,
    code: str,
    base: Skill,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    since = now - timedelta(days=USAGE_WINDOW_DAYS)
    stats = usage_stats(db, yard_id, code, days=USAGE_WINDOW_DAYS)

    session_ids = [
        str(sid)
        for (sid,) in db.execute(
            select(SkillUsageEvent.session_id)
            .where(
                SkillUsageEvent.yard_id == yard_id,
                SkillUsageEvent.skill_code == code,
                SkillUsageEvent.created_at >= since,
                SkillUsageEvent.session_id.is_not(None),
            )
            .distinct()
            .limit(MAX_SESSION_TITLES)
        )
    ]
    titles: list[str] = []
    if session_ids:
        titles = [
            _short(title, TITLE_CHARS)
            for (title,) in db.execute(
                select(ChatSession.title).where(
                    ChatSession.yard_id == yard_id, ChatSession.id.in_(session_ids)
                )
            )
            if title
        ]

    feedback = [
        {
            "rating": row.rating,
            "text": _short(row.feedback_text, FEEDBACK_CHARS),
        }
        for row in db.scalars(
            select(Feedback)
            .where(Feedback.yard_id == yard_id, Feedback.created_at >= since)
            .order_by(Feedback.created_at.desc())
            .limit(MAX_FEEDBACK_SAMPLES)
        )
        if row.rating == "down" or row.feedback_text
    ]
    sources = Counter(
        str(s)
        for (s,) in db.execute(
            select(SkillUsageEvent.source).where(
                SkillUsageEvent.yard_id == yard_id,
                SkillUsageEvent.skill_code == code,
                SkillUsageEvent.created_at >= since,
            )
        )
    )
    definition = base.definition_json if isinstance(base.definition_json, dict) else {}
    return {
        "window": {"from": since.isoformat(), "to": now.isoformat(), "days": USAGE_WINDOW_DAYS},
        "stats": stats,
        "sources": dict(sources),
        "session_titles": titles,
        "feedback": feedback,
        "current": {
            "version": base.version,
            "title": _short(str(definition.get("title") or ""), 120),
            "description": _short(
                str(definition.get("description") or definition.get("summary") or ""), 500
            ),
            "prompt": _short(
                str(definition.get("prompt") or definition.get("content") or ""), 1500
            ),
            "steps": [
                _short(str(step), 200)
                for step in (definition.get("steps") or [])
                if str(step).strip()
            ][:MAX_STEPS],
        },
    }


def _build_messages(evidence: dict[str, Any]) -> list[ChatMessage]:
    return [
        ChatMessage(
            role="system",
            content=(
                "你是技能维护助手。根据技能定义与最近的真实调用证据，判断是否需要更新该技能。\n"
                '只输出 JSON：{"needs_update": bool, "reason": str, "description": str, '
                '"prompt": str, "steps": [str]}\n'
                "要求：\n"
                "1) 不改变技能用途与边界；不得加入权限绕过、外部指令执行或未经验证的事实；\n"
                "2) description 一句话说明能做什么；prompt 是可执行的提示词；steps 3-8 条；\n"
                "3) 证据不足时 needs_update=false，并说明缺什么；\n"
                "4) 中文输出，不要输出 Markdown 代码围栏以外的内容。"
            ),
        ),
        ChatMessage(
            role="user",
            content=json.dumps(evidence, ensure_ascii=False, default=str),
        ),
    ]


def _parse_payload(content: str) -> dict[str, Any]:
    text = (content or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("payload is not an object")
    return data


def _bump_version(version: str) -> str:
    parts = str(version or "").split(".")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    return "1.0.1"


def _latest_base(db: Session, yard_id: str, code: str) -> Skill | None:
    return db.scalar(
        select(Skill)
        .where(
            Skill.yard_id == yard_id,
            Skill.skill_code == code,
            Skill.status.in_(("published", "testing", "draft", "pending_review")),
        )
        .order_by(Skill.created_at.desc())
    )


def run_out(row: SkillUpgradeRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "skill_code": row.skill_code,
        "trigger": row.trigger,
        "status": row.status,
        "summary": row.summary,
        "error": row.error,
        "draft_skill_id": row.draft_skill_id,
        "evidence": row.evidence_json,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _fail(
    db: Session,
    run: SkillUpgradeRun,
    setting: SkillAutoUpgrade,
    message: str,
) -> SkillUpgradeRun:
    run.status = "failed"
    run.error = message[:300]
    setting.last_run_at = datetime.now(UTC)
    setting.last_status = "failed"
    setting.last_error = message[:300]
    db.flush()
    return run


def upgrade_skill(
    db: Session,
    *,
    yard_id: str,
    code: str,
    trigger: str,
    actor_id: str | None,
    request_id: str,
    manual: bool = False,
) -> SkillUpgradeRun:
    """执行一次按技能的升级；只产草稿，不发布。"""
    setting = get_setting(db, yard_id, code)
    if setting is None or not setting.enabled:
        raise AppError("SKILL_AUTO_UPGRADE_DISABLED", "该技能未开启自动升级。", 409)
    base = _latest_base(db, yard_id, code)
    if base is None:
        raise AppError("SKILL_NOT_FOUND", "技能不存在或不可升级。", 404)
    stats = usage_stats(db, yard_id, code, days=USAGE_WINDOW_DAYS)
    if stats["calls"] < (1 if manual else int(setting.min_calls)):
        raise AppError(
            "SKILL_UPGRADE_NO_EVIDENCE",
            f"最近 {USAGE_WINDOW_DAYS} 天调用 {stats['calls']} 次，未达到升级门槛。",
            409,
        )
    now = datetime.now(UTC)
    if not manual and setting.last_run_at:
        last = setting.last_run_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if now - last < timedelta(hours=int(setting.cooldown_hours)):
            raise AppError("SKILL_UPGRADE_COOLDOWN", "冷却时间未到，暂不升级。", 409)

    evidence = collect_evidence(db, yard_id=yard_id, code=code, base=base, now=now)
    run = SkillUpgradeRun(
        yard_id=yard_id,
        skill_code=code,
        trigger=trigger,
        status="running",
        evidence_json=evidence,
        actor_id=actor_id,
    )
    db.add(run)
    db.flush()

    payload: dict[str, Any] | None = None
    last_error = ""
    for _ in range(LLM_ATTEMPTS):
        try:
            resp = asyncio.run(
                complete_with_logging(
                    db,
                    LLMRequest(
                        messages=_build_messages(evidence),
                        max_tokens=MAX_OUTPUT_TOKENS,
                        metadata=LLMMetadata(
                            yard_id=yard_id,
                            user_id=actor_id,
                            request_id=request_id,
                            purpose="skill_upgrade",
                            agent_ref="evolution.auto_upgrade",
                        ),
                    ),
                )
            )
            payload = _parse_payload(resp.content or "")
            break
        except AppError as e:
            last_error = f"llm_unavailable:{e.code}"
        except (ValueError, json.JSONDecodeError) as e:
            last_error = f"parse_failed:{e}"
        except Exception as e:  # noqa: BLE001
            last_error = f"{type(e).__name__}:{e}"

    if payload is None:
        return _fail(db, run, setting, last_error or "llm_failed")

    if not payload.get("needs_update"):
        run.status = "skipped"
        run.summary = _short(str(payload.get("reason") or "模型判断无需更新"), 1000)
        setting.last_run_at = now
        setting.last_status = "skipped"
        setting.last_error = None
        db.flush()
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=actor_id,
            action="skill_upgrade_run",
            resource_type="skill",
            resource_id=base.id,
            result="skipped",
            request_id=request_id,
            metadata={"skill_code": code, "trigger": trigger},
        )
        return run

    description = _short(str(payload.get("description") or ""), 500)
    prompt = _short(str(payload.get("prompt") or ""), MAX_TEXT)
    steps = [_short(str(step), 300) for step in (payload.get("steps") or []) if str(step).strip()][
        :MAX_STEPS
    ]
    if not description or not prompt or not steps:
        return _fail(db, run, setting, "模型输出缺少 description/prompt/steps")

    base_def = dict(base.definition_json if isinstance(base.definition_json, dict) else {})
    definition = {
        **base_def,
        "description": description,
        "prompt": prompt,
        "steps": steps,
        "upgrade": {
            "based_on_version": base.version,
            "reason": _short(str(payload.get("reason") or ""), 1000),
            "window": evidence["window"],
            "calls": stats["calls"],
        },
    }
    version = _bump_version(base.version)
    for _ in range(20):
        dup = db.scalar(
            select(Skill.id).where(
                Skill.yard_id == yard_id,
                Skill.skill_code == code,
                Skill.version == version,
            )
        )
        if dup is None:
            break
        version = _bump_version(version)

    draft = Skill(
        yard_id=yard_id,
        skill_code=code,
        version=version,
        definition_json=definition,
        status="pending_review",
        created_from="auto:upgrade",
        visibility=base.visibility,
        owner_user_id=base.owner_user_id,
        origin="skill_auto_upgrade",
    )
    db.add(draft)
    db.flush()

    run.status = "success"
    run.draft_skill_id = draft.id
    run.summary = _short(
        f"基于 v{base.version} 生成 v{version} 草稿：{str(payload.get('reason') or '')}", 1000
    )
    setting.last_run_at = now
    setting.last_status = "success"
    setting.last_error = None
    db.flush()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="skill_upgrade_run",
        resource_type="skill",
        resource_id=draft.id,
        result="success",
        request_id=request_id,
        metadata={"skill_code": code, "trigger": trigger, "version": version},
    )
    return run


def run_due_upgrades(db: Session, *, yard_id: str, request_id: str) -> dict[str, int]:
    """调度入口：处理本小院所有已到门槛的自动升级；单条失败不影响其余。"""
    counts = {"ran": 0, "success": 0, "skipped": 0, "failed": 0}
    now = datetime.now(UTC)
    rows = list(
        db.scalars(
            select(SkillAutoUpgrade).where(
                SkillAutoUpgrade.yard_id == yard_id, SkillAutoUpgrade.enabled.is_(True)
            )
        )
    )
    for setting in rows:
        last = setting.last_run_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if last and now - last < timedelta(hours=int(setting.cooldown_hours)):
            continue
        stats = usage_stats(db, yard_id, setting.skill_code, days=USAGE_WINDOW_DAYS)
        if stats["calls"] < int(setting.min_calls):
            continue
        counts["ran"] += 1
        try:
            run = upgrade_skill(
                db,
                yard_id=yard_id,
                code=setting.skill_code,
                trigger="schedule",
                actor_id=None,
                request_id=request_id,
            )
            counts[run.status] = counts.get(run.status, 0) + 1
        except AppError as e:
            counts["failed"] += 1
            setting.last_status = "failed"
            setting.last_error = e.message[:300]
            db.flush()
            log.warning("skill upgrade skipped/failed code=%s: %s", setting.skill_code, e.message)
    return counts
