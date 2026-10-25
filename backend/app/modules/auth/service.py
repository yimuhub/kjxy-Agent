"""认证业务逻辑。

开发环境 seed 账号（由 app/db/seed.py 创建，本模块不实现 seed）：
- admin / admin123：平台管理员（归属 0 个租户，登录后 tenant_id=None）
- tenant_admin / admin123：租户管理员（归属演示租户）
- expert / admin123：专家角色（归属演示租户）
生产环境必须通过环境变量/安全流程重置这些默认口令。
"""

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.config import WEAK_PLATFORM_ADMIN_USERNAMES, settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)
from app.modules.audit.service import log_audit
from app.modules.auth import repository as repo
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    LoginResponseData,
    MeResponseData,
    RefreshResponseData,
    TenantBrief,
    UserOut,
)
from app.modules.tenants import repository as tenant_repo

log = get_logger(__name__)

#: 弱管理员用户名告警去重：同一进程内每个用户名只 log.warning 一次
_weak_bootstrap_warned: set[str] = set()


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _bootstrap_platform_admin(
    db: Session, user: User, roles: list[str], *, request_id: str
) -> list[str]:
    """平台管理员 bootstrap：用户名在配置名单中则追加 platform_admin 角色。

    运行时防御（规范 §5.1）：admin/root 等弱名单用户名在生产环境本应被启动校验
    阻断；若仍走到此处，拒绝授予并写 denied 审计。非生产允许，但 log.warning
    一次并写审计，保证可追责。
    """
    if user.username not in settings.platform_admin_username_list or "platform_admin" in roles:
        return roles
    if user.username.lower() in WEAK_PLATFORM_ADMIN_USERNAMES:
        if settings.is_production:
            log_audit(
                db,
                tenant_id=None,
                actor_id=user.id,
                action="admin_bootstrap_weak_username",
                resource_type="auth",
                resource_id=user.id,
                result="denied",
                request_id=request_id,
                metadata={"username": user.username, "reason": "production_weak_admin"},
            )
            db.commit()
            raise AppError(
                "FORBIDDEN", "生产环境禁止使用弱管理员用户名授予平台管理员权限。", http_status=403
            )
        if user.username not in _weak_bootstrap_warned:
            _weak_bootstrap_warned.add(user.username)
            log.warning(
                "已为弱管理员用户名 %s 授予 platform_admin（仅限开发/测试；生产必须改用专有用户名）",  # noqa: E501
                user.username,
            )
        log_audit(
            db,
            tenant_id=None,
            actor_id=user.id,
            action="admin_bootstrap_weak_username",
            resource_type="auth",
            resource_id=user.id,
            result="success",
            request_id=request_id,
            metadata={"username": user.username, "reason": "dev_weak_admin"},
        )
    return ["platform_admin", *roles]


def _user_out(user: User) -> UserOut:
    return UserOut(
        user_id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        last_login_at=user.last_login_at,
    )


def _tenant_briefs(db: Session, user_id: str) -> list[TenantBrief]:
    """用户全部有效租户归属（附租户名）。"""
    briefs: list[TenantBrief] = []
    for m in tenant_repo.get_memberships_by_user(db, user_id):
        tenant = tenant_repo.get_tenant(db, m.tenant_id)
        if tenant is None or tenant.status != "active":
            continue
        briefs.append(TenantBrief(tenant_id=tenant.id, tenant_name=tenant.name, roles=[m.role]))
    return briefs


def _audit_login_failed(db: Session, username: str, request_id: str, reason: str) -> None:
    log_audit(
        db,
        tenant_id=None,
        actor_id=None,
        action="login_failed",
        resource_type="auth",
        resource_id=username,
        result="failure",
        request_id=request_id,
        metadata={"reason": reason},
    )
    db.commit()


def authenticate(db: Session, username: str, password: str) -> User:
    user = repo.get_by_username(db, username)
    if user is None or not verify_password(password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "用户名或密码错误。", http_status=401)
    if user.status == "disabled":
        raise AppError("ACCOUNT_DISABLED", "账号已停用，请联系管理员。", http_status=403)
    return user


def login(
    db: Session, username: str, password: str, tenant_id: str | None, *, request_id: str
) -> LoginResponseData:
    try:
        user = authenticate(db, username, password)
    except AppError as e:
        _audit_login_failed(db, username, request_id, e.code.lower())
        raise

    tenants = _tenant_briefs(db, user.id)

    if tenant_id is None:
        if not tenants:
            chosen: TenantBrief | None = None
        elif len(tenants) == 1:
            chosen = tenants[0]
        else:
            raise AppError(
                "TENANT_REQUIRED",
                "请选择要登录的租户。",
                http_status=400,
                details={"tenants": [t.model_dump() for t in tenants]},
            )
    else:
        # 显式指定 tenant_id 时一律校验成员关系（即使只有一个归属租户也不能绕过）。
        chosen = next((t for t in tenants if t.tenant_id == tenant_id), None)
        if chosen is None:
            log_audit(
                db,
                tenant_id=tenant_id,
                actor_id=user.id,
                action="login_failed",
                resource_type="auth",
                resource_id=username,
                result="denied",
                request_id=request_id,
                metadata={"reason": "tenant_forbidden"},
            )
            db.commit()
            raise AppError("TENANT_FORBIDDEN", "您不属于该租户。", http_status=403)

    roles = chosen.roles if chosen else []
    chosen_tenant_id = chosen.tenant_id if chosen else None
    # 平台管理员 bootstrap：用户名在配置名单中则授予 platform_admin（含弱名单运行时防御）
    roles = _bootstrap_platform_admin(db, user, roles, request_id=request_id)

    user.last_login_at = _utcnow()
    log_audit(
        db,
        tenant_id=chosen_tenant_id,
        actor_id=user.id,
        action="login_success",
        resource_type="auth",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={"username": username, "tenant_id": chosen_tenant_id},
    )
    db.commit()

    return LoginResponseData(
        access_token=create_access_token(user_id=user.id, tenant_id=chosen_tenant_id, roles=roles),
        refresh_token=create_refresh_token(user_id=user.id, tenant_id=chosen_tenant_id),
        user=_user_out(user),
        roles=roles,
        tenants=tenants,
    )


def refresh(db: Session, refresh_token: str, *, request_id: str) -> RefreshResponseData:
    payload = decode_token(refresh_token, expected_type="refresh")
    user = repo.get_by_id(db, payload["sub"])
    if user is None:
        raise AppError("UNAUTHORIZED", "身份凭证无效。", http_status=401)
    if user.status == "disabled":
        raise AppError("ACCOUNT_DISABLED", "账号已停用，请联系管理员。", http_status=403)

    tenant_id: str | None = payload.get("tenant_id")
    roles: list[str] = []
    if tenant_id:
        membership = tenant_repo.get_membership(db, tenant_id, user.id)
        if membership is None:
            raise AppError("TENANT_FORBIDDEN", "您不属于该租户。", http_status=403)
        roles = [membership.role]
    roles = _bootstrap_platform_admin(db, user, roles, request_id=request_id)

    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=user.id,
        action="token_refreshed",
        resource_type="auth",
        resource_id=user.id,
        result="success",
        request_id=request_id,
    )
    db.commit()

    return RefreshResponseData(
        access_token=create_access_token(user_id=user.id, tenant_id=tenant_id, roles=roles)
    )


def get_me(db: Session, user_id: str, roles: list[str]) -> MeResponseData:
    """当前用户信息 + token 中的角色 + 全部租户归属。"""
    user = repo.get_by_id(db, user_id)
    if user is None:
        raise AppError("UNAUTHORIZED", "身份凭证无效。", http_status=401)
    return MeResponseData(user=_user_out(user), roles=roles, tenants=_tenant_briefs(db, user.id))


def logout(db: Session, user_id: str, *, request_id: str) -> None:
    """P0 无状态实现：服务端不维护令牌黑名单，仅记录审计。"""
    log_audit(
        db,
        tenant_id=None,
        actor_id=user_id,
        action="logout",
        resource_type="auth",
        resource_id=user_id,
        result="success",
        request_id=request_id,
    )
    db.commit()
