"""Celery 后台任务：任务执行器。

- service 层不直接依赖本模块；投递经由 enqueue_task（broker 不可用时降级为仅告警）。
- _session_factory 可被测试覆盖，用于注入测试数据库会话。
"""

import os
import threading
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.workers.celery_app import celery_app
from app.workers.heartbeat import ROLE_INLINE_API, ROLE_WORKER

log = get_logger(__name__)

#: 投递等待上限（秒）。broker 健康时 send_task 为毫秒级；broker 不可用时
#: kombu 会长时间重试连接，这里用守护线程截断，避免阻塞 API 请求。
#: 可用环境变量 TASK_ENQUEUE_TIMEOUT_SECONDS 覆盖（测试场景可设小），调用时读取。
DEFAULT_ENQUEUE_TIMEOUT_SECONDS = 3.0


def _enqueue_timeout() -> float:
    try:
        return float(
            os.environ.get("TASK_ENQUEUE_TIMEOUT_SECONDS", DEFAULT_ENQUEUE_TIMEOUT_SECONDS)
        )
    except ValueError:
        return DEFAULT_ENQUEUE_TIMEOUT_SECONDS


_session_factory: Callable[[], Session] | None = None


def _get_session_factory() -> Callable[[], Session]:
    if _session_factory is not None:
        return _session_factory
    from app.db.session import SessionLocal

    return SessionLocal


def enqueue_task(task_id: str, timeout: float | None = None) -> bool:
    """把任务投递到 Celery 队列。broker 不可用/响应慢时在 timeout 秒后放弃，
    返回 False，不抛异常（任务保持 queued，可稍后补投递）。"""
    if timeout is None:
        timeout = _enqueue_timeout()
    outcome: dict = {}

    def _send() -> None:
        try:
            celery_app.send_task("tasks.execute", args=[task_id])
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001
            outcome["error"] = exc

    worker = threading.Thread(target=_send, name=f"enqueue-{task_id[:8]}", daemon=True)
    worker.start()
    worker.join(timeout=timeout)
    if worker.is_alive():
        log.warning("enqueue task %s timed out after %ss (broker unavailable?)", task_id, timeout)
        return False
    if outcome.get("ok"):
        return True
    log.warning("enqueue task %s failed (broker unavailable?): %s", task_id, outcome.get("error"))
    return False


def _cancel_requested(task) -> bool:
    """input_json.meta 是否已标记取消请求（API 侧取消 running 任务时写入，规范 §7.5）。"""
    meta = (task.input_json or {}).get("meta") or {}
    return bool(meta.get("cancel_requested"))


def _fail_task(db: Session, task_id: str, error_code: str, error_message: str) -> None:
    """把仍处于 running 的任务置为 failed；已终态的任务不动。"""
    from app.modules.tasks import state_machine as sm
    from app.modules.tasks.models import Task
    from app.modules.tasks.service import TaskService

    try:
        db.rollback()
        task = db.get(Task, task_id)
        if task is not None and task.status == sm.STATUS_RUNNING:
            TaskService.set_status(
                db,
                task,
                sm.STATUS_FAILED,
                event_message="任务执行失败。",
                error_code=error_code,
                error_message=error_message,
            )
        db.commit()
    except Exception:
        db.rollback()
        log.exception("failed to mark task %s as failed", task_id)


def _await_review_task(db: Session, task_id: str, approval_id: str | None) -> None:
    """高风险工具被拦截：running → awaiting_review，等待人工审批。"""
    from app.modules.tasks import state_machine as sm
    from app.modules.tasks.models import Task
    from app.modules.tasks.service import TaskService

    try:
        task = db.get(Task, task_id)
        if task is not None and task.status == sm.STATUS_RUNNING:
            TaskService.set_status(
                db,
                task,
                sm.STATUS_AWAITING_REVIEW,
                event_message="高风险操作被拦截，等待人工审批。",
                result={"approval_id": approval_id, "stage": "awaiting_approval"},
            )
        db.commit()
    except Exception:
        db.rollback()
        log.exception("failed to mark task %s as awaiting_review", task_id)


@celery_app.task(name="tasks.execute", bind=True)
def execute_task(self, task_id: str) -> None:
    """Celery 入口：委托给 run_task_inline。

    跑在**真实 Celery worker 进程**里，因此心跳按 worker 角色写。
    """
    run_task_inline(task_id, role=ROLE_WORKER)


def run_task_inline(task_id: str, *, role: str = ROLE_INLINE_API) -> None:
    """执行单个任务：queued → running → succeeded/failed，全程写事件。

    与 Celery 解耦的纯函数；纯本机模式（无 Redis）下由 API 进程的后台线程直接调用。

    ``role`` 决定心跳写哪个键：

    - ``ROLE_WORKER``——由 Celery worker 进程执行（``execute_task``），写 worker 心跳键；
    - ``ROLE_INLINE_API``——由 **API 进程**的后台线程执行（同步兜底路径），
      写独立的内联心跳键。它不是 worker，写进 worker 心跳键会造成
      "有活 worker"的假阳性，让知识库看门狗把兜底阈值从 180s 拉到 600s，
      文档实打实多等十分钟（审查 D-03 场景 C）。
    """
    from app.core.errors import AppError
    from app.modules.tasks import handlers  # noqa: F401 - 触发各模块处理器注册
    from app.modules.tasks import state_machine as sm
    from app.modules.tasks.handlers import TASK_HANDLERS
    from app.modules.tasks.models import Task
    from app.modules.tasks.service import TaskService
    from app.workers.heartbeat import mark_inline_alive, mark_worker_alive

    db = _get_session_factory()()
    try:
        # 执行任务即刷新心跳（规范 §18.4；内部吞异常，不影响任务执行）。
        if role == ROLE_WORKER:
            mark_worker_alive()
        else:
            mark_inline_alive()

        task = db.get(Task, task_id)
        if task is None:
            log.warning("execute_task: task %s not found, skip", task_id)
            return
        if task.status in sm.TERMINAL_STATUSES:
            # 已被取消/终态：无需再执行（API 侧取消会先把 running 置为 cancelled）
            log.info("execute_task: task %s already terminal (%s), skip", task_id, task.status)
            return

        # 安全点一（规范 §7.5）：执行处理器前已收到取消请求 → 直接取消，不执行处理器
        if _cancel_requested(task):
            TaskService.set_status(
                db,
                task,
                sm.STATUS_CANCELLED,
                event_message="取消在安全点生效，未执行处理器。",
            )
            db.commit()
            return

        try:
            TaskService.set_status(db, task, sm.STATUS_RUNNING, event_message="任务开始执行。")
            db.commit()
        except AppError:
            # 任务已被取消等，状态机拒绝转移时不再执行
            db.rollback()
            log.info("execute_task: task %s not in executable state, skip", task_id)
            return

        handler = TASK_HANDLERS.get(task.task_type)
        if handler is None:
            _fail_task(db, task_id, "INVALID_REQUEST", f"未注册的任务类型：{task.task_type}。")
            return

        context = {
            "task_id": task.id,
            "yard_id": task.yard_id,
            "actor_id": task.created_by,
            "request_id": f"req_worker_{task.id[:8]}",
        }
        result = handler((task.input_json or {}).get("data", {}), context)
        result_payload = result if isinstance(result, dict) else {"result": result}

        # 安全点二（规范 §7.5）：处理器执行期间收到取消请求——外部副作用已经发生，
        # 不丢弃结果、不静默：保留 result 并发事件明确标注。
        db.refresh(task)
        if _cancel_requested(task):
            task.result_json = result_payload
            message = "任务完成前收到取消请求，外部副作用可能已发生；结果已保留。"
            if task.status == sm.STATUS_RUNNING:
                TaskService.set_status(
                    db,
                    task,
                    sm.STATUS_CANCELLED,
                    event_message=message,
                    payload={"cancel_after_execution": True},
                )
            else:
                # API 侧已先行取消（状态为终态）：只保留结果并补事件，不动状态机
                TaskService._add_event(
                    db,
                    task,
                    "cancel_after_execution",
                    message,
                    payload={"status": task.status, "cancel_after_execution": True},
                )
            db.commit()
            return

        TaskService.set_status(
            db,
            task,
            sm.STATUS_SUCCEEDED,
            event_message="任务执行成功。",
            result=result_payload,
        )
        db.commit()
    except AppError as e:
        log.warning("execute_task %s failed: %s %s", task_id, e.code, e.message)
        if e.code == "TOOL_RISK_BLOCKED":
            # 高风险工具拦截：转人工审批而非直接失败
            _await_review_task(db, task_id, (e.details or {}).get("approval_id"))
        else:
            _fail_task(db, task_id, e.code, e.message)
    except Exception:
        log.exception("execute_task %s crashed", task_id)
        _fail_task(db, task_id, "INTERNAL_ERROR", "任务执行异常中断。")
    finally:
        try:
            # 安全网：任何情况下不让任务永久卡在 running
            task = db.get(Task, task_id)
            if task is not None and task.status == sm.STATUS_RUNNING:
                _fail_task(db, task_id, "INTERNAL_ERROR", "任务执行异常中断。")
        except Exception:
            db.rollback()
        finally:
            db.close()
