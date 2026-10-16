"""审计日志查询接口。

GET /audit/logs：需 audit:read 权限；分页；按租户过滤；
平台管理员（无租户上下文）可查看平台级（含 tenant_id 为空）的全部日志。
"""

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUser, get_current_user, get_db_session
from app.core.errors import AppError
from app.core.pagination import pagination_params
from app.core.responses import paged
from app.modules.audit.models import AuditLog
from app.modules.evolution.service import has_permission

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs")
def get_audit_logs(
    action: str | None = Query(default=None, description="按动作过滤"),
    resource_type: str | None = Query(default=None, description="按资源类型过滤"),
    request_id: str | None = Query(default=None, description="按请求 ID 过滤"),
    page_page: tuple[int, int] = Depends(pagination_params),
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    if not has_permission(user.roles, "audit:read"):
        raise AppError("FORBIDDEN", "无审计日志查看权限。", http_status=403)
    page, page_size = page_page
    q = db.query(AuditLog)
    if user.tenant_id:
        # 租户成员只能看本租户日志；平台管理员无租户时可看全部（含平台级）
        q = q.filter(AuditLog.tenant_id == user.tenant_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)
    if request_id:
        q = q.filter(AuditLog.request_id == request_id)
    total = q.count()
    items = (
        q.order_by(AuditLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    )
    return paged(
        [
            {
                "id": i.id,
                "tenant_id": i.tenant_id,
                "actor_id": i.actor_id,
                "action": i.action,
                "resource_type": i.resource_type,
                "resource_id": i.resource_id,
                "result": i.result,
                "request_id": i.request_id,
                "metadata": i.metadata_json,
                "created_at": i.created_at.isoformat() if i.created_at else None,
            }
            for i in items
        ],
        page,
        page_size,
        total,
    )
