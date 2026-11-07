"""运营定时任务（celery beat 驱动，规范 §7.5 超时回收 / §18.4 worker 心跳）。

- ``tasks.reap_stale``：每 5 分钟回收超时任务——queued 超过
  settings.task_queued_timeout_minutes 置 failed(QUEUED_TIMEOUT)；running 超过
  settings.task_running_timeout_minutes（按 updated_at 判定）置 failed(RUNNING_TIMEOUT)。
  每条回收写 task_events 与审计，单轮结束按日期去重汇总一条 send_alert。
- ``workers.heartbeat``：每 60 秒刷新 worker 心跳键，保证无在途任务时
  /health/worker 也能反映 beat/worker 进程存活。
- ``quota.monthly_reset``：每日检查一次 legacy ``YardQuota`` 自然月清零，用
  ``reset_period`` 列做幂等标记（条件 UPDATE，多进程并发也只会清一次）。

queued → failed 不经过状态机 set_status：状态机只允许 queued → running/cancelled，
修改 TRANSITIONS 需 ADR（见 state_machine.py）。回收属规范 §7.5 明确定义的运维兜底，
对 queued 直接写库进入终态，但同时写入 task_events 与审计保证可追溯；
running → failed 是合法转移，仍经由 TaskService.set_status。

桌面单机（TASKS_INLINE_MODE=true）没有 beat/worker，``start_inline_scheduler``
在 API 进程内用 daemon 线程补齐这两项维护（审查 O6）：
- 超时回收：每 5 分钟一轮，与 beat 同口径；
- legacy 月清零：每小时检查一次，靠 ``reset_period`` 条件 UPDATE 保证当月只清一次
  （原实现依赖 Redis 键，桌面无 Redis 时永远不清）。
"""

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.core.notify import send_alert
from app.modules.audit.service import log_audit
from app.workers.celery_app import celery_app
from app.workers.heartbeat import mark_worker_alive

log = get_logger(__name__)

#: 单轮最多回收条数：防止极端堆积时长事务阻塞 beat 工作进程
_REAP_BATCH_LIMIT = 200

#: inline 模式的巡检节奏（秒）：回收与 beat 对齐；月清零只做日期判断，1 小时足够
INLINE_REAP_SECONDS = 300
INLINE_QUOTA_CHECK_SECONDS = 3600

_scheduler_started = False
_scheduler_lock = threading.Lock()
_stop_event = threading.Event()

#: 会话工厂，可被测试覆盖（与 worker_tasks 同一约定）
_session_factory: Callable[[], Session] | None = None


def _get_session_factory() -> Callable[[], Session]:
    if _session_factory is not None:
        return _session_factory
    from app.db.session import SessionLocal

    return SessionLocal


def _utcnow() -> datetime:
    return datetime.now(UTC)


def reap_once(db: Session) -> dict[str, int]:
    """执行一轮超时回收，返回 {"queued_timeout": n, "running_timeout": n}。"""
    from app.modules.tasks import state_machine as sm
    from app.modules.tasks.models import Task, TaskEvent
    from app.modules.tasks.service import TaskService

    now = _utcnow()
    queued_cutoff = now - timedelta(minutes=settings.task_queued_timeout_minutes)
    running_cutoff = now - timedelta(minutes=settings.task_running_timeout_minutes)
    request_id = f"req_reaper_{now:%Y%m%d%H%M}"
    stats = {"queued_timeout": 0, "running_timeout": 0}

    # queued 超时（以 created_at 计排队时长）：状态机不允许 queued→failed，直接写终态并补事件
    stale_queued = list(
        db.scalars(
            select(Task)
            .where(Task.status == sm.STATUS_QUEUED, Task.created_at < queued_cutoff)
            .order_by(Task.created_at)
            .limit(_REAP_BATCH_LIMIT)
        )
    )
    for task in stale_queued:
        message = f"排队超过 {settings.task_queued_timeout_minutes} 分钟未被领取，系统回收为失败。"
        task.status = sm.STATUS_FAILED
        task.error_code = "QUEUED_TIMEOUT"
        task.error_message = message
        task.finished_at = now
        db.add(
            TaskEvent(
                yard_id=task.yard_id,
                task_id=task.id,
                event_type="status_changed",
                message=message,
                payload_json={"from": sm.STATUS_QUEUED, "to": sm.STATUS_FAILED},
            )
        )
        log_audit(
            db,
            yard_id=task.yard_id,
            actor_id=None,
            action="task.reap",
            resource_type="task",
            resource_id=task.id,
            result="success",
            request_id=request_id,
            metadata={"reason": "QUEUED_TIMEOUT", "task_type": task.task_type},
        )
        db.commit()
        stats["queued_timeout"] += 1

    # running 超时（规范 §7.5：按 updated_at 判定无推进）：合法转移，走 set_status
    stale_running = list(
        db.scalars(
            select(Task)
            .where(Task.status == sm.STATUS_RUNNING, Task.updated_at < running_cutoff)
            .order_by(Task.updated_at)
            .limit(_REAP_BATCH_LIMIT)
        )
    )
    for task in stale_running:
        message = f"执行超过 {settings.task_running_timeout_minutes} 分钟无进展，系统回收为失败。"
        TaskService.set_status(
            db,
            task,
            sm.STATUS_FAILED,
            event_message=message,
            error_code="RUNNING_TIMEOUT",
            error_message=message,
        )
        log_audit(
            db,
            yard_id=task.yard_id,
            actor_id=None,
            action="task.reap",
            resource_type="task",
            resource_id=task.id,
            result="success",
            request_id=request_id,
            metadata={"reason": "RUNNING_TIMEOUT", "task_type": task.task_type},
        )
        db.commit()
        stats["running_timeout"] += 1

    total = stats["queued_timeout"] + stats["running_timeout"]
    if total:
        # 汇总一条告警：dedupe_key 含日期，同一进程同一天只推一次，防止刷屏（规范 §16.2）
        send_alert(
            title="tasks_reaper",
            message=(
                f"超时任务回收：queued 超时 {stats['queued_timeout']} 条、"
                f"running 超时 {stats['running_timeout']} 条。"
            ),
            dedupe_key=f"reaper:{now:%Y%m%d}",
        )
        log.info("reap_stale: %s", stats)
    return stats


@celery_app.task(name="tasks.reap_stale")
def reap_stale_tasks() -> dict[str, int]:
    """Celery 入口：执行一轮超时回收。"""
    db = _get_session_factory()()
    try:
        return reap_once(db)
    finally:
        db.close()


@celery_app.task(name="workers.heartbeat")
def worker_heartbeat() -> None:
    """Beat 心跳任务：即使没有任务在途也持续刷新 worker 存活标记。"""
    mark_worker_alive()


def reset_legacy_quota_periods(db: Session, period: str | None = None) -> int:
    """legacy ``YardQuota`` 自然月清零，返回本轮被清零的行数。

    幂等标记落在 ``yard_quotas.reset_period``（YYYY-MM）：条件 UPDATE 只命中
    「从未清零」或「记的是别的月份」的行，数据库保证只清一次——不再依赖 Redis 键，
    桌面单机没有 Redis 也能按月清零；多进程/多次调用重复执行也安全（第二次 0 行）。
    """
    from app.modules.yards.quota import YardQuota

    period = period or datetime.now(UTC).strftime("%Y-%m")
    result = db.execute(
        update(YardQuota)
        .where(
            (YardQuota.reset_period.is_(None)) | (YardQuota.reset_period != period),
        )
        .values(
            llm_calls_used=0,
            knowledge_docs_used=0,
            tasks_used=0,
            reset_period=period,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    reset_rows = int(getattr(result, "rowcount", 0) or 0)
    if reset_rows:
        log.info("legacy quota monthly reset done for %s: %s rows", period, reset_rows)
    return reset_rows


@celery_app.task(name="quota.monthly_reset")
def quota_monthly_reset() -> int:
    """Celery 入口：legacy 配额月清零（幂等，见 reset_legacy_quota_periods）。"""
    db = _get_session_factory()()
    try:
        return reset_legacy_quota_periods(db)
    finally:
        db.close()


@celery_app.task(name="workorders.escalate_overdue")
def escalate_overdue_work_orders() -> dict[str, int]:
    """逾期未结束的工单自动升级（v3.10）。"""
    from app.modules.farm.workorder_service import escalate_overdue

    db = _get_session_factory()()
    try:
        stats = escalate_overdue(db)
        log.info("workorder overdue scan: %s", stats)
        return stats
    finally:
        db.close()


def _run_with_session(fn: Callable[[Session], object], label: str) -> None:
    db = _get_session_factory()()
    try:
        fn(db)
    except Exception as e:  # noqa: BLE001 - 调度线程绝不因单次失败退出
        db.rollback()
        log.warning("inline %s failed: %s", label, e)
    finally:
        db.close()


def start_inline_scheduler() -> bool:
    """TASKS_INLINE_MODE 下启动维护线程（幂等）。返回是否新启动。

    桌面单机没有 Celery worker/beat：超时任务回收与 legacy 配额月清零必须由
    API 进程补上，否则僵尸任务永远停在 running、旧额度也永远不清零（审查 O6）。
    """
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return False
        _scheduler_started = True
    if not settings.tasks_inline_mode:
        return False

    def _loop() -> None:
        log.info(
            "ops inline scheduler started (reap=%ss, quota=%ss)",
            INLINE_REAP_SECONDS,
            INLINE_QUOTA_CHECK_SECONDS,
        )
        next_reap = 0.0
        next_quota = 0.0
        while True:
            now = time.monotonic()
            wait = max(0.5, min(next_reap, next_quota) - now)
            if _stop_event.wait(timeout=wait):
                return
            now = time.monotonic()
            if now >= next_reap:
                _run_with_session(reap_once, "reap_stale")
                next_reap = now + INLINE_REAP_SECONDS
            if now >= next_quota:
                _run_with_session(reset_legacy_quota_periods, "quota monthly reset")
                next_quota = now + INLINE_QUOTA_CHECK_SECONDS

    thread = threading.Thread(target=_loop, name="ops-maintenance", daemon=True)
    thread.start()
    return True
