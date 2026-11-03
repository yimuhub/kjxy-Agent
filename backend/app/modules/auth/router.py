"""认证路由：只做鉴权/校验与 service 调用，业务逻辑在 service 层。

登录限流：全局按 client_ip + username 做内存滑动窗口限流（默认 300 秒内失败
10 次）；指定小院时再叠加该院安全策略（max_login_attempts/lockout_seconds）。
实现见 app.core.rate_limit。
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
    ChangePasswordRequest,
    ChangePasswordResponseData,
    DesktopBootstrapRequest,
    LoginRequest,
    LoginResponseData,
    LogoutRequest,
    MeResponseData,
    ProfileUpdateRequest,
    RefreshRequest,
    RefreshResponseData,
)
from app.modules.yards import registration_service, settings_service
from app.modules.yards.schemas import RegisterRequest

router = APIRouter()

# 登录限流器：模块级单例；超限检查失败本身不计次，成功后清零。
_login_limiter = RateLimiter(
    max_attempts=settings.login_rate_limit_max_attempts,
    window_seconds=settings.login_rate_limit_window_seconds,
)

#: 小院级安全策略限流器（按小院的 max_login_attempts/lockout_seconds 惰性创建）
_yard_limiters: dict[tuple[str, int, int], RateLimiter] = {}
_YARD_LIMITER_CAP = 256


def _yard_login_limiter(db, yard_id: str | None) -> RateLimiter | None:
    if not yard_id:
        return None
    attempts = settings.login_rate_limit_max_attempts
    window = settings.login_rate_limit_window_seconds
    try:
        cfg = settings_service.get_setting(db, yard_id, "security")
        attempts = int(cfg.get("max_login_attempts") or attempts)
        window = int(cfg.get("lockout_seconds") or window)
    except Exception:  # 配置读取失败回退部署默认值，不影响登录
        pass
    key = (yard_id, attempts, window)
    limiter = _yard_limiters.get(key)
    if limiter is None:
        if len(_yard_limiters) >= _YARD_LIMITER_CAP:
            _yard_limiters.pop(next(iter(_yard_limiters)))
        limiter = RateLimiter(max_attempts=attempts, window_seconds=window)
        _yard_limiters[key] = limiter
    return limiter


# 会被计入限流的失败码：凭证错误、账号停用。其他错误（如小院越权）
# 不计入，避免与限流语义混淆。
_COUNTED_FAILURE_CODES = ("INVALID_CREDENTIALS", "ACCOUNT_DISABLED")


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _client_ip(request: Request) -> str:
    """取限流键用的客户端 IP。

    直连部署取 socket 对端；配置了 ``trusted_proxy_count``（受信反代链长度）
    时按规范取 X-Forwarded-For 倒数第 N 跳——只信自家代理链，不盲信客户端
    可伪造的整条 XFF（审计 S-8：反代部署下全网共享一个桶会误锁无辜用户）。
    """
    socket_ip = request.client.host if request.client else None
    proxy_hops = max(0, int(getattr(settings, "trusted_proxy_count", 0) or 0))
    if proxy_hops == 0:
        return socket_ip or "unknown"
    if socket_ip == "testclient":
        return socket_ip
    forwarded = request.headers.get("x-forwarded-for", "")
    parts = [p.strip() for p in forwarded.split(",") if p.strip()]
    if len(parts) >= proxy_hops:
        return parts[-proxy_hops]
    return socket_ip or "unknown"


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
    yard_limiter = _yard_login_limiter(db, req.yard_id)
    yard_key = f"login:{req.yard_id}:{_client_ip(request)}:{req.username}"
    if yard_limiter is not None and not yard_limiter.is_allowed(yard_key):
        raise AppError(
            "RATE_LIMITED",
            "登录尝试过于频繁，请稍后再试。",
            http_status=429,
        )
    try:
        data: LoginResponseData = service.login(
            db, req.username, req.password, req.yard_id, request_id=_request_id(request)
        )
    except AppError as exc:
        if exc.code in _COUNTED_FAILURE_CODES:
            _login_limiter.record_failure(key)
            if yard_limiter is not None:
                yard_limiter.record_failure(yard_key)
        raise
    # 成功登录：清零该 key 的失败计数。
    _login_limiter.reset(key)
    if yard_limiter is not None:
        yard_limiter.reset(yard_key)
    return ok(data.model_dump(), request)


@router.post("/auth/desktop-bootstrap")  # 公开接口：仅回环 + 壳注入令牌
def desktop_bootstrap(
    req: DesktopBootstrapRequest,
    request: Request,
    db=Depends(get_db_session),
):
    """桌面安装包双击启动时的静默登录；服务器部署下环境变量缺失直接 404。"""
    if _client_ip(request) not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise AppError("FORBIDDEN", "该接口仅允许本机调用。", http_status=403)
    data: LoginResponseData = service.desktop_bootstrap(
        db, req.token, request_id=_request_id(request)
    )
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
    payload: LogoutRequest | None = None,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    service.logout(
        db,
        user.user_id,
        refresh_token=payload.refresh_token if payload else None,
        request_id=_request_id(request),
    )
    return ok({"ok": True}, request)


@router.get("/auth/me")
def me(request: Request, user: CurrentUser = Depends(get_current_user), db=Depends(get_db_session)):
    data: MeResponseData = service.get_me(db, user.user_id, user.roles, user.yard_id)
    return ok(data.model_dump(), request)


@router.post("/auth/register", status_code=201)  # 公开接口：加入现有小院（待审核/直接加入）
def register(req: RegisterRequest, request: Request, db=Depends(get_db_session)):
    """公开注册：必须指定 yard_id；按小院注册模式进入待审核或直接加入。"""
    from app.modules.yards.signup_router import signup_limiter

    ip = _client_ip(request)
    if not signup_limiter.is_allowed(ip):
        raise AppError("RATE_LIMITED", "注册过于频繁，请稍后再试。", http_status=429)
    signup_limiter.record_failure(ip)
    data = registration_service.register(db, req, request_id=_request_id(request))
    return ok(data, request)


@router.patch("/auth/me")
def update_me(
    req: ProfileUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    data: MeResponseData = service.update_profile(
        db,
        user.user_id,
        user.roles,
        req,
        yard_id=user.yard_id,
        request_id=_request_id(request),
    )
    return ok(data.model_dump(mode="json"), request)


@router.post("/auth/change-password")
def change_password(
    req: ChangePasswordRequest,
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db_session),
):
    data: ChangePasswordResponseData = service.change_password(
        db,
        user_id=user.user_id,
        yard_id=user.yard_id,
        old_password=req.old_password,
        new_password=req.new_password,
        request_id=_request_id(request),
    )
    return ok(data.model_dump(), request)
