/** 角色权限点的中文标签、受保护权限与分组兜底（系统设置 → 角色与权限）。 */
import type { PermissionGroup } from '@/api/v3'

/** 权限点 -> 中文文案（后端暂未下发 permission_labels 时使用） */
export const PERMISSION_LABELS: Record<string, string> = {
  'knowledge:read': '查看知识库',
  'knowledge:write': '维护知识库',
  'tasks:create': '创建任务',
  'tasks:read': '查看任务',
  'tasks:retry': '重试任务',
  'tasks:cancel': '取消任务',
  'feedback:write': '提交反馈',
  'feedback:read': '查看反馈',
  'review:approve': '审核内容',
  'skills:manage': '管理技能',
  'members:manage': '管理成员',
  'members:manage_limited': '管理普通成员',
  'members:reset_password': '重置成员密码',
  'members:approve': '审核注册',
  'roles:manage': '管理角色',
  'settings:manage': '修改系统设置',
  'settings:manage_protected': '修改保护设置',
  'search:manage': '管理联网搜索',
  'market:install': '安装市场内容',
  'market:review': '审核市场内容',
  'insights:read': '查看运营洞察',
  'insights:sensitive': '查看敏感数据',
  'finance:read': '查看财务数据',
  'audit:read': '查看审计日志',
  'admin:stats': '查看平台统计',
  'tools:execute': '执行工具',
  'owner:transfer': '转让负责人',
  'members:manage_admins': '管理管理员',
}

/**
 * 保护型权限：只能由代码按角色/负责人逐人授权发放，
 * 角色配置矩阵中一律置灰，不随角色保存提交。
 */
export const PROTECTED_PERMISSIONS: string[] = [
  'owner:transfer',
  'members:manage_admins',
  'settings:manage_protected',
  'insights:sensitive',
  'finance:read',
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
    ],
  },
  {
    key: 'market',
    label: '市场与搜索',
    permissions: ['market:install', 'market:review', 'search:manage'],
  },
  {
    key: 'insights',
    label: '数据与审计',
    permissions: ['insights:read', 'insights:sensitive', 'finance:read', 'audit:read'],
  },
  {
    key: 'runtime',
    label: '任务与工具',
    permissions: ['tasks:create', 'tasks:read', 'tasks:retry', 'tasks:cancel', 'tools:execute'],
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

/** 离线/受限时的统一提示文案 */
export const OFFLINE_SEARCH_TIP =
  '当前设备为本地部署形态，不具备联网功能，联网插件或技能受限。请联网后使用，或前往「专家审核 → Skill 管理」进行本地 Skill 管理操作。'

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
