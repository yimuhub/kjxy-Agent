"""自我总结调度的任务入口：Celery beat 与桌面 inline 模式两条通道。

- 服务器部署：beat 每 5 分钟触发 ``yards.run_due_jobs``，任务内部按各小院
  ``next_run_at`` 判断“到点”才执行（beat 节奏与业务周期解耦）；
- 桌面单机（TASKS_INLINE_MODE=true）：standalone 永不启动 worker/beat，改由
  API 进程内 daemon 线程周期跑同一 run_due_jobs（与 devices 轮询同哲学）；
  测试环境开关为 False，不产生线程。
"""

import threading
import time

from app.core.config import settings
from app.core.logging import get_logger
from app.modules.self_improve.service import run_due_jobs
from app.workers.celery_app import celery_app

log = get_logger(__name__)

#: inline 模式下的巡检节奏（秒）。真正的运行周期由各小院调度设置决定。
INLINE_POLL_SECONDS = 300

_scheduler_started = False
_scheduler_lock = threading.Lock()
_stop_event = threading.Event()


@celery_app.task(name="yards.run_due_jobs")
def run_due_jobs_task() -> dict[str, int]:
    """Celery 入口：执行一轮到期的自我总结任务。"""
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        try:
            stats = run_due_jobs(db)
        finally:
            db.rollback()
            from app.modules.agents.teams.scheduler import run_due_jobs as run_due_teams

            team_stats = run_due_teams(db)
            log.info("teams.run_due_jobs: %s", team_stats)
            try:
                from app.modules.devices.analysis_jobs import run_due_analyses

                analysis_stats = run_due_analyses(db)
                if analysis_stats["due"]:
                    log.info("devices.run_due_analyses: %s", analysis_stats)
            except Exception as e:  # noqa: BLE001 - 设备分析失败不影响其他调度
                db.rollback()
                log.warning("devices.run_due_analyses failed: %s", e)
    finally:
        db.close()
    if stats["due"]:
        log.info("yards.run_due_jobs: %s", stats)
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
        log.info("self_improve inline scheduler started (every %ss)", INLINE_POLL_SECONDS)
        deadline = time.monotonic() + INLINE_POLL_SECONDS
        while not _stop_event.wait(timeout=max(0.5, deadline - time.monotonic())):
            try:
                from app.db.session import SessionLocal

                db = SessionLocal()
                try:
                    try:
                        run_due_jobs(db)
                    finally:
                        db.rollback()
                        from app.modules.agents.teams.scheduler import run_due_jobs as run_due_teams

                        run_due_teams(db)
                        db.rollback()
                        from app.modules.devices.analysis_jobs import run_due_analyses

                        run_due_analyses(db)
                finally:
                    db.close()
            except Exception as e:  # noqa: BLE001 - 调度线程绝不因单次失败退出
                log.warning("inline self_improve poll failed: %s", e)
            deadline = time.monotonic() + INLINE_POLL_SECONDS

    thread = threading.Thread(target=_loop, name="self-improve-poll", daemon=True)
    thread.start()
    return True


__all__ = ["INLINE_POLL_SECONDS", "run_due_jobs_task", "start_inline_scheduler"]
