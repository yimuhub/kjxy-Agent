"""经验资产巡检：桌面 inline 模式下每日回填工作流/黑名单并重算技能。

不引入 Celery/Redis：只扫描近 8 天有运行的小院，逐院提交，单院失败不影响其他小院。
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.commander import learning
from app.modules.commander import settings as commander_settings
from app.modules.commander.models import CommanderRun

log = get_logger(__name__)

INLINE_POLL_SECONDS = 6 * 3600
_ACTIVE_DAYS = 8

_scheduler_started = False
_scheduler_lock = threading.Lock()
_stop_event = threading.Event()


def _active_yard_ids(db: Session, since: datetime) -> list[str]:
    rows = db.scalars(
        select(CommanderRun.yard_id).where(CommanderRun.created_at >= since).distinct()
    )
    return [str(value) for value in rows]


def run_once(now: datetime | None = None) -> int:
    from app.db.session import SessionLocal

    moment = now or datetime.now(UTC)
    since = moment - timedelta(days=_ACTIVE_DAYS)
    db = SessionLocal()
    scanned = 0
    try:
        for yard_id in _active_yard_ids(db, since):
            try:
                cfg = commander_settings.settings_for(db, yard_id)
                learn_workflows = bool(cfg.get("auto_learn_workflows", True))
                extract_skills = bool(cfg.get("auto_extract_skills", True))
                if not learn_workflows and not extract_skills:
                    continue
                learning.scan_yard(
                    db,
                    yard_id=yard_id,
                    failure_threshold=int(cfg.get("failure_blacklist_threshold") or 2),
                    learn_workflows=learn_workflows,
                    record_failures=learn_workflows,
                    extract_skills=extract_skills,
                )
                db.commit()
                scanned += 1
            except Exception as exc:  # noqa: BLE001 - 单院失败不阻塞其他小院
                db.rollback()
                log.warning("commander learning scan failed yard=%s: %s", yard_id, exc)
        return scanned
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
        log.info("commander learning scheduler started (every %ss)", INLINE_POLL_SECONDS)
        deadline = time.monotonic() + INLINE_POLL_SECONDS
        while not _stop_event.wait(timeout=max(0.5, deadline - time.monotonic())):
            try:
                scanned = run_once()
                if scanned:
                    log.info("commander learning scanned yards: %s", scanned)
            except Exception as exc:  # noqa: BLE001 - 调度线程不退出
                log.warning("commander learning poll failed: %s", exc)
            deadline = time.monotonic() + INLINE_POLL_SECONDS

    thread = threading.Thread(target=_loop, name="commander-learning", daemon=True)
    thread.start()
    return True


def stop_inline_scheduler() -> None:
    _stop_event.set()


__all__ = ["INLINE_POLL_SECONDS", "run_once", "start_inline_scheduler", "stop_inline_scheduler"]
