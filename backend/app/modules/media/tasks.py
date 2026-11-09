"""媒体后台任务：在途视频续查 + 过期清理（v3.73）。

- ``media.poll_videos``：每 5 秒续查在途视频任务，成功后转存并回填对话卡片；
- ``media.expire``：每小时按保留期清理视频文件（行保留，标记为已过期）。

生产由 Celery beat 驱动；桌面单机（TASKS_INLINE_MODE=true）由
``start_inline_scheduler`` 在 API 进程内起 daemon 线程，口径一致。
"""

from __future__ import annotations

import asyncio
import threading
import time

from app.core.config import settings
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.modules.media import service
from app.workers.celery_app import celery_app

log = get_logger(__name__)

INLINE_POLL_SECONDS = 5
INLINE_EXPIRE_SECONDS = 3600

_scheduler_lock = threading.Lock()
_scheduler_started = False
_stop_event = threading.Event()


@celery_app.task(name="media.poll_videos")
def poll_videos_task() -> dict[str, int]:
    db = SessionLocal()
    try:
        processed = asyncio.run(service.poll_pending(db, limit=6))
        return {"processed": processed}
    except Exception as e:  # noqa: BLE001 - 后台任务失败只记日志，不拖垮 beat
        log.warning("视频轮询任务失败: %s", e)
        db.rollback()
        return {"processed": 0}
    finally:
        db.close()


@celery_app.task(name="media.expire")
def expire_task() -> dict[str, int]:
    db = SessionLocal()
    try:
        removed = service.expire_media(db)
        db.commit()
        return {"removed": removed}
    except Exception as e:  # noqa: BLE001
        log.warning("媒体过期清理失败: %s", e)
        db.rollback()
        return {"removed": 0}
    finally:
        db.close()


def run_inline_poll_once() -> int:
    """同步跑一轮轮询（测试与桌面 inline 共用）。"""
    db = SessionLocal()
    try:
        processed = asyncio.run(service.poll_pending(db, limit=6))
        db.commit()
        return processed
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

    def _poll_loop() -> None:
        log.info("media inline video poller started (every %ss)", INLINE_POLL_SECONDS)
        deadline = time.monotonic() + INLINE_POLL_SECONDS
        while not _stop_event.wait(timeout=max(0.5, deadline - time.monotonic())):
            run_inline_poll_once()
            deadline = time.monotonic() + INLINE_POLL_SECONDS

    def _expire_loop() -> None:
        deadline = time.monotonic() + INLINE_EXPIRE_SECONDS
        while not _stop_event.wait(timeout=max(0.5, deadline - time.monotonic())):
            expire_task()
            deadline = time.monotonic() + INLINE_EXPIRE_SECONDS

    threading.Thread(target=_poll_loop, name="media-poll", daemon=True).start()
    threading.Thread(target=_expire_loop, name="media-expire", daemon=True).start()
    return True


def stop_inline_scheduler() -> None:
    _stop_event.set()


__all__ = [
    "expire_task",
    "poll_videos_task",
    "run_inline_poll_once",
    "start_inline_scheduler",
    "stop_inline_scheduler",
]
