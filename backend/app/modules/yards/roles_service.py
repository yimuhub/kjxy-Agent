"""小院角色定义：系统角色覆盖 + 自定义角色。"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.yards.models import YardMembership, YardRole
from app.modules.yards.permissions import (
    ALL_PERMISSIONS,
    PERMISSIONS,
    PROTECTED_PERMISSIONS,
    ROLE_LABELS,
    default_role_permissions,
)

_SYSTEM_ROLES = ("yard_owner", "yard_admin", "expert", "operator", "viewer")
_MAX_CUSTOM = 20
_CODE_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")


def _out(code: str, name: str, perms: set[str], *, is_system: bool, is_protected: bool) -> dict:
    return {
        "code": code,
        "name": name,
        "permissions": sorted(perms),
        "is_system": is_system,
        "is_protected": is_protected,
    }


def list_roles(db: Session, yard_id: str) -> list[dict[str, Any]]:
    rows = {
        r.code: r for r in db.scalars(select(YardRole).where(YardRole.yard_id == yard_id)).all()
    }
    items: list[dict[str, Any]] = []
    for code in _SYSTEM_ROLES:
        row = rows.get(code)
        name = row.name if row is not None else ROLE_LABELS.get(code, code)
        perms = (
            {str(p) for p in row.permissions_json}
            if row is not None and isinstance(row.permissions_json, list)
            else set(default_role_permissions(code))
        )
        # 负责人永远全权限，UI 只读展示
        if code == "yard_owner":
            perms = set(ALL_PERMISSIONS)
        items.append(_out(code, name, perms, is_system=True, is_protected=code == "yard_owner"))
    for code, row in rows.items():
        if code in _SYSTEM_ROLES:
            continue
        perms = (
            {str(p) for p in row.permissions_json}
            if isinstance(row.permissions_json, list)
            else set()
        )
        items.append(_out(code, row.name, perms, is_system=False, is_protected=False))
    return items


def update_role(
    db: Session,
    *,
    yard_id: str,
    code: str,
    name: str | None,
    permissions: list[str] | None,
    actor_id: str,
    request_id: str,
) -> dict[str, Any]:
    if code not in PERMISSIONS:
        raise AppError("ROLE_NOT_FOUND", "角色不存在。", http_status=404)
    row = db.scalar(select(YardRole).where(YardRole.yard_id == yard_id, YardRole.code == code))
    if row is None:
        row = YardRole(
            yard_id=yard_id,
            code=code,
            name=ROLE_LABELS.get(code, code),
            permissions_json=sorted(default_role_permissions(code)),
            is_system=True,
            created_by=actor_id,
        )
        db.add(row)
    if name is not None:
        clean = name.strip()
        if not clean or len(clean) > 64:
            raise AppError("INVALID_REQUEST", "角色名称不能为空且不超过 64 字。", http_status=400)
        row.name = clean
    if permissions is not None:
        if code == "yard_owner":
            raise AppError("FORBIDDEN", "负责人权限不可修改。", http_status=403)
        requested = {str(p) for p in permissions}
        unknown = requested - ALL_PERMISSIONS
        if unknown:
            raise AppError(
                "INVALID_REQUEST",
                f"未知权限点：{', '.join(sorted(unknown))}",
                http_status=400,
            )
        # 系统管理员角色也不能通过配置获得保护权限
        requested -= PROTECTED_PERMISSIONS
        row.permissions_json = sorted(requested)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="role_updated",
        resource_type="yard_role",
        resource_id=code,
        result="success",
        request_id=request_id,
        metadata={"name": row.name, "permissions": row.permissions_json},
    )
    db.flush()
    return _out(
        row.code,
        row.name,
        {str(p) for p in row.permissions_json},
        is_system=True,
        is_protected=code == "yard_owner",
    )


def create_custom_role(
    db: Session,
    *,
    yard_id: str,
    code: str,
    name: str,
    permissions: list[str],
    actor_id: str,
    request_id: str,
) -> dict[str, Any]:
    clean = (code or "").strip().lower()
    if not _CODE_RE.match(clean):
        raise AppError(
            "INVALID_REQUEST",
            "角色编码需为 3–32 位小写字母/数字/下划线，且以字母开头。",
            http_status=400,
        )
    if clean in PERMISSIONS:
        raise AppError("ROLE_EXISTS", "系统角色编码不可复用。", http_status=409)
    count = len(
        db.scalars(
            select(YardRole).where(YardRole.yard_id == yard_id, YardRole.is_system.is_(False))
        ).all()
    )
    if count >= _MAX_CUSTOM:
        raise AppError("ROLE_LIMIT", f"自定义角色最多 {_MAX_CUSTOM} 个。", http_status=400)
    if db.scalar(select(YardRole).where(YardRole.yard_id == yard_id, YardRole.code == clean)):
        raise AppError("ROLE_EXISTS", "角色编码已存在。", http_status=409)
    requested = ({str(p) for p in permissions} & ALL_PERMISSIONS) - PROTECTED_PERMISSIONS
    row = YardRole(
        yard_id=yard_id,
        code=clean,
        name=(name or "").strip()[:64] or clean,
        permissions_json=sorted(requested),
        is_system=False,
        created_by=actor_id,
    )
    db.add(row)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="role_created",
        resource_type="yard_role",
        resource_id=clean,
        result="success",
        request_id=request_id,
        metadata={"permissions": sorted(requested)},
    )
    db.flush()
    return _out(clean, row.name, requested, is_system=False, is_protected=False)


def delete_custom_role(
    db: Session, *, yard_id: str, code: str, actor_id: str, request_id: str
) -> None:
    row = db.scalar(select(YardRole).where(YardRole.yard_id == yard_id, YardRole.code == code))
    if row is None or row.is_system:
        raise AppError("ROLE_NOT_FOUND", "自定义角色不存在。", http_status=404)
    in_use = db.scalar(
        select(YardMembership).where(YardMembership.yard_id == yard_id, YardMembership.role == code)
    )
    if in_use is not None:
        raise AppError("ROLE_IN_USE", "仍有成员使用该角色，不能删除。", http_status=409)
    db.delete(row)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="role_deleted",
        resource_type="yard_role",
        resource_id=code,
        result="success",
        request_id=request_id,
    )
    db.flush()


__all__ = [
    "create_custom_role",
    "delete_custom_role",
    "list_roles",
    "update_role",
]
