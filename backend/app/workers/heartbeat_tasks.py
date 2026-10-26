"""运营定时任务（celery beat 驱动，规范 §7.5 超时回收 / §18.4 worker 心跳）。

- ``tasks.reap_stale``：每 5 分钟回收超时任务——queued 超过
  settings.task_queued_timeout_minutes 置 failed(QUEUED_TIMEOUT)；running 超过
  settings.task_running_timeout_minutes（按 updated_at 判定）置 failed(RUNNING_TIMEOUT)。
  每条回收写 task_events 与审计，单轮结束按日期去重汇总一条 send_alert。
- ``workers.heartbeat``：每 60 秒刷新 worker 心跳键，保证无在途任务时
  /health/worker 也能反映 beat/worker 进程存活。

queued → failed 不经过状态机 set_status：状态机只允许 queued → running/cancelled，
修改 TRANSITIONS 需 ADR（见 state_machine.py）。回收属规范 §7.5 明确定义的运维兜底，
对 queued 直接写库进入终态，但同时写入 task_events 与审计保证可追溯；
running → failed 是合法转移，仍经由 TaskService.set_status。
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
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


@celery_app.task(name="quota.monthly_reset")
def quota_monthly_reset() -> bool:
    """月度配额清零（幂等，不依赖数据库加列）。

    用 Redis 键 ``quota:reset:{YYYY-MM}``（SET NX + 40 天 TTL）判定"本月是否已重置"：
    只有当月第一个抢到键的进程执行清零；Redis 不可用时跳过（下一轮 beat 再试），
    宁可晚清不可重复清。YardQuota 加 period 列需 migration，按规范 §6.4 走 ADR 后再演进。
    """
    from datetime import UTC, datetime

    from sqlalchemy import update

    period = datetime.now(UTC).strftime("%Y-%m")
    key = f"techyard:quota:reset:{period}"
    try:
        import redis

        client = redis.Redis.from_url(
            settings.redis_url, socket_connect_timeout=2, socket_timeout=2
        )
        ok = client.set(key, "1", nx=True, ex=40 * 86400)
        if not ok:
            return False  # 本月已重置过
    except Exception as e:  # Redis 故障：跳过本轮，下一轮 beat 再试
        log.warning("quota_monthly_reset skipped (redis unavailable): %s", e)
        return False

    from app.modules.yards.quota import YardQuota

    db = _get_session_factory()()
    try:
        db.execute(update(YardQuota).values(llm_calls_used=0, knowledge_docs_used=0, tasks_used=0))
        db.commit()
    finally:
        db.close()
    log.info("quota monthly reset done for %s", period)
    return True
