"""注册与入小院审核业务逻辑。

注册模式（yard_settings.registration.mode）：
- closed：拒绝注册（403 REGISTRATION_CLOSED）；
- approval：创建 pending 账号与 pending 入小院申请，等待 owner/admin 审核；
- open：直接创建 active 账号与成员关系（仍需登录，不签发令牌）。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.auth.models import User
from app.modules.auth.repository import create_user, get_by_username
from app.modules.yards import repository as repo
from app.modules.yards import settings_service
from app.modules.yards.member_service import (
    actor_role,
    get_membership_any,
    member_out,
)
from app.modules.yards.models import YardJoinRequest, YardMembership
from app.modules.yards.permissions import ASSIGNABLE_ROLES
from app.modules.yards.schemas import RegisterRequest, RegistrationOut, YardMemberOut
from app.modules.yards.signup import (
    ensure_contact_unique,
    normalize_email,
    normalize_phone,
    normalize_username,
    validate_password,
)

REGISTRATION_STATUSES = ("pending", "approved", "rejected")


def _now() -> datetime:
    return datetime.now(UTC)


def _to_out(row: YardJoinRequest, user: User) -> RegistrationOut:
    return RegistrationOut(
        id=row.id,
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        phone=user.phone,
        note=row.note,
        status=row.status,
        created_at=row.created_at,
        reviewed_at=row.reviewed_at,
        reject_reason=row.reject_reason,
    )


def _default_role(cfg: dict) -> str:
    role = str(cfg.get("default_role") or "viewer")
    return role if role in ASSIGNABLE_ROLES else "viewer"


def register(db: Session, req: RegisterRequest, *, request_id: str) -> dict:
    yard = repo.get_yard(db, req.yard_id)
    if yard is None or yard.status != "active":
        raise AppError("YARD_NOT_FOUND", "小院不存在或已停用。", http_status=404)

    cfg = settings_service.get_setting(db, yard.id, "registration")
    mode = str(cfg.get("mode") or "approval")
    if mode == "closed":
        raise AppError("REGISTRATION_CLOSED", "该小院暂未开放注册。", http_status=403)

    username = normalize_username(req.username)
    security = settings_service.get_setting(db, yard.id, "security")
    validate_password(req.password, min_len=int(security.get("password_min_length") or 0))
    email = normalize_email(req.email)
    phone = normalize_phone(req.phone)
    if cfg.get("require_email") and not email:
        raise AppError("INVALID_REQUEST", "该小院要求填写邮箱。", http_status=400)
    if cfg.get("require_phone") and not phone:
        raise AppError("INVALID_REQUEST", "该小院要求填写手机号。", http_status=400)
    if get_by_username(db, username) is not None:
        raise AppError("USERNAME_TAKEN", "该用户名已被占用。", http_status=409)
    ensure_contact_unique(db, email=email, phone=phone)

    is_open = mode == "open"
    user = create_user(
        db,
        username=username,
        password=req.password,
        email=email,
        display_name=(req.display_name or "").strip() or username,
        status="active" if is_open else "pending",
    )
    user.phone = phone
    user.must_change_password = False

    row = YardJoinRequest(
        yard_id=yard.id,
        user_id=user.id,
        kind="join",
        note=(req.note or None),
        status="approved" if is_open else "pending",
        reviewed_at=_now() if is_open else None,
    )
    db.add(row)
    db.flush()

    if is_open:
        repo.add_membership(db, yard_id=yard.id, user_id=user.id, role=_default_role(cfg))

    log_audit(
        db,
        yard_id=yard.id,
        actor_id=user.id,
        action="registration_submitted",
        resource_type="yard_join_request",
        resource_id=row.id,
        result="success",
        request_id=request_id,
        metadata={"username": username, "mode": mode},
    )
    db.commit()
    return {
        "request_id": row.id,
        "status": "active" if is_open else "pending",
        "message": "注册成功，请直接登录。" if is_open else "注册已提交，请等待管理员审核。",
    }


def list_registrations(
    db: Session, yard_id: str, *, status: str | None = None
) -> list[RegistrationOut]:
    if status is not None and status not in REGISTRATION_STATUSES:
        raise AppError("INVALID_REQUEST", "注册状态不合法。", http_status=400)
    stmt = (
        select(YardJoinRequest, User)
        .join(User, User.id == YardJoinRequest.user_id)
        .where(YardJoinRequest.yard_id == yard_id, YardJoinRequest.kind == "join")
        .order_by(YardJoinRequest.created_at.desc())
    )
    if status is not None:
        stmt = stmt.where(YardJoinRequest.status == status)
    rows = db.execute(stmt).all()
    return [_to_out(row, user) for row, user in rows]


def _get_pending_request(db: Session, ctx: YardContext, join_request_id: str) -> YardJoinRequest:
    row = db.get(YardJoinRequest, join_request_id)
    if row is None or row.yard_id != ctx.yard_id or row.kind != "join" or row.status != "pending":
        raise AppError("NOT_FOUND", "待审核的注册申请不存在。", http_status=404)
    return row


def approve_registration(
    db: Session, ctx: YardContext, join_request_id: str, *, request_id: str = ""
) -> tuple[RegistrationOut, YardMemberOut]:
    row = _get_pending_request(db, ctx, join_request_id)
    user = db.get(User, row.user_id)
    if user is None:
        raise AppError("NOT_FOUND", "申请账号不存在。", http_status=404)

    cfg = settings_service.get_setting(db, ctx.yard_id, "registration")
    role = _default_role(cfg)
    if role == "yard_admin" and actor_role(db, ctx) != "yard_owner":
        raise AppError("FORBIDDEN", "只有小院负责人可以批准管理员角色。", http_status=403)

    membership = get_membership_any(db, ctx.yard_id, user.id)
    if membership is not None and membership.status == "active":
        raise AppError("MEMBER_EXISTS", "该用户已是本小院成员。", http_status=409)
    if membership is None:
        membership = repo.add_membership(db, yard_id=ctx.yard_id, user_id=user.id, role=role)
    else:
        membership.role = role
        membership.status = "active"

    user.status = "active"
    row.status = "approved"
    row.reviewed_by = ctx.user_id
    row.reviewed_at = _now()

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="registration_approved",
        resource_type="yard_join_request",
        resource_id=row.id,
        result="success",
        request_id=request_id,
        metadata={"username": user.username, "role": role},
    )
    db.commit()
    return (
        _to_out(row, user),
        member_out(membership, user, full=True),
    )


def _has_other_active_membership(db: Session, user_id: str, yard_id: str) -> bool:
    """用户在其他小院是否仍是有效成员：是则不因本次拒绝而停用账号。"""
    row = db.scalar(
        select(YardMembership).where(
            YardMembership.user_id == user_id,
            YardMembership.yard_id != yard_id,
            YardMembership.status == "active",
        )
    )
    return row is not None


def reject_registration(
    db: Session,
    ctx: YardContext,
    join_request_id: str,
    *,
    reason: str | None = None,
    request_id: str = "",
) -> RegistrationOut:
    row = _get_pending_request(db, ctx, join_request_id)
    user = db.get(User, row.user_id)
    if user is None:
        raise AppError("NOT_FOUND", "申请账号不存在。", http_status=404)

    row.status = "rejected"
    row.reviewed_by = ctx.user_id
    row.reviewed_at = _now()
    row.reject_reason = reason or None
    if not _has_other_active_membership(db, user.id, ctx.yard_id):
        user.status = "disabled"
        user.token_version = int(user.token_version or 0) + 1

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="registration_rejected",
        resource_type="yard_join_request",
        resource_id=row.id,
        result="success",
        request_id=request_id,
        metadata={"username": user.username, "reason": row.reject_reason},
    )
    db.commit()
    return _to_out(row, user)


__all__ = [
    "REGISTRATION_STATUSES",
    "approve_registration",
    "list_registrations",
    "register",
    "reject_registration",
]
