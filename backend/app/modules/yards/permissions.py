"""角色-权限映射（唯一权限源）。

权限点只增不改；角色默认值可以被小院覆盖（``yard_roles``），但保护规则由代码硬约束：
- 小院负责人始终拥有全部权限，不能被削弱、不能被移除/停用，只能转让；
- 只有负责人能管理管理员、转让负责人、修改保护型设置；
- 管理员对敏感数据（会话正文/用量明细/财务）默认不可见，需负责人逐人授权。
"""

from collections.abc import Iterable

# ---------------------------------------------------------------------------
# 权限点
# ---------------------------------------------------------------------------

ALL_PERMISSIONS: frozenset[str] = frozenset(
    {
        "knowledge:read",
        "knowledge:write",
        "farm:read",
        "farm:write",
        "tasks:create",
        "tasks:read",
        "tasks:retry",
        "tasks:cancel",
        "feedback:write",
        "feedback:read",
        "review:approve",
        "skills:manage",
        "skills:generate",
        "skills:review",
        "skills:publish_any",
        "members:manage",
        "members:manage_limited",
        "members:reset_password",
        "members:approve",
        "roles:manage",
        "settings:manage",
        "settings:manage_protected",
        "search:manage",
        "market:install",
        "market:review",
        "insights:read",
        "insights:sensitive",
        "finance:read",
        "audit:read",
        "admin:stats",
        "tools:execute",
        "owner:transfer",
        "members:manage_admins",
    }
)

#: 任何角色配置都不能授予的保护型权限（只由代码按角色/授权发放）
PROTECTED_PERMISSIONS: frozenset[str] = frozenset(
    {
        "owner:transfer",
        "members:manage_admins",
        "settings:manage_protected",
        "insights:sensitive",
        "finance:read",
        # 私有技能转公共是负责人特权，只能逐人下放，不能写进角色默认值
        "skills:publish_any",
    }
)

#: 负责人可以逐人授予管理员的敏感权限
GRANTABLE_PERMISSIONS: frozenset[str] = frozenset(
    {
        "insights:sensitive",
        "finance:read",
        "audit:read",
        "skills:publish_any",
        # v3.4：自我总结提升设置涉及成员数据归纳，负责人可逐人下放给管理员
        "settings:manage_protected",
    }
)

#: 角色默认权限。yard_owner 在判定时始终补齐全集（此处保留可读默认值）。
_ADMIN_DEFAULTS = ALL_PERMISSIONS - {
    "owner:transfer",
    "members:manage_admins",
    "settings:manage_protected",
    "insights:sensitive",
    "finance:read",
    "skills:publish_any",
}

PERMISSIONS: dict[str, frozenset[str]] = {
    "platform_admin": ALL_PERMISSIONS,
    "yard_owner": ALL_PERMISSIONS,
    "yard_admin": _ADMIN_DEFAULTS,
    "expert": frozenset(
        {
            "knowledge:read",
            "knowledge:write",
            "farm:read",
            "farm:write",
            "feedback:write",
            "feedback:read",
            "review:approve",
            "skills:manage",
            "skills:generate",
            "skills:review",
            "members:manage_limited",
            "tasks:create",
            "tasks:read",
            "market:install",
            "market:review",
            "insights:read",
            "tools:execute",
        }
    ),
    "operator": frozenset(
        {
            "knowledge:read",
            "farm:read",
            "farm:write",
            "tasks:create",
            "tasks:read",
            "tasks:retry",
            "feedback:write",
            "skills:generate",
            "tools:execute",
        }
    ),
    "viewer": frozenset(
        {
            "knowledge:read",
            "farm:read",
            "tasks:read",
            "feedback:write",
            "skills:generate",
            "tools:execute",
        }
    ),
    "service_worker": frozenset({"tasks:create", "tasks:read"}),
}

#: 系统角色显示名（可被小院设置覆盖）
ROLE_LABELS: dict[str, str] = {
    "platform_admin": "平台管理员",
    "yard_owner": "小院负责人",
    "yard_admin": "小院管理员",
    "expert": "专家",
    "operator": "科研操作员",
    "viewer": "普通成员",
    "service_worker": "服务账号",
}

#: 可分配给成员的常规角色（负责人只能转让产生）
ASSIGNABLE_ROLES: tuple[str, ...] = ("yard_admin", "expert", "operator", "viewer")

#: 权限分组（系统设置 UI 用）
PERMISSION_GROUPS: tuple[dict[str, object], ...] = (
    {
        "key": "members",
        "label": "成员与注册",
        "permissions": [
            "members:manage",
            "members:manage_limited",
            "members:reset_password",
            "members:approve",
        ],
    },
    {
        "key": "governance",
        "label": "角色与设置",
        "permissions": [
            "roles:manage",
            "settings:manage",
            "settings:manage_protected",
            "members:manage_admins",
            "owner:transfer",
        ],
    },
    {
        "key": "farm",
        "label": "农业台账",
        "permissions": ["farm:read", "farm:write"],
    },
    {
        "key": "content",
        "label": "知识与审核",
        "permissions": [
            "knowledge:read",
            "knowledge:write",
            "farm:read",
            "farm:write",
            "feedback:write",
            "feedback:read",
            "review:approve",
            "skills:manage",
            "skills:generate",
            "skills:review",
            "skills:publish_any",
        ],
    },
    {
        "key": "market",
        "label": "市场与搜索",
        "permissions": ["market:install", "market:review", "search:manage"],
    },
    {
        "key": "insights",
        "label": "数据与审计",
        "permissions": ["insights:read", "insights:sensitive", "finance:read", "audit:read"],
    },
    {
        "key": "runtime",
        "label": "任务与工具",
        "permissions": [
            "tasks:create",
            "tasks:read",
            "tasks:retry",
            "tasks:cancel",
            "tools:execute",
        ],
    },
)


def has_permission(roles: Iterable[str], permission: str) -> bool:
    """静态角色判定（JWT 兜底 / 无 DB 上下文时使用）。"""
    return any(permission in PERMISSIONS.get(role, frozenset()) for role in roles)


def default_role_permissions(role: str) -> frozenset[str]:
    return PERMISSIONS.get(role, frozenset())


def is_known_role(role: str) -> bool:
    return role in PERMISSIONS


__all__ = [
    "ALL_PERMISSIONS",
    "ASSIGNABLE_ROLES",
    "GRANTABLE_PERMISSIONS",
    "PERMISSIONS",
    "PERMISSION_GROUPS",
    "PROTECTED_PERMISSIONS",
    "ROLE_LABELS",
    "default_role_permissions",
    "has_permission",
    "is_known_role",
]
