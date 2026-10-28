"""一句话业务路由（v3.22）。

权限：预览与确认都要求 farm:write（三个模块共用同一写入权限）。
预览不写库；确认走既有服务层，权限、校验与审计不绕过。
"""

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.quick_actions import service
from app.modules.quick_actions.schemas import QuickActionConfirmIn, QuickActionPreviewIn

router = APIRouter(prefix="/quick-actions", tags=["quick-actions"])


def _yard_id(user: CurrentUser) -> str:
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


@router.post("/preview")
def preview(
    payload: QuickActionPreviewIn,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = service.preview(
        db,
        yard_id=_yard_id(user),
        user_id=user.user_id,
        request_id=getattr(request.state, "request_id", "") or "",
        req=payload,
    )
    return ok(data, request)


@router.post("/confirm", status_code=201)
def confirm(
    payload: QuickActionConfirmIn,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = service.confirm(
        db,
        yard_id=_yard_id(user),
        user_id=user.user_id,
        request_id=getattr(request.state, "request_id", "") or "",
        req=payload,
    )
    return ok(data, request)
