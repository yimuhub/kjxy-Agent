"""认证路由：只做鉴权/校验与 service 调用，业务逻辑在 service 层。

登录限流：按 client_ip + username 做内存滑动窗口限流
（默认 300 秒内失败 10 次后返回 429），实现见 app.core.rate_limit。
注意：内存实现只对单进程有效，多实例部署需在网关/Redis 层集中限流。
"""

from fastapi import APIRouter, Depends, Request

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user, get_db_session
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.core.responses import ok
from app.modules.auth import service
from app.modules.auth.schemas import (
    LoginRequest,
    LoginResponseData,
    MeResponseData,
    RefreshRequest,
    RefreshResponseData,
)

router = APIRouter()

# 登录限流器：模块级单例；超限检查失败本身不计次，成功后清零。
_login_limiter = RateLimiter(
    max_attempts=settings.login_rate_limit_max_attempts,
    window_seconds=settings.login_rate_limit_window_seconds,
)

# 会被计入限流的失败码：凭证错误、账号停用。其他错误（如租户越权）
# 不计入，避免与限流语义混淆。
_COUNTED_FAILURE_CODES = ("INVALID_CREDENTIALS", "ACCOUNT_DISABLED")


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _client_ip(request: Request) -> str:
    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


@router.post("/auth/login")  # 公开接口（无全局 security，默认无需令牌）
def login(req: LoginRequest, request: Request, db=Depends(get_db_session)):
    """登录。公开接口，无需 Bearer 令牌。"""
    key = f"login:{_client_ip(request)}:{req.username}"
    # 先检查限流，超限直接 429，不调用 service（避免计时攻击面扩大）。
    if not _login_limiter.is_allowed(key):
        raise AppError(
            "RATE_LIMITED",
            "登录尝试过于频繁，请稍后再试。",
            http_status=429,
        )
    try:
        data: LoginResponseData = service.login(
            db, req.username, req.password, req.tenant_id, request_id=_request_id(request)
        )
    except AppError as exc:
        if exc.code in _COUNTED_FAILURE_CODES:
            _login_limiter.record_failure(key)
        raise
    # 成功登录：清零该 key 的失败计数。
    _login_limiter.reset(key)
    return ok(data.model_dump(), request)


@router.post("/auth/refresh")  # 公开接口：凭 refresh_token 换 access_token
def refresh_token(req: RefreshRequest, request: Request, db=Depends(get_db_session)):
    data: RefreshResponseData = service.refresh(
        db, req.refresh_token, request_id=_request_id(request)
    )
    return ok(data.model_dump(), request)


@router.post("/auth/logout")
def logout(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    service.logout(db, user.user_id, request_id=_request_id(request))
    return ok({"ok": True}, request)


@router.get("/auth/me")
def me(request: Request, user: CurrentUser = Depends(get_current_user), db=Depends(get_db_session)):
    data: MeResponseData = service.get_me(db, user.user_id, user.roles)
    return ok(data.model_dump(), request)
