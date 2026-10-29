"""成员级权限总览与逐人授权（v3.24）。

- GET  查看某成员的「角色权限 ∪ 逐人授权 → 有效权限」；
- PUT  负责人为该成员增删逐人授权（保护型权限新增需二次确认）。

授权是角色默认值之上的增量，不会削弱角色权限；负责人始终拥有全集。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.auth.models import User
from app.modules.yards.authz import (
    effective_permissions,
    get_active_membership,
    is_owner,
    member_grants,
    role_permissions,
)
from app.modules.yards.models import YardMemberGrant, YardRole
from app.modules.yards.permissions import (
    GRANTABLE_PERMISSIONS,
    PERMISSION_GROUPS,
    PERMISSION_LABELS,
    PROTECTED_PERMISSIONS,
    ROLE_LABELS,
)

router = APIRouter(tags=["yard-permissions"])


class MemberPermissionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[str] = Field(default_factory=list, max_length=len(GRANTABLE_PERMISSIONS))
    confirm_protected: bool = False


def _yard_id(user: CurrentUser) -> str:
    if not user.yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


def _target(db: Session, yard_id: str, user_id: str) -> tuple[User, Any]:
    user = db.get(User, user_id)
    if user is None:
        raise AppError("NOT_FOUND", "成员账号不存在。", http_status=404)
    membership = get_active_membership(db, yard_id, user_id)
    if membership is None:
        raise AppError("NOT_FOUND", "该成员不在当前小院或已停用。", http_status=404)
    return user, membership


def _role_name(db: Session, yard_id: str, code: str) -> str:
    row = db.scalar(select(YardRole).where(YardRole.yard_id == yard_id, YardRole.code == code))
    if row is not None and row.name:
        return row.name
    return ROLE_LABELS.get(code, code)


def _payload(db: Session, yard_id: str, user: User, membership: Any) -> dict[str, Any]:
    role = str(membership.role)
    grants = member_grants(db, yard_id, user.id)
    effective = effective_permissions(db, yard_id=yard_id, user_id=user.id, jwt_roles=[role])
    return {
        "user_id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": role,
        "role_name": _role_name(db, yard_id, role),
        "role_permissions": sorted(role_permissions(db, yard_id, role)),
        "grants": sorted(grants),
        "effective_permissions": sorted(effective),
        "is_owner": is_owner(db, yard_id, user.id),
        "permission_groups": list(PERMISSION_GROUPS),
        "permission_labels": PERMISSION_LABELS,
        "protected_permissions": sorted(PROTECTED_PERMISSIONS),
        "grantable_permissions": sorted(GRANTABLE_PERMISSIONS),
    }


@router.get("/yards/current/members/{user_id}/permissions")
def get_member_permissions(
    user_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("members:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    target, membership = _target(db, yard_id, user_id)
    return ok(_payload(db, yard_id, target, membership), request)


@router.put("/yards/current/members/{user_id}/permissions")
def update_member_permissions(
    user_id: str,
    req: MemberPermissionsRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("members:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    allowed = "platform_admin" in user.roles or is_owner(db, yard_id, user.user_id)
    if not allowed:
        raise AppError("FORBIDDEN", "只有小院负责人可以逐人授权。", http_status=403)
    target, membership = _target(db, yard_id, user_id)
    if is_owner(db, yard_id, user_id) or membership.role == "yard_owner":
        raise AppError("INVALID_REQUEST", "负责人始终拥有全部权限，无需逐人授权。", http_status=400)

    requested = {str(grant) for grant in req.grants}
    unknown = requested - GRANTABLE_PERMISSIONS
    if unknown:
        raise AppError(
            "INVALID_REQUEST",
            f"不可授予的权限点：{', '.join(sorted(unknown))}",
            http_status=400,
        )
    current = set(member_grants(db, yard_id, user_id))
    added_protected = (requested - current) & PROTECTED_PERMISSIONS
    if added_protected and not req.confirm_protected:
        raise AppError(
            "PROTECTED_CONFIRM_REQUIRED",
            "新增保护型权限需要二次确认。",
            http_status=400,
        )

    grants = sorted(requested)
    row = db.scalar(
        select(YardMemberGrant).where(
            YardMemberGrant.yard_id == yard_id, YardMemberGrant.user_id == user_id
        )
    )
    if row is None:
        row = YardMemberGrant(
            yard_id=yard_id, user_id=user_id, grants_json=grants, updated_by=user.user_id
        )
        db.add(row)
    else:
        row.grants_json = grants
        row.updated_by = user.user_id
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="member_permissions_updated",
        resource_type="yard_member",
        resource_id=user_id,
        result="success",
        request_id=getattr(request.state, "request_id", "req_unknown"),
        metadata={
            "target_user_id": user_id,
            "added": sorted(requested - current),
            "removed": sorted(current - requested),
            "protected": sorted(added_protected),
        },
    )
    db.commit()
    return ok(_payload(db, yard_id, target, membership), request)


__all__ = ["router"]
