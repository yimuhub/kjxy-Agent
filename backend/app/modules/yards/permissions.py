"""角色-权限映射（唯一权限源）。

权限点只增不改；角色默认值可以被小院覆盖（``yard_roles``），但保护规则由代码硬约束：
- 小院负责人始终拥有全部权限，不能被削弱、不能被移除/停用，只能转让；
- 只有负责人能管理管理员、转让负责人、修改保护型权限；
- 保护型权限的增减（角色或逐人授权）必须由负责人二次确认；
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
        "quota:manage",
        "quota:request",
        "quota:approve",
        "tools:execute",
        "tools:manage",
        "llm:manage",
        "mcp:manage",
        "menu:manage",
        "demo:manage",
        "chat:use",
        "agents:manage",
        "org:read",
        "org:manage",
        "runtime:read",
        "runtime:use",
        "workspace:read",
        "workspace:write",
        "workspace:manage",
        "devices:read",
        "devices:write",
        "policies:read",
        "policies:write",
        "evolution:manage",
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

#: 负责人可以逐人授予的权限：除「转让负责人 / 管理管理员」外的全部权限（v3.24 扩展）。
#: 管理管理员涉及层级越权，只能通过负责人转让/调整角色产生，不能逐人下放。
GRANTABLE_PERMISSIONS: frozenset[str] = ALL_PERMISSIONS - {
    "owner:transfer",
    "members:manage_admins",
}

#: 权限点中文名（前端「角色与权限」矩阵直接使用，不再各自维护一份）
PERMISSION_LABELS: dict[str, str] = {
    "knowledge:read": "查看知识库",
    "knowledge:write": "维护知识库",
    "farm:read": "查看农业台账",
    "farm:write": "维护农业台账",
    "tasks:create": "发起任务",
    "tasks:read": "查看任务",
    "tasks:retry": "重试任务",
    "tasks:cancel": "取消任务",
    "feedback:write": "提交反馈",
    "feedback:read": "查看反馈",
    "review:approve": "审核内容",
    "skills:manage": "管理私有技能",
    "skills:generate": "一句话生成技能",
    "skills:review": "审核技能",
    "skills:publish_any": "推广技能为公共",
    "members:manage": "管理成员",
    "members:manage_limited": "管理普通成员",
    "members:reset_password": "重置成员密码",
    "members:approve": "审核注册",
    "roles:manage": "管理角色与权限",
    "settings:manage": "管理系统设置",
    "settings:manage_protected": "修改保护型设置",
    "search:manage": "管理联网搜索",
    "market:install": "安装市场内容",
    "market:review": "审核市场内容",
    "insights:read": "查看运营洞察",
    "insights:sensitive": "查看敏感数据",
    "finance:read": "查看财务数据",
    "audit:read": "查看审计日志",
    "admin:stats": "查看平台统计",
    "quota:manage": "调整用量额度",
    "quota:request": "申请提额",
    "quota:approve": "审批提额申请",
    "tools:execute": "执行工具",
    "tools:manage": "管理可用工具",
    "llm:manage": "管理模型配置",
    "mcp:manage": "管理 MCP 服务",
    "menu:manage": "管理工作台视图",
    "demo:manage": "管理演示数据",
    "chat:use": "使用智能对话",
    "agents:manage": "管理智能体与班组",
    "org:read": "查看组织架构",
    "org:manage": "维护组织架构",
    "runtime:read": "查看本机能力",
    "runtime:use": "使用本机能力",
    "workspace:read": "查看工作区",
    "workspace:write": "修改工作区文件",
    "workspace:manage": "管理工作区",
    "devices:read": "查看设备",
    "devices:write": "管理设备",
    "policies:read": "查看政策",
    "policies:write": "管理政策",
    "evolution:manage": "管理自我升级",
    "owner:transfer": "转让负责人",
    "members:manage_admins": "管理管理员",
}

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
            "quota:request",
            "tools:execute",
            "chat:use",
            "agents:manage",
            "org:read",
            "org:manage",
            "runtime:read",
            "workspace:read",
            "workspace:write",
            "devices:read",
            "policies:read",
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
            "quota:request",
            "tools:execute",
            "chat:use",
            "runtime:read",
            "devices:read",
            "org:read",
        }
    ),
    "viewer": frozenset(
        {
            "knowledge:read",
            "farm:read",
            "tasks:read",
            "feedback:write",
            "skills:generate",
            "quota:request",
            "tools:execute",
            "chat:use",
            "org:read",
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
            "demo:manage",
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
            "feedback:write",
            "feedback:read",
            "review:approve",
            "skills:manage",
            "skills:generate",
            "skills:review",
            "skills:publish_any",
            "evolution:manage",
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
        "permissions": [
            "insights:read",
            "insights:sensitive",
            "finance:read",
            "audit:read",
            "admin:stats",
            "quota:manage",
            "quota:request",
            "quota:approve",
        ],
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
            "tools:manage",
            "llm:manage",
            "mcp:manage",
            "menu:manage",
        ],
    },
    {
        "key": "capability",
        "label": "对话与智能体",
        "permissions": ["chat:use", "agents:manage", "org:read", "org:manage"],
    },
    {
        "key": "local_runtime",
        "label": "本机能力与工作区",
        "permissions": [
            "runtime:read",
            "runtime:use",
            "workspace:read",
            "workspace:write",
            "workspace:manage",
        ],
    },
    {
        "key": "devices",
        "label": "设备与政策",
        "permissions": ["devices:read", "devices:write", "policies:read", "policies:write"],
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
    "PERMISSION_LABELS",
    "PROTECTED_PERMISSIONS",
    "ROLE_LABELS",
    "default_role_permissions",
    "has_permission",
    "is_known_role",
]
