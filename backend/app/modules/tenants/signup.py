"""租户自助注册业务逻辑。只做业务规则；HTTP/鉴权在 signup_router 层。"""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.audit.service import log_audit
from app.modules.auth.repository import create_user, get_by_username
from app.modules.tenants import repository as repo
from app.modules.tenants.models import Tenant

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


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
    username = admin_username.strip()
    if len(username) < 3 or len(username) > 32 or not USERNAME_PATTERN.match(username):
        raise AppError(
            "INVALID_REQUEST", "管理员账号需为 3-32 位字母、数字或下划线。", http_status=400
        )
    if len(password) < 6:
        raise AppError("INVALID_REQUEST", "密码长度不能少于 6 位。", http_status=400)
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
