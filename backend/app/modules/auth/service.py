"""认证业务逻辑。

开发环境 seed 账号（由 app/db/seed.py 创建，本模块不实现 seed）：
- admin / admin123：平台管理员（归属 0 个小院，登录后 yard_id=None）
- yard_admin / admin123：小院管理员（归属演示小院）
- expert / admin123：专家角色（归属演示小院）
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
    hash_password,
    resolve_yard_id,
    verify_password,
)
from app.modules.audit.service import log_audit
from app.modules.auth import repository as repo
from app.modules.auth.models import User
from app.modules.auth.schemas import (
    ChangePasswordResponseData,
    LoginResponseData,
    MeResponseData,
    ProfileUpdateRequest,
    RefreshResponseData,
    UserOut,
    YardBrief,
)
from app.modules.yards import repository as yard_repo
from app.modules.yards import settings_service as yard_settings
from app.modules.yards.signup import (
    ensure_contact_unique,
    normalize_email,
    normalize_phone,
    validate_password,
)

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
                yard_id=None,
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
            yard_id=None,
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
        phone=user.phone,
        display_name=user.display_name,
        status=user.status,
        must_change_password=bool(user.must_change_password),
        last_login_at=user.last_login_at,
    )


def _yard_briefs(db: Session, user_id: str) -> list[YardBrief]:
    """用户全部有效小院归属（附小院名）。"""
    briefs: list[YardBrief] = []
    for m in yard_repo.get_memberships_by_user(db, user_id):
        yard = yard_repo.get_yard(db, m.yard_id)
        if yard is None or yard.status != "active":
            continue
        briefs.append(YardBrief(yard_id=yard.id, yard_name=yard.name, roles=[m.role]))
    return briefs


def _audit_login_failed(db: Session, username: str, request_id: str, reason: str) -> None:
    log_audit(
        db,
        yard_id=None,
        actor_id=None,
        action="login_failed",
        resource_type="auth",
        resource_id=username,
        result="failure",
        request_id=request_id,
        metadata={"reason": reason},
    )
    db.commit()


# 2.0.0 术语改名把演示账号 tenant_admin 一并改成 yard_admin，但老用户手里记的还是旧名，
# 而且若那套安装里已自建过 yard_admin，迁移会保留旧名（见迁移 0022 的冲突保护）。
# 这里只在"旧名查无此人且新名存在"时换一次候选名，密码校验一字不改，不构成任何绕过。
_LEGACY_USERNAMES = {"tenant_admin": "yard_admin"}


def authenticate(db: Session, username: str, password: str) -> User:
    user = repo.get_by_username(db, username)
    if user is None:
        legacy_as = _LEGACY_USERNAMES.get(username)
        if legacy_as is not None:
            user = repo.get_by_username(db, legacy_as)
    if user is None or not verify_password(password, user.password_hash):
        raise AppError("INVALID_CREDENTIALS", "用户名或密码错误。", http_status=401)
    if user.status == "disabled":
        raise AppError("ACCOUNT_DISABLED", "账号已停用，请联系管理员。", http_status=403)
    if user.status == "pending":
        raise AppError("ACCOUNT_PENDING", "账号正在等待审核。", http_status=403)
    return user


def login(
    db: Session, username: str, password: str, yard_id: str | None, *, request_id: str
) -> LoginResponseData:
    try:
        user = authenticate(db, username, password)
    except AppError as e:
        _audit_login_failed(db, username, request_id, e.code.lower())
        raise

    yards = _yard_briefs(db, user.id)

    if yard_id is None:
        if not yards:
            chosen: YardBrief | None = None
        elif len(yards) == 1:
            chosen = yards[0]
        else:
            raise AppError(
                "YARD_REQUIRED",
                "请选择要登录的小院。",
                http_status=400,
                details={"yards": [t.model_dump() for t in yards]},
            )
    else:
        # 显式指定 yard_id 时一律校验成员关系（即使只有一个归属小院也不能绕过）。
        chosen = next((t for t in yards if t.yard_id == yard_id), None)
        if chosen is None:
            log_audit(
                db,
                yard_id=yard_id,
                actor_id=user.id,
                action="login_failed",
                resource_type="auth",
                resource_id=username,
                result="denied",
                request_id=request_id,
                metadata={"reason": "yard_forbidden"},
            )
            db.commit()
            raise AppError("YARD_FORBIDDEN", "您不属于该小院。", http_status=403)

    roles = chosen.roles if chosen else []
    chosen_yard_id = chosen.yard_id if chosen else None
    # 平台管理员 bootstrap：用户名在配置名单中则授予 platform_admin（含弱名单运行时防御）
    roles = _bootstrap_platform_admin(db, user, roles, request_id=request_id)

    user.last_login_at = _utcnow()
    log_audit(
        db,
        yard_id=chosen_yard_id,
        actor_id=user.id,
        action="login_success",
        resource_type="auth",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={"username": username, "yard_id": chosen_yard_id},
    )
    db.commit()

    return LoginResponseData(
        access_token=create_access_token(
            user_id=user.id,
            yard_id=chosen_yard_id,
            roles=roles,
            token_version=user.token_version,
        ),
        refresh_token=create_refresh_token(
            user_id=user.id, yard_id=chosen_yard_id, token_version=user.token_version
        ),
        user=_user_out(user),
        roles=roles,
        yards=yards,
    )


def refresh(db: Session, refresh_token: str, *, request_id: str) -> RefreshResponseData:
    payload = decode_token(refresh_token, expected_type="refresh")
    user = repo.get_by_id(db, payload["sub"])
    if user is None:
        raise AppError("UNAUTHORIZED", "身份凭证无效。", http_status=401)
    if user.status == "disabled":
        raise AppError("ACCOUNT_DISABLED", "账号已停用，请联系管理员。", http_status=403)
    if user.status == "pending":
        raise AppError("ACCOUNT_PENDING", "账号正在等待审核。", http_status=403)
    if "ver" in payload and int(payload.get("ver") or 0) != int(user.token_version or 0):
        raise AppError("UNAUTHORIZED", "登录状态已失效，请重新登录。", http_status=401)

    # 旧令牌（重命名前签发）只带 tenant_id：此处漏回退会静默得到 roles=[]（降权）。
    yard_id: str | None = resolve_yard_id(payload)
    roles: list[str] = []
    if yard_id:
        membership = yard_repo.get_membership(db, yard_id, user.id)
        if membership is None:
            raise AppError("YARD_FORBIDDEN", "您不属于该小院。", http_status=403)
        roles = [membership.role]
    roles = _bootstrap_platform_admin(db, user, roles, request_id=request_id)

    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.id,
        action="token_refreshed",
        resource_type="auth",
        resource_id=user.id,
        result="success",
        request_id=request_id,
    )
    db.commit()

    return RefreshResponseData(
        access_token=create_access_token(
            user_id=user.id, yard_id=yard_id, roles=roles, token_version=user.token_version
        )
    )


def get_me(db: Session, user_id: str, roles: list[str]) -> MeResponseData:
    """当前用户信息 + token 中的角色 + 全部小院归属。"""
    user = repo.get_by_id(db, user_id)
    if user is None:
        raise AppError("UNAUTHORIZED", "身份凭证无效。", http_status=401)
    return MeResponseData(user=_user_out(user), roles=roles, yards=_yard_briefs(db, user.id))


def logout(db: Session, user_id: str, *, request_id: str) -> None:
    """P0 无状态实现：服务端不维护令牌黑名单，仅记录审计。"""
    log_audit(
        db,
        yard_id=None,
        actor_id=user_id,
        action="logout",
        resource_type="auth",
        resource_id=user_id,
        result="success",
        request_id=request_id,
    )
    db.commit()


def update_profile(
    db: Session,
    user_id: str,
    roles: list[str],
    req: ProfileUpdateRequest,
    *,
    yard_id: str | None,
    request_id: str,
) -> MeResponseData:
    """自助更新资料（显示名/邮箱/手机号），格式与全局唯一校验。"""
    user = repo.get_by_id(db, user_id)
    if user is None:
        raise AppError("UNAUTHORIZED", "身份凭证无效。", http_status=401)

    provided = req.model_fields_set
    if not provided:
        raise AppError("INVALID_REQUEST", "没有需要更新的字段。", http_status=400)

    changed: list[str] = []
    if "display_name" in provided:
        clean = (req.display_name or "").strip()
        if not clean:
            raise AppError("INVALID_REQUEST", "显示名称不能为空。", http_status=400)
        if clean != user.display_name:
            user.display_name = clean
            changed.append("display_name")
    if "email" in provided:
        email = normalize_email(req.email)
        ensure_contact_unique(db, email=email, phone=None, exclude_user_id=user.id)
        if email != user.email:
            user.email = email
            changed.append("email")
    if "phone" in provided:
        phone = normalize_phone(req.phone)
        ensure_contact_unique(db, email=None, phone=phone, exclude_user_id=user.id)
        if phone != user.phone:
            user.phone = phone
            changed.append("phone")

    if changed:
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=user.id,
            action="profile_updated",
            resource_type="user",
            resource_id=user.id,
            result="success",
            request_id=request_id,
            metadata={"fields": changed},
        )
        db.commit()
    return get_me(db, user.id, roles)


def change_password(
    db: Session,
    *,
    user_id: str,
    yard_id: str | None,
    old_password: str,
    new_password: str,
    request_id: str,
) -> ChangePasswordResponseData:
    """自助改密：校验旧密码/长度/不得复用，成功后旧令牌全部失效。"""
    user = repo.get_by_id(db, user_id)
    if user is None:
        raise AppError("UNAUTHORIZED", "身份凭证无效。", http_status=401)
    if not verify_password(old_password, user.password_hash):
        raise AppError("INVALID_OLD_PASSWORD", "当前密码不正确。", http_status=400)
    min_len = None
    if yard_id:
        security = yard_settings.get_setting(db, yard_id, "security")
        min_len = int(security.get("password_min_length") or 0)
    validate_password(new_password, min_len=min_len)
    if new_password == old_password or verify_password(new_password, user.password_hash):
        raise AppError("PASSWORD_UNCHANGED", "新密码不能与当前密码相同。", http_status=400)

    user.password_hash = hash_password(new_password)
    user.password_updated_at = _utcnow()
    user.must_change_password = False
    user.token_version = int(user.token_version or 0) + 1

    # 用当前小院/角色重新签发（角色可能已变化；成员关系失效则清空上下文）
    roles: list[str] = []
    if yard_id:
        membership = yard_repo.get_membership(db, yard_id, user.id)
        if membership is not None:
            roles = [membership.role]
        else:
            yard_id = None
    roles = _bootstrap_platform_admin(db, user, roles, request_id=request_id)

    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.id,
        action="password_changed",
        resource_type="auth",
        resource_id=user.id,
        result="success",
        request_id=request_id,
    )
    db.commit()
    return ChangePasswordResponseData(
        access_token=create_access_token(
            user_id=user.id, yard_id=yard_id, roles=roles, token_version=user.token_version
        ),
        refresh_token=create_refresh_token(
            user_id=user.id, yard_id=yard_id, token_version=user.token_version
        ),
        must_change_password=False,
    )
