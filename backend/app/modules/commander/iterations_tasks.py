"""月度迭代巡检：每月为上月有运行的小院生成版本报告与设置快照。

进化锁开启时跳过自动迭代；手动接口不受影响（人工指令优先）。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import timezone as business_time
from app.core.config import settings
from app.core.deps import YardContext
from app.core.logging import get_logger
from app.modules.commander import reports
from app.modules.commander import settings as commander_settings
from app.modules.commander.evolution_models import CommanderIteration
from app.modules.commander.models import CommanderRun

log = get_logger(__name__)

INLINE_POLL_SECONDS = 6 * 3600

_scheduler_started = False
_scheduler_lock = threading.Lock()
_stop_event = threading.Event()


def _month_window(now: datetime) -> tuple[str, datetime, datetime]:
    """上一个自然月的键与窗口；月界按业务时区切，与月度迭代报告的统计口径一致。"""
    local = now.astimezone(business_time.business_tz())
    first_of_month = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    previous = (first_of_month - timedelta(days=1)).replace(day=1)
    month_key = f"{previous.year:04d}-{previous.month:02d}"
    start, end = business_time.month_bounds(month_key)
    return month_key, start, end


def _yard_ids(db: Session, start: datetime, end: datetime) -> list[str]:
    rows = db.scalars(
        select(CommanderRun.yard_id)
        .where(
            CommanderRun.created_at >= start,
            CommanderRun.created_at < end,
        )
        .distinct()
    )
    return [str(value) for value in rows]


def run_once(now: datetime | None = None) -> int:
    from app.db.session import SessionLocal

    moment = now or datetime.now(UTC)
    month_key, start, end = _month_window(moment)
    db = SessionLocal()
    created = 0
    try:
        for yard_id in _yard_ids(db, start, end):
            existing = db.scalar(
                select(CommanderIteration).where(
                    CommanderIteration.yard_id == yard_id,
                    CommanderIteration.month_key == month_key,
                )
            )
            if existing is not None:
                continue
            cfg = commander_settings.settings_for(db, yard_id)
            if cfg.get("evolution_lock"):
                continue
            ctx = YardContext(
                yard_id=yard_id,
                user_id="commander-scheduler",
                roles=["platform_admin"],
                request_id=f"iteration-{month_key}",
            )
            reports.run_iteration(db, ctx=ctx, month=month_key, settings=cfg)
            db.commit()
            created += 1
        return created
    finally:
        db.close()


def start_inline_scheduler() -> bool:
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return False
        _scheduler_started = True
    if not settings.tasks_inline_mode:
        return False

    def _loop() -> None:
        log.info("commander iteration scheduler started (every %ss)", INLINE_POLL_SECONDS)
        deadline = time.monotonic() + INLINE_POLL_SECONDS
        while not _stop_event.wait(timeout=max(0.5, deadline - time.monotonic())):
            try:
                created = run_once()
                if created:
                    log.info("commander iterations created: %s", created)
            except Exception as exc:  # noqa: BLE001 - 调度线程不退出
                log.warning("commander iteration poll failed: %s", exc)
            deadline = time.monotonic() + INLINE_POLL_SECONDS

    thread = threading.Thread(target=_loop, name="commander-iteration", daemon=True)
    thread.start()
    return True


def stop_inline_scheduler() -> None:
    _stop_event.set()


__all__ = ["INLINE_POLL_SECONDS", "run_once", "start_inline_scheduler", "stop_inline_scheduler"]
