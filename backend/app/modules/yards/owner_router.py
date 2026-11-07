"""小院负责人驾驶舱 / 敏感授权 / 负责人转让 API。

权限口径：
- 驾驶舱聚合需 ``insights:read``；成员联系方式与会话正文需 ``insights:sensitive``；
- 授权管理、负责人转让为 owner-only（``require_yard_permission`` 后再显式校验
  ``is_owner``，管理员即使被授予敏感权限也不能越权）；
- 查看会话正文必须填写原因，原因只写审计 metadata，不落日志。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.pagination import pagination_params
from app.core.responses import ok, paged
from app.modules.audit.models import AuditLog
from app.modules.audit.service import log_audit
from app.modules.auth.models import User
from app.modules.insights import service as insights_service
from app.modules.yards.authz import effective_permissions, has_permission, is_owner, member_grants
from app.modules.yards.models import Yard, YardMemberGrant, YardMembership
from app.modules.yards.permissions import GRANTABLE_PERMISSIONS

router = APIRouter(prefix="/yards/current/owner", tags=["owner"])

REASON_MIN = 2
REASON_MAX = 200


class GrantsUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[str] = Field(default_factory=list, max_length=len(GRANTABLE_PERMISSIONS))


class TransferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(min_length=1, max_length=36)


def _yard_id(user: CurrentUser) -> str:
    if not user.yard_id:  # require_yard_permission 已兜底，防御性判断
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


def _require_owner(db: Session, user: CurrentUser) -> str:
    yard_id = _yard_id(user)
    if not is_owner(db, yard_id, user.user_id):
        raise AppError("FORBIDDEN", "仅小院负责人可执行该操作。", http_status=403)
    return yard_id


def _target_admin(db: Session, yard_id: str, user_id: str) -> YardMembership:
    membership = db.scalar(
        select(YardMembership).where(
            YardMembership.yard_id == yard_id,
            YardMembership.user_id == user_id,
            YardMembership.status == "active",
            YardMembership.role == "yard_admin",
        )
    )
    if membership is None:
        raise AppError(
            "TARGET_NOT_YARD_ADMIN",
            "目标成员必须是本小院在职管理员。",
            http_status=400,
        )
    return membership


# ---------------------------------------------------------------------------
# 驾驶舱
# ---------------------------------------------------------------------------


@router.get("/overview")
def overview(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("insights:read")),
    db: Session = Depends(get_db_session),
):
    return ok(insights_service.overview(db, _yard_id(user)), request)


@router.get("/roi")
def roi(
    request: Request,
    days: int = Query(default=30, ge=1, le=90),
    user: CurrentUser = Depends(require_yard_permission("insights:read")),
    db: Session = Depends(get_db_session),
):
    """投入产出看板（v3.71）：效率/质量/成本/人时节省一页聚合。"""
    return ok(insights_service.roi_dashboard(db, _yard_id(user), days=days), request)


@router.get("/member-usage")
def member_usage(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    user: CurrentUser = Depends(require_yard_permission("insights:read")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    data = insights_service.member_usage(db, yard_id, days=days)
    sensitive = has_permission(
        db,
        yard_id=yard_id,
        user_id=user.user_id,
        jwt_roles=user.roles,
        permission="insights:sensitive",
    )
    if not sensitive:
        for item in data["items"]:
            item["email"] = insights_service.mask_email(item["email"])
            item["phone"] = insights_service.mask_phone(item["phone"])
    data["sensitive"] = sensitive
    return ok(data, request)


@router.get("/daily-usage")
def daily_usage(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    user: CurrentUser = Depends(require_yard_permission("insights:read")),
    db: Session = Depends(get_db_session),
):
    return ok(insights_service.daily_usage(db, _yard_id(user), days=days), request)


@router.get("/agent-usage")
def agent_usage(
    request: Request,
    days: int = Query(default=7, ge=1, le=90),
    user: CurrentUser = Depends(require_yard_permission("insights:read")),
    db: Session = Depends(get_db_session),
):
    return ok(insights_service.agent_usage(db, _yard_id(user), days=days), request)


@router.get("/sessions")
def list_sessions(
    request: Request,
    user_id: str | None = Query(default=None, max_length=36),
    keyword: str | None = Query(default=None, max_length=100),
    page_params: tuple[int, int] = Depends(pagination_params),
    user: CurrentUser = Depends(require_yard_permission("insights:sensitive")),
    db: Session = Depends(get_db_session),
):
    page, page_size = page_params
    items, total = insights_service.list_sessions(
        db,
        _yard_id(user),
        page=page,
        page_size=page_size,
        user_id=user_id,
        keyword=keyword,
    )
    return paged(items, page, page_size, total, request)


@router.get("/sessions/{session_id}/messages")
def session_messages(
    session_id: str,
    request: Request,
    reason: str | None = Query(default=None),
    user: CurrentUser = Depends(require_yard_permission("insights:sensitive")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    reason_text = (reason or "").strip()
    if not (REASON_MIN <= len(reason_text) <= REASON_MAX):
        raise AppError(
            "AUDIT_REASON_REQUIRED",
            f"查看会话正文必须填写 {REASON_MIN}-{REASON_MAX} 字原因。",
            http_status=400,
        )
    data = insights_service.session_messages(db, yard_id, session_id)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="sensitive_view",
        resource_type="chat_session",
        resource_id=session_id,
        result="success",
        request_id=user.request_id,
        metadata={
            "reason": reason_text,
            "session_id": session_id,
            "user_id": data["session"]["created_by"],
        },
    )
    db.commit()
    return ok(data, request)


# ---------------------------------------------------------------------------
# 敏感授权（owner only）
# ---------------------------------------------------------------------------


@router.get("/grants")
def list_grants(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("members:manage_admins")),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_owner(db, user)
    rows = db.execute(
        select(YardMembership, User)
        .join(User, User.id == YardMembership.user_id)
        .where(
            YardMembership.yard_id == yard_id,
            YardMembership.status == "active",
            YardMembership.role == "yard_admin",
        )
        .order_by(YardMembership.created_at)
    ).all()
    items: list[dict[str, Any]] = []
    for membership, target in rows:
        items.append(
            {
                "user_id": target.id,
                "username": target.username,
                "display_name": target.display_name or target.username,
                "role": membership.role,
                "grants": sorted(member_grants(db, yard_id, target.id)),
            }
        )
    return ok(
        {
            "items": items,
            "grantable": sorted(GRANTABLE_PERMISSIONS),
        },
        request,
    )


@router.put("/grants/{user_id}")
def update_grants(
    user_id: str,
    payload: GrantsUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("members:manage_admins")),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_owner(db, user)
    _target_admin(db, yard_id, user_id)

    requested = {str(grant) for grant in payload.grants}
    invalid = sorted(requested - GRANTABLE_PERMISSIONS)
    if invalid:
        raise AppError(
            "INVALID_GRANTS",
            "包含不可授予的权限点。",
            http_status=400,
            details={"invalid": invalid, "grantable": sorted(GRANTABLE_PERMISSIONS)},
        )
    grants = sorted(requested)

    row = db.scalar(
        select(YardMemberGrant).where(
            YardMemberGrant.yard_id == yard_id,
            YardMemberGrant.user_id == user_id,
        )
    )
    if row is None:
        db.add(
            YardMemberGrant(
                yard_id=yard_id,
                user_id=user_id,
                grants_json=grants,
                updated_by=user.user_id,
            )
        )
    else:
        row.grants_json = grants
        row.updated_by = user.user_id
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="member_grants_updated",
        resource_type="member_grant",
        resource_id=user_id,
        result="success",
        request_id=user.request_id,
        metadata={"target_user_id": user_id, "grants": grants},
    )
    db.commit()

    permissions = sorted(
        effective_permissions(db, yard_id=yard_id, user_id=user_id, jwt_roles=None)
    )
    return ok(
        {
            "user_id": user_id,
            "grants": grants,
            "effective_permissions": permissions,
        },
        request,
    )


# ---------------------------------------------------------------------------
# 负责人转让（owner only）
# ---------------------------------------------------------------------------


@router.post("/transfer")
def transfer_owner(
    payload: TransferRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("owner:transfer")),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_owner(db, user)
    target_id = payload.user_id
    if target_id == user.user_id:
        raise AppError(
            "OWNER_TRANSFER_INVALID",
            "不能将负责人转让给自己。",
            http_status=400,
        )
    _target_admin(db, yard_id, target_id)

    yard = db.get(Yard, yard_id)
    current_owner = user.user_id

    target_membership = db.scalar(
        select(YardMembership).where(
            YardMembership.yard_id == yard_id,
            YardMembership.user_id == target_id,
        )
    )
    old_membership = db.scalar(
        select(YardMembership).where(
            YardMembership.yard_id == yard_id,
            YardMembership.user_id == current_owner,
        )
    )
    if yard is not None:
        yard.owner_user_id = target_id
    if target_membership is not None:
        target_membership.role = "yard_owner"
        target_membership.status = "active"
    if old_membership is not None:
        old_membership.role = "yard_admin"
    else:
        db.add(
            YardMembership(
                yard_id=yard_id,
                user_id=current_owner,
                role="yard_admin",
                status="active",
            )
        )

    for owner_id in (target_id, current_owner):
        account = db.get(User, owner_id)
        if account is not None:
            # 角色互换后旧令牌立即失效，刷新后按新角色签发
            account.token_version = int(account.token_version or 0) + 1

    log_audit(
        db,
        yard_id=yard_id,
        actor_id=current_owner,
        action="owner_transferred",
        resource_type="yard",
        resource_id=yard_id,
        result="success",
        request_id=user.request_id,
        metadata={"from_user_id": current_owner, "to_user_id": target_id},
    )
    db.commit()
    return ok({"ok": True, "owner_user_id": target_id}, request)


# ---------------------------------------------------------------------------
# 最近审计（owner / audit:read）
# ---------------------------------------------------------------------------


@router.get("/audit")
def recent_audit(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    actor_id: str | None = Query(default=None, max_length=36),
    action: str | None = Query(default=None, max_length=100),
    user: CurrentUser = Depends(require_yard_permission("audit:read")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    conditions = [AuditLog.yard_id == yard_id]
    if actor_id:
        conditions.append(AuditLog.actor_id == actor_id)
    if action:
        conditions.append(AuditLog.action == action)
    rows = db.scalars(
        select(AuditLog).where(*conditions).order_by(AuditLog.created_at.desc()).limit(limit)
    ).all()
    items = [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "result": row.result,
            "request_id": row.request_id,
            "metadata": row.metadata_json,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    return ok({"items": items}, request)


__all__ = ["router"]
