"""租户自助注册路由：公开接口，无需登录。

挂载在 /api/v1 下（由总协调统一挂载），完整路径为 POST /api/v1/tenants/signup。
"""

from fastapi import APIRouter, Depends, Request

from app.core.deps import get_db_session
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.core.responses import ok
from app.modules.tenants.schemas import TenantSignupRequest, TenantSignupResponse
from app.modules.tenants.signup import signup_tenant

router = APIRouter()

# 同一 IP 每小时最多发起 5 次注册；每次请求（含成功与失败）计一次。
# 测试可 monkeypatch 替换为该模块属性。
signup_limiter = RateLimiter(max_attempts=5, window_seconds=3600)


@router.post("/tenants/signup", status_code=201)
def tenant_signup(
    body: TenantSignupRequest,
    request: Request,
    db=Depends(get_db_session),
):
    ip = request.client.host if request.client else "unknown"
    if not signup_limiter.is_allowed(ip):
        raise AppError("RATE_LIMITED", "注册过于频繁，请稍后再试。", http_status=429)
    signup_limiter.record_failure(ip)
    result = signup_tenant(
        db,
        tenant_name=body.tenant_name,
        admin_username=body.admin_username,
        password=body.password,
        contact=body.contact,
        client_ip=ip,
    )
    return ok(TenantSignupResponse(**result).model_dump(), request)
