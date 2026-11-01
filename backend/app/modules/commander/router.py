"""指挥官 REST（计划预览 / 计划编辑 / 运行详情 / 执行 / 重试 / 取消 / 指标）。

设置接口由 ``yards/settings_router.py`` 统一提供，这里不重复。
所有接口要求登录；读取/操作按 ``yard_id + 创建者或小院负责人`` 边界校验，
越界统一 404 ``COMMANDER_RUN_NOT_FOUND``（不泄露他人运行是否存在）。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, YardContext, get_current_user, get_db_session
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.commander import metrics, service
from app.modules.commander.models import RUN_QUEUED

router = APIRouter(prefix="/commander", tags=["commander"])

#: 后台任务强引用集合（避免 create_task 返回值被 GC 提前回收）
_BACKGROUND_TASKS: set[asyncio.Task] = set()


class PreviewRequest(BaseModel):
    """只规划不执行：内容 + 会话 + 可选附件/历史上下文。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    content: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(min_length=1, max_length=100)
    context: str = Field(default="", max_length=8000)


class PlanSubtaskIn(BaseModel):
    """计划卡提交的一个子任务；depends_on 按新数组位置 1-based 解释。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    title: str = Field(min_length=1, max_length=200)
    instruction: str = Field(min_length=1, max_length=8000)
    expected_output: str | None = Field(default=None, max_length=1000)
    agent_id: str | None = Field(default=None, max_length=36)
    depends_on: list[int] | None = None


class PlanEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    subtasks: list[PlanSubtaskIn] = Field(min_length=1, max_length=12)


def _ctx(user: CurrentUser) -> YardContext:
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return YardContext(
        yard_id=user.yard_id,
        user_id=user.user_id,
        roles=list(user.roles),
        request_id=user.request_id,
    )


def _load(db: Session, user: CurrentUser, run_id: str):
    run, subtasks = service.get_run(db, _ctx(user).yard_id, run_id)
    service.require_run_access(db, run, user_id=user.user_id, roles=user.roles)
    return run, subtasks


@router.get("/stats")
def commander_stats(
    request: Request,
    days: int = 30,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    return ok({"stats": metrics.stats(db, ctx.yard_id, max(1, min(int(days), 365)))}, request)


@router.post("/preview")
async def preview_plan(
    body: PreviewRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """只规划不执行：落 queued 运行与子任务，供前端计划卡预览/编辑。"""
    ctx = _ctx(user)
    run = await service.create_plan_run(
        db,
        ctx=ctx,
        session_id=body.session_id,
        user_content=body.content,
        context=body.context,
    )
    _run, subtasks = service.get_run(db, ctx.yard_id, run.id)
    return ok(
        {
            "run": service.run_out(run, subtasks),
            "clarify": list((run.plan_json or {}).get("clarify") or []),
        },
        request,
    )


@router.put("/runs/{run_id}/plan")
def edit_plan(
    run_id: str,
    body: PlanEditRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    run = service.update_plan(db, ctx, run_id, [item.model_dump() for item in body.subtasks])
    _run, subtasks = service.get_run(db, ctx.yard_id, run.id)
    return ok({"run": service.run_out(run, subtasks)}, request)


@router.get("/runs/{run_id}")
def get_run_detail(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    run, subtasks = _load(db, user, run_id)
    return ok({"run": service.run_out(run, subtasks)}, request)


@router.post("/runs/{run_id}/execute", status_code=202)
async def execute_run(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    run, _subtasks = _load(db, user, run_id)
    if run.status != RUN_QUEUED:
        raise AppError(
            "COMMANDER_RUN_STATE",
            f"当前状态为 {run.status}，只有排队中的运行可以启动。",
            http_status=409,
        )
    task = asyncio.create_task(service.run_in_background(run_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return ok({"run_id": run.id, "status": run.status}, request)


@router.post("/runs/{run_id}/retry")
def retry_run(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    run = service.retry_run(db, ctx, run_id)
    _run, subtasks = service.get_run(db, ctx.yard_id, run.id)
    return ok({"run": service.run_out(run, subtasks)}, request)


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    ctx = _ctx(user)
    run = service.cancel_run(db, ctx, run_id)
    _run, subtasks = service.get_run(db, ctx.yard_id, run.id)
    return ok({"run": service.run_out(run, subtasks)}, request)
