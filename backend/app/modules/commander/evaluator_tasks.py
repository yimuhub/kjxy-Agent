"""监督测评的桌面 inline 调度（v3.44 P1）。

每周一为最近 8 天有运行的小院补一份周报；报告按周幂等，重复巡检不会堆历史。
人格是否真正调整仍由 ``run_evaluation`` 内部的锁与开关决定。
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import YardContext
from app.core.logging import get_logger
from app.modules.commander import evaluator
from app.modules.commander import settings as commander_settings
from app.modules.commander.models import CommanderRun

log = get_logger(__name__)

INLINE_POLL_SECONDS = 3600

_scheduler_started = False
_scheduler_lock = threading.Lock()
_stop_event = threading.Event()


def _active_yard_ids(db: Session, since: datetime) -> list[str]:
    return list(
        db.scalars(select(CommanderRun.yard_id).where(CommanderRun.created_at >= since).distinct())
    )


def run_once(now: datetime | None = None) -> int:
    from app.db.session import SessionLocal

    moment = now or datetime.now(UTC)
    if moment.weekday() != 0:
        return 0
    db = SessionLocal()
    created = 0
    try:
        for yard_id in _active_yard_ids(db, moment - timedelta(days=8)):
            cfg = commander_settings.settings_for(db, yard_id)
            if not cfg.get("evaluator_enabled", True):
                continue
            ctx = YardContext(
                yard_id=yard_id,
                user_id="system:evaluator",
                roles=["yard_owner"],
                request_id="commander-eval-scheduler",
            )
            row, is_new = asyncio.run(evaluator.run_evaluation(db, ctx, use_llm=True, now=moment))
            db.commit()
            if is_new:
                created += 1
            log.info(
                "weekly evaluation done yard=%s week=%s persona=%s",
                yard_id,
                row.week_key,
                row.persona,
            )
    except Exception as exc:  # noqa: BLE001 - 调度线程不因单院失败退出
        db.rollback()
        log.warning("commander evaluation poll failed: %s", exc)
    finally:
        db.close()
    return created


def start_inline_scheduler() -> bool:
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return False
        _scheduler_started = True
    if not settings.tasks_inline_mode:
        return False

    def _loop() -> None:
        log.info("commander evaluation inline scheduler started (every %ss)", INLINE_POLL_SECONDS)
        deadline = 0.0
        while not _stop_event.wait(timeout=max(0.5, deadline)):
            try:
                created = run_once()
                if created:
                    log.info("commander evaluations created: %s", created)
            finally:
                deadline = INLINE_POLL_SECONDS

    thread = threading.Thread(target=_loop, name="commander-evaluation", daemon=True)
    thread.start()
    return True


def stop_inline_scheduler() -> None:
    _stop_event.set()


__all__ = [
    "INLINE_POLL_SECONDS",
    "run_once",
    "start_inline_scheduler",
    "stop_inline_scheduler",
]
