"""认证业务逻辑。

开发环境 seed 账号（由 app/db/seed.py 创建，本模块不实现 seed）：
- admin / admin123：平台管理员（归属 0 个小院，登录后 yard_id=None）
- yard_admin / admin123：小院管理员（归属演示小院）
- expert / admin123：专家角色（归属演示小院）
生产环境必须通过环境变量/安全流程重置这些默认口令。
"""

import hmac
import os
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
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
from app.modules.auth.models import RefreshSession, User
from app.modules.auth.schemas import (
    ChangePasswordResponseData,
    LoginResponseData,
    MeResponseData,
    ProfileUpdateRequest,
    RefreshResponseData,
    UserOut,
    YardBrief,
)
from app.modules.yards import authz as yard_authz
from app.modules.yards import repository as yard_repo
from app.modules.yards import settings_service as yard_settings
from app.modules.yards.models import Yard
from app.modules.yards.permissions import ALL_PERMISSIONS, default_role_permissions
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


def _issue_refresh_session(
    db: Session, *, user_id: str, yard_id: str | None, token_version: int
) -> str:
    """签发带 jti 的刷新令牌并登记会话；顺手清理已过期行（其 JWT 也已失效）。"""
    now = _utcnow()
    jti = uuid.uuid4().hex
    db.add(
        RefreshSession(
            jti=jti,
            user_id=user_id,
            yard_id=yard_id,
            expires_at=now + timedelta(days=settings.refresh_token_expire_days),
        )
    )
    db.query(RefreshSession).filter(RefreshSession.expires_at < now).delete(
        synchronize_session=False
    )
    return create_refresh_token(
        user_id=user_id, yard_id=yard_id, token_version=token_version, jti=jti
    )


def _revoke_user_sessions(db: Session, user_id: str) -> None:
    db.query(RefreshSession).filter(
        RefreshSession.user_id == user_id, RefreshSession.revoked_at.is_(None)
    ).update({RefreshSession.revoked_at: _utcnow()}, synchronize_session=False)


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
    refresh_token = _issue_refresh_session(
        db, user_id=user.id, yard_id=chosen_yard_id, token_version=user.token_version
    )
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
        refresh_token=refresh_token,
        user=_user_out(user),
        roles=roles,
        yards=yards,
    )


def issue_login_response(
    db: Session,
    user: User,
    yard_id: str,
    *,
    request_id: str = "",
    method: str = "password",
) -> LoginResponseData:
    """按指定小院签发与密码登录完全一致的登录响应（SSO 复用）。

    角色一律从数据库 membership 重算，不信任外部 claim 直接写进 JWT；
    audit 的 method 字段用于区分 password / sso，便于审计追溯。
    """
    yards = _yard_briefs(db, user.id)
    chosen = next((t for t in yards if t.yard_id == yard_id), None)
    if chosen is None:
        raise AppError("YARD_FORBIDDEN", "您不属于该小院。", http_status=403)
    roles = _bootstrap_platform_admin(db, user, list(chosen.roles), request_id=request_id)
    user.last_login_at = _utcnow()
    refresh_token = _issue_refresh_session(
        db, user_id=user.id, yard_id=chosen.yard_id, token_version=user.token_version
    )
    log_audit(
        db,
        yard_id=chosen.yard_id,
        actor_id=user.id,
        action="login_success",
        resource_type="auth",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={"username": user.username, "yard_id": chosen.yard_id, "method": method},
    )
    db.commit()
    return LoginResponseData(
        access_token=create_access_token(
            user_id=user.id,
            yard_id=chosen.yard_id,
            roles=roles,
            token_version=user.token_version,
        ),
        refresh_token=refresh_token,
        user=_user_out(user),
        roles=roles,
        yards=yards,
    )


def desktop_bootstrap(db: Session, token: str, *, request_id: str = "") -> LoginResponseData:
    """桌面壳引导登录：用壳注入的令牌换本机负责人的登录态。

    安全边界：
    - 令牌只存在于 sidecar 进程环境（由壳生成并随每次启动注入），不落库；
    - 路由层限制只允许 127.0.0.1/::1 调用，令牌比较用恒定时间；
    - 只签发给本机数据目录里第一个小院的负责人，不提供用户名/密码入口。

    这样安装后双击即用，不再要求用户去读 desktop-login.txt。
    """
    expected = os.environ.get("TECHYARD_DESKTOP_BOOTSTRAP", "").strip()
    if not expected:
        raise AppError("DESKTOP_DISABLED", "桌面引导登录未启用。", http_status=404)
    if not token or not hmac.compare_digest(token, expected):
        raise AppError("INVALID_BOOTSTRAP_TOKEN", "桌面启动凭证无效。", http_status=401)

    yard = db.scalar(select(Yard).where(Yard.owner_user_id.is_not(None)).limit(1))
    if yard is None or not yard.owner_user_id:
        raise AppError(
            "DESKTOP_NOT_READY",
            "本机数据尚未初始化完成，请稍候或重启应用。",
            http_status=503,
        )
    user = repo.get_by_id(db, yard.owner_user_id)
    if user is None or user.status != "active":
        raise AppError(
            "DESKTOP_NOT_READY",
            "本机负责人账号不可用，请重启应用或重置本地数据。",
            http_status=503,
        )
    return issue_login_response(db, user, yard.id, request_id=request_id, method="desktop")


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

    # 刷新令牌轮换：旧 jti 必须在会话表中且未吊销；缺失 jti 的存量令牌
    # （v3.7 前签发）放行一次，换取带 jti 的新令牌后进入轮换。
    jti = payload.get("jti")
    session: RefreshSession | None = None
    if jti:
        session = db.query(RefreshSession).filter(RefreshSession.jti == str(jti)).first()
        if session is None or session.user_id != user.id:
            raise AppError("UNAUTHORIZED", "登录状态已失效，请重新登录。", http_status=401)
        if session.revoked_at is not None:
            # 已轮换/已登出的令牌被再次使用：按泄露处理，吊销该用户全部会话。
            _revoke_user_sessions(db, user.id)
            db.commit()
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

    if session is not None:
        session.revoked_at = _utcnow()
    new_refresh_token = _issue_refresh_session(
        db, user_id=user.id, yard_id=yard_id, token_version=user.token_version
    )
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
        ),
        refresh_token=new_refresh_token,
    )


def get_me(
    db: Session, user_id: str, roles: list[str], yard_id: str | None = None
) -> MeResponseData:
    """当前用户信息 + 角色 + 小院归属 + 当前小院有效权限（千人千面唯一事实源）。"""
    user = repo.get_by_id(db, user_id)
    if user is None:
        raise AppError("UNAUTHORIZED", "身份凭证无效。", http_status=401)
    # v3.66：枢纽上下文与枢纽权限包（延迟 import 避免 core → modules 的循环）
    from app.modules.hubs import service as hub_service

    is_platform_admin = "platform_admin" in roles
    hubs = hub_service.hub_context(db, user_id, is_platform_admin=is_platform_admin)
    if is_platform_admin:
        permissions = ALL_PERMISSIONS
        owner = True
    elif yard_id:
        permissions = yard_authz.effective_permissions(
            db, yard_id=yard_id, user_id=user_id, jwt_roles=roles
        )
        owner = yard_authz.is_owner(db, yard_id, user_id)
    else:
        permissions = frozenset().union(*(default_role_permissions(r) for r in roles))
        owner = False
    # 枢纽权限与 JWT 角色解耦：纯枢纽账号没有小院角色，也能拿到 hubs:view/manage 等
    permissions = frozenset(permissions) | hub_service.hub_permissions(
        db, user_id, is_platform_admin=is_platform_admin
    )
    # v3.67：平台级权限（品牌/门户）与 JWT/小院角色正交，只由超管直通或
    # platform_grants 逐人授予——小院负责人不会因为「负责人=全集」而拿到它
    from app.modules.branding import service as branding_service

    permissions = frozenset(permissions) | branding_service.platform_permissions(
        db, user_id, is_platform_admin=is_platform_admin
    )
    return MeResponseData(
        user=_user_out(user),
        roles=roles,
        yards=_yard_briefs(db, user.id),
        yard_id=yard_id,
        is_owner=owner,
        permissions=sorted(permissions),
        hubs=hubs,
    )


def logout(db: Session, user_id: str, *, refresh_token: str | None = None, request_id: str) -> None:
    """登出即吊销当前刷新会话（v3.7）。

    客户端带上 refresh_token 时精确吊销该会话；未带时仅记审计（兼容旧客户端，
    访问令牌仍按自身 exp 过期）。令牌解析失败不阻断登出。
    """
    revoked = 0
    if refresh_token:
        try:
            payload = decode_token(refresh_token, expected_type="refresh")
        except AppError:
            payload = None
        jti = payload.get("jti") if payload else None
        if payload and payload.get("sub") == user_id and jti:
            revoked = (
                db.query(RefreshSession)
                .filter(
                    RefreshSession.jti == str(jti),
                    RefreshSession.user_id == user_id,
                    RefreshSession.revoked_at.is_(None),
                )
                .update({RefreshSession.revoked_at: _utcnow()}, synchronize_session=False)
            )
    log_audit(
        db,
        yard_id=None,
        actor_id=user_id,
        action="logout",
        resource_type="auth",
        resource_id=user_id,
        result="success",
        request_id=request_id,
        metadata={"refresh_session_revoked": True} if revoked else None,
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
    return get_me(db, user.id, roles, yard_id)


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
    # 改密后旧刷新会话全部作废（token_version 已让旧 JWT 失效，这里清掉服务端会话）
    _revoke_user_sessions(db, user.id)

    # 用当前小院/角色重新签发（角色可能已变化；成员关系失效则清空上下文）
    roles: list[str] = []
    if yard_id:
        membership = yard_repo.get_membership(db, yard_id, user.id)
        if membership is not None:
            roles = [membership.role]
        else:
            yard_id = None
    roles = _bootstrap_platform_admin(db, user, roles, request_id=request_id)
    refresh_token = _issue_refresh_session(
        db, user_id=user.id, yard_id=yard_id, token_version=user.token_version
    )

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
        refresh_token=refresh_token,
        must_change_password=False,
    )
