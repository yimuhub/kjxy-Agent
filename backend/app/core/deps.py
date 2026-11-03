"""身份与小院上下文依赖。

所有受保护接口通过 get_current_user / get_yard_context 获取身份；
绝不信任前端提交的 user_id / role / yard_id。
"""

from dataclasses import dataclass, field

from app.core.errors import AppError
from app.core.security import decode_token, resolve_yard_id
from app.db.session import get_db
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user_id: str
    yard_id: str | None
    roles: list[str]
    request_id: str


@dataclass
class YardContext:
    yard_id: str
    user_id: str
    roles: list[str]
    request_id: str
    #: 本轮工具授权模式（chat_sessions.approval_mode 的当轮快照）：ask|auto|full。
    #: 缺省 ask——非对话入口（任务/机器人/脚本）一律沿用「高风险必须人工审批」。
    approval_mode: str = "ask"
    #: 发起本轮的会话 id：auto 模式据此只在**同一会话内**复用人工批准。
    session_id: str = ""
    #: 本轮被风险闸拦下、已建审批单的调用（{tool_code, approval_id}）。
    #: 对话链路据此在回答里给出「去批准」入口；其他入口不读它，无副作用。
    approval_requests: list[dict] = field(default_factory=list)


def get_db_session(db: Session = Depends(get_db)) -> Session:
    return db


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db_session),
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise AppError("UNAUTHORIZED", "请先登录。", http_status=401)
    payload = decode_token(credentials.credentials, expected_type="access")
    # 令牌版本校验：改密/重置/停用后旧令牌立即失效（旧令牌无 ver 时跳过，兼容测试/在途）
    if "ver" in payload:
        from app.modules.auth.models import User

        user = db.get(User, payload["sub"])
        # 查不到用户时保持既有兼容（测试/在途令牌直接携带角色）；真实登录用户一定存在
        if user is not None:
            if int(payload.get("ver") or 0) != int(user.token_version or 0):
                raise AppError("UNAUTHORIZED", "登录状态已失效，请重新登录。", http_status=401)
            if user.status == "disabled":
                raise AppError("ACCOUNT_DISABLED", "账号已停用，请联系管理员。", http_status=403)
            if user.status == "pending":
                raise AppError("ACCOUNT_PENDING", "账号正在等待审核。", http_status=403)
    return CurrentUser(
        user_id=payload["sub"],
        yard_id=resolve_yard_id(payload),
        roles=payload.get("roles", []),
        request_id=getattr(request.state, "request_id", "req_unknown"),
    )


def get_yard_context(user: CurrentUser = Depends(get_current_user)) -> YardContext:
    """要求请求携带小院上下文；平台管理员无小院时访问小院接口同样被拒绝。"""
    if not user.yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return YardContext(
        yard_id=user.yard_id,
        user_id=user.user_id,
        roles=user.roles,
        request_id=user.request_id,
    )


def require_roles(*allowed: str):
    def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if "platform_admin" in user.roles:
            return user
        # 负责人等价于本小院管理员通过既有小院闸门，但不越过平台管理员专属端点
        if "yard_owner" in user.roles and "yard_admin" in allowed:
            return user
        if not set(user.roles) & set(allowed):
            raise AppError("FORBIDDEN", "权限不足。", http_status=403)
        return user

    return checker


def require_permission(permission: str):
    """要求当前用户拥有指定权限点。

    有小院上下文时按 DB 实际成员角色判定（角色覆盖 + 逐人授权立即生效），
    无小院上下文（平台级接口）回退 JWT 角色。
    """

    def checker(
        user: CurrentUser = Depends(get_current_user),
        db: Session = Depends(get_db_session),
    ) -> CurrentUser:
        if user.yard_id:
            from app.modules.yards.authz import has_permission as yard_has_permission

            allowed = yard_has_permission(
                db,
                yard_id=user.yard_id,
                user_id=user.user_id,
                jwt_roles=user.roles,
                permission=permission,
            )
        else:
            from app.modules.yards.permissions import has_permission as static_has_permission

            allowed = static_has_permission(user.roles, permission)
        if not allowed:
            raise AppError(
                "FORBIDDEN",
                f"缺少 {permission} 权限，无法执行该操作。",
                http_status=403,
            )
        return user

    return checker


def require_platform_admin(message: str = "仅平台管理员可执行该操作。"):
    """平台管理员闸门：JWT 角色 + 数据库现况双重校验。

    JWT 里的 roles 是签发时的快照。撤权、停用、删号要即时生效，不能等 access
    token 过期（默认 30 分钟）。platform_admin 只由部署名单 bootstrap 授予，
    所以以「用户仍在库中、状态正常、用户名仍在名单内」为准；查不到用户即 401，
    不再沿用 JWT 快照放行。
    """

    def checker(
        user: CurrentUser = Depends(get_current_user),
        db: Session = Depends(get_db_session),
    ) -> CurrentUser:
        # 延迟 import：deps 被几乎所有路由加载，避免启动期循环依赖
        from app.core.config import settings
        from app.modules.auth.models import User

        if "platform_admin" not in user.roles:
            raise AppError("FORBIDDEN", message, http_status=403)
        row = db.get(User, user.user_id)
        if row is None:
            raise AppError("UNAUTHORIZED", "登录状态已失效，请重新登录。", http_status=401)
        if row.status == "disabled":
            raise AppError("ACCOUNT_DISABLED", "账号已停用，请联系管理员。", http_status=403)
        if row.status == "pending":
            raise AppError("ACCOUNT_PENDING", "账号正在等待审核。", http_status=403)
        if row.username not in settings.platform_admin_username_list:
            raise AppError("FORBIDDEN", message, http_status=403)
        return user

    return checker


def require_yard_permission(permission: str):
    """小院级权限校验：DB 角色覆盖 + 个人授权 + 保护规则（唯一权限源）。

    与 ``require_permission`` 的区别：必须有小院上下文，并按数据库中的实际成员
    角色判定（JWT 只作兜底），因此改角色/停用后立即生效。
    """

    def checker(
        user: CurrentUser = Depends(get_current_user),
        db: Session = Depends(get_db_session),
    ) -> CurrentUser:
        if not user.yard_id:
            raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
        from app.modules.yards.authz import has_permission

        if not has_permission(
            db,
            yard_id=user.yard_id,
            user_id=user.user_id,
            jwt_roles=user.roles,
            permission=permission,
        ):
            raise AppError(
                "FORBIDDEN",
                f"缺少 {permission} 权限，无法执行该操作。",
                http_status=403,
            )
        return user

    return checker
