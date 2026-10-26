"""小院自助注册路由：公开接口，无需登录。

挂载在 /api/v1 下（由总协调统一挂载），完整路径为 POST /api/v1/yards/signup。
"""

from fastapi import APIRouter, Depends, Request

from app.core.config import settings
from app.core.deps import get_db_session
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.core.responses import ok
from app.modules.yards.schemas import YardSignupRequest, YardSignupResponse
from app.modules.yards.signup import signup_yard

router = APIRouter()

# 同一 IP 在窗口内最多发起 N 次注册；每次请求（含成功与失败）计一次。
# 阈值可由部署配置，E2E/压测环境可放宽；测试可 monkeypatch 替换该模块属性。
signup_limiter = RateLimiter(
    max_attempts=settings.signup_rate_limit_max_attempts,
    window_seconds=settings.signup_rate_limit_window_seconds,
)


@router.post("/yards/signup", status_code=201)
def yard_signup(
    body: YardSignupRequest,
    request: Request,
    db=Depends(get_db_session),
):
    ip = request.client.host if request.client else "unknown"
    # 运营开关：自助注册关闭时公开端点直接 403（规范 §16，试运行/公网部署）
    if not settings.signup_enabled:
        raise AppError("SIGNUP_DISABLED", "暂未开放自助注册，请联系管理员开通。", http_status=403)
    if not signup_limiter.is_allowed(ip):
        raise AppError("RATE_LIMITED", "注册过于频繁，请稍后再试。", http_status=429)
    signup_limiter.record_failure(ip)
    result = signup_yard(
        db,
        yard_name=body.yard_name,
        admin_username=body.admin_username,
        password=body.password,
        contact=body.contact,
        client_ip=ip,
    )
    return ok(YardSignupResponse(**result).model_dump(), request)
