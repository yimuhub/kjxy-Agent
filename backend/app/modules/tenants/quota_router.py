"""配额路由：只做鉴权与出参封装，业务逻辑在 quota 模块。"""

from fastapi import APIRouter, Depends, Request

from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.responses import ok
from app.modules.tenants import quota

router = APIRouter()


@router.get("/tenants/quota")
def get_quota(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db=Depends(get_db_session),
):
    data = quota.get_quota_status(db, ctx.tenant_id)
    return ok(data, request)
