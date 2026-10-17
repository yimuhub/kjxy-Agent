"""任务业务服务：创建 / 查询 / 取消 / 重试 / 状态机转移。

- 所有查询强制 tenant_id 过滤，跨租户访问统一视为不存在（404 TASK_NOT_FOUND）。
- 状态变化只允许经由 set_status；每次状态变化写入 task_events。
- 本模块不直接依赖 Celery；任务投递由 worker_tasks.enqueue_task（带降级）完成。
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.deps import TenantContext
from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.tasks import state_machine as sm
from app.modules.tasks.models import Task, TaskEvent


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TaskService:
    # ---------- 查询 ----------

    @staticmethod
    def get_by_idempotency_key(db: Session, tenant_id: str, idempotency_key: str) -> Task | None:
        stmt = select(Task).where(
            Task.tenant_id == tenant_id, Task.idempotency_key == idempotency_key
        )
        return db.scalar(stmt)

    @staticmethod
    def get(db: Session, ctx: TenantContext, task_id: str) -> Task:
        """按 id + 租户取任务；不存在或跨租户 → 404 TASK_NOT_FOUND（防枚举）。"""
        stmt = select(Task).where(Task.tenant_id == ctx.tenant_id, Task.id == task_id)
        task = db.scalar(stmt)
        if task is None:
            raise AppError("TASK_NOT_FOUND", "任务不存在或无权访问。", http_status=404)
        return task

    @staticmethod
    def list_tasks(
        db: Session,
        ctx: TenantContext,
        *,
        status: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[Task], int]:
        stmt = select(Task).where(Task.tenant_id == ctx.tenant_id)
        count_stmt = select(func.count()).select_from(Task).where(Task.tenant_id == ctx.tenant_id)
        if status:
            stmt = stmt.where(Task.status == status)
            count_stmt = count_stmt.where(Task.status == status)
        total = db.scalar(count_stmt) or 0
        stmt = stmt.order_by(Task.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
        return list(db.scalars(stmt)), total

    @staticmethod
    def list_events(db: Session, ctx: TenantContext, task_id: str) -> list[TaskEvent]:
        task = TaskService.get(db, ctx, task_id)
        stmt = (
            select(TaskEvent)
            .where(TaskEvent.tenant_id == ctx.tenant_id, TaskEvent.task_id == task.id)
            .order_by(TaskEvent.created_at.asc())
        )
        return list(db.scalars(stmt))

    # ---------- 写入 ----------

    @staticmethod
    def _add_event(
        db: Session,
        task: Task,
        event_type: str,
        message: str,
        *,
        progress: float | None = None,
        payload: dict | None = None,
    ) -> TaskEvent:
        event = TaskEvent(
            tenant_id=task.tenant_id,
            task_id=task.id,
            event_type=event_type,
            message=message,
            progress=progress,
            payload_json=payload,
        )
        db.add(event)
        db.flush()
        return event

    @staticmethod
    def create(
        db: Session,
        ctx: TenantContext,
        task_type: str,
        input: dict,  # noqa: A002 - 与 API 契约字段名保持一致
        priority: str = "normal",
        idempotency_key: str | None = None,
        request_id: str = "req_unknown",
    ) -> Task:
        """创建任务。同一租户同一幂等键直接返回已存在的任务（不重复创建）。"""
        if idempotency_key:
            existing = TaskService.get_by_idempotency_key(db, ctx.tenant_id, idempotency_key)
            if existing is not None:
                return existing

        task = Task(
            tenant_id=ctx.tenant_id,
            created_by=ctx.user_id,
            task_type=task_type,
            status=sm.STATUS_QUEUED,
            input_json={"data": copy.deepcopy(input or {}), "meta": {}},
            priority=priority or "normal",
            idempotency_key=idempotency_key,
        )
        db.add(task)
        db.flush()
        TaskService._add_event(
            db,
            task,
            "created",
            "任务已创建，进入排队等待执行。",
            payload={"task_type": task_type, "priority": task.priority},
        )
        log_audit(
            db,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.user_id,
            action="task.create",
            resource_type="task",
            resource_id=task.id,
            result="success",
            request_id=request_id,
            metadata={"task_type": task_type},
        )
        return task

    @staticmethod
    def cancel(db: Session, ctx: TenantContext, task_id: str, *, request_id: str) -> Task:
        """取消任务：仅 queued/running 可取消。running 的任务在 input_json.meta 中标记
        cancel_requested=True，供 Worker 在安全点感知取消。"""
        task = TaskService.get(db, ctx, task_id)
        if task.status not in (sm.STATUS_QUEUED, sm.STATUS_RUNNING):
            raise AppError(
                "TASK_NOT_CANCELLABLE",
                f"当前状态（{task.status}）不允许取消。",
                http_status=409,
            )
        was_running = task.status == sm.STATUS_RUNNING
        if was_running:
            meta = task.input_json.get("meta") or {}
            meta["cancel_requested"] = True
            task.input_json["meta"] = meta
            flag_modified(task, "input_json")
        TaskService.set_status(
            db,
            task,
            sm.STATUS_CANCELLED,
            event_message="运行中的任务已标记取消，状态转为已取消。"
            if was_running
            else "排队中的任务已取消。",
        )
        log_audit(
            db,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.user_id,
            action="task.cancel",
            resource_type="task",
            resource_id=task.id,
            result="success",
            request_id=request_id,
            metadata={"was_running": was_running},
        )
        return task

    @staticmethod
    def retry(
        db: Session,
        ctx: TenantContext,
        task_id: str,
        *,
        request_id: str,
        idempotency_key: str | None = None,
    ) -> Task:
        """重试任务：failed/cancelled 可重试；投递失败的 queued（meta.enqueue_failed=True）
        也可重试（规范 §7.5：broker 故障场景允许运营补救）。生成一条新任务记录，复用原任务输入。"""
        task = TaskService.get(db, ctx, task_id)
        meta = (task.input_json or {}).get("meta") or {}
        requeue_enqueue_failed = (
            task.status == sm.STATUS_QUEUED and meta.get("enqueue_failed") is True
        )
        if task.status not in (sm.STATUS_FAILED, sm.STATUS_CANCELLED) and not (
            requeue_enqueue_failed
        ):
            raise AppError(
                "TASK_NOT_RETRYABLE",
                f"当前状态（{task.status}）不允许重试。",
                http_status=409,
            )
        new_input = copy.deepcopy((task.input_json or {}).get("data", {}))
        new_task = TaskService.create(
            db,
            ctx,
            task.task_type,
            new_input,
            priority=task.priority,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
        # 标记血缘：meta.retry_of 指向原任务
        meta = new_task.input_json.get("meta") or {}
        meta["retry_of"] = task.id
        new_task.input_json["meta"] = meta
        flag_modified(new_task, "input_json")
        db.flush()
        log_audit(
            db,
            tenant_id=ctx.tenant_id,
            actor_id=ctx.user_id,
            action="task.retry",
            resource_type="task",
            resource_id=new_task.id,
            result="success",
            request_id=request_id,
            metadata={"from_task_id": task.id, "task_type": task.task_type},
        )
        return new_task

    # ---------- 状态机 ----------

    @staticmethod
    def set_status(
        db: Session,
        task: Task,
        to_status: str,
        *,
        event_message: str | None = None,
        progress: float | None = None,
        payload: dict | None = None,
        result: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> Task:
        """状态转移。非法转移抛 INVALID_TASK_TRANSITION(409)，并写入 task_events。"""
        from_status = task.status
        if not sm.can_transition(from_status, to_status):
            raise AppError(
                "INVALID_TASK_TRANSITION",
                f"不允许的状态转移：{from_status} → {to_status}。",
                http_status=409,
                details={"from": from_status, "to": to_status},
            )
        task.status = to_status
        if to_status == sm.STATUS_RUNNING and task.started_at is None:
            task.started_at = _utcnow()
        if to_status in sm.TERMINAL_STATUSES and task.finished_at is None:
            task.finished_at = _utcnow()
        if result is not None:
            task.result_json = result
        if error_code is not None:
            task.error_code = error_code
        if error_message is not None:
            task.error_message = error_message
        db.flush()
        TaskService._add_event(
            db,
            task,
            "status_changed",
            event_message or f"任务状态变更：{from_status} → {to_status}。",
            progress=progress,
            payload={"from": from_status, "to": to_status, **(payload or {})},
        )
        return task
