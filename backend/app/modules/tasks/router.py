"""任务 REST API（/api/v1/tasks）。

挂载说明：由技术负责人在集成阶段挂入 app/api/v1/router.py：
    from app.modules.tasks.router import router as tasks_router
    router.include_router(tasks_router)
"""

import threading

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
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
from app.modules.tasks.stream import build_stream_response
from app.modules.tasks.worker_tasks import enqueue_task, run_task_inline

router = APIRouter(prefix="/tasks", tags=["tasks"])

log = get_logger(__name__)


def _start_local_thread(task_id: str) -> None:
    """本机后台线程执行任务（同步兜底路径的公共出口）。"""
    thread = threading.Thread(
        target=run_task_inline, args=(task_id,), name=f"task-{task_id[:8]}", daemon=True
    )
    thread.start()


def _enqueue_or_inline(db: Session, task: Task) -> None:
    """投递任务到 Celery；broker 不可用且开启本机降级时，在后台线程同步执行。

    tasks_inline_mode=True（桌面单机模式）：不看 Celery，直接走后台线程兜底路径。
    投递失败且没有同步兜底：在 input_json.meta 写 enqueue_failed=True 并发事件，
    该 queued 任务随后可经 retry 复用输入重新入队（若无人处理，超时由 reaper 回收）。
    """
    if settings.tasks_inline_mode:
        log.info("tasks_inline_mode 开启，任务 %s 跳过 Celery 直接本机线程执行", task.id[:8])
        _start_local_thread(task.id)
        return
    if enqueue_task(task.id):
        return
    if settings.worker_sync_fallback:
        log.info("Celery 不可用，任务 %s 转本机线程执行", task.id[:8])
        _start_local_thread(task.id)
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


@router.get("/{task_id}/events/stream")
def stream_task_events(
    task_id: str,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
) -> StreamingResponse:
    """SSE 任务事件流（text/event-stream）。

    - 权限与 GET /{task_id}/events 完全一致：同一 get_tenant_context 依赖 +
      TaskService.get 可见性检查（阅读本文前先检查，失败按普通 JSON 错误返回）。
    - 本端点不套统一 {data, request_id} 信封：SSE 帧由 event:/data: 行构成，
      帧协议与命名见 stream.py 模块注释（stream-init/task-event/task-status/stream-end）。
    - 断连仅停止推送，任务继续执行；取消仍须显式调用 POST /{task_id}/cancel。
    """
    task = TaskService.get(db, ctx, task_id)
    return build_stream_response(task.tenant_id, task.id)


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
