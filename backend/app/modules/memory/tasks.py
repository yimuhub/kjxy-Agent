"""记忆日摘要的桌面 inline 调度。

不引入 Celery/Redis：桌面单机由 API 进程 daemon 线程周期补齐最近两天的日复盘。
服务器部署如已有 beat，可把 ``run_once`` 挂到现有调度；本模块只负责桌面兜底。
"""

from __future__ import annotations

import threading
import time

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.memory.service import summarize_yesterday_for_all

log = get_logger(__name__)

#: 日摘要巡检节奏（秒）；真正是否写库由「最近两天有没有运行」决定
INLINE_POLL_SECONDS = 1800

_scheduler_started = False
_scheduler_lock = threading.Lock()
_stop_event = threading.Event()


def run_once() -> int:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return summarize_yesterday_for_all(db)
    except Exception as exc:  # noqa: BLE001 - 调度线程绝不因单次失败退出
        db.rollback()
        log.warning("memory daily summary poll failed: %s", exc)
        return 0
    finally:
        db.close()


def start_inline_scheduler() -> bool:
    """TASKS_INLINE_MODE 下启动 daemon 线程（幂等）。返回是否新启动。"""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return False
        _scheduler_started = True
    if not settings.tasks_inline_mode:
        return False

    def _loop() -> None:
        log.info("memory inline scheduler started (every %ss)", INLINE_POLL_SECONDS)
        deadline = time.monotonic() + INLINE_POLL_SECONDS
        while not _stop_event.wait(timeout=max(0.5, deadline - time.monotonic())):
            created = run_once()
            if created:
                log.info("memory daily summaries created: %s", created)
            deadline = time.monotonic() + INLINE_POLL_SECONDS

    thread = threading.Thread(target=_loop, name="memory-daily-summary", daemon=True)
    thread.start()
    return True


def stop_inline_scheduler() -> None:
    _stop_event.set()


__all__ = ["INLINE_POLL_SECONDS", "run_once", "start_inline_scheduler", "stop_inline_scheduler"]
