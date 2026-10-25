"""租户自助注册业务逻辑。只做业务规则；HTTP/鉴权在 signup_router 层。"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.audit.service import log_audit
from app.modules.auth.repository import create_user, get_by_username
from app.modules.tenants import repository as repo
from app.modules.tenants.models import Tenant

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")

#: 保留管理员用户名：自助注册一律拒绝（大小写不敏感），防止冒充系统/运维账号。
RESERVED_ADMIN_USERNAMES = frozenset(
    {
        "admin",
        "root",
        "system",
        "administrator",
        "service_worker",
        "platform_admin",
    }
)


def reserved_usernames() -> frozenset[str]:
    """静态保留名 ∪ 运行时平台管理员名单，统一小写比较。

    否则部署方把 PLATFORM_ADMIN_USERNAMES 改为 ops_admin 后，攻击者可先自助
    注册同名账号，登录时被 bootstrap 成平台管理员（条件性提权）。
    """
    configured = {u.strip().lower() for u in settings.platform_admin_username_list}
    return RESERVED_ADMIN_USERNAMES | configured


def min_password_length() -> int:
    """密码最小长度：跟随 settings.password_min_length；生产环境强制不低于 10。"""
    min_len = settings.password_min_length
    if settings.is_production:
        min_len = max(min_len, 10)
    return min_len


def normalize_username(raw: str | None, *, field_label: str = "账号") -> str:
    """校验并归一化账号：3-32 位字母/数字/下划线，且不得占用保留名。

    自助注册与邀请接受共用同一套规则，避免两条创建账号路径口径不一致。
    """
    username = (raw or "").strip()
    if len(username) < 3 or len(username) > 32 or not USERNAME_PATTERN.match(username):
        raise AppError(
            "INVALID_REQUEST",
            f"{field_label}需为 3-32 位字母、数字或下划线。",
            http_status=400,
        )
    if username.lower() in reserved_usernames():
        raise AppError(
            "RESERVED_USERNAME",
            "该用户名为保留管理员名称，不能使用。",
            http_status=400,
        )
    return username


def validate_password(password: str) -> None:
    min_len = min_password_length()
    if len(password) < min_len:
        raise AppError("INVALID_REQUEST", f"密码长度不能少于 {min_len} 位。", http_status=400)


def _get_tenant_by_name(db: Session, name: str) -> Tenant | None:
    return db.execute(select(Tenant).where(Tenant.name == name)).scalar_one_or_none()


def signup_tenant(
    db: Session,
    *,
    tenant_name: str,
    admin_username: str,
    password: str,
    contact: str | None = None,
    client_ip: str,
) -> dict:
    name = tenant_name.strip()
    if len(name) < 2 or len(name) > 64:
        raise AppError("INVALID_REQUEST", "租户名称需为 2-64 个字符。", http_status=400)
    username = normalize_username(admin_username, field_label="管理员账号")
    validate_password(password)
    if _get_tenant_by_name(db, name) is not None:
        raise AppError("TENANT_NAME_TAKEN", "该租户名称已被占用，请换一个名称。", http_status=409)
    if get_by_username(db, username) is not None:
        raise AppError("USERNAME_TAKEN", "该管理员账号已被占用，请换一个账号。", http_status=409)
    email = contact.strip() if contact and "@" in contact else None
    tenant = repo.create_tenant(db, name=name)
    user = create_user(db, username=username, password=password, email=email, display_name=username)
    repo.add_membership(db, tenant_id=tenant.id, user_id=user.id, role="tenant_admin")
    log_audit(
        db,
        tenant_id=tenant.id,
        actor_id=user.id,
        action="tenant_signup",
        resource_type="tenant",
        resource_id=tenant.id,
        result="success",
        request_id=f"signup:{client_ip}",
        metadata={"username": username},
    )
    db.commit()
    from app.modules.tenants.quota import ensure_quota

    ensure_quota(db, tenant.id)
    token = create_access_token(user_id=user.id, tenant_id=tenant.id, roles=["tenant_admin"])
    return {"tenant_id": tenant.id, "user_id": user.id, "access_token": token}
