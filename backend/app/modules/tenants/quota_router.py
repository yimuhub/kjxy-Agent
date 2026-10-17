"""配额路由：只做鉴权与出参封装，业务逻辑在 quota 模块。

- GET /tenants/quota：本租户配额状态（含所属期标注 period）。
- PATCH /admin/quotas/{tenant_id}：运营调整租户配额限额（权限点 admin:stats），写审计。
"""

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, get_current_user, get_db_session, get_tenant_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.tenants import quota
from app.modules.tenants import repository as tenant_repo
from app.modules.tenants.permissions import has_permission
from app.modules.tenants.schemas import QuotaAdminPatchRequest

router = APIRouter()


@router.get("/tenants/quota")
def get_quota(
    request: Request,
    ctx=Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    data = quota.get_quota_status(db, ctx.tenant_id)
    return ok(data, request)


@router.patch("/admin/quotas/{tenant_id}")
def admin_patch_quota(
    tenant_id: str,
    body: QuotaAdminPatchRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """运营调整租户配额限额（ge=0）；每次调整写审计，返回调整后的配额状态。"""
    if not has_permission(user.roles, "admin:stats"):
        raise AppError("FORBIDDEN", "权限不足。", http_status=403)
    if tenant_repo.get_tenant(db, tenant_id) is None:
        raise AppError("TENANT_NOT_FOUND", "租户不存在或无权访问。", http_status=404)

    quota_row = quota.ensure_quota(db, tenant_id)
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(quota_row, field, value)
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=user.user_id,
        action="quota_admin_patch",
        resource_type="tenant_quota",
        resource_id=quota_row.id,
        result="success",
        request_id=user.request_id,
        metadata={"changes": changes},
    )
    db.commit()
    return ok(quota.get_quota_status(db, tenant_id), request)
