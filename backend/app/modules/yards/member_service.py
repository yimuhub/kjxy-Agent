"""成员管理业务逻辑：创建/挂接、资料与角色调整、停用、重置密码、软删除。

权限规则集中在此层：
- 负责人可管理除自身角色/状态外的任何成员；
- 管理员（yard_admin）可管理 expert/operator/viewer，不能动管理员与负责人；
- 专家（members:manage_limited）只能管理 operator/viewer；
- 任何人都不能改自己的角色/状态（防自提权）。
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.security import hash_password
from app.modules.audit.service import log_audit
from app.modules.auth.models import User
from app.modules.auth.repository import create_user, get_by_username
from app.modules.yards import repository as repo
from app.modules.yards import settings_service
from app.modules.yards.authz import (
    can_manage_target_role,
    effective_permissions,
    has_permission,
    is_owner,
)
from app.modules.yards.models import YardMembership, YardRole
from app.modules.yards.permissions import ASSIGNABLE_ROLES, ROLE_LABELS
from app.modules.yards.schemas import (
    MemberCreateRequest,
    MemberCreateResult,
    MemberUpdateRequest,
    YardMemberOut,
)
from app.modules.yards.signup import (
    ensure_contact_unique,
    normalize_email,
    normalize_phone,
    normalize_username,
    validate_password,
)

_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
MEMBER_STATUSES = ("active", "disabled")


def _now() -> datetime:
    return datetime.now(UTC)


def generate_password(length: int = 12) -> str:
    """生成一次性密码（去掉易混淆字符）。"""
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def mask_email(email: str | None) -> str | None:
    if not email:
        return None
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    if len(phone) < 8:
        return "***"
    return f"{phone[:3]}****{phone[-4:]}"


def get_membership_any(db: Session, yard_id: str, user_id: str) -> YardMembership | None:
    """取成员关系（含 disabled），用于重新启用/软删除后的管理。"""
    return db.scalar(
        select(YardMembership).where(
            YardMembership.yard_id == yard_id,
            YardMembership.user_id == user_id,
        )
    )


def actor_role(db: Session, ctx: YardContext) -> str:
    """当前操作人的小院角色：负责人优先，其次 DB membership，最后 JWT 兜底。"""
    if is_owner(db, ctx.yard_id, ctx.user_id):
        return "yard_owner"
    if "platform_admin" in ctx.roles:
        return "yard_owner"
    membership = repo.get_membership(db, ctx.yard_id, ctx.user_id)
    if membership is not None:
        return membership.role
    for role in ("yard_admin", "expert", "operator", "viewer"):
        if role in ctx.roles:
            return role
    return ""


def _role_names(db: Session, yard_id: str) -> dict[str, str]:
    names = dict(ROLE_LABELS)
    for row in db.scalars(select(YardRole).where(YardRole.yard_id == yard_id)).all():
        names[row.code] = row.name
    return names


def custom_role_permissions(db: Session, yard_id: str, code: str) -> frozenset[str] | None:
    """自定义角色的权限集合；系统角色或未定义时返回 None。"""
    row = db.scalar(select(YardRole).where(YardRole.yard_id == yard_id, YardRole.code == code))
    if row is None or row.is_system:
        return None
    return frozenset(str(p) for p in (row.permissions_json or []))


def member_out(
    membership: YardMembership,
    user: User,
    *,
    full: bool,
    role_names: dict[str, str] | None = None,
) -> YardMemberOut:
    names = role_names or ROLE_LABELS
    return YardMemberOut(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email if full else mask_email(user.email),
        phone=user.phone if full else mask_phone(user.phone),
        role=membership.role,
        role_name=names.get(membership.role, membership.role),
        status=membership.status,
        joined_at=membership.created_at,
        last_login_at=user.last_login_at,
        must_change_password=bool(user.must_change_password),
        tokens_7d=0,
    )


def can_view_full_contacts(db: Session, ctx: YardContext) -> bool:
    """拥有 members:manage 才可看到完整邮箱/手机号。"""
    return has_permission(
        db,
        yard_id=ctx.yard_id,
        user_id=ctx.user_id,
        jwt_roles=ctx.roles,
        permission="members:manage",
    )


def list_members(
    db: Session, ctx: YardContext, *, page: int, page_size: int
) -> tuple[list[YardMemberOut], int]:
    rows, total = repo.list_members(db, ctx.yard_id, page=page, page_size=page_size)
    full = can_view_full_contacts(db, ctx)
    names = _role_names(db, ctx.yard_id)
    items = [member_out(m, u, full=full, role_names=names) for m, u in rows]
    return items, total


def _ensure_can_assign(
    db: Session,
    ctx: YardContext,
    actor: str,
    role: str,
    actor_perms: frozenset[str],
) -> None:
    """校验角色可授予：系统角色按层级，自定义角色须为 actor 自身权限的子集。"""
    if role in ASSIGNABLE_ROLES:
        if role == "yard_admin" and actor != "yard_owner":
            raise AppError("FORBIDDEN", "只有小院负责人可以授予管理员角色。", http_status=403)
        if not can_manage_target_role(actor, role):
            raise AppError("FORBIDDEN", "无权授予该角色。", http_status=403)
        return
    custom = custom_role_permissions(db, ctx.yard_id, role)
    if custom is None:
        raise AppError(
            "INVALID_REQUEST",
            f"角色不合法，可选：{', '.join(ASSIGNABLE_ROLES)} 或本小院自定义角色。",
            http_status=400,
        )
    if actor not in {"yard_owner", "yard_admin"}:
        raise AppError("FORBIDDEN", "只有负责人或管理员可以授予自定义角色。", http_status=403)
    if not custom <= actor_perms:
        raise AppError("FORBIDDEN", "不能授予超出自身权限的角色。", http_status=403)


def _has_other_active_membership(db: Session, user_id: str, yard_id: str) -> bool:
    row = db.scalar(
        select(YardMembership).where(
            YardMembership.user_id == user_id,
            YardMembership.yard_id != yard_id,
            YardMembership.status == "active",
        )
    )
    return row is not None


def create_member(
    db: Session, ctx: YardContext, req: MemberCreateRequest, *, request_id: str
) -> MemberCreateResult:
    actor = actor_role(db, ctx)
    role = (req.role or "viewer").strip()
    actor_perms = effective_permissions(
        db, yard_id=ctx.yard_id, user_id=ctx.user_id, jwt_roles=ctx.roles
    )
    _ensure_can_assign(db, ctx, actor, role, actor_perms)
    username = normalize_username(req.username)

    email = normalize_email(req.email)
    phone = normalize_phone(req.phone)

    password = req.password
    if password is not None:
        security = settings_service.get_setting(db, ctx.yard_id, "security")
        validate_password(password, min_len=int(security.get("password_min_length") or 0))
    else:
        password = generate_password(12)

    user = get_by_username(db, username)
    initial_password: str | None = None

    if user is not None:
        existing = get_membership_any(db, ctx.yard_id, user.id)
        if existing is not None:
            raise AppError("MEMBER_EXISTS", "该用户已是本小院成员。", http_status=409)
        if user.status == "disabled":
            raise AppError("ACCOUNT_DISABLED", "该账号已停用，无法加入。", http_status=403)
        # 已有全局账号必须由本人确认加入：邀请链接接受时校验原密码（invitations.accept）。
        # 不允许管理员凭用户名单方面挂接，否则不可信小院管理员可挂接受害者后再重置其
        # 全局密码，接管其在原院的身份（审查 B1）。新账号仍走下方创建分支。
        raise AppError(
            "MEMBER_INVITE_REQUIRED",
            "该用户名已是平台账号，须由本人通过「邀请链接」确认加入，不能直接挂接。",
            http_status=409,
        )
    else:
        ensure_contact_unique(db, email=email, phone=phone)
        clean_display = (req.display_name or "").strip() or username
        user = create_user(
            db,
            username=username,
            password=password,
            email=email,
            display_name=clean_display,
        )
        user.phone = phone
        user.must_change_password = True
        user.password_updated_at = _now()
        initial_password = password

    membership = repo.add_membership(db, yard_id=ctx.yard_id, user_id=user.id, role=role)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="member_created",
        resource_type="yard_member",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={
            "username": user.username,
            "role": role,
            "attached": initial_password is None,
            "note": req.note,
        },
    )
    db.commit()
    return MemberCreateResult(
        member=member_out(membership, user, full=True, role_names=_role_names(db, ctx.yard_id)),
        initial_password=initial_password,
    )


def update_member(
    db: Session, ctx: YardContext, user_id: str, req: MemberUpdateRequest, *, request_id: str
) -> YardMemberOut:
    membership = get_membership_any(db, ctx.yard_id, user_id)
    if membership is None:
        raise AppError("NOT_FOUND", "成员不存在。", http_status=404)
    user = db.get(User, user_id)
    if user is None:
        raise AppError("NOT_FOUND", "成员账号不存在。", http_status=404)

    provided = req.model_fields_set
    if not provided:
        raise AppError("INVALID_REQUEST", "没有需要更新的字段。", http_status=400)

    actor = actor_role(db, ctx)
    is_self = ctx.user_id == user_id
    target_is_owner = is_owner(db, ctx.yard_id, user_id) or membership.role == "yard_owner"

    role_changed = "role" in provided and req.role != membership.role
    status_changed = "status" in provided and req.status != membership.status

    if role_changed or status_changed:
        if is_self:
            raise AppError("FORBIDDEN", "不能修改自己的角色或状态。", http_status=403)
        if target_is_owner:
            raise AppError("FORBIDDEN", "小院负责人不能被降级或停用。", http_status=403)

    if not is_self and not can_manage_target_role(actor, membership.role):
        raise AppError("FORBIDDEN", "无权管理该成员。", http_status=403)

    changed: list[str] = []
    role_audit = False

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

    if role_changed:
        actor_perms = effective_permissions(
            db, yard_id=ctx.yard_id, user_id=ctx.user_id, jwt_roles=ctx.roles
        )
        _ensure_can_assign(db, ctx, actor, req.role or "", actor_perms)
        membership.role = req.role or membership.role
        # 令牌版本递增：旧 access token 立即失效，前端刷新后拿到新角色
        user.token_version = int(user.token_version or 0) + 1
        role_audit = True
        changed.append("role")

    if status_changed:
        new_status = (req.status or "").strip()
        if new_status not in MEMBER_STATUSES:
            raise AppError("INVALID_REQUEST", "状态只能是 active 或 disabled。", http_status=400)
        if new_status == "disabled":
            _disable_membership(db, ctx.yard_id, membership, user)
        else:
            membership.status = "active"
            if user.status == "disabled" and not _has_other_active_membership(
                db, user.id, ctx.yard_id
            ):
                user.status = "active"
        changed.append("status")

    if not changed:
        return member_out(
            membership,
            user,
            full=can_view_full_contacts(db, ctx),
            role_names=_role_names(db, ctx.yard_id),
        )

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="member_updated",
        resource_type="yard_member",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={"fields": changed, "note": req.note},
    )
    if role_audit:
        log_audit(
            db,
            yard_id=ctx.yard_id,
            actor_id=ctx.user_id,
            action="member_role_changed",
            resource_type="yard_member",
            resource_id=user.id,
            result="success",
            request_id=request_id,
            metadata={"role": membership.role},
        )
    if status_changed:
        log_audit(
            db,
            yard_id=ctx.yard_id,
            actor_id=ctx.user_id,
            action="member_disabled" if membership.status == "disabled" else "member_enabled",
            resource_type="yard_member",
            resource_id=user.id,
            result="success",
            request_id=request_id,
        )
    db.commit()
    return member_out(
        membership,
        user,
        full=can_view_full_contacts(db, ctx),
        role_names=_role_names(db, ctx.yard_id),
    )


def _disable_membership(db: Session, yard_id: str, membership: YardMembership, user: User) -> None:
    """停用成员：成员关系置 disabled；无其他有效小院时账号同时停用。"""
    membership.status = "disabled"
    user.token_version = int(user.token_version or 0) + 1
    if not _has_other_active_membership(db, user.id, yard_id):
        user.status = "disabled"


def reset_member_password(
    db: Session, ctx: YardContext, user_id: str, *, request_id: str
) -> tuple[str, YardMemberOut]:
    membership = get_membership_any(db, ctx.yard_id, user_id)
    if membership is None:
        raise AppError("NOT_FOUND", "成员不存在。", http_status=404)
    user = db.get(User, user_id)
    if user is None:
        raise AppError("NOT_FOUND", "成员账号不存在。", http_status=404)

    actor = actor_role(db, ctx)
    if (
        actor != "yard_owner"
        and user_id != ctx.user_id
        and membership.role in {"yard_owner", "yard_admin"}
    ):
        raise AppError("FORBIDDEN", "无权重置该成员密码。", http_status=403)

    # 重置改的是全局 User 凭证。非平台管理员且非本人时，不得重置平台账号或
    # 跨院共享账号（在其他小院仍有效）的全局密码，否则本院管理员可借此接管
    # 受害者在原院的身份（审查 B1）。本人自助与平台管理员不受此限。
    if "platform_admin" not in ctx.roles and user_id != ctx.user_id:
        if getattr(user, "platform_admin", False):
            raise AppError("FORBIDDEN", "不能重置平台管理员的全局密码。", http_status=403)
        if _has_other_active_membership(db, user_id, ctx.yard_id):
            raise AppError(
                "FORBIDDEN",
                "该账号在其他小院仍是有效成员，不能由本院重置其全局密码；请由本人自助修改或联系平台管理员。",
                http_status=403,
            )

    temporary = generate_password(12)
    user.password_hash = hash_password(temporary)
    user.must_change_password = True
    user.password_updated_at = _now()
    user.token_version = int(user.token_version or 0) + 1

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="member_password_reset",
        resource_type="yard_member",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={"role": membership.role},
    )
    db.commit()
    return (
        temporary,
        member_out(
            membership,
            user,
            full=can_view_full_contacts(db, ctx),
            role_names=_role_names(db, ctx.yard_id),
        ),
    )


def remove_member(db: Session, ctx: YardContext, user_id: str, *, request_id: str) -> None:
    """软删除：保留成员关系并置 disabled，便于审计与恢复。"""
    membership = get_membership_any(db, ctx.yard_id, user_id)
    if membership is None:
        raise AppError("NOT_FOUND", "成员不存在。", http_status=404)
    if ctx.user_id == user_id:
        raise AppError("FORBIDDEN", "不能移除自己。", http_status=403)
    if is_owner(db, ctx.yard_id, user_id) or membership.role == "yard_owner":
        raise AppError("FORBIDDEN", "不能移除小院负责人。", http_status=403)
    if not can_manage_target_role(actor_role(db, ctx), membership.role):
        raise AppError("FORBIDDEN", "无权移除该成员。", http_status=403)
    if membership.role == "yard_admin" and repo.count_active_admins(db, ctx.yard_id) <= 1:
        raise AppError("CONFLICT", "不能移除最后一个小院管理员。", http_status=409)

    user = db.get(User, user_id)
    if user is None:
        raise AppError("NOT_FOUND", "成员账号不存在。", http_status=404)
    _disable_membership(db, ctx.yard_id, membership, user)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="member_removed",
        resource_type="yard_member",
        resource_id=user_id,
        result="success",
        request_id=request_id,
        metadata={"role": membership.role, "soft_delete": True},
    )
    db.commit()


__all__ = [
    "MEMBER_STATUSES",
    "actor_role",
    "can_view_full_contacts",
    "create_member",
    "generate_password",
    "list_members",
    "mask_email",
    "mask_phone",
    "member_out",
    "remove_member",
    "reset_member_password",
    "update_member",
]
