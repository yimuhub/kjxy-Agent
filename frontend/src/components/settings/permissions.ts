/** 角色权限点的中文标签、受保护权限与分组兜底（系统设置 → 角色与权限）。 */
import type { PermissionGroup } from '@/api/v3'

/** 权限点 -> 中文文案（后端暂未下发 permission_labels 时使用） */
export const PERMISSION_LABELS: Record<string, string> = {
  'knowledge:read': '查看知识库',
  'knowledge:write': '维护知识库',
  'farm:read': '查看农业台账',
  'farm:write': '维护农业台账',
  'tasks:create': '发起任务',
  'tasks:read': '查看任务',
  'tasks:retry': '重试任务',
  'tasks:cancel': '取消任务',
  'feedback:write': '提交反馈',
  'feedback:read': '查看反馈',
  'review:approve': '审核内容',
  'skills:manage': '管理私有技能',
  'skills:generate': '一句话生成技能',
  'skills:review': '审核技能',
  'skills:publish_any': '推广技能为公共',
  'members:manage': '管理成员',
  'members:manage_limited': '管理普通成员',
  'members:reset_password': '重置成员密码',
  'members:approve': '审核注册',
  'roles:manage': '管理角色与权限',
  'settings:manage': '管理系统设置',
  'settings:manage_protected': '修改保护型设置',
  'search:manage': '管理联网搜索',
  'market:install': '安装市场内容',
  'market:review': '审核市场内容',
  'insights:read': '查看运营洞察',
  'insights:sensitive': '查看敏感数据',
  'finance:read': '查看财务数据',
  'audit:read': '查看审计日志',
  'admin:stats': '查看平台统计',
  'tools:execute': '执行工具',
  'tools:manage': '管理可用工具',
  'llm:manage': '管理模型配置',
  'mcp:manage': '管理 MCP 服务',
  'menu:manage': '管理工作台视图',
  'demo:manage': '管理演示数据',
  'chat:use': '使用智能对话',
  'agents:manage': '管理智能体与班组',
  'devices:read': '查看设备',
  'devices:write': '管理设备',
  'policies:read': '查看政策',
  'policies:write': '管理政策',
  'evolution:manage': '管理自我升级',
  'owner:transfer': '转让负责人',
  'members:manage_admins': '管理管理员',
}

/**
 * 保护型权限：管理员一律不可配置；负责人可在二次确认后写入角色，
 * 或由负责人逐人授权（见成员管理 → 权限）。
 */
export const PROTECTED_PERMISSIONS: string[] = [
  'finance:read',
  'insights:sensitive',
  'members:manage_admins',
  'owner:transfer',
  'settings:manage_protected',
  'skills:publish_any',
]

export const PROTECTED_PERMISSION_SET = new Set(PROTECTED_PERMISSIONS)

/** 后端未返回 permission_groups 时的兜底分组（与后端权限分组保持一致） */
export const FALLBACK_PERMISSION_GROUPS: PermissionGroup[] = [
  {
    key: 'members',
    label: '成员与注册',
    permissions: [
    'members:manage',
    'members:manage_limited',
    'members:reset_password',
    'members:approve',
  ],
 },
  {
    key: 'governance',
    label: '角色与设置',
    permissions: [
    'roles:manage',
    'settings:manage',
    'settings:manage_protected',
    'members:manage_admins',
    'owner:transfer',
    'demo:manage',
  ],
 },
  {
    key: 'farm',
    label: '农业台账',
    permissions: [
    'farm:read',
    'farm:write',
  ],
 },
  {
    key: 'content',
    label: '知识与审核',
    permissions: [
    'knowledge:read',
    'knowledge:write',
    'feedback:write',
    'feedback:read',
    'review:approve',
    'skills:manage',
    'skills:generate',
    'skills:review',
    'skills:publish_any',
    'evolution:manage',
  ],
 },
  {
    key: 'market',
    label: '市场与搜索',
    permissions: [
    'market:install',
    'market:review',
    'search:manage',
  ],
 },
  {
    key: 'insights',
    label: '数据与审计',
    permissions: [
    'insights:read',
    'insights:sensitive',
    'finance:read',
    'audit:read',
    'admin:stats',
  ],
 },
  {
    key: 'runtime',
    label: '任务与工具',
    permissions: [
    'tasks:create',
    'tasks:read',
    'tasks:retry',
    'tasks:cancel',
    'tools:execute',
    'tools:manage',
    'llm:manage',
    'mcp:manage',
    'menu:manage',
  ],
 },
  {
    key: 'capability',
    label: '对话与智能体',
    permissions: [
    'chat:use',
    'agents:manage',
  ],
 },
  {
    key: 'devices',
    label: '设备与政策',
    permissions: [
    'devices:read',
    'devices:write',
    'policies:read',
    'policies:write',
  ],
 },
]

/** 敏感授权的三项开关（负责人授权中心 / 驾驶舱授权中心共用） */
export interface GrantItem {
  key: string
  label: string
  hint: string
}

export const GRANT_ITEMS: GrantItem[] = [
  {
    key: 'insights:sensitive',
    label: '敏感数据',
    hint: '查看全小院会话正文与成员联系方式（查看正文仍需填写原因并留审计）',
  },
  {
    key: 'finance:read',
    label: '财务数据',
    hint: '查看 Token 成本等财务口径数据',
  },
  {
    key: 'audit:read',
    label: '审计日志',
    hint: '查看小院审计日志（敏感查看记录等）',
  },
]

/** 真离线（mode=offline 且确认无互联网）时的本地部署形态提示——仅限无网场景使用，不要在降级场景复用 */
export const OFFLINE_SEARCH_TIP =
  '当前设备为本地部署形态，不具备联网功能，联网插件或技能受限。请联网后使用，或前往「专家审核 → Skill 管理」进行本地 Skill 管理操作。'

/** 联网受限（degraded：仍有可用搜索源）时的提示——不得出现「不具备联网功能」等本地形态文案 */
export const DEGRADED_SEARCH_TIP = '部分联网能力暂不可用，已自动降级到可用搜索源'

/** 角色 code -> 默认中文名兜底 */
export const ROLE_LABELS: Record<string, string> = {
  platform_admin: '平台管理员',
  yard_owner: '小院负责人',
  yard_admin: '小院管理员',
  expert: '专家',
  operator: '科研操作员',
  viewer: '普通成员',
  service_worker: '服务账号',
}

/**
 * 角色默认权限点（与 backend/app/modules/yards/permissions.py 的 PERMISSIONS 对齐）。
 *
 * 只用于前端「入口显隐」：小院可以在系统设置里覆盖角色默认值、负责人还能逐人授权，
 * 所以前端判定永远只是体验优化，真正的拦截由后端按数据库角色 + 授权再次校验。
 */
const ADMIN_DEFAULT_PERMISSIONS: string[] = [
  'knowledge:read',
  'knowledge:write',
  'tasks:create',
  'tasks:read',
  'tasks:retry',
  'tasks:cancel',
  'feedback:write',
  'feedback:read',
  'review:approve',
  'skills:manage',
  'skills:generate',
  'skills:review',
  'members:manage',
  'members:manage_limited',
  'members:reset_password',
  'members:approve',
  'roles:manage',
  'settings:manage',
  'search:manage',
  'market:install',
  'market:review',
  'insights:read',
  'audit:read',
  'admin:stats',
  'tools:execute',
  'farm:read',
  'farm:write',
  'tools:manage',
  'llm:manage',
  'mcp:manage',
  'menu:manage',
  'demo:manage',
  'chat:use',
  'agents:manage',
  'devices:read',
  'devices:write',
  'policies:read',
  'policies:write',
  'evolution:manage',
]

export const ROLE_PERMISSIONS: Record<string, string[]> = {
  // 负责人/平台管理员实际上恒定拥有全集（含保护型权限），判定时直接短路
  yard_owner: [...ADMIN_DEFAULT_PERMISSIONS, ...PROTECTED_PERMISSIONS, 'skills:publish_any'],
  platform_admin: [...ADMIN_DEFAULT_PERMISSIONS, ...PROTECTED_PERMISSIONS, 'skills:publish_any'],
  yard_admin: ADMIN_DEFAULT_PERMISSIONS,
  expert: [
    'knowledge:read',
    'knowledge:write',
    'feedback:write',
    'feedback:read',
    'review:approve',
    'skills:manage',
    'skills:generate',
    'skills:review',
    'members:manage_limited',
    'tasks:create',
    'tasks:read',
    'market:install',
    'market:review',
    'insights:read',
    'tools:execute',
    'chat:use',
    'agents:manage',
    'devices:read',
    'policies:read',
  ],
  operator: [
    'knowledge:read',
    'tasks:create',
    'tasks:read',
    'tasks:retry',
    'feedback:write',
    'skills:generate',
    'tools:execute',
    'chat:use',
    'devices:read',
  ],
  viewer: [
    'knowledge:read',
    'tasks:read',
    'feedback:write',
    'skills:generate',
    'tools:execute',
    'chat:use',
  ],
  service_worker: ['tasks:create', 'tasks:read'],
}

/** 角色集合 -> 权限点集合（多角色取并集） */
export function permissionsOfRoles(roles: string[]): Set<string> {
  const granted = new Set<string>()
  for (const role of roles) {
    for (const permission of ROLE_PERMISSIONS[role] ?? []) granted.add(permission)
  }
  return granted
}

/**
 * 前端权限判定（只控制入口显隐，不替代后端校验）。
 *
 * - 角色为空视为「尚未加载」：不隐藏入口，避免把功能整体锁死；
 * - 出现未知角色（小院自定义角色）时同样放行：前端没有它的权限表，
 *   硬判会误伤，交由后端按库内配置裁决。
 */
export function hasPermission(roles: string[], permission: string): boolean {
  if (!roles.length) return true
  const knownRoles = roles.filter(role => role in ROLE_PERMISSIONS)
  if (knownRoles.length !== roles.length) return true
  return permissionsOfRoles(knownRoles).has(permission)
}
