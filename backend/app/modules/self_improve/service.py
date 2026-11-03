"""自我总结提升（v3.4）：调度执行引擎。

诚实口径：
- 只沉淀**摘要与提案**（偏好记忆/技能草稿/知识候选/运行报告），不存会话原文；
- LLM 不可用 / 输出不可解析 / 超日预算 → 不产生半成品数据（失败/跳过的运行
  台账保留，但没有任何新记忆/技能/候选入库）；
- 手动触发（``run_once``）与调度触发（``run_due_jobs``）共用同一执行体，
  调度多一层租约互斥（多实例并发安全）。

调度底座（可复用）：``yard_jobs`` 每小院每 kind 一行；``lease_until`` 租约防
多实例重复执行；桌面 inline 模式与 Celery beat 共用 ``run_due_jobs``。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.evolution.models import KnowledgeCandidate, Skill
from app.modules.llm.schemas import ChatMessage, LLMMetadata, LLMRequest, LLMResponse
from app.modules.llm.service import complete_with_logging
from app.modules.self_improve.evidence import collect_evidence
from app.modules.self_improve.models import (
    JOB_SELF_IMPROVE,
    JOB_STATUS_ACTIVE,
    JOB_STATUS_PAUSED,
    MEMORY_SUPERSEDED,
    RUN_FAILED,
    RUN_RUNNING,
    RUN_SKIPPED,
    RUN_SUCCESS,
    TRIGGER_MANUAL,
    TRIGGER_SCHEDULE,
    SelfImproveMemory,
    YardJob,
    YardJobRun,
)
from app.modules.yards.settings_service import get_setting

log = get_logger(__name__)

#: 租约时长（秒）：单轮执行超过该时长后其他实例才可接管（防进程崩溃死锁）
LEASE_SECONDS = 300
#: LLM 失败重试（1 次执行 + 2 次重试）
LLM_ATTEMPTS = 3
#: 单次产出 token 上限与证据截断（成本控制）
MAX_OUTPUT_TOKENS = 2000
MAX_EVIDENCE_CHARS = 12000
#: 产出条数上限
MAX_PREFERENCES = 30
MAX_SKILL_PROPOSALS = 3
MAX_KNOWLEDGE_CANDIDATES = 3
MAX_SUMMARY_CHARS = 200
_MAX_TAGS = 5
_MAX_TAG_CHARS = 24
_MAX_PROCEDURE = 8
_MAX_PROCEDURE_CHARS = 200
_MAX_CONTENT_CHARS = 800
_MAX_REPORT_CHARS = 500

PRESET_INTERVALS: dict[str, timedelta] = {
    "hours6": timedelta(hours=6),
    "hours12": timedelta(hours=12),
    "daily": timedelta(days=1),
    "days3": timedelta(days=3),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}
CUSTOM_UNITS: dict[str, timedelta] = {
    "hours": timedelta(hours=1),
    "days": timedelta(days=1),
    "months": timedelta(days=30),
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _sha12(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def schedule_interval(setting: dict[str, Any]) -> timedelta:
    """设置项 → 运行周期。custom 非法时回落 daily。"""
    schedule = setting.get("schedule") or {}
    preset = str(schedule.get("preset") or "daily")
    if preset == "custom":
        raw_value = schedule.get("custom_value")
        unit = str(schedule.get("custom_unit") or "days")
        try:
            value = int(raw_value) if raw_value is not None else 0
        except (TypeError, ValueError):
            return PRESET_INTERVALS["daily"]
        base = CUSTOM_UNITS.get(unit, CUSTOM_UNITS["days"])
        return base * max(1, min(value, 720))
    return PRESET_INTERVALS.get(preset, PRESET_INTERVALS["daily"])


# ---------------------------------------------------------------------------
# 任务同步（设置变更时对齐 yard_jobs）
# ---------------------------------------------------------------------------


def sync_job(db: Session, *, yard_id: str, setting: dict[str, Any], actor_id: str) -> YardJob:
    """创建/对齐 self_improve 定时任务：状态与周期随设置即时生效。"""
    now = _utcnow()
    interval = schedule_interval(setting)
    enabled = bool(setting.get("enabled"))
    job = db.scalar(
        select(YardJob).where(YardJob.yard_id == yard_id, YardJob.kind == JOB_SELF_IMPROVE)
    )
    schedule = dict(setting.get("schedule") or {})
    payload = {
        "scope": setting.get("scope"),
        "retain_days": setting.get("retain_days"),
        "notify_owner": setting.get("notify_owner"),
        "auto_publish": setting.get("auto_publish"),
    }
    if job is None:
        job = YardJob(
            yard_id=yard_id,
            kind=JOB_SELF_IMPROVE,
            status=JOB_STATUS_ACTIVE if enabled else JOB_STATUS_PAUSED,
            schedule_json=schedule,
            payload_json=payload,
            created_by=actor_id,
        )
        db.add(job)
        job.next_run_at = now + interval if enabled else None
        job.lease_until = None
    else:
        was_enabled = job.status == JOB_STATUS_ACTIVE
        job.schedule_json = schedule
        job.payload_json = payload
        job.status = JOB_STATUS_ACTIVE if enabled else JOB_STATUS_PAUSED
        if enabled and (not was_enabled or _interval_changed(db, job, interval, now)):
            # 新开启或周期已变化：重新排期；已排期且未到期则保持，避免保存即抢跑
            job.next_run_at = now + interval
        elif not enabled:
            job.next_run_at = None
            job.lease_until = None
    db.flush()
    return job


def _interval_changed(db: Session, job: YardJob, interval: timedelta, now: datetime) -> bool:
    """周期已变化：上次排期落后一个周期以上，或尚未排期。"""
    next_at = _as_utc(job.next_run_at)
    if next_at is None:
        return True
    return (now - next_at) >= interval


# ---------------------------------------------------------------------------
# 调度入口（Celery beat / 桌面 inline 线程 / 测试）
# ---------------------------------------------------------------------------


def run_due_jobs(db: Session, *, now: datetime | None = None, limit: int = 20) -> dict[str, int]:
    """执行一轮到期的 self_improve 任务（租约互斥）。单任务失败不影响其余。"""
    now = _as_utc(now) or _utcnow()
    stats = {"due": 0, "success": 0, "failed": 0, "skipped": 0}
    jobs = list(
        db.scalars(
            select(YardJob).where(
                YardJob.kind == JOB_SELF_IMPROVE,
                YardJob.status == JOB_STATUS_ACTIVE,
                YardJob.next_run_at.isnot(None),
            )
        ).all()
    )
    for job in jobs:
        next_at = _as_utc(job.next_run_at)
        if next_at is None or next_at > now:
            continue
        lease = _as_utc(job.lease_until)
        if lease is not None and lease > now:
            continue  # 其他实例正持有租约
        # 原子抢租（v3.56）：UPDATE 带租约条件，按 rowcount 判定——多实例/
        # beat+inline 双调度同时到期时只有一个能抢到，杜绝双跑双倍 LLM 消耗。
        claimed = cast(
            CursorResult[Any],
            db.execute(
                update(YardJob)
                .where(
                    YardJob.id == job.id,
                    or_(YardJob.lease_until.is_(None), YardJob.lease_until <= now),
                )
                .values(lease_until=now + timedelta(seconds=LEASE_SECONDS))
            ),
        )
        db.flush()
        if claimed.rowcount != 1:
            continue  # 竞态落败：别的实例刚抢到
        stats["due"] += 1
        db.refresh(job)
        try:
            run = _execute(
                db,
                job,
                trigger=TRIGGER_SCHEDULE,
                actor_id=job.created_by,
                request_id=f"job:{job.id}",
                now=now,
            )
            stats[run.status] += 1
        except Exception as e:  # noqa: BLE001 - 调度线程绝不因单任务崩溃
            log.warning("self_improve due job failed: %s", e)
            # 回滚半执行（可能已 flush 的 running 行），只落一条失败台账
            db.rollback()
            _record_failed_run(db, job, str(e)[:200], now, trigger=TRIGGER_SCHEDULE)
            stats["failed"] += 1
        finally:
            interval = schedule_interval(get_setting(db, job.yard_id, "self_improve"))
            job.lease_until = None
            job.last_run_at = now
            job.next_run_at = now + interval
            db.commit()
    return stats


def _record_failed_run(
    db: Session, job: YardJob, error_text: str, now: datetime, *, trigger: str
) -> None:
    run = YardJobRun(
        yard_id=job.yard_id,
        job_id=job.id,
        kind=job.kind,
        status=RUN_FAILED,
        trigger=trigger,
        started_at=now,
        finished_at=now,
        duration_ms=0,
        error_text=error_text,
    )
    db.add(run)
    db.commit()


# ---------------------------------------------------------------------------
# 手动触发
# ---------------------------------------------------------------------------


def run_once(
    db: Session, *, yard_id: str, actor_id: str, request_id: str, now: datetime | None = None
) -> YardJobRun:
    """负责人/被授权管理员手动触发一次（同步执行，前端 120s 内可完成）。"""
    now = _as_utc(now) or _utcnow()
    setting = get_setting(db, yard_id, "self_improve")
    job = sync_job(db, yard_id=yard_id, setting=setting, actor_id=actor_id)
    if job.status == JOB_STATUS_PAUSED:
        raise AppError(
            "SELF_IMPROVE_DISABLED",
            "自我总结提升尚未开启，请先在设置中开启后再手动运行。",
            http_status=409,
        )
    used = _used_budget_today(db, yard_id, now)
    budget = int(setting.get("daily_budget_tokens") or 0)
    if used >= budget:
        raise AppError(
            "SELF_IMPROVE_BUDGET_EXHAUSTED",
            "今日自我总结 token 预算已用尽，明天再试或提高预算。",
            http_status=409,
            details={"used_today": used, "budget": budget},
        )
    run = _execute(
        db,
        job,
        trigger=TRIGGER_MANUAL,
        actor_id=actor_id,
        request_id=request_id,
        now=now,
    )
    job.last_run_at = now
    db.commit()
    return run


# ---------------------------------------------------------------------------
# 执行体（调度/手动共用）
# ---------------------------------------------------------------------------


def _day_start(now: datetime) -> datetime:
    return datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC)


def _used_budget_today(db: Session, yard_id: str, now: datetime) -> int:
    start = _day_start(now)
    total = db.scalar(
        select(func.coalesce(func.sum(YardJobRun.tokens_used), 0)).where(
            YardJobRun.yard_id == yard_id, YardJobRun.started_at >= start
        )
    )
    return int(total or 0)


def _build_prompt(evidence: dict[str, Any], scope: str) -> list[ChatMessage]:
    scope_hint = (
        "按成员逐人归纳偏好（preferences 的 user 填 username，usernames 必须取自证据）"
        if scope == "per_member"
        else "归纳小院整体偏好（preferences 的 user 填空字符串）"
    )
    text = json.dumps(evidence, ensure_ascii=False)
    if len(text) > MAX_EVIDENCE_CHARS:
        text = text[: MAX_EVIDENCE_CHARS - 1] + "…"
    system = (
        "你是农业科教小院的改进助手。基于给定窗口内的主题级证据（会话主题/反馈/工具与失败/"
        "token 消耗，不含会话原文），产出 JSON 对象（不要解释、不要代码块围栏），结构："
        '{"preferences":[{"user":str,"summary":str(≤200字),"tags":[str(≤5项,每项≤24字)]}],'
        '"skill_proposals":[{"title":str,"when_to_use":str,"procedure":[str(2-8步,每步≤200字)],'
        '"evidence":[str(≤3条)}],"knowledge_candidates":[{"title":str,"content":str(≤800字)}],'
        '"report":str(≤500字，说明本轮发现了什么、产出了什么)}。'
        "要求：全部中文；skill_proposals≤3 条、knowledge_candidates≤3 条、preferences 按证据裁剪；"
        "证据不足的类别给空数组，禁止编造；不出现会话原文。"
    )
    user = f"归纳口径：{scope_hint}。\n窗口证据（JSON）：\n{text}"
    return [ChatMessage(role="system", content=system), ChatMessage(role="user", content=user)]


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _parse_payload(content: str) -> dict[str, Any]:
    text = _FENCE_RE.sub("", (content or "").strip()).strip()
    payloads: list[str] = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        payloads.append(text[start : end + 1])
    for payload in payloads:
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    raise ValueError("模型输出中未找到合法的总结 JSON")


def _clean_str(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _persist_outputs(
    db: Session,
    *,
    yard_id: str,
    payload: dict[str, Any],
    evidence: dict[str, Any],
    window: tuple[datetime, datetime],
    run_id: str,
    scope: str,
    auto_publish: bool,
    actor_id: str | None,
    request_id: str,
) -> dict[str, int]:
    counts = {"preferences": 0, "skills": 0, "knowledge": 0, "auto_published": 0}

    # 用户名 → 成员 id（per_member 映射；映射不上的条目直接丢弃，不编造归属）
    members = {
        _clean_str(item.get("username"), 64): str(item.get("user_id"))
        for item in evidence.get("members", [])
    }

    prefs = [p for p in (payload.get("preferences") or []) if isinstance(p, dict)][:MAX_PREFERENCES]
    for pref in prefs:
        summary = _clean_str(pref.get("summary"), MAX_SUMMARY_CHARS)
        if not summary:
            continue
        if scope == "per_member":
            user_name = _clean_str(pref.get("user"), 64)
            user_id = members.get(user_name)
            if user_id is None:
                continue  # 证据里没有的成员不产生记忆
        else:
            user_id = None
        # 同 scope 旧记忆接管（superseded 保留审计）
        if user_id is None:
            _mem_cond: Any = SelfImproveMemory.user_id.is_(None)
        else:
            _mem_cond = SelfImproveMemory.user_id == user_id
        db.query(SelfImproveMemory).filter(
            SelfImproveMemory.yard_id == yard_id,
            _mem_cond,
            SelfImproveMemory.status == "active",
        ).update({SelfImproveMemory.status: MEMORY_SUPERSEDED}, synchronize_session=False)
        tags = [
            _clean_str(tag, _MAX_TAG_CHARS)
            for tag in (pref.get("tags") or [])
            if _clean_str(tag, _MAX_TAG_CHARS)
        ][:_MAX_TAGS]
        db.add(
            SelfImproveMemory(
                yard_id=yard_id,
                user_id=user_id,
                summary=summary,
                tags_json=tags,
                window_from=window[0],
                window_to=window[1],
                source_run_id=run_id,
            )
        )
        counts["preferences"] += 1
        db.flush()

    for proposal in [p for p in (payload.get("skill_proposals") or []) if isinstance(p, dict)][
        :MAX_SKILL_PROPOSALS
    ]:
        title = _clean_str(proposal.get("title"), 200)
        procedure = [
            _clean_str(step, _MAX_PROCEDURE_CHARS)
            for step in (proposal.get("procedure") or [])
            if _clean_str(step, _MAX_PROCEDURE_CHARS)
        ][:_MAX_PROCEDURE]
        if not title or not procedure:
            continue  # 空壳提案不落库
        skill_code = "auto-" + _sha12("|".join([title, *procedure]))
        existing = db.scalar(
            select(Skill).where(
                Skill.yard_id == yard_id,
                Skill.skill_code == skill_code,
                Skill.status.in_(("pending_review", "published", "draft")),
            )
        )
        if existing is not None:
            continue  # 已有同名提案不重复堆积
        version = "1.0.0"
        definition = {
            "title": title,
            "when_to_use": _clean_str(proposal.get("when_to_use"), 500),
            "procedure": procedure,
            "examples": [],
            "evidence": [_clean_str(item, 200) for item in (proposal.get("evidence") or [])][:3],
            "window": {"from": window[0].isoformat(), "to": window[1].isoformat()},
        }
        db.add(
            Skill(
                yard_id=yard_id,
                skill_code=skill_code,
                version=version,
                definition_json=definition,
                status="pending_review",
                created_from="auto:self_improve",
                visibility="yard",
                origin="self_improve",
            )
        )
        counts["skills"] += 1
        db.flush()

    for cand in [c for c in (payload.get("knowledge_candidates") or []) if isinstance(c, dict)][
        :MAX_KNOWLEDGE_CANDIDATES
    ]:
        title = _clean_str(cand.get("title"), 200)
        content = _clean_str(cand.get("content"), _MAX_CONTENT_CHARS)
        if not title or not content:
            continue
        dup = db.scalar(
            select(KnowledgeCandidate).where(
                KnowledgeCandidate.yard_id == yard_id,
                KnowledgeCandidate.source_type == "self_improve",
                KnowledgeCandidate.title == title,
                KnowledgeCandidate.status.in_(("pending_review", "approved")),
            )
        )
        if dup is not None:
            continue
        candidate = KnowledgeCandidate(
            yard_id=yard_id,
            title=title,
            content=content,
            source_type="self_improve",
            status="pending_review",
        )
        db.add(candidate)
        db.flush()
        counts["knowledge"] += 1
        if auto_publish:
            # 自动通过知识候选（技能提案始终保留人工审核）；入库失败不阻断本轮
            try:
                from app.core.deps import YardContext
                from app.modules.evolution.service import review_candidate

                ctx = YardContext(
                    yard_id=yard_id,
                    user_id=actor_id or "",
                    roles=["yard_owner"],
                    request_id=request_id,
                )
                review_candidate(db, ctx, candidate.id, "approve", "自我总结自动发布", request_id)
                counts["auto_published"] += 1
            except Exception as e:  # noqa: BLE001 - 单条失败不影响其余产出
                log.warning("self_improve auto publish failed: %s", e)

    db.flush()
    return counts


def _retain(db: Session, yard_id: str, retain_days: int | None, now: datetime) -> None:
    """保留窗口外的运行台账与旧记忆（审计留痕，不无限增长）。"""
    days = int(retain_days or 90)
    cutoff = now - timedelta(days=days)
    db.query(YardJobRun).filter(
        YardJobRun.yard_id == yard_id, YardJobRun.created_at < cutoff
    ).delete(synchronize_session=False)
    db.query(SelfImproveMemory).filter(
        SelfImproveMemory.yard_id == yard_id,
        SelfImproveMemory.status == MEMORY_SUPERSEDED,
        SelfImproveMemory.updated_at < cutoff,
    ).delete(synchronize_session=False)


def _execute(
    db: Session,
    job: YardJob,
    *,
    trigger: str,
    actor_id: str | None,
    request_id: str,
    now: datetime,
) -> YardJobRun:
    started = time.monotonic()
    yard_id = job.yard_id
    setting = get_setting(db, yard_id, "self_improve")
    interval = schedule_interval(setting)
    retain_days = int(setting.get("retain_days") or 90)

    run = YardJobRun(
        yard_id=yard_id,
        job_id=job.id,
        kind=job.kind,
        status=RUN_RUNNING,
        trigger=trigger,
        started_at=now,
    )
    db.add(run)
    db.flush()

    def finish(
        status: str, *, output: dict[str, Any] | None = None, error: str | None = None
    ) -> None:
        run.status = status
        run.finished_at = _utcnow()
        run.duration_ms = int((time.monotonic() - started) * 1000)
        if output is not None:
            run.output_json = output
        run.error_text = error

    if not setting.get("enabled"):
        finish(RUN_SKIPPED, error="disabled")
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=actor_id,
            action="self_improve_run",
            resource_type="yard_job",
            resource_id=job.id,
            result="skipped",
            request_id=request_id,
            metadata={"trigger": trigger, "reason": "disabled"},
        )
        db.flush()
        return run

    budget = int(setting.get("daily_budget_tokens") or 0)
    used = _used_budget_today(db, yard_id, now)
    if used >= budget:
        finish(RUN_SKIPPED, error="daily_budget_exhausted")
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=actor_id,
            action="self_improve_run",
            resource_type="yard_job",
            resource_id=job.id,
            result="skipped",
            request_id=request_id,
            metadata={"trigger": trigger, "reason": "daily_budget_exhausted", "used": used},
        )
        db.flush()
        return run

    # 窗口：上次运行 → 本次（首次运行/手动补跑取一个周期），保留期封顶
    last = _as_utc(job.last_run_at)
    window_start = last if last and last > now - interval * 2 else now - interval
    window_start = max(window_start, now - timedelta(days=retain_days))
    window = (window_start, now)

    evidence = collect_evidence(db, yard_id, window_start, now)
    if (
        evidence["sessions_total"] == 0
        and evidence["messages_total"] == 0
        and not evidence["feedback"]["samples"]
    ):
        # 窗口内无可用数据：诚实记一次成功（无产出），不调用 LLM、不落脏数据
        finish(RUN_SUCCESS, output={"note": "窗口内无可用会话数据，本轮未调用模型", "counts": {}})
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=actor_id,
            action="self_improve_run",
            resource_type="yard_job",
            resource_id=job.id,
            result="success",
            request_id=request_id,
            metadata={"trigger": trigger, "counts": {}, "empty_window": True},
        )
        db.flush()
        return run

    messages = _build_prompt(evidence, str(setting.get("scope") or "per_member"))
    remaining = max(0, budget - used)
    max_tokens = min(MAX_OUTPUT_TOKENS, max(500, remaining))

    payload: dict[str, Any] | None = None
    tokens_used = 0
    last_error: str | None = None
    for _ in range(LLM_ATTEMPTS):
        try:
            resp: LLMResponse = asyncio.run(
                complete_with_logging(
                    db,
                    LLMRequest(
                        messages=messages,
                        max_tokens=max_tokens,
                        metadata=LLMMetadata(
                            yard_id=yard_id,
                            user_id=actor_id,
                            request_id=request_id,
                            purpose="self_improve",
                            agent_ref="self_improve.run",
                        ),
                    ),
                )
            )
            tokens_used = int(resp.input_tokens or 0) + int(resp.output_tokens or 0)
            payload = _parse_payload(resp.content or "")
            break
        except AppError as e:
            last_error = f"llm_unavailable:{e.code}"
        except (ValueError, json.JSONDecodeError) as e:
            last_error = f"parse_failed:{e}"
        except Exception as e:  # noqa: BLE001 - 非业务异常同样不落半成品
            last_error = f"{type(e).__name__}:{e}"

    if payload is None:
        finish(RUN_FAILED, error=last_error or "llm_failed")
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=actor_id,
            action="self_improve_run",
            resource_type="yard_job",
            resource_id=job.id,
            result="failed",
            request_id=request_id,
            metadata={"trigger": trigger, "reason": last_error, "tokens_used": tokens_used},
        )
        db.flush()
        return run

    auto_publish = bool(setting.get("auto_publish"))
    counts = _persist_outputs(
        db,
        yard_id=yard_id,
        payload=payload,
        evidence=evidence,
        window=window,
        run_id=run.id,
        scope=str(setting.get("scope") or "per_member"),
        auto_publish=auto_publish,
        actor_id=actor_id,
        request_id=request_id,
    )
    # 同一条每日调度顺带处理「已开启自动升级且到门槛」的技能，单条失败不影响总结
    upgrade_counts: dict[str, Any] = {}
    try:
        from app.modules.evolution.auto_upgrade import run_due_upgrades

        upgrade_counts = run_due_upgrades(db, yard_id=yard_id, request_id=request_id)
    except Exception as e:  # noqa: BLE001 - 自动升级失败不阻断自我总结
        log.warning("skill auto upgrade failed: %s", e)
        upgrade_counts = {"error": str(e)[:200]}
    finish(
        RUN_SUCCESS,
        output={
            "report": _clean_str(payload.get("report"), _MAX_REPORT_CHARS),
            "counts": counts,
            "skill_upgrades": upgrade_counts,
            "window": {"from": window[0].isoformat(), "to": window[1].isoformat()},
        },
    )
    run.tokens_used = tokens_used
    _retain(db, yard_id, retain_days, now)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="self_improve_run",
        resource_type="yard_job",
        resource_id=job.id,
        result="success",
        request_id=request_id,
        metadata={
            "trigger": trigger,
            "counts": counts,
            "tokens_used": tokens_used,
            # 审计不存正文：只记产出条数与窗口
        },
    )
    notify = bool(setting.get("notify_owner"))
    has_window = isinstance(run.output_json, dict) and "window" in run.output_json
    if notify and has_window:
        try:
            from app.modules.push.service import dispatch

            dispatch(
                db,
                yard_id=yard_id,
                event_type="self_improve_run",
                payload={
                    "title": "自我总结已完成",
                    "outputs": (
                        f"偏好 {counts['preferences']} / 技能提案 {counts['skills']} / "
                        f"知识候选 {counts['knowledge']}"
                    ),
                    "window": str((run.output_json or {}).get("window", {}).get("from", ""))[:10],
                },
                request_id=request_id,
            )
        except Exception as e:  # noqa: BLE001 - 推送失败不影响主流程
            log.warning("self_improve notify failed: %s", e)
    db.flush()
    return run


# ---------------------------------------------------------------------------
# 运行台账与预算视图（负责人设置页/运行报告）
# ---------------------------------------------------------------------------


def run_out(run: YardJobRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "job_id": run.job_id,
        "trigger": run.trigger,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "duration_ms": run.duration_ms,
        "tokens_used": run.tokens_used,
        "output": run.output_json,
        "error": run.error_text,
        "actor_id": run.actor_id,
    }


def list_runs(db: Session, yard_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
    rows = list(
        db.scalars(
            select(YardJobRun)
            .where(YardJobRun.yard_id == yard_id)
            .order_by(YardJobRun.started_at.desc())
            .limit(min(max(int(limit), 1), 100))
        )
    )
    return [run_out(row) for row in rows]


def list_memories(
    db: Session, yard_id: str, *, user_id: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    if user_id:
        user_cond = SelfImproveMemory.user_id == user_id
    else:
        user_cond = SelfImproveMemory.user_id.is_(None)
    rows = db.scalars(
        select(SelfImproveMemory)
        .where(
            SelfImproveMemory.yard_id == yard_id,
            SelfImproveMemory.status == "active",
            user_cond,
        )
        .order_by(SelfImproveMemory.created_at.desc())
        .limit(min(max(int(limit), 1), 100))
    ).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "summary": row.summary,
            "tags": row.tags_json or [],
            "window_from": row.window_from.isoformat() if row.window_from else None,
            "window_to": row.window_to.isoformat() if row.window_to else None,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


__all__ = [
    "JOB_SELF_IMPROVE",
    "MAX_OUTPUT_TOKENS",
    "PRESET_INTERVALS",
    "list_memories",
    "list_runs",
    "run_due_jobs",
    "run_once",
    "run_out",
    "schedule_interval",
    "sync_job",
]
