"""小院自助注册业务逻辑。只做业务规则；HTTP/鉴权在 signup_router 层。"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.audit.service import log_audit
from app.modules.auth.models import User
from app.modules.auth.repository import create_user, get_by_username
from app.modules.yards import repository as repo
from app.modules.yards.models import Yard

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")

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


def min_password_length(override: int | None = None) -> int:
    """密码最小长度：部署下限与生产强制值取大；小院安全设置只能更严。

    ``override`` 来自 ``yard_settings.security.password_min_length``。
    """
    min_len = settings.password_min_length
    if settings.is_production:
        min_len = max(min_len, 10)
    if override is not None:
        min_len = max(min_len, int(override))
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


def validate_password(password: str, *, min_len: int | None = None) -> None:
    required = min_password_length(min_len)
    if len(password) < required:
        raise AppError("INVALID_REQUEST", f"密码长度不能少于 {required} 位。", http_status=400)


def normalize_email(raw: str | None) -> str | None:
    """邮箱归一化（小写）+ 格式校验；空值返回 None。"""
    value = (raw or "").strip()
    if not value:
        return None
    if len(value) > 255 or not EMAIL_PATTERN.match(value):
        raise AppError("INVALID_REQUEST", "邮箱格式不正确。", http_status=400)
    return value.lower()


def normalize_phone(raw: str | None) -> str | None:
    """手机号归一化（去空格/横线/可选 +86）+ 格式校验；空值返回 None。"""
    value = (raw or "").strip().replace(" ", "").replace("-", "")
    if not value:
        return None
    if value.startswith("+86"):
        value = value[3:]
    if not PHONE_PATTERN.match(value):
        raise AppError(
            "INVALID_REQUEST", "手机号格式不正确（需为 11 位大陆手机号）。", http_status=400
        )
    return value


def ensure_contact_unique(
    db: Session,
    *,
    email: str | None,
    phone: str | None,
    exclude_user_id: str | None = None,
) -> None:
    """邮箱/手机号全局唯一校验（可排除指定用户）。"""
    if email:
        stmt = select(User).where(User.email == email)
        if exclude_user_id:
            stmt = stmt.where(User.id != exclude_user_id)
        if db.scalar(stmt) is not None:
            raise AppError("EMAIL_TAKEN", "该邮箱已被使用。", http_status=409)
    if phone:
        stmt = select(User).where(User.phone == phone)
        if exclude_user_id:
            stmt = stmt.where(User.id != exclude_user_id)
        if db.scalar(stmt) is not None:
            raise AppError("PHONE_TAKEN", "该手机号已被使用。", http_status=409)


def _get_yard_by_name(db: Session, name: str) -> Yard | None:
    return db.execute(select(Yard).where(Yard.name == name)).scalar_one_or_none()


def signup_yard(
    db: Session,
    *,
    yard_name: str,
    admin_username: str,
    password: str,
    contact: str | None = None,
    client_ip: str,
) -> dict:
    name = yard_name.strip()
    if len(name) < 2 or len(name) > 64:
        raise AppError("INVALID_REQUEST", "小院名称需为 2-64 个字符。", http_status=400)
    username = normalize_username(admin_username, field_label="管理员账号")
    validate_password(password)
    if _get_yard_by_name(db, name) is not None:
        raise AppError("YARD_NAME_TAKEN", "该小院名称已被占用，请换一个名称。", http_status=409)
    if get_by_username(db, username) is not None:
        raise AppError("USERNAME_TAKEN", "该管理员账号已被占用，请换一个账号。", http_status=409)
    email = contact.strip() if contact and "@" in contact else None
    yard = repo.create_yard(db, name=name)
    user = create_user(db, username=username, password=password, email=email, display_name=username)
    yard.owner_user_id = user.id
    repo.add_membership(db, yard_id=yard.id, user_id=user.id, role="yard_owner")
    from app.modules.growth.skills import ensure_growth_skills
    from app.modules.market.agri_skills import ensure_builtin_skills

    ensure_builtin_skills(db, yard_id=yard.id, actor_id=user.id, request_id=f"signup:{client_ip}")
    ensure_growth_skills(db, yard_id=yard.id, actor_id=user.id, request_id=f"signup:{client_ip}")
    log_audit(
        db,
        yard_id=yard.id,
        actor_id=user.id,
        action="yard_signup",
        resource_type="yard",
        resource_id=yard.id,
        result="success",
        request_id=f"signup:{client_ip}",
        metadata={"username": username, "owner": True},
    )
    db.commit()
    from app.modules.yards.quota import ensure_quota

    ensure_quota(db, yard.id)
    db.commit()
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=["yard_owner"])
    return {"yard_id": yard.id, "user_id": user.id, "access_token": token}
