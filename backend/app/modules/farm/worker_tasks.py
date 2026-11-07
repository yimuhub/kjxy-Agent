"""工单逾期升级的 inline 调度（v3.71）。

Celery 部署形态由 beat 任务 ``workorders.escalate_overdue``（每 6 小时）巡检；
桌面/单机（TASKS_INLINE_MODE）永不启动 beat，此前逾期升级在桌面端完全不跑。
本模块用与 devices.worker_tasks 相同的 daemon 线程模式补齐：每 6 小时巡检一次。
"""

import threading
import time

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger(__name__)

#: 与 Celery beat 口径一致：每 6 小时巡检一次
INLINE_ESCALATE_SECONDS = 6 * 3600

_scheduler_started = False
_scheduler_lock = threading.Lock()
_stop_event = threading.Event()


def run_inline_escalate_once(db: Session | None = None) -> dict[str, int]:
    """跑一轮逾期升级（异常全兜住：调度线程绝不能因单次失败退出）。"""
    from app.modules.farm.workorder_service import escalate_overdue

    stats = {"scanned": 0, "escalated": 0}
    session = db
    close_after = False
    if session is None:
        from app.db.session import SessionLocal

        session = SessionLocal()
        close_after = True
    try:
        stats = escalate_overdue(session)
        if stats.get("escalated"):
            log.info("workorders inline escalate: %s", stats)
    except Exception as e:  # noqa: BLE001
        log.warning("inline workorder escalate failed: %s", e)
    finally:
        if close_after:
            session.close()
    return stats


def start_inline_scheduler() -> bool:
    """TASKS_INLINE_MODE 下启动 daemon 巡检线程（幂等）。返回是否新启动。"""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return False
        _scheduler_started = True
    if not settings.tasks_inline_mode:
        return False

    def _loop() -> None:
        log.info(
            "workorders inline escalate scheduler started (every %ss)", INLINE_ESCALATE_SECONDS
        )
        deadline = time.monotonic() + INLINE_ESCALATE_SECONDS
        while not _stop_event.wait(timeout=max(0.5, deadline - time.monotonic())):
            run_inline_escalate_once()
            deadline = time.monotonic() + INLINE_ESCALATE_SECONDS

    thread = threading.Thread(target=_loop, name="workorders-escalate", daemon=True)
    thread.start()
    return True


def stop_inline_scheduler() -> None:
    """测试/停机用：结束 inline 调度循环。"""
    _stop_event.set()
