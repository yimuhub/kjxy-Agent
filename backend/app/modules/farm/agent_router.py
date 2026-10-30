"""Agent 自动执行路由（v3.28）：``/farm/automation``。

权限点与台账一致：``farm:read`` 只读概览/运行记录/动作；``farm:write`` 可立即运行与
确认/忽略动作。所有查询强制小院隔离（跨院一律 404）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.responses import ok, paged
from app.modules.farm import agent_service

router = APIRouter(prefix="/farm/automation", tags=["farm-automation"])


def _yard_id(user: CurrentUser) -> str:
    if user.yard_id is None:  # require_yard_permission 已保证，防御性兜底
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


@router.get("/overview")
def overview(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    return ok(agent_service.overview(db, _yard_id(user)), request)


@router.get("/runs")
def list_runs(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    items, total = agent_service.list_runs(db, _yard_id(user), page=page, page_size=page_size)
    return paged(items, total, page, page_size, request)


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    return ok(agent_service.get_run(db, _yard_id(user), run_id), request)


@router.get("/actions")
def list_actions(
    request: Request,
    status: str | None = Query(default=None, max_length=16),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    items, total = agent_service.list_actions(
        db, _yard_id(user), status=status, page=page, page_size=page_size
    )
    return paged(items, total, page, page_size, request)


@router.post("/run")
def run_now(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    request_id = getattr(request.state, "request_id", "")
    data = agent_service.run_agent(
        db,
        yard_id=_yard_id(user),
        actor_id=user.user_id,
        request_id=request_id,
        trigger="manual",
    )
    return ok(data, request)


@router.post("/actions/{action_id}/confirm")
def confirm_action(
    action_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    request_id = getattr(request.state, "request_id", "")
    data = agent_service.confirm_action(
        db, _yard_id(user), action_id, actor_id=user.user_id, request_id=request_id
    )
    return ok(data, request)


@router.post("/actions/{action_id}/dismiss")
def dismiss_action(
    action_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    return ok(
        agent_service.dismiss_action(db, _yard_id(user), action_id, actor_id=user.user_id),
        request,
    )
