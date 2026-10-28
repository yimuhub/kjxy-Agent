"""跨院只读分享路由（v3.12）。

- ``/shares*``：院内管理（创建/列表/撤销），走 farm:read / farm:write 权限；
- ``/public/shares/{token}``：匿名只读快照，令牌不可猜、可撤销、可过期。
"""

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.core.responses import ok
from app.modules.share import service
from app.modules.share.schemas import ShareCreate

router = APIRouter(prefix="/shares", tags=["shares"])
public_router = APIRouter(prefix="/public", tags=["public"])

# 匿名访问限流：同一 IP 每小时最多 120 次（正常阅读足够，异常刷取会被挡）
public_limiter = RateLimiter(max_attempts=120, window_seconds=3600)


def _yard_id(user: CurrentUser) -> str:
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


@router.post("", status_code=201)
def create_share(
    payload: ShareCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    """生成只读分享链接；原始 token 只在本次响应中出现一次。"""
    return ok(service.create_share(db, _yard_id(user), user.user_id, payload), request)


@router.get("")
def list_shares(
    request: Request,
    include_revoked: bool = Query(default=False),
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    return ok(service.list_shares(db, _yard_id(user), include_revoked=include_revoked), request)


@router.delete("/{share_id}")
def revoke_share(
    share_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    """撤销分享：立即失效（访客拿到的旧链接返回 404）。"""
    return ok(service.revoke_share(db, _yard_id(user), share_id, user.user_id), request)


@public_router.get("/shares/{token}")
def view_public_share(token: str, request: Request, db=Depends(get_db_session)):
    """匿名读取分享快照；限流按来源 IP，失败不区分“不存在/撤销/过期”。"""
    ip = request.client.host if request.client else "unknown"
    if not public_limiter.is_allowed(f"share:{ip}"):
        raise AppError("RATE_LIMITED", "访问过于频繁，请稍后再试。", http_status=429)
    public_limiter.record_failure(f"share:{ip}")
    return ok(service.resolve_public(db, token), request)
