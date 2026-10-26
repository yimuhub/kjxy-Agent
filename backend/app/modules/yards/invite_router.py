"""成员邀请公开路由：接受邀请页无需登录。

挂载在 /api/v1 下：
- GET  /invitations/{token}         预览空间与角色
- POST /invitations/{token}/accept  创建/复用账号并加入空间，直接返回登录令牌
"""

from fastapi import APIRouter, Depends, Request

from app.core.deps import get_db_session
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.core.responses import ok
from app.modules.yards import invitations as service
from app.modules.yards.schemas import InvitationAcceptRequest

router = APIRouter()

# 同一 IP 每小时最多尝试 20 次接受邀请（含失败），防令牌与口令爆破
accept_limiter = RateLimiter(max_attempts=20, window_seconds=3600)


@router.get("/invitations/{token}")
def preview_invitation(
    token: str,
    request: Request,
    db=Depends(get_db_session),
):
    data = service.preview_invitation(db, token)
    return ok(data.model_dump(mode="json"), request)


@router.post("/invitations/{token}/accept")
def accept_invitation(
    token: str,
    body: InvitationAcceptRequest,
    request: Request,
    db=Depends(get_db_session),
):
    ip = request.client.host if request.client else "unknown"
    if not accept_limiter.is_allowed(ip):
        raise AppError("RATE_LIMITED", "尝试过于频繁，请稍后再试。", http_status=429)
    try:
        result = service.accept_invitation(
            db,
            token=token,
            username=body.username,
            password=body.password,
            display_name=body.display_name,
            request_id=getattr(request.state, "request_id", ""),
        )
    except AppError:
        accept_limiter.record_failure(ip)
        raise
    accept_limiter.reset(ip)
    return ok(result, request)
