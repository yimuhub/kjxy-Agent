"""成员邀请业务逻辑：一次性令牌邀请链接。

设计要点：
- 令牌只存 sha256 摘要，明文仅在创建响应中出现一次；库泄露也无法兑换。
- 状态机 pending → accepted | revoked；过期按 expires_at 计算（不改状态，保留审计事实）。
- 接受邀请可创建新账号，也可让已有账号加入新空间（需校验原密码，防止冒用）。
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.security import verify_password
from app.modules.audit.service import log_audit
from app.modules.auth import service as auth_service
from app.modules.auth.repository import create_user, get_by_username
from app.modules.yards import repository as repo
from app.modules.yards import settings_service
from app.modules.yards.models import YardInvitation
from app.modules.yards.schemas import InvitationOut, InvitationPreview
from app.modules.yards.service import YARD_ASSIGNABLE_ROLES
from app.modules.yards.signup import normalize_username, validate_password

INVITATION_STATUSES = ("pending", "accepted", "revoked")


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """SQLite 读回的时间不带 tzinfo，统一按 UTC 解释后再比较。"""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _display_status(inv: YardInvitation) -> str:
    if inv.status == "pending" and _as_utc(inv.expires_at) <= _now():
        return "expired"
    return inv.status


def _to_out(inv: YardInvitation, invite_path: str | None = None) -> InvitationOut:
    return InvitationOut(
        id=inv.id,
        role=inv.role,
        note=inv.note,
        status=_display_status(inv),
        expires_at=inv.expires_at,
        created_at=inv.created_at,
        created_by=inv.created_by,
        accepted_at=inv.accepted_at,
        invite_path=invite_path,
    )


def _validate_role(role: str) -> None:
    if role not in YARD_ASSIGNABLE_ROLES:
        raise AppError(
            "INVALID_REQUEST",
            f"角色不合法，可选：{', '.join(YARD_ASSIGNABLE_ROLES)}。",
            http_status=400,
        )


def create_invitation(
    db: Session,
    ctx: YardContext,
    *,
    role: str,
    expires_in_days: int = 7,
    note: str | None = None,
    request_id: str = "",
) -> tuple[InvitationOut, str]:
    _validate_role(role)
    token = secrets.token_urlsafe(32)
    inv = YardInvitation(
        yard_id=ctx.yard_id,
        token_hash=_hash_token(token),
        role=role,
        note=(note or None),
        status="pending",
        expires_at=_now() + timedelta(days=expires_in_days),
        created_by=ctx.user_id,
    )
    db.add(inv)
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="member_invited",
        resource_type="yard_invitation",
        resource_id=inv.id,
        result="success",
        request_id=request_id,
        metadata={"role": role, "expires_in_days": expires_in_days},
    )
    db.commit()
    return _to_out(inv, invite_path=f"/invite/{token}"), token


def list_invitations(db: Session, ctx: YardContext) -> list[InvitationOut]:
    rows = db.scalars(
        select(YardInvitation)
        .where(YardInvitation.yard_id == ctx.yard_id)
        .order_by(YardInvitation.created_at.desc())
    )
    return [_to_out(inv) for inv in rows]


def revoke_invitation(
    db: Session, ctx: YardContext, invitation_id: str, *, request_id: str = ""
) -> InvitationOut:
    inv = db.scalars(
        select(YardInvitation).where(
            YardInvitation.id == invitation_id,
            YardInvitation.yard_id == ctx.yard_id,
        )
    ).first()
    if inv is None:
        raise AppError("NOT_FOUND", "邀请不存在。", http_status=404)
    if inv.status != "pending":
        raise AppError("INVALID_REQUEST", "该邀请已接受或已撤销。", http_status=400)
    inv.status = "revoked"
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="member_invitation_revoked",
        resource_type="yard_invitation",
        resource_id=inv.id,
        result="success",
        request_id=request_id,
    )
    db.commit()
    return _to_out(inv)


def _get_pending(db: Session, token: str) -> YardInvitation:
    token = (token or "").strip()
    if not token:
        raise AppError("INVITATION_INVALID", "邀请链接无效或已过期。", http_status=404)
    inv = db.scalars(
        select(YardInvitation).where(YardInvitation.token_hash == _hash_token(token))
    ).first()
    if inv is None or inv.status != "pending" or _as_utc(inv.expires_at) <= _now():
        raise AppError("INVITATION_INVALID", "邀请链接无效或已过期。", http_status=404)
    return inv


def preview_invitation(db: Session, token: str) -> InvitationPreview:
    """公开预览：只暴露空间名、角色与有效期，供接受页渲染。"""
    inv = _get_pending(db, token)
    yard = repo.get_yard(db, inv.yard_id)
    if yard is None or yard.status != "active":
        raise AppError("INVITATION_INVALID", "邀请所属空间已停用。", http_status=404)
    return InvitationPreview(
        yard_id=inv.yard_id,
        yard_name=yard.name,
        role=inv.role,
        note=inv.note,
        expires_at=inv.expires_at,
    )


def accept_invitation(
    db: Session,
    *,
    token: str,
    username: str,
    password: str,
    display_name: str | None = None,
    request_id: str = "",
) -> dict:
    """接受邀请：创建或复用账号 → 建立成员关系 → 直接登录返回令牌。"""
    inv = _get_pending(db, token)
    yard = repo.get_yard(db, inv.yard_id)
    if yard is None or yard.status != "active":
        raise AppError("INVITATION_INVALID", "邀请所属空间已停用。", http_status=404)

    username = normalize_username(username)
    security = settings_service.get_setting(db, yard.id, "security")
    validate_password(password, min_len=int(security.get("password_min_length") or 0))

    user = get_by_username(db, username)
    if user is None:
        new_display_name = (display_name or "").strip() or username
    else:
        if user.status == "disabled":
            raise AppError("ACCOUNT_DISABLED", "账号已停用，请联系管理员。", http_status=403)
        if not verify_password(password, user.password_hash):
            # 账号名被占用时必须证明是本人，避免冒用既有账号加入空间；
            # 校验失败不消耗邀请令牌，对方可用正确密码重试。
            raise AppError(
                "INVALID_CREDENTIALS",
                "该账号已存在，请填写其原密码接受邀请；或换一个账号名。",
                http_status=401,
            )
        if repo.get_membership(db, inv.yard_id, user.id) is not None:
            raise AppError("MEMBER_EXISTS", "您已是该空间成员，请直接登录。", http_status=409)

    # 原子占用令牌：并发接受同一链接时只有一个请求能把 pending 改成 accepted。
    claimed_at = _now()
    claimed = cast(
        CursorResult[Any],
        db.execute(
            update(YardInvitation)
            .where(YardInvitation.id == inv.id, YardInvitation.status == "pending")
            .values(status="accepted", accepted_at=claimed_at)
        ),
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise AppError("INVITATION_INVALID", "邀请链接无效或已过期。", http_status=404)

    if user is None:
        user = create_user(
            db,
            username=username,
            password=password,
            display_name=new_display_name,
        )
    elif display_name and not user.display_name:
        user.display_name = display_name.strip()

    repo.add_membership(db, yard_id=inv.yard_id, user_id=user.id, role=inv.role)
    inv.status = "accepted"
    inv.accepted_user_id = user.id
    inv.accepted_at = claimed_at
    log_audit(
        db,
        yard_id=inv.yard_id,
        actor_id=user.id,
        action="member_invitation_accepted",
        resource_type="yard_invitation",
        resource_id=inv.id,
        result="success",
        request_id=request_id,
        metadata={"username": user.username, "role": inv.role},
    )
    db.commit()
    return auth_service.login(
        db, user.username, password, inv.yard_id, request_id=request_id
    ).model_dump(mode="json")
