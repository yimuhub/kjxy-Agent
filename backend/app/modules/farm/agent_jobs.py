"""Agent 自动执行的定时调度（v3.28）：复用 ``yard_jobs`` 底座，kind=farm_agent。

节奏：默认每天一轮（可配每 6/12 小时）；每轮先跑设备分析（已有独立任务）再跑本任务，
这样规则读到的是最新分析结论。Celery beat 与桌面 inline 线程都通过
``run_due_automations`` 进入，与 self_improve/设备分析同哲学（DB 租约互斥）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.farm import agent_service
from app.modules.self_improve.models import (
    JOB_STATUS_ACTIVE,
    JOB_STATUS_PAUSED,
    RUN_FAILED,
    RUN_RUNNING,
    RUN_SUCCESS,
    TRIGGER_SCHEDULE,
    YardJob,
    YardJobRun,
)
from app.modules.yards.settings_service import get_setting

log = get_logger(__name__)

JOB_KIND = agent_service.JOB_KIND
LEASE_SECONDS = 900
PRESET_INTERVALS: dict[str, timedelta] = {
    "hours6": timedelta(hours=6),
    "hours12": timedelta(hours=12),
    "daily": timedelta(days=1),
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def schedule_interval(setting: dict[str, Any]) -> timedelta:
    preset = str((setting.get("schedule") or {}).get("preset") or "daily")
    return PRESET_INTERVALS.get(preset, PRESET_INTERVALS["daily"])


def sync_job(db: Session, *, yard_id: str, setting: dict[str, Any], actor_id: str) -> YardJob:
    """设置变更时对齐定时任务：启停与周期即时生效。"""
    now = _utcnow()
    interval = schedule_interval(setting)
    enabled = bool(setting.get("enabled"))
    job = db.scalar(select(YardJob).where(YardJob.yard_id == yard_id, YardJob.kind == JOB_KIND))
    if job is None:
        job = YardJob(
            yard_id=yard_id,
            kind=JOB_KIND,
            status=JOB_STATUS_ACTIVE if enabled else JOB_STATUS_PAUSED,
            schedule_json=dict(setting.get("schedule") or {}),
            payload_json={"dedupe_hours": setting.get("dedupe_hours")},
            created_by=actor_id,
        )
        db.add(job)
        job.next_run_at = now + interval if enabled else None
        job.lease_until = None
    else:
        was_enabled = job.status == JOB_STATUS_ACTIVE
        job.schedule_json = dict(setting.get("schedule") or {})
        job.payload_json = {"dedupe_hours": setting.get("dedupe_hours")}
        job.status = JOB_STATUS_ACTIVE if enabled else JOB_STATUS_PAUSED
        if enabled and not was_enabled:
            job.next_run_at = now + interval
        elif not enabled:
            job.next_run_at = None
            job.lease_until = None
    db.flush()
    return job


def ensure_jobs(db: Session) -> int:
    """为已启用自动执行、但还没建任务的小院补建任务（首轮不抢跑）。"""
    created = 0
    from app.modules.yards.models import YardSettings

    yard_ids = list(
        db.scalars(select(YardSettings.yard_id).where(YardSettings.key == "farm_agent").distinct())
    )
    for yard_id in yard_ids:
        setting = get_setting(db, yard_id, "farm_agent")
        if not setting.get("enabled"):
            continue
        exists = db.scalar(
            select(YardJob.id).where(YardJob.yard_id == yard_id, YardJob.kind == JOB_KIND)
        )
        if exists:
            continue
        sync_job(db, yard_id=yard_id, setting=setting, actor_id="system")
        created += 1
    return created


def run_due_automations(
    db: Session, *, now: datetime | None = None, limit: int = 10
) -> dict[str, int]:
    now = _as_utc(now) or _utcnow()
    stats = {"due": 0, "ok": 0, "insufficient": 0, "failed": 0}
    try:
        ensure_jobs(db)
        db.flush()
    except Exception as e:  # noqa: BLE001 —— 补建任务失败不阻断已有任务
        db.rollback()
        log.warning("farm agent ensure_jobs failed: %s", e)

    jobs = list(
        db.scalars(
            select(YardJob).where(
                YardJob.kind == JOB_KIND,
                YardJob.status == JOB_STATUS_ACTIVE,
                YardJob.next_run_at.isnot(None),
            )
        )
    )
    for job in jobs:
        if stats["due"] >= limit:
            break
        next_at = _as_utc(job.next_run_at)
        if next_at is None or next_at > now:
            continue
        lease = _as_utc(job.lease_until)
        if lease is not None and lease > now:
            continue
        stats["due"] += 1
        job.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        db.flush()
        started = now
        run = YardJobRun(
            yard_id=job.yard_id,
            job_id=job.id,
            kind=JOB_KIND,
            status=RUN_RUNNING,
            trigger=TRIGGER_SCHEDULE,
            started_at=started,
        )
        db.add(run)
        db.flush()
        try:
            result = agent_service.run_agent(
                db,
                yard_id=job.yard_id,
                actor_id=None,
                request_id=f"job:{job.id}",
                trigger="schedule",
                now=now,
            )
            status = str(result.get("status") or "ok")
            if status in ("ok", "insufficient"):
                stats[status] += 1
            else:
                stats["failed"] += 1
            run.status = RUN_SUCCESS if status != "failed" else RUN_FAILED
            run.output_json = {
                "status": status,
                "findings": len(result.get("findings") or []),
                "actions": len(result.get("actions") or []),
            }
            run.tokens_used = int(result.get("tokens_used") or 0)
        except Exception as e:  # noqa: BLE001 —— 单院落失败不影响其他院落
            db.rollback()
            stats["failed"] += 1
            run.status = RUN_FAILED
            run.output_json = {"error": str(e)[:200]}
            log.warning("farm agent job failed yard=%s: %s", job.yard_id, e)
        finished = _utcnow()
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        try:
            fresh = db.get(YardJob, job.id)
            if fresh is not None:
                fresh.lease_until = None
                fresh.last_run_at = now
                fresh.next_run_at = now + schedule_interval(
                    get_setting(db, fresh.yard_id, "farm_agent")
                )
            db.commit()
        except Exception as e:  # noqa: BLE001
            db.rollback()
            log.warning("farm agent job finalize failed: %s", e)
    return stats


__all__ = ["JOB_KIND", "PRESET_INTERVALS", "ensure_jobs", "run_due_automations", "sync_job"]
