"""配对 HTTP 接口：``/pairing``（管理员建码/设备台账，交换端点公开）。

安全口径：
- ``POST /pairing/codes``、``GET /pairing/devices``、
  ``POST /pairing/devices/{id}/revoke``：小院管理员专属（与推送通道同一
  角色口径：platform_admin/admin/yard_admin）；
- ``POST /pairing/exchange``：**公开端点**（手机扫码时尚未持有任何凭证），
  防伪完全依赖配对码本身（高熵 + 5 分钟 TTL + 一次性 + 数量上限）；
- 建码响应只回 pair_url + expires_in，**不回显码本身以外的任何哈希**；
  设备台账绝不回显 token_hash；
- 吊销与不存在设备统一 404（防枚举），吊销幂等。
"""

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import YardContext, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.core.responses import ok
from app.modules.pairing import service
from app.modules.pairing.schemas import PairExchangeRequest

router = APIRouter(prefix="/pairing", tags=["pairing"])

_ADMIN_ROLES = {"platform_admin", "admin", "yard_admin"}

#: 公开交换端点单 IP 限流：码是 token_urlsafe(16) + 5 分钟 TTL，爆破本就不可行；
#: 这里挡的是扫描式试错与无效请求刷量。失败计数、成功清零。
_exchange_limiter = RateLimiter(max_attempts=30, window_seconds=60)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _admin(ctx: YardContext = Depends(get_yard_context)) -> YardContext:
    if not set(ctx.roles) & _ADMIN_ROLES:
        raise AppError("FORBIDDEN", "配对管理需要小院管理员权限。", http_status=403)
    return ctx


@router.post("/codes", response_model=None)
def create_code(
    request: Request,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    code, _row = service.create_pair_code(
        db, yard_id=ctx.yard_id, actor_id=ctx.user_id, request_id=ctx.request_id
    )
    return ok(
        {
            "pair_url": service.build_pair_url(request, code),
            "expires_in": settings.pairing_code_ttl_seconds,
        }
    )


@router.post("/exchange", response_model=None)
def exchange(
    payload: PairExchangeRequest,
    request: Request,
    db: Session = Depends(get_db_session),
):
    ip = _client_ip(request)
    if not _exchange_limiter.is_allowed(ip):
        raise AppError("RATE_LIMITED", "请求过于频繁，请稍后再试。", http_status=429)
    try:
        device, token = service.exchange(
            db, code=payload.code, device_name=payload.device_name, request_id="pair_exchange"
        )
    except AppError:
        _exchange_limiter.record_failure(ip)
        raise
    _exchange_limiter.reset(ip)
    return ok({"token": token, "device_id": device.id})


@router.get("/devices", response_model=None)
def list_devices(
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    rows = service.list_devices(db, ctx.yard_id)
    return ok([service.device_to_out(d) for d in rows])


@router.post("/devices/{device_id}/revoke", response_model=None)
def revoke_device(
    device_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    device = service.revoke_device(
        db,
        yard_id=ctx.yard_id,
        device_id=device_id,
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    return ok(service.device_to_out(device))
