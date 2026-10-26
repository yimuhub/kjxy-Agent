"""小院权限判定：DB 角色覆盖 + 个人授权 + 保护规则。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.yards.models import (
    Yard,
    YardMemberGrant,
    YardMembership,
    YardRole,
)
from app.modules.yards.permissions import (
    ALL_PERMISSIONS,
    GRANTABLE_PERMISSIONS,
    PERMISSIONS,
    PROTECTED_PERMISSIONS,
    default_role_permissions,
)


def get_active_membership(db: Session, yard_id: str, user_id: str) -> YardMembership | None:
    return db.scalar(
        select(YardMembership).where(
            YardMembership.yard_id == yard_id,
            YardMembership.user_id == user_id,
            YardMembership.status == "active",
        )
    )


def is_owner(db: Session, yard_id: str, user_id: str) -> bool:
    yard = db.get(Yard, yard_id)
    return bool(yard and yard.owner_user_id == user_id)


def role_permissions(db: Session, yard_id: str, role: str) -> frozenset[str]:
    """角色权限：小院覆盖优先，否则系统默认；未知自定义角色为空。"""
    override = db.scalar(select(YardRole).where(YardRole.yard_id == yard_id, YardRole.code == role))
    if override is not None and isinstance(override.permissions_json, list):
        allowed = {str(p) for p in override.permissions_json}
        # 数据库里的角色配置也不能突破保护权限
        return frozenset(allowed & (ALL_PERMISSIONS - PROTECTED_PERMISSIONS))
    return default_role_permissions(role)


def member_grants(db: Session, yard_id: str, user_id: str) -> frozenset[str]:
    row = db.scalar(
        select(YardMemberGrant).where(
            YardMemberGrant.yard_id == yard_id, YardMemberGrant.user_id == user_id
        )
    )
    if row is None or not isinstance(row.grants_json, list):
        return frozenset()
    return frozenset({str(g) for g in row.grants_json} & GRANTABLE_PERMISSIONS)


def effective_permissions(
    db: Session,
    *,
    yard_id: str,
    user_id: str,
    jwt_roles: list[str] | None = None,
) -> frozenset[str]:
    """计算有效权限。

    - platform_admin：全权限（平台运维）；
    - 小院负责人：永远全权限（不可被角色配置削弱）；
    - 其余成员：角色权限 ∪ 负责人授予的敏感权限；
    - 查不到 membership 时回退 JWT 角色（兼容测试与在途令牌），但不授予保护权限。
    """
    roles = list(jwt_roles or [])
    if "platform_admin" in roles:
        return ALL_PERMISSIONS
    if is_owner(db, yard_id, user_id):
        return ALL_PERMISSIONS
    membership = get_active_membership(db, yard_id, user_id)
    if membership is None:
        fallback: set[str] = set()
        for role in roles:
            fallback |= set(default_role_permissions(role))
        return frozenset(fallback - PROTECTED_PERMISSIONS)
    perms = set(role_permissions(db, yard_id, membership.role))
    perms |= member_grants(db, yard_id, user_id)
    return frozenset(perms)


def has_permission(
    db: Session,
    *,
    yard_id: str,
    user_id: str,
    jwt_roles: list[str] | None,
    permission: str,
) -> bool:
    return permission in effective_permissions(
        db, yard_id=yard_id, user_id=user_id, jwt_roles=jwt_roles
    )


def can_manage_target_role(actor_role: str, target_role: str) -> bool:
    """角色层级：管理员不能动负责人/管理员；专家只能动普通成员与操作员。"""
    if target_role == "yard_owner":
        return False
    if actor_role == "yard_owner":
        return True
    if actor_role == "yard_admin":
        return target_role not in {"yard_admin"}
    if actor_role == "expert":
        return target_role in {"operator", "viewer"}
    return False


def sanitize_role_permissions(permissions: set[str], *, allow_protected: bool) -> frozenset[str]:
    allowed = set(permissions) & ALL_PERMISSIONS
    if not allow_protected:
        allowed -= PROTECTED_PERMISSIONS
    return frozenset(allowed)


__all__ = [
    "PERMISSIONS",
    "PROTECTED_PERMISSIONS",
    "can_manage_target_role",
    "effective_permissions",
    "get_active_membership",
    "has_permission",
    "is_owner",
    "member_grants",
    "role_permissions",
    "sanitize_role_permissions",
]
