"""系统设置与角色管理路由（owner/admin）。

说明：本 router 由主程序统一挂载到 /api/v1（本模块不修改全局路由）。
- GET/PUT /yards/current/settings[/{key}]
- GET/PUT/POST/DELETE /yards/current/roles[/{code}]
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    get_current_user,
    get_db_session,
    require_yard_permission,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.yards import roles_service, settings_service
from app.modules.yards.authz import has_permission, is_owner
from app.modules.yards.models import YardRole
from app.modules.yards.permissions import (
    ALL_PERMISSIONS,
    ASSIGNABLE_ROLES,
    PERMISSION_GROUPS,
    PERMISSIONS,
    PROTECTED_PERMISSIONS,
    ROLE_LABELS,
)
from app.modules.yards.schemas import RoleCreateRequest, RoleUpdateRequest

router = APIRouter()

#: 设置项 → 所需权限（注册/安全/技能生成为普通设置，搜索为搜索管理）
SETTINGS_PERMISSIONS: dict[str, str] = {
    "registration": "settings:manage",
    "security": "settings:manage",
    "search": "search:manage",
    "skill_lab": "settings:manage",
}


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _yard_id(user: CurrentUser) -> str:
    if not user.yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


def _is_owner(db: Session, user: CurrentUser) -> bool:
    if "platform_admin" in user.roles:
        return True
    return is_owner(db, _yard_id(user), user.user_id)


def _require(db: Session, user: CurrentUser, permission: str) -> None:
    if not has_permission(
        db,
        yard_id=_yard_id(user),
        user_id=user.user_id,
        jwt_roles=user.roles,
        permission=permission,
    ):
        raise AppError(
            "FORBIDDEN",
            f"缺少 {permission} 权限，无法执行该操作。",
            http_status=403,
        )


def _roles_payload(db: Session, yard_id: str) -> dict[str, Any]:
    return {
        "roles": roles_service.list_roles(db, yard_id),
        "permission_groups": list(PERMISSION_GROUPS),
        "role_labels": ROLE_LABELS,
        "assignable_roles": list(ASSIGNABLE_ROLES),
    }


@router.get("/yards/current/settings")
def get_settings(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("settings:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    data = settings_service.public_view(db, yard_id)
    return ok({**data, **_roles_payload(db, yard_id)}, request)


@router.put("/yards/current/settings/{key}")
def update_setting(
    key: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    permission = SETTINGS_PERMISSIONS.get(key)
    if permission is None:
        raise AppError("INVALID_REQUEST", f"未知设置项：{key}", http_status=400)
    _require(db, user, permission)
    yard_id = _yard_id(user)
    if (
        key == "registration"
        and payload.get("default_role") == "yard_admin"
        and not _is_owner(db, user)
    ):
        raise AppError("FORBIDDEN", "只有小院负责人可以设置管理员为默认角色。", http_status=403)

    settings_service.update_setting(
        db,
        yard_id=yard_id,
        key=key,
        value=payload,
        actor_id=user.user_id,
        request_id=_request_id(request),
    )
    db.commit()
    return ok(settings_service.public_view(db, yard_id), request)


@router.get("/yards/current/roles")
def list_roles(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("roles:manage")),
    db: Session = Depends(get_db_session),
):
    return ok(_roles_payload(db, _yard_id(user)), request)


def _validate_permissions(permissions: list[str], *, actor_is_owner: bool) -> set[str]:
    requested = {str(p) for p in permissions}
    unknown = requested - ALL_PERMISSIONS
    if unknown:
        raise AppError(
            "INVALID_REQUEST",
            f"未知权限点：{', '.join(sorted(unknown))}",
            http_status=400,
        )
    if not actor_is_owner and requested & PROTECTED_PERMISSIONS:
        raise AppError("FORBIDDEN", "保护型权限只能由小院负责人配置。", http_status=403)
    return requested - PROTECTED_PERMISSIONS


def _update_custom_role(
    db: Session,
    row: YardRole,
    req: RoleUpdateRequest,
    *,
    actor_id: str,
    actor_is_owner: bool,
    request_id: str,
) -> dict[str, Any]:
    if req.name is None and req.permissions is None:
        raise AppError("INVALID_REQUEST", "没有需要更新的字段。", http_status=400)
    if req.name is not None:
        clean = req.name.strip()
        if not clean or len(clean) > 64:
            raise AppError("INVALID_REQUEST", "角色名称不能为空且不超过 64 字。", http_status=400)
        row.name = clean
    if req.permissions is not None:
        row.permissions_json = sorted(
            _validate_permissions(req.permissions, actor_is_owner=actor_is_owner)
        )
    log_audit(
        db,
        yard_id=row.yard_id,
        actor_id=actor_id,
        action="role_updated",
        resource_type="yard_role",
        resource_id=row.code,
        result="success",
        request_id=request_id,
        metadata={"name": row.name, "permissions": row.permissions_json},
    )
    db.flush()
    return {
        "code": row.code,
        "name": row.name,
        "permissions": sorted(str(p) for p in row.permissions_json),
        "is_system": False,
        "is_protected": False,
    }


@router.put("/yards/current/roles/{code}")
def update_role(
    code: str,
    req: RoleUpdateRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("roles:manage")),
    db: Session = Depends(get_db_session),
):
    yard_id = _yard_id(user)
    owner = _is_owner(db, user)
    row = db.scalar(select(YardRole).where(YardRole.yard_id == yard_id, YardRole.code == code))
    if row is not None and not row.is_system:
        data = _update_custom_role(
            db,
            row,
            req,
            actor_id=user.user_id,
            actor_is_owner=owner,
            request_id=_request_id(request),
        )
    else:
        if code not in PERMISSIONS:
            raise AppError("ROLE_NOT_FOUND", "角色不存在。", http_status=404)
        if not owner:
            raise AppError("FORBIDDEN", "只有小院负责人可以修改系统角色。", http_status=403)
        data = roles_service.update_role(
            db,
            yard_id=yard_id,
            code=code,
            name=req.name,
            permissions=req.permissions,
            actor_id=user.user_id,
            request_id=_request_id(request),
        )
    db.commit()
    return ok(data, request)


@router.post("/yards/current/roles", status_code=201)
def create_role(
    req: RoleCreateRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("roles:manage")),
    db: Session = Depends(get_db_session),
):
    permissions = _validate_permissions(req.permissions, actor_is_owner=_is_owner(db, user))
    data = roles_service.create_custom_role(
        db,
        yard_id=_yard_id(user),
        code=req.code,
        name=req.name,
        permissions=sorted(permissions),
        actor_id=user.user_id,
        request_id=_request_id(request),
    )
    db.commit()
    return ok(data, request)


@router.delete("/yards/current/roles/{code}")
def delete_role(
    code: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("roles:manage")),
    db: Session = Depends(get_db_session),
):
    roles_service.delete_custom_role(
        db,
        yard_id=_yard_id(user),
        code=code,
        actor_id=user.user_id,
        request_id=_request_id(request),
    )
    db.commit()
    return ok({"code": code, "deleted": True}, request)
