"""配额路由：只做鉴权与出参封装，业务逻辑在 quota 模块。

- GET /yards/quota：本小院配额状态（含所属期标注 period）。
- PATCH /admin/quotas/{yard_id}：调整小院配额限额（权限点 quota:manage），写审计。
- POST /admin/quotas/{yard_id}/reset：清零已用量（超限后立刻恢复），写审计。
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.yards import quota
from app.modules.yards import repository as yard_repo
from app.modules.yards.authz import get_active_membership, has_permission, is_owner
from app.modules.yards.schemas import QuotaAdminPatchRequest, QuotaAdminResetRequest

router = APIRouter()


@router.get("/yards/quota")
def get_quota(
    request: Request,
    ctx=Depends(get_yard_context),
    db=Depends(get_db_session),
):
    data = quota.get_quota_status(db, ctx.yard_id)
    return ok(data, request)


def _require_quota_manager(db: Session, user: CurrentUser, yard_id: str) -> None:
    """管理目标小院的额度：平台管理员放行，其余必须是该小院的负责人或成员。

    先要求「成员身份」再判权限：否则 JWT 里的 yard_admin 走到无成员关系的兜底分支
    也能拿到 quota:manage，等于可以改任意小院的额度。
    """
    if "platform_admin" in user.roles:
        return
    if is_owner(db, yard_id, user.user_id):
        return
    if get_active_membership(db, yard_id, user.user_id) is None:
        raise AppError("FORBIDDEN", "无权管理该小院的用量额度。", http_status=403)
    if not has_permission(
        db,
        yard_id=yard_id,
        user_id=user.user_id,
        jwt_roles=user.roles,
        permission="quota:manage",
    ):
        raise AppError(
            "FORBIDDEN",
            "缺少 quota:manage 权限，无法调整用量额度。",
            http_status=403,
        )


@router.patch("/admin/quotas/{yard_id}")
def admin_patch_quota(
    yard_id: str,
    body: QuotaAdminPatchRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """调整小院配额限额（ge=0）；每次调整写审计，返回调整后的配额状态。"""
    if yard_repo.get_yard(db, yard_id) is None:
        raise AppError("YARD_NOT_FOUND", "小院不存在或无权访问。", http_status=404)
    _require_quota_manager(db, user, yard_id)

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


@router.post("/admin/quotas/{yard_id}/reset")
def admin_reset_quota(
    yard_id: str,
    body: QuotaAdminResetRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    """清零已用量（不动上限）：本地/桌面部署没有 Redis 时月度清零不会执行，靠这里恢复。"""
    if yard_repo.get_yard(db, yard_id) is None:
        raise AppError("YARD_NOT_FOUND", "小院不存在或无权访问。", http_status=404)
    _require_quota_manager(db, user, yard_id)

    before = quota.get_quota_status(db, yard_id)["quotas"]
    data = quota.reset_usage(db, yard_id, body.kinds or None)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="quota_admin_reset",
        resource_type="yard_quota",
        resource_id=yard_id,
        result="success",
        request_id=user.request_id,
        metadata={
            "kinds": body.kinds or list(quota.KIND_LABELS),
            "before": {k: v["used"] for k, v in before.items()},
        },
    )
    db.commit()
    return ok(data, request)
