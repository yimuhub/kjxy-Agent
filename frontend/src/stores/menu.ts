import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { menuApi, type MenuConfig } from '@/api/v3'
import { collectMenuKeys, surfaceMissingAtoms } from '@/utils/menuAtoms'
import { useAuthStore } from '@/stores/auth'

/**
 * 左侧菜单栏自由 DIY 配置 Store（支持一级直达/二级折叠自由编排、拖拽排序、
 * 分组管理、显隐控制、预设模板切换与一键恢复默认）。
 */

export interface DiyMenuLeaf {
  key: string
  title: string
  iconName: string
  path: string
  roles?: string[]
  /** 后端有效权限点：缺失即不渲染（千人千面） */
  permission?: string
  /** 系统设置不可完全隐藏，防止丢失配置入口 */
  locked?: boolean
}

export interface DiyMenuNode {
  key: string
  title: string
  iconName: string
  /** 若有 path 且无 children，则为一级直达菜单项；若有 children，则为二级折叠菜单夹 */
  path?: string
  roles?: string[]
  permission?: string
  locked?: boolean
  children?: DiyMenuLeaf[]
}

export interface DiyMenuGroup {
  id: string
  label: string
  items: DiyMenuNode[]
}

const ADMIN_ROLES = ['yard_owner', 'yard_admin', 'platform_admin']
const REVIEW_ROLES = ['yard_owner', 'yard_admin', 'platform_admin', 'expert']
/** 插件与技能库：可检索/导入/安装（市场安装权限） */
const LIBRARY_ROLES = ['yard_owner', 'yard_admin', 'platform_admin', 'expert']

/** 全系统所有可配置的功能原子池（唯一事实源） */
export const ALL_MENU_ATOMS: DiyMenuLeaf[] = [
  { key: 'dashboard', path: '/', title: '概览', iconName: 'Odometer' },
  { key: 'chat', path: '/chat', title: '智能对话', iconName: 'ChatDotRound', permission: 'chat:use' },
  { key: 'commander', path: '/commander', title: '指挥官工作台', iconName: 'Compass', permission: 'chat:use' },
  { key: 'custom-agents', path: '/agents/custom', title: '我的智能体', iconName: 'UserFilled', permission: 'chat:use' },
  { key: 'teams', path: '/teams', title: '协作班组', iconName: 'Connection', permission: 'chat:use' },
  { key: 'org', path: '/org', title: '组织架构', iconName: 'OfficeBuilding', permission: 'org:read' },
  { key: 'agents', path: '/agents', title: '专业助手', iconName: 'Cpu', permission: 'chat:use' },
  { key: 'evolution', path: '/evolution', title: '自我升级', iconName: 'TrendCharts', permission: 'evolution:manage', roles: ADMIN_ROLES },
  { key: 'market', path: '/market', title: 'Agent 市场', iconName: 'Shop', permission: 'market:install', roles: ADMIN_ROLES },
  { key: 'library', path: '/library', title: '插件与技能库', iconName: 'Shop', permission: 'market:install', roles: LIBRARY_ROLES },
  { key: 'factory', path: '/agents/factory', title: '智能工厂', iconName: 'Operation', permission: 'agents:manage', roles: ADMIN_ROLES },
  { key: 'knowledge', path: '/knowledge', title: '知识库', iconName: 'Files', permission: 'knowledge:read' },
  { key: 'farm', path: '/farm', title: '农业台账', iconName: 'Grid', permission: 'farm:read' },
  { key: 'diagnosis', path: '/diagnosis', title: '作物诊断', iconName: 'Reading', permission: 'farm:read' },
  { key: 'work-orders', path: '/work-orders', title: '告警与工单', iconName: 'Checked', permission: 'tasks:read' },
  { key: 'review', path: '/review', title: '专家审核', iconName: 'Checked', permission: 'review:approve', roles: REVIEW_ROLES },
  { key: 'policies', path: '/policies', title: '政策服务', iconName: 'Document', permission: 'policies:read' },
  { key: 'devices', path: '/devices', title: '设备接入', iconName: 'Connection', permission: 'devices:read' },
  { key: 'tasks', path: '/tasks', title: '任务中心', iconName: 'List', permission: 'tasks:read' },
  { key: 'bots', path: '/bots', title: 'IM 机器人', iconName: 'ChatDotRound', permission: 'settings:manage', roles: ADMIN_ROLES },
  { key: 'quota', path: '/quota', title: 'Token 与用量', iconName: 'DataLine', permission: 'insights:read' },
  { key: 'model-center', path: '/model-center', title: '模型中心', iconName: 'Cpu', permission: 'llm:manage', roles: ADMIN_ROLES },
  { key: 'members', path: '/members', title: '成员管理', iconName: 'User', permission: 'members:manage', roles: ADMIN_ROLES },
  { key: 'settings', path: '/settings', title: '系统设置', iconName: 'Setting', permission: 'settings:manage', roles: ADMIN_ROLES, locked: true },
  {
    key: 'owner',
    path: '/owner',
    title: '负责人驾驶舱',
    iconName: 'DataLine',
    roles: ['yard_owner', 'platform_admin'],
  },
]

const ATOM_BY_KEY = new Map(ALL_MENU_ATOMS.map(atom => [atom.key, atom]))

/** 给手写布局补齐权限点/角色（原子池是唯一事实源） */
function decorateGroups(groups: DiyMenuGroup[]): DiyMenuGroup[] {
  return groups.map(group => ({
    ...group,
    items: group.items.map(node => {
      const std = ATOM_BY_KEY.get(node.key)
      const next: DiyMenuNode = {
        ...node,
        permission: node.permission ?? std?.permission,
        roles: node.roles ?? std?.roles,
      }
      if (node.children) {
        next.children = node.children.map(child => {
          const childStd = ATOM_BY_KEY.get(child.key)
          return {
            ...child,
            permission: child.permission ?? childStd?.permission,
            roles: child.roles ?? childStd?.roles,
          }
        })
      }
      return next
    }),
  }))
}

/** 默认标准布局（支持能力中心含“我的智能体”、知识中心、接入与任务、小院管理） */
export function buildDefaultMenuGroups(): DiyMenuGroup[] {
  return decorateGroups(buildDefaultMenuGroupsRaw())
}

function buildDefaultMenuGroupsRaw(): DiyMenuGroup[] {
  return [
    {
      id: 'grp-workbench',
      label: '工作台',
      items: [
        { key: 'dashboard', path: '/', title: '概览', iconName: 'Odometer' },
        { key: 'chat', path: '/chat', title: '智能对话', iconName: 'ChatDotRound', permission: 'chat:use' },
        { key: 'commander', path: '/commander', title: '指挥官工作台', iconName: 'Compass', permission: 'chat:use' },
      ],
    },
    {
      id: 'grp-capability',
      label: '能力协作',
      items: [
        {
          key: 'capability',
          title: '能力中心',
          iconName: 'Cpu',
          children: [
            { key: 'custom-agents', path: '/agents/custom', title: '我的智能体', iconName: 'UserFilled', permission: 'chat:use' },
            { key: 'agents', path: '/agents', title: '专业助手', iconName: 'Cpu', permission: 'chat:use' },
            { key: 'teams', path: '/teams', title: '协作班组', iconName: 'Connection', permission: 'chat:use' },
            { key: 'org', path: '/org', title: '组织架构', iconName: 'OfficeBuilding', permission: 'org:read' },
            { key: 'market', path: '/market', title: 'Agent 市场', iconName: 'Shop', permission: 'market:install', roles: ADMIN_ROLES },
            { key: 'library', path: '/library', title: '插件与技能库', iconName: 'Shop', permission: 'market:install', roles: LIBRARY_ROLES },
            { key: 'factory', path: '/agents/factory', title: '智能工厂', iconName: 'Operation', permission: 'agents:manage', roles: ADMIN_ROLES },
          ],
        },
        // 自我升级（/evolution）恢复为一级入口：藏在折叠菜单里时用户找不到
        { key: 'evolution', path: '/evolution', title: '自我升级', iconName: 'TrendCharts', permission: 'evolution:manage', roles: ADMIN_ROLES },
      ],
    },
    {
      id: 'grp-knowledge',
      label: '知识数据',
      items: [
        { key: 'knowledge', path: '/knowledge', title: '知识库', iconName: 'Files', permission: 'knowledge:read' },
        { key: 'farm', path: '/farm', title: '农业台账', iconName: 'Grid', permission: 'farm:read' },
        { key: 'diagnosis', path: '/diagnosis', title: '作物诊断', iconName: 'Reading', permission: 'farm:read' },
        { key: 'work-orders', path: '/work-orders', title: '告警与工单', iconName: 'Checked', permission: 'tasks:read' },
        { key: 'tasks', path: '/tasks', title: '任务中心', iconName: 'List', permission: 'tasks:read' },
        {
          key: 'data',
          title: '设备与政策',
          iconName: 'Connection',
          children: [
            { key: 'devices', path: '/devices', title: '设备接入', iconName: 'Connection', permission: 'devices:read' },
            { key: 'policies', path: '/policies', title: '政策服务', iconName: 'Document', permission: 'policies:read' },
          ],
        },
      ],
    },
    {
      id: 'grp-admin',
      label: '管理与统计',
      items: [
        { key: 'quota', path: '/quota', title: '用量额度', iconName: 'DataLine', permission: 'insights:read' },
        { key: 'review', path: '/review', title: '专家审核', iconName: 'Checked', permission: 'review:approve', roles: REVIEW_ROLES },
        {
          key: 'admin',
          title: '小院管理',
          iconName: 'Setting',
          roles: ADMIN_ROLES,
          children: [
            { key: 'owner', path: '/owner', title: '负责人驾驶舱', iconName: 'DataLine', permission: 'insights:read', roles: ['yard_owner', 'platform_admin'] },
            { key: 'model-center', path: '/model-center', title: '模型中心', iconName: 'Cpu', permission: 'llm:manage', roles: ADMIN_ROLES },
            { key: 'members', path: '/members', title: '成员管理', iconName: 'User', permission: 'members:manage', roles: ADMIN_ROLES },
            { key: 'settings', path: '/settings', title: '系统设置', iconName: 'Setting', permission: 'settings:manage', roles: ADMIN_ROLES, locked: true },
            { key: 'bots', path: '/bots', title: 'IM 机器人', iconName: 'ChatDotRound', permission: 'settings:manage', roles: ADMIN_ROLES },
          ],
        },
      ],
    },
  ]
}

/** 预设模板 2：高频全一级直达模式（把我的智能体、知识库、班组等全部提到一级菜单） */
export function buildFlatQuickGroups(): DiyMenuGroup[] {
  return decorateGroups(buildFlatQuickGroupsRaw())
}

function buildFlatQuickGroupsRaw(): DiyMenuGroup[] {
  return [
    {
      id: 'grp-quick',
      label: '高频直达',
      items: [
        { key: 'dashboard', path: '/', title: '概览', iconName: 'Odometer' },
        { key: 'chat', path: '/chat', title: '智能对话', iconName: 'ChatDotRound', permission: 'chat:use' },
        { key: 'commander', path: '/commander', title: '指挥官工作台', iconName: 'Compass', permission: 'chat:use' },
        { key: 'custom-agents', path: '/agents/custom', title: '我的智能体', iconName: 'UserFilled', permission: 'chat:use' },
        { key: 'teams', path: '/teams', title: '协作班组', iconName: 'Connection', permission: 'chat:use' },
        { key: 'knowledge', path: '/knowledge', title: '知识库', iconName: 'Files', permission: 'knowledge:read' },
        { key: 'farm', path: '/farm', title: '农业台账', iconName: 'Grid', permission: 'farm:read' },
        { key: 'diagnosis', path: '/diagnosis', title: '作物诊断', iconName: 'Reading', permission: 'farm:read' },
        { key: 'work-orders', path: '/work-orders', title: '告警与工单', iconName: 'Checked', permission: 'tasks:read' },
        { key: 'quota', path: '/quota', title: '用量额度', iconName: 'DataLine', permission: 'insights:read' },
      ],
    },
    {
      id: 'grp-more',
      label: '更多模块',
      items: [
        {
          key: 'capability',
          title: '能力扩展',
          iconName: 'Cpu',
          children: [
            { key: 'agents', path: '/agents', title: '专业助手', iconName: 'Cpu', permission: 'chat:use' },
            { key: 'org', path: '/org', title: '组织架构', iconName: 'OfficeBuilding', permission: 'org:read' },
            { key: 'evolution', path: '/evolution', title: '自我升级', iconName: 'TrendCharts', permission: 'evolution:manage', roles: ADMIN_ROLES },
            { key: 'market', path: '/market', title: 'Agent 市场', iconName: 'Shop', permission: 'market:install', roles: ADMIN_ROLES },
            { key: 'library', path: '/library', title: '插件与技能库', iconName: 'Shop', permission: 'market:install', roles: LIBRARY_ROLES },
            { key: 'factory', path: '/agents/factory', title: '智能工厂', iconName: 'Operation', permission: 'agents:manage', roles: ADMIN_ROLES },
          ],
        },
        {
          key: 'ops',
          title: '业务与系统',
          iconName: 'Setting',
          children: [
            { key: 'tasks', path: '/tasks', title: '任务中心', iconName: 'List', permission: 'tasks:read' },
            { key: 'devices', path: '/devices', title: '设备接入', iconName: 'Connection', permission: 'devices:read' },
            { key: 'policies', path: '/policies', title: '政策服务', iconName: 'Document', permission: 'policies:read' },
            { key: 'review', path: '/review', title: '专家审核', iconName: 'Checked', permission: 'review:approve', roles: REVIEW_ROLES },
            { key: 'bots', path: '/bots', title: 'IM 机器人', iconName: 'ChatDotRound', permission: 'settings:manage', roles: ADMIN_ROLES },
            { key: 'members', path: '/members', title: '成员管理', iconName: 'User', permission: 'members:manage', roles: ADMIN_ROLES },
            { key: 'settings', path: '/settings', title: '系统设置', iconName: 'Setting', permission: 'settings:manage', roles: ADMIN_ROLES, locked: true },
          ],
        },
      ],
    },
  ]
}

// ---------------------------------------------------------------------------
// 工作台视图：三种模式 + 内置角色视图（v3.24）
// ---------------------------------------------------------------------------

export type MenuMode = 'minimal' | 'standard' | 'full'

export const MENU_MODE_LABELS: Record<MenuMode, string> = {
  minimal: '极简模式',
  standard: '标准模式',
  full: '全功能模式',
}

function leafOf(key: string): DiyMenuLeaf | null {
  const atom = ATOM_BY_KEY.get(key)
  if (!atom) return null
  return {
    key: atom.key,
    title: atom.title,
    iconName: atom.iconName,
    path: atom.path,
    permission: atom.permission,
    roles: atom.roles,
    locked: atom.locked,
  }
}

function groupsFromKeys(spec: Array<{ label: string; keys: string[] }>): DiyMenuGroup[] {
  return spec.map((group, index) => ({
    id: `grp-view-${index + 1}`,
    label: group.label,
    items: group.keys
      .map(leafOf)
      .filter((leaf): leaf is DiyMenuLeaf => leaf !== null)
      .map(({ key, title, iconName, path, permission, roles, locked }) => ({
        key,
        title,
        iconName,
        path,
        permission,
        roles,
        locked,
      })),
  }))
}

/** 极简：只保留最高频入口；管理入口仍在（无权限时自动隐藏） */
export function buildMinimalGroups(): DiyMenuGroup[] {
  return decorateGroups(
    groupsFromKeys([
      { label: '常用', keys: ['dashboard', 'chat', 'knowledge', 'tasks'] },
      { label: '管理', keys: ['members', 'settings'] },
    ]),
  )
}

/** 全功能：所有原子平铺到「全部功能」 */
export function buildFullGroups(): DiyMenuGroup[] {
  return decorateGroups(
    groupsFromKeys([{ label: '全部功能', keys: ALL_MENU_ATOMS.map(atom => atom.key) }]),
  )
}

/** 按角色给出的推荐视图（自定义角色回落到普通成员） */
export function buildRolePreset(role: string): DiyMenuGroup[] {
  if (role === 'yard_owner' || role === 'platform_admin') return buildFullGroups()
  if (role === 'yard_admin') return buildDefaultMenuGroups()
  if (role === 'expert') {
    return decorateGroups(
      groupsFromKeys([
        {
          label: '科研工作台',
          keys: [
            'dashboard',
            'chat',
            'custom-agents',
            'teams',
            'agents',
            'knowledge',
            'farm',
            'diagnosis',
            'work-orders',
            'review',
            'devices',
            'policies',
            'tasks',
            'quota',
          ],
        },
      ]),
    )
  }
  return decorateGroups(
    groupsFromKeys([
      {
        label: '我的工作台',
        keys: ['dashboard', 'chat', 'knowledge', 'farm', 'diagnosis', 'work-orders', 'devices', 'tasks'],
      },
    ]),
  )
}

export function buildModeGroups(mode: MenuMode): DiyMenuGroup[] {
  if (mode === 'minimal') return buildMinimalGroups()
  if (mode === 'full') return buildFullGroups()
  return buildDefaultMenuGroups()
}

export interface MenuPresetMeta {
  id: string
  name: string
  description: string
  mode: MenuMode
}

/** 内置视图模板（侧栏 DIY 与系统设置共用） */
export const MENU_PRESETS: MenuPresetMeta[] = [
  {
    id: 'standard',
    name: '标准视图',
    description: '工作台 / 能力协作 / 知识数据 / 管理与统计四组',
    mode: 'standard',
  },
  {
    id: 'flat',
    name: '高频直达',
    description: '常用功能全部提到一级，少一层折叠',
    mode: 'standard',
  },
  {
    id: 'minimal',
    name: '极简视图',
    description: '只留概览、对话、知识库、任务与管理入口',
    mode: 'minimal',
  },
  {
    id: 'full',
    name: '全功能视图',
    description: '所有功能平铺展示，适合负责人巡检',
    mode: 'full',
  },
  {
    id: 'role-expert',
    name: '专家视图',
    description: '科研台账、诊断、审核、设备与政策',
    mode: 'standard',
  },
  {
    id: 'role-member',
    name: '成员视图',
    description: '对话、知识、台账、任务与设备',
    mode: 'minimal',
  },
  {
    id: 'role-admin',
    name: '管理员视图',
    description: '标准视图 + 成员/设置/模型治理入口',
    mode: 'standard',
  },
]

export function buildPresetGroups(id: string): DiyMenuGroup[] | null {
  if (id === 'standard') return buildDefaultMenuGroups()
  if (id === 'flat') return buildFlatQuickGroups()
  if (id === 'minimal') return buildMinimalGroups()
  if (id === 'full') return buildFullGroups()
  if (id === 'role-expert') return buildRolePreset('expert')
  if (id === 'role-member') return buildRolePreset('viewer')
  if (id === 'role-admin') return buildRolePreset('yard_admin')
  return null
}

// v4：恢复「自我升级」一级入口后再次提升版本号，旧布局（缺该入口且标题被钉死）一次性重建
const STORAGE_KEY_PREFIX = 'kjxy_menu_diy_v4_'
/** 已补齐过的功能入口：用户再次隐藏后不再重复补 */
const SEEN_KEY_PREFIX = 'kjxy_menu_surfaced_v1_'

function cloneGroups(groups: DiyMenuGroup[]): DiyMenuGroup[] {
  return JSON.parse(JSON.stringify(groups)) as DiyMenuGroup[]
}

/** 确保系统新增的关键模块（如 custom-agents）在旧缓存中未出现时自动合并进来，且同步最新权限定义 */
function normalizeAndMergeGroups(raw: DiyMenuGroup[]): DiyMenuGroup[] {
  const atomByKey = new Map(ALL_MENU_ATOMS.map(a => [a.key, a]))
  const groups = cloneGroups(raw)

  for (const g of groups) {
    for (const node of g.items) {
      if (node.children?.length) {
        for (const child of node.children) {
          const std = atomByKey.get(child.key)
          if (std) {
            // 标题/图标一并同步：菜单改名（如「自我升级」）要能穿透旧缓存布局
            child.title = std.title
            child.iconName = std.iconName
            child.path = std.path
            child.roles = std.roles
            child.permission = std.permission
            child.locked = std.locked
          }
        }
      } else {
        const std = atomByKey.get(node.key)
        if (std) {
          node.title = std.title
          node.iconName = std.iconName
          node.path = std.path
          node.roles = std.roles
          node.permission = std.permission
          node.locked = std.locked
        }
      }
    }
  }
  return groups
}

export const useMenuStore = defineStore('menu-diy', () => {
  const auth = useAuthStore()
  const groups = ref<DiyMenuGroup[]>(buildDefaultMenuGroups())
  const sidebarEditMode = ref(false)
  const currentMode = ref<MenuMode>('standard')
  /** 后端下发的工作台视图默认值（默认模式/角色默认/共享预设） */
  const serverConfig = ref<MenuConfig | null>(null)
  const serverLoaded = ref(false)

  const storageKey = computed(() => {
    const uid = auth.user?.id || auth.user?.username || 'default'
    const yid = auth.yard?.yard_id || 'global'
    return `${STORAGE_KEY_PREFIX}${uid}_${yid}`
  })

  /** 当前角色在服务端配置中的默认模式（自定义角色回落到全局默认） */
  function serverModeForRole(): MenuMode {
    const role = auth.roles[0] ?? ''
    return serverConfig.value?.role_modes?.[role] ?? serverConfig.value?.default_mode ?? 'standard'
  }

  function seenKey(): string {
    return `${SEEN_KEY_PREFIX}${storageKey.value}`
  }

  /**
   * 老缓存布局里缺失的新功能入口补一次（指挥官工作台等）；
   * 补齐过的 key 记进 seen 标记，用户之后主动隐藏就不再弹回来。
   */
  function surfaceNewAtoms(cached: DiyMenuGroup[]): DiyMenuGroup[] {
    let seen: string[] = []
    try {
      const rawSeen = localStorage.getItem(seenKey())
      seen = rawSeen ? (JSON.parse(rawSeen) as string[]) : []
    } catch {
      seen = []
    }
    const result = surfaceMissingAtoms<DiyMenuGroup, DiyMenuLeaf>(
      cached,
      ALL_MENU_ATOMS,
      buildDefaultMenuGroups(),
      seen,
    )
    writeSeen(result.seen)
    return result.groups
  }

  /** 记下当前布局里已有的入口，作为「新功能补齐」的判断基线 */
  function writeSeen(keys: string[]): void {
    try {
      localStorage.setItem(seenKey(), JSON.stringify(keys))
    } catch {
      /* 存储不可用时本次补齐仍生效，下次启动会再补一次 */
    }
  }

  async function loadServerConfig(): Promise<void> {
    try {
      serverConfig.value = await menuApi.get()
    } catch {
      /* 后端未升级/离线时回落到本地默认，不阻断登录 */
    } finally {
      serverLoaded.value = true
    }
  }

  function loadFromStorage() {
    try {
      const raw = localStorage.getItem(storageKey.value)
      if (!raw) {
        // 本地没有自定义布局：跟随负责人/管理员设置的角色默认视图
        const mode = serverModeForRole()
        groups.value = buildModeGroups(mode)
        currentMode.value = mode
        // 记下基线，之后版本新增的入口才能被识别为「新功能」并补回
        writeSeen(collectMenuKeys(groups.value))
        return
      }
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed) && parsed.length > 0) {
        groups.value = surfaceNewAtoms(normalizeAndMergeGroups(parsed))
      } else {
        groups.value = buildDefaultMenuGroups()
      }
    } catch {
      groups.value = buildDefaultMenuGroups()
    }
  }

  /** 登录后加载：先取服务端默认，再应用本地自定义（本地优先） */
  async function loadFromServer(): Promise<void> {
    await loadServerConfig()
    loadFromStorage()
  }

  function saveToStorage() {
    try {
      localStorage.setItem(storageKey.value, JSON.stringify(groups.value))
    } catch {
      /* localStorage 不可用时保持内存态 */
    }
  }

  /** 当前已放入菜单树中的所有叶子 key 集合 */
  const activeLeafKeys = computed<Set<string>>(() => {
    const set = new Set<string>()
    for (const g of groups.value) {
      for (const node of g.items) {
        if (node.children?.length) {
          for (const c of node.children) set.add(c.key)
        } else if (node.path) {
          set.add(node.key)
        }
      }
    }
    return set
  })

  /**
   * 尚未放入左侧菜单栏的功能备选池。
   * v3.24：展示全系统所有用户组的所有功能（不按当前角色裁剪），
   * 是否真正可见由权限在渲染时裁决；负责人可据此为其他用户组编排。
   */
  const hiddenAtoms = computed<DiyMenuLeaf[]>(() =>
    ALL_MENU_ATOMS.filter(atom => !activeLeafKeys.value.has(atom.key)),
  )

  function updateGroups(next: DiyMenuGroup[]) {
    groups.value = cloneGroups(next)
    saveToStorage()
  }

  function resetToDefault() {
    groups.value = buildDefaultMenuGroups()
    saveToStorage()
  }

  function applyQuickTemplate() {
    groups.value = buildFlatQuickGroups()
    saveToStorage()
  }

  /** 切换工作台视图模式（极简/标准/全功能） */
  function applyMode(mode: MenuMode, persist = true) {
    groups.value = buildModeGroups(mode)
    currentMode.value = mode
    if (persist) saveToStorage()
  }

  /** 应用内置视图模板（含专家/成员/管理员角色视图） */
  function applyPreset(id: string, persist = true) {
    const next = buildPresetGroups(id)
    if (!next) return
    groups.value = next
    if (persist) saveToStorage()
  }

  /** 将某个二级子菜单项直接提升为所在分组的一级直达菜单 */
  function promoteToTopLevel(groupId: string, parentKey: string, leafKey: string) {
    const g = groups.value.find(x => x.id === groupId)
    if (!g) return
    const pIdx = g.items.findIndex(x => x.key === parentKey && x.children?.length)
    if (pIdx < 0) return
    const parent = g.items[pIdx]
    const cIdx = parent.children!.findIndex(c => c.key === leafKey)
    if (cIdx < 0) return
    const [leaf] = parent.children!.splice(cIdx, 1)
    g.items.splice(pIdx, 0, {
      key: leaf.key,
      title: leaf.title,
      iconName: leaf.iconName,
      path: leaf.path,
      roles: leaf.roles,
      permission: leaf.permission,
      locked: leaf.locked,
    })
    if (parent.children!.length === 0) {
      g.items.splice(pIdx + 1, 1)
    }
    saveToStorage()
  }

  /** 将某个一级直达菜单收纳进同组或首个可用的二级菜单夹 */
  function demoteToSubmenu(groupId: string, nodeKey: string, targetFolderKey?: string) {
    const g = groups.value.find(x => x.id === groupId)
    if (!g) return
    const nIdx = g.items.findIndex(x => x.key === nodeKey && !x.children?.length)
    if (nIdx < 0) return
    const node = g.items[nIdx]
    if (!node.path) return

    let folder = targetFolderKey
      ? g.items.find(x => x.key === targetFolderKey && Array.isArray(x.children))
      : g.items.find(x => Array.isArray(x.children))

    if (!folder) {
      // 查找全局首个二级菜单夹，若无则在当前组新建一个
      for (const grp of groups.value) {
        const hit = grp.items.find(x => Array.isArray(x.children))
        if (hit) {
          folder = hit
          break
        }
      }
    }
    if (!folder) {
      folder = {
        key: `folder-${Date.now()}`,
        title: '收纳菜单',
        iconName: 'Menu',
        children: [],
      }
      g.items.push(folder)
    }
    g.items.splice(nIdx, 1)
    folder.children = folder.children || []
    folder.children.push({
      key: node.key,
      title: node.title,
      iconName: node.iconName,
      path: node.path,
      roles: node.roles,
      permission: node.permission,
      locked: node.locked,
    })
    saveToStorage()
  }

  /** 在一级菜单内上下移动或拖拽调序 */
  function moveItemInGroup(groupId: string, fromIdx: number, toIdx: number) {
    const g = groups.value.find(x => x.id === groupId)
    if (!g) return
    if (fromIdx < 0 || toIdx < 0 || fromIdx >= g.items.length || toIdx >= g.items.length) return
    const [moved] = g.items.splice(fromIdx, 1)
    g.items.splice(toIdx, 0, moved)
    saveToStorage()
  }

  /** 在二级菜单内上下移动或拖拽调序 */
  function moveChildInFolder(groupId: string, folderKey: string, fromIdx: number, toIdx: number) {
    const g = groups.value.find(x => x.id === groupId)
    if (!g) return
    const folder = g.items.find(x => x.key === folderKey && x.children?.length)
    if (!folder || !folder.children) return
    if (
      fromIdx < 0 ||
      toIdx < 0 ||
      fromIdx >= folder.children.length ||
      toIdx >= folder.children.length
    )
      return
    const [moved] = folder.children.splice(fromIdx, 1)
    folder.children.splice(toIdx, 0, moved)
    saveToStorage()
  }

  return {
    groups,
    sidebarEditMode,
    activeLeafKeys,
    hiddenAtoms,
    currentMode,
    serverConfig,
    serverLoaded,
    loadFromStorage,
    loadFromServer,
    saveToStorage,
    updateGroups,
    resetToDefault,
    applyQuickTemplate,
    applyMode,
    applyPreset,
    promoteToTopLevel,
    demoteToSubmenu,
    moveItemInGroup,
    moveChildInFolder,
  }
})
