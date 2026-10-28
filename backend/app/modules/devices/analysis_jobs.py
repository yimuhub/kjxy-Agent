"""设备分析的定时调度（v3.23）：复用 ``yard_jobs`` 底座，kind=device_analysis。

节奏：每天（可配 12 小时/每周）为本小院所有启用设备各跑一次分析；同一天同一设备
重复运行按 ``analysis_key`` 幂等。Celery beat 与桌面 inline 线程都通过
``run_due_analyses`` 进入，与 self_improve 调度同哲学（DB 租约互斥）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.devices import analysis_service
from app.modules.devices.models import Device
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

JOB_DEVICE_ANALYSIS = analysis_service.DEVICE_ANALYSIS_JOB
LEASE_SECONDS = 900
PRESET_INTERVALS: dict[str, timedelta] = {
    "hours12": timedelta(hours=12),
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
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
    job = db.scalar(
        select(YardJob).where(YardJob.yard_id == yard_id, YardJob.kind == JOB_DEVICE_ANALYSIS)
    )
    if job is None:
        job = YardJob(
            yard_id=yard_id,
            kind=JOB_DEVICE_ANALYSIS,
            status=JOB_STATUS_ACTIVE if enabled else JOB_STATUS_PAUSED,
            schedule_json=dict(setting.get("schedule") or {}),
            payload_json={"window_hours": setting.get("window_hours")},
            created_by=actor_id,
        )
        db.add(job)
        job.next_run_at = now + interval if enabled else None
        job.lease_until = None
    else:
        was_enabled = job.status == JOB_STATUS_ACTIVE
        job.schedule_json = dict(setting.get("schedule") or {})
        job.payload_json = {"window_hours": setting.get("window_hours")}
        job.status = JOB_STATUS_ACTIVE if enabled else JOB_STATUS_PAUSED
        if enabled and not was_enabled:
            job.next_run_at = now + interval
        elif not enabled:
            job.next_run_at = None
            job.lease_until = None
    db.flush()
    return job


def ensure_jobs(db: Session) -> int:
    """为「有启用设备」且尚未建任务的小院补建每日任务（首轮不抢跑）。"""
    created = 0
    yard_ids = list(db.scalars(select(Device.yard_id).where(Device.enabled.is_(True)).distinct()))
    for yard_id in yard_ids:
        exists = db.scalar(
            select(YardJob.id).where(
                YardJob.yard_id == yard_id, YardJob.kind == JOB_DEVICE_ANALYSIS
            )
        )
        if exists:
            continue
        setting = get_setting(db, yard_id, "device_analysis")
        if not setting.get("enabled"):
            continue
        sync_job(db, yard_id=yard_id, setting=setting, actor_id="system")
        created += 1
    return created


def run_due_analyses(
    db: Session, *, now: datetime | None = None, limit: int = 20
) -> dict[str, int]:
    now = _as_utc(now) or _utcnow()
    stats = {"due": 0, "devices": 0, "ok": 0, "insufficient": 0, "failed": 0, "skipped": 0}
    ensure_jobs(db)
    db.flush()
    jobs = list(
        db.scalars(
            select(YardJob).where(
                YardJob.kind == JOB_DEVICE_ANALYSIS,
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
            kind=JOB_DEVICE_ANALYSIS,
            status=RUN_RUNNING,
            trigger=TRIGGER_SCHEDULE,
            started_at=started,
        )
        db.add(run)
        db.flush()
        devices = list(
            db.scalars(
                select(Device).where(Device.yard_id == job.yard_id, Device.enabled.is_(True))
            )
        )
        errors: list[dict[str, str]] = []
        counts = {"ok": 0, "insufficient": 0, "failed": 0, "skipped": 0}
        tokens = 0
        for device in devices:
            try:
                # 每台设备一个保存点：单台失败只回滚该台，不影响同批其他设备
                with db.begin_nested():
                    row, created = analysis_service.run_analysis(
                        db,
                        device,
                        trigger="schedule",
                        actor_id=None,
                        request_id=f"job:{job.id}",
                        now=now,
                    )
                tokens += int(row.tokens_used or 0)
                if not created:
                    counts["skipped"] += 1
                elif row.status == "insufficient":
                    counts["insufficient"] += 1
                else:
                    counts["ok"] += 1
            except AppError as e:
                if e.code == "DEVICE_ANALYSIS_INSUFFICIENT_DATA":
                    counts["insufficient"] += 1
                else:
                    counts["failed"] += 1
                    errors.append({"device_id": device.id, "error": f"{e.code}: {e.message}"})
            except Exception as e:  # noqa: BLE001 - 单设备失败不影响其余设备
                counts["failed"] += 1
                errors.append({"device_id": device.id, "error": str(e)[:200]})
        stats["devices"] += len(devices)
        for key in ("ok", "insufficient", "failed", "skipped"):
            stats[key] += counts[key]
        finished = _utcnow()
        run.status = (
            RUN_FAILED
            if counts["failed"] and not (counts["ok"] or counts["insufficient"])
            else RUN_SUCCESS
        )
        run.finished_at = finished
        run.duration_ms = int((finished - started).total_seconds() * 1000)
        run.tokens_used = tokens
        run.output_json = {
            "devices": len(devices),
            "ok": counts["ok"],
            "insufficient": counts["insufficient"],
            "failed": counts["failed"],
            "skipped": counts["skipped"],
            "errors": errors[:5],
        }
        job.lease_until = None
        job.last_run_at = now
        job.next_run_at = now + schedule_interval(get_setting(db, job.yard_id, "device_analysis"))
        db.commit()
    return stats
