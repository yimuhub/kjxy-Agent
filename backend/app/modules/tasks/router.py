"""任务 REST API（/api/v1/tasks）。

挂载说明：由技术负责人在集成阶段挂入 app/api/v1/router.py：
    from app.modules.tasks.router import router as tasks_router
    router.include_router(tasks_router)
"""

import threading

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.pagination import pagination_params
from app.core.responses import ok, paged
from app.modules.tasks.models import Task
from app.modules.tasks.schemas import TaskCreate, TaskEventOut, TaskOut
from app.modules.tasks.service import TaskService
from app.modules.tasks.worker_tasks import enqueue_task, run_task_inline

router = APIRouter(prefix="/tasks", tags=["tasks"])

log = get_logger(__name__)


def _enqueue_or_inline(db: Session, task: Task) -> None:
    """投递任务到 Celery；broker 不可用且开启本机降级时，在后台线程同步执行。

    投递失败且没有同步兜底：在 input_json.meta 写 enqueue_failed=True 并发事件，
    该 queued 任务随后可经 retry 复用输入重新入队（若无人处理，超时由 reaper 回收）。
    """
    if enqueue_task(task.id):
        return
    if settings.worker_sync_fallback:
        log.info("Celery 不可用，任务 %s 转本机线程执行", task.id[:8])
        thread = threading.Thread(
            target=run_task_inline, args=(task.id,), name=f"task-{task.id[:8]}", daemon=True
        )
        thread.start()
        return
    meta = dict((task.input_json or {}).get("meta") or {})
    meta["enqueue_failed"] = True
    task.input_json = {**(task.input_json or {}), "meta": meta}
    flag_modified(task, "input_json")
    TaskService._add_event(
        db,
        task,
        "enqueue_failed",
        "Celery 投递失败且未开启本机兜底；该任务可通过重试重新入队。",
    )
    db.commit()


def _commit_or_rollback(db: Session, fn):
    try:
        result = fn()
        db.commit()
        return result
    except AppError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


@router.post("")
def create_task(
    payload: TaskCreate,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    is_replay = False
    if idempotency_key:
        existing = TaskService.get_by_idempotency_key(db, ctx.tenant_id, idempotency_key)
        if existing is not None:
            is_replay = True

    def _do():
        return TaskService.create(
            db,
            ctx,
            task_type=payload.task_type,
            input=payload.input,
            priority=payload.priority,
            idempotency_key=idempotency_key,
            request_id=ctx.request_id,
        )

    task = _commit_or_rollback(db, _do)
    if not is_replay:
        _enqueue_or_inline(db, task)
    data = TaskOut.model_validate(task).model_dump(mode="json")
    return JSONResponse(content=ok(data, request), status_code=200 if is_replay else 201)


@router.get("")
def list_tasks(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
    status: str | None = Query(default=None, description="按状态过滤"),
    page_page_size: tuple[int, int] = Depends(pagination_params),
):
    page, page_size = page_page_size
    tasks, total = TaskService.list_tasks(db, ctx, status=status, page=page, page_size=page_size)
    items = [TaskOut.model_validate(t).model_dump(mode="json") for t in tasks]
    return paged(items, page, page_size, total, request)


@router.get("/{task_id}")
def get_task(
    task_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    task = TaskService.get(db, ctx, task_id)
    return ok(TaskOut.model_validate(task).model_dump(mode="json"), request)


@router.get("/{task_id}/events")
def list_task_events(
    task_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    events = TaskService.list_events(db, ctx, task_id)
    items = [TaskEventOut.model_validate(e).model_dump(mode="json") for e in events]
    return ok(items, request)


@router.post("/{task_id}/cancel")
def cancel_task(
    task_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    task = _commit_or_rollback(
        db, lambda: TaskService.cancel(db, ctx, task_id, request_id=ctx.request_id)
    )
    return ok(TaskOut.model_validate(task).model_dump(mode="json"), request)


@router.post("/{task_id}/retry")
def retry_task(
    task_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    task = _commit_or_rollback(
        db,
        lambda: TaskService.retry(
            db, ctx, task_id, request_id=ctx.request_id, idempotency_key=idempotency_key
        ),
    )
    _enqueue_or_inline(db, task)
    return JSONResponse(
        content=ok(TaskOut.model_validate(task).model_dump(mode="json"), request),
        status_code=201,
    )
