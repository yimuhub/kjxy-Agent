"""连接器轮询的任务入口：Celery beat 与桌面 inline 模式两条通道。

- 服务器部署：beat 每 60s 触发 ``devices.poll_connectors``，任务内部按各设备
  interval_seconds 判断"到点"才真正拉取（beat 节奏与设备周期解耦）。
- 桌面单机（TASKS_INLINE_MODE=true）：standalone 永不启动 worker/beat，改由
  API 进程内一个 daemon 线程周期跑同一 poll_due_connectors——与 tasks/router.py
  的 inline 线程兜底哲学一致。线程由 main._startup_wiring 按开关拉起；
  测试环境该开关为 False，不产生线程。
"""

import threading
import time

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.devices.connector import poll_due_connectors
from app.workers.celery_app import celery_app

log = get_logger(__name__)

#: inline 模式下的轮询节奏（秒）。真正的采集周期仍由每台设备 interval_seconds 决定。
INLINE_POLL_SECONDS = 30

_scheduler_started = False
_scheduler_lock = threading.Lock()
_stop_event = threading.Event()


@celery_app.task(name="devices.poll_connectors")
def poll_connectors_task() -> dict[str, int]:
    """Celery 入口：执行一轮到期连接器轮询。"""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        stats = poll_due_connectors(db)
    finally:
        db.close()
    if stats["due"]:
        log.info("devices.poll_connectors: %s", stats)
    return stats


def run_inline_poll_once() -> None:
    """跑一轮轮询（异常全兜住：调度线程绝不能因单次失败退出）。"""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        poll_due_connectors(db)
    except Exception as e:  # noqa: BLE001
        log.warning("inline connector poll failed: %s", e)
    finally:
        db.close()


def start_inline_scheduler() -> bool:
    """TASKS_INLINE_MODE 下启动 daemon 轮询线程（幂等）。返回是否新启动。

    先查 DB 是否有启用的连接器设备再决定拉线程也行，但设备可能随时被创建，
    线程成本极低（30s 一次空转查询），统一启动更可靠。
    """
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return False
        _scheduler_started = True
    if not settings.tasks_inline_mode:
        return False

    def _loop() -> None:
        log.info("devices inline connector scheduler started (every %ss)", INLINE_POLL_SECONDS)
        deadline = time.monotonic() + INLINE_POLL_SECONDS
        while not _stop_event.wait(timeout=max(0.5, deadline - time.monotonic())):
            run_inline_poll_once()
            deadline = time.monotonic() + INLINE_POLL_SECONDS

    thread = threading.Thread(target=_loop, name="devices-poll", daemon=True)
    thread.start()
    return True


def stop_inline_scheduler() -> None:
    """测试/停机用：结束 inline 调度循环。"""
    _stop_event.set()
