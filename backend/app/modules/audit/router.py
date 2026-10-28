"""审计日志查询接口。

GET /audit/logs：需 audit:read 权限；分页；按小院过滤；
平台管理员（无小院上下文）可查看平台级（含 yard_id 为空）的全部日志。
"""

from fastapi import APIRouter, Depends, Query

from app.core.deps import CurrentUser, get_db_session, require_permission
from app.core.pagination import pagination_params
from app.core.responses import paged
from app.modules.audit.models import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/logs")
def get_audit_logs(
    action: str | None = Query(default=None, description="按动作过滤"),
    resource_type: str | None = Query(default=None, description="按资源类型过滤"),
    request_id: str | None = Query(default=None, description="按请求 ID 过滤"),
    page_page: tuple[int, int] = Depends(pagination_params),
    user: CurrentUser = Depends(require_permission("audit:read")),
    db=Depends(get_db_session),
):
    page, page_size = page_page
    q = db.query(AuditLog)
    if user.yard_id:
        # 小院成员只能看本小院日志；平台管理员无小院时可看全部（含平台级）
        q = q.filter(AuditLog.yard_id == user.yard_id)
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
                "yard_id": i.yard_id,
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
