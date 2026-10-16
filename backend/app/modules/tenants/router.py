"""租户路由：只做鉴权/校验与 service 调用，业务逻辑在 service 层。"""

from fastapi import APIRouter, Depends, Request

from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.errors import AppError
from app.core.pagination import pagination_params
from app.core.responses import ok, paged
from app.modules.tenants import service
from app.modules.tenants.permissions import has_permission
from app.modules.tenants.schemas import AddMemberRequest, TenantMemberOut, TenantOut

router = APIRouter()


def require_permission(permission: str):
    """基于权限点的守卫；platform_admin 因拥有全部权限自然通过。"""

    def checker(ctx: TenantContext = Depends(get_tenant_context)) -> TenantContext:
        if not has_permission(ctx.roles, permission):
            raise AppError("FORBIDDEN", "权限不足。", http_status=403)
        return ctx

    return checker


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


@router.get("/tenants/current")
def current_tenant(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    data: TenantOut = service.get_current_tenant(db, ctx)
    return ok(data.model_dump(), request)


@router.get("/tenants/current/members")
def list_members(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
    page_params=Depends(pagination_params),
):
    page, page_size = page_params
    items, total = service.list_members(db, ctx, page=page, page_size=page_size)
    return paged([i.model_dump() for i in items], page, page_size, total, request)


@router.post("/tenants/current/members", status_code=201)
def add_member(
    req: AddMemberRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission("members:manage")),
    db=Depends(get_db_session),
):
    data: TenantMemberOut = service.add_member(db, ctx, req, request_id=_request_id(request))
    return ok(data.model_dump(), request)


@router.delete("/tenants/current/members/{user_id}")
def remove_member(
    user_id: str,
    request: Request,
    ctx: TenantContext = Depends(require_permission("members:manage")),
    db=Depends(get_db_session),
):
    service.remove_member(db, ctx, user_id, request_id=_request_id(request))
    return ok({"ok": True}, request)
