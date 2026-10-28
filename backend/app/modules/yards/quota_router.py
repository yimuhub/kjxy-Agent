"""配额路由：只做鉴权与出参封装，业务逻辑在 quota 模块。

- GET /yards/quota：本小院配额状态（含所属期标注 period）。
- PATCH /admin/quotas/{yard_id}：运营调整小院配额限额（权限点 admin:stats），写审计。
"""

from fastapi import APIRouter, Depends, Request

from app.core.deps import CurrentUser, get_current_user, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.yards import quota
from app.modules.yards import repository as yard_repo
from app.modules.yards.authz import has_permission
from app.modules.yards.schemas import QuotaAdminPatchRequest

router = APIRouter()


@router.get("/yards/quota")
def get_quota(
    request: Request,
    ctx=Depends(get_yard_context),
    db=Depends(get_db_session),
):
    data = quota.get_quota_status(db, ctx.yard_id)
    return ok(data, request)


@router.patch("/admin/quotas/{yard_id}")
def admin_patch_quota(
    yard_id: str,
    body: QuotaAdminPatchRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """运营调整小院配额限额（ge=0）；每次调整写审计，返回调整后的配额状态。"""
    if not has_permission(
        db,
        yard_id=yard_id,
        user_id=user.user_id,
        jwt_roles=user.roles,
        permission="admin:stats",
    ):
        raise AppError("FORBIDDEN", "权限不足。", http_status=403)
    if yard_repo.get_yard(db, yard_id) is None:
        raise AppError("YARD_NOT_FOUND", "小院不存在或无权访问。", http_status=404)

    quota_row = quota.ensure_quota(db, yard_id)
    changes = body.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(quota_row, field, value)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="quota_admin_patch",
        resource_type="yard_quota",
        resource_id=quota_row.id,
        result="success",
        request_id=user.request_id,
        metadata={"changes": changes},
    )
    db.commit()
    return ok(quota.get_quota_status(db, yard_id), request)
