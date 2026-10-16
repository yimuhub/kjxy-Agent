"""身份与租户上下文依赖。

所有受保护接口通过 get_current_user / get_tenant_context 获取身份；
绝不信任前端提交的 user_id / role / tenant_id。
"""

from dataclasses import dataclass

from app.core.errors import AppError
from app.core.security import decode_token
from app.db.session import get_db
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user_id: str
    tenant_id: str | None
    roles: list[str]
    request_id: str


@dataclass
class TenantContext:
    tenant_id: str
    user_id: str
    roles: list[str]
    request_id: str


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise AppError("UNAUTHORIZED", "请先登录。", http_status=401)
    payload = decode_token(credentials.credentials, expected_type="access")
    return CurrentUser(
        user_id=payload["sub"],
        tenant_id=payload.get("tenant_id"),
        roles=payload.get("roles", []),
        request_id=getattr(request.state, "request_id", "req_unknown"),
    )


def get_tenant_context(user: CurrentUser = Depends(get_current_user)) -> TenantContext:
    """要求请求携带租户上下文；平台管理员无租户时访问租户接口同样被拒绝。"""
    if not user.tenant_id:
        raise AppError("TENANT_REQUIRED", "该操作需要租户上下文。", http_status=400)
    return TenantContext(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        roles=user.roles,
        request_id=user.request_id,
    )


def require_roles(*allowed: str):
    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if "platform_admin" in user.roles:
            return user
        if not set(user.roles) & set(allowed):
            raise AppError("FORBIDDEN", "权限不足。", http_status=403)
        return user

    return checker


def get_db_session(db: Session = Depends(get_db)) -> Session:
    return db
