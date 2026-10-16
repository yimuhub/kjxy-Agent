"""角色-权限映射。

权限点（P0 全集）：
knowledge:read/write、tasks:create/read/retry/cancel、feedback:write/read、
review:approve、skills:manage、members:manage、audit:read、admin:stats、tools:execute。

- platform_admin：拥有全部权限（含跨租户平台能力）。
- tenant_admin：拥有全部租户级权限。
- 其余角色按最小权限授予。
"""

from collections.abc import Iterable

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "knowledge:read",
        "knowledge:write",
        "tasks:create",
        "tasks:read",
        "tasks:retry",
        "tasks:cancel",
        "feedback:write",
        "feedback:read",
        "review:approve",
        "skills:manage",
        "members:manage",
        "audit:read",
        "admin:stats",
        "tools:execute",
    }
)

PERMISSIONS: dict[str, frozenset[str]] = {
    "platform_admin": ALL_PERMISSIONS,
    "tenant_admin": ALL_PERMISSIONS,
    "expert": frozenset(
        {
            "knowledge:read",
            "feedback:write",
            "feedback:read",
            "review:approve",
            "tasks:create",
            "tasks:read",
        }
    ),
    "operator": frozenset(
        {
            "knowledge:read",
            "tasks:create",
            "tasks:read",
            "tasks:retry",
            "feedback:write",
        }
    ),
    "viewer": frozenset({"knowledge:read", "tasks:read"}),
    "service_worker": frozenset({"tasks:create", "tasks:read"}),
}


def has_permission(roles: Iterable[str], permission: str) -> bool:
    """任一角色拥有该权限即返回 True；未知角色视为无权限。"""
    return any(permission in PERMISSIONS.get(role, frozenset()) for role in roles)
