import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
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
  { key: 'chat', path: '/chat', title: '智能对话', iconName: 'ChatDotRound' },
  { key: 'custom-agents', path: '/agents/custom', title: '我的智能体', iconName: 'UserFilled' },
  { key: 'teams', path: '/teams', title: '协作班组', iconName: 'Connection' },
  { key: 'agents', path: '/agents', title: '专业助手', iconName: 'Cpu' },
  { key: 'evolution', path: '/evolution', title: '自我升级', iconName: 'TrendCharts', roles: ADMIN_ROLES },
  { key: 'market', path: '/market', title: 'Agent 市场', iconName: 'Shop', roles: ADMIN_ROLES },
  { key: 'library', path: '/library', title: '插件与技能库', iconName: 'Shop', roles: LIBRARY_ROLES },
  { key: 'factory', path: '/agents/factory', title: '智能工厂', iconName: 'Operation', roles: ADMIN_ROLES },
  { key: 'knowledge', path: '/knowledge', title: '知识库', iconName: 'Files' },
  { key: 'farm', path: '/farm', title: '农业台账', iconName: 'Grid' },
  { key: 'diagnosis', path: '/diagnosis', title: '作物诊断', iconName: 'Reading' },
  { key: 'work-orders', path: '/work-orders', title: '告警与工单', iconName: 'Checked' },
  { key: 'review', path: '/review', title: '专家审核', iconName: 'Checked', roles: REVIEW_ROLES },
  { key: 'policies', path: '/policies', title: '政策服务', iconName: 'Document' },
  { key: 'devices', path: '/devices', title: '设备接入', iconName: 'Connection' },
  { key: 'tasks', path: '/tasks', title: '任务中心', iconName: 'List' },
  { key: 'bots', path: '/bots', title: 'IM 机器人', iconName: 'ChatDotRound', roles: ADMIN_ROLES },
  { key: 'quota', path: '/quota', title: 'Token 与用量', iconName: 'DataLine' },
  { key: 'members', path: '/members', title: '成员管理', iconName: 'User', roles: ADMIN_ROLES },
  { key: 'settings', path: '/settings', title: '系统设置', iconName: 'Setting', roles: ADMIN_ROLES, locked: true },
  {
    key: 'owner',
    path: '/owner',
    title: '负责人驾驶舱',
    iconName: 'DataLine',
    roles: ['yard_owner', 'platform_admin'],
  },
]

/** 默认标准布局（支持能力中心含“我的智能体”、知识中心、接入与任务、小院管理） */
export function buildDefaultMenuGroups(): DiyMenuGroup[] {
  return [
    {
      id: 'grp-workbench',
      label: '工作台',
      items: [
        { key: 'dashboard', path: '/', title: '概览', iconName: 'Odometer' },
        { key: 'chat', path: '/chat', title: '对话', iconName: 'ChatDotRound' },
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
            { key: 'custom-agents', path: '/agents/custom', title: '我的智能体', iconName: 'UserFilled' },
            { key: 'agents', path: '/agents', title: '专业助手', iconName: 'Cpu' },
            { key: 'teams', path: '/teams', title: '协作班组', iconName: 'Connection' },
            { key: 'market', path: '/market', title: 'Agent 市场', iconName: 'Shop', roles: ADMIN_ROLES },
            { key: 'library', path: '/library', title: '插件与技能库', iconName: 'Shop', roles: LIBRARY_ROLES },
            { key: 'factory', path: '/agents/factory', title: '智能工厂', iconName: 'Operation', roles: ADMIN_ROLES },
          ],
        },
        // 自我升级（/evolution）恢复为一级入口：藏在折叠菜单里时用户找不到
        { key: 'evolution', path: '/evolution', title: '自我升级', iconName: 'TrendCharts', roles: ADMIN_ROLES },
      ],
    },
    {
      id: 'grp-knowledge',
      label: '知识数据',
      items: [
        { key: 'knowledge', path: '/knowledge', title: '知识库', iconName: 'Files' },
        { key: 'farm', path: '/farm', title: '农业台账', iconName: 'Grid' },
        { key: 'diagnosis', path: '/diagnosis', title: '作物诊断', iconName: 'Reading' },
        { key: 'work-orders', path: '/work-orders', title: '告警与工单', iconName: 'Checked' },
        { key: 'tasks', path: '/tasks', title: '任务中心', iconName: 'List' },
        {
          key: 'data',
          title: '设备与政策',
          iconName: 'Connection',
          children: [
            { key: 'devices', path: '/devices', title: '设备接入', iconName: 'Connection' },
            { key: 'policies', path: '/policies', title: '政策服务', iconName: 'Document' },
          ],
        },
      ],
    },
    {
      id: 'grp-admin',
      label: '管理与统计',
      items: [
        { key: 'quota', path: '/quota', title: '用量额度', iconName: 'DataLine' },
        { key: 'review', path: '/review', title: '专家审核', iconName: 'Checked', roles: REVIEW_ROLES },
        {
          key: 'admin',
          title: '小院管理',
          iconName: 'Setting',
          roles: ADMIN_ROLES,
          children: [
            { key: 'owner', path: '/owner', title: '负责人驾驶舱', iconName: 'DataLine', roles: ['yard_owner', 'platform_admin'] },
            { key: 'members', path: '/members', title: '成员管理', iconName: 'User', roles: ADMIN_ROLES },
            { key: 'settings', path: '/settings', title: '系统设置', iconName: 'Setting', roles: ADMIN_ROLES, locked: true },
            { key: 'bots', path: '/bots', title: 'IM 机器人', iconName: 'ChatDotRound', roles: ADMIN_ROLES },
          ],
        },
      ],
    },
  ]
}

/** 预设模板 2：高频全一级直达模式（把我的智能体、知识库、班组等全部提到一级菜单） */
export function buildFlatQuickGroups(): DiyMenuGroup[] {
  return [
    {
      id: 'grp-quick',
      label: '高频直达',
      items: [
        { key: 'dashboard', path: '/', title: '概览', iconName: 'Odometer' },
        { key: 'chat', path: '/chat', title: '对话', iconName: 'ChatDotRound' },
        { key: 'custom-agents', path: '/agents/custom', title: '我的智能体', iconName: 'UserFilled' },
        { key: 'teams', path: '/teams', title: '协作班组', iconName: 'Connection' },
        { key: 'knowledge', path: '/knowledge', title: '知识库', iconName: 'Files' },
        { key: 'farm', path: '/farm', title: '农业台账', iconName: 'Grid' },
        { key: 'diagnosis', path: '/diagnosis', title: '作物诊断', iconName: 'Reading' },
        { key: 'work-orders', path: '/work-orders', title: '告警与工单', iconName: 'Checked' },
        { key: 'quota', path: '/quota', title: '用量额度', iconName: 'DataLine' },
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
            { key: 'agents', path: '/agents', title: '专业助手', iconName: 'Cpu' },
            { key: 'evolution', path: '/evolution', title: '自我升级', iconName: 'TrendCharts', roles: ADMIN_ROLES },
            { key: 'market', path: '/market', title: 'Agent 市场', iconName: 'Shop', roles: ADMIN_ROLES },
            { key: 'library', path: '/library', title: '插件与技能库', iconName: 'Shop', roles: LIBRARY_ROLES },
            { key: 'factory', path: '/agents/factory', title: '智能工厂', iconName: 'Operation', roles: ADMIN_ROLES },
          ],
        },
        {
          key: 'ops',
          title: '业务与系统',
          iconName: 'Setting',
          children: [
            { key: 'tasks', path: '/tasks', title: '任务中心', iconName: 'List' },
            { key: 'devices', path: '/devices', title: '设备接入', iconName: 'Connection' },
            { key: 'policies', path: '/policies', title: '政策服务', iconName: 'Document' },
            { key: 'review', path: '/review', title: '专家审核', iconName: 'Checked', roles: REVIEW_ROLES },
            { key: 'bots', path: '/bots', title: 'IM 机器人', iconName: 'ChatDotRound', roles: ADMIN_ROLES },
            { key: 'members', path: '/members', title: '成员管理', iconName: 'User', roles: ADMIN_ROLES },
            { key: 'settings', path: '/settings', title: '系统设置', iconName: 'Setting', roles: ADMIN_ROLES, locked: true },
          ],
        },
      ],
    },
  ]
}

// v4：恢复「自我升级」一级入口后再次提升版本号，旧布局（缺该入口且标题被钉死）一次性重建
const STORAGE_KEY_PREFIX = 'kjxy_menu_diy_v4_'

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

  const storageKey = computed(() => {
    const uid = auth.user?.id || auth.user?.username || 'default'
    const yid = auth.yard?.yard_id || 'global'
    return `${STORAGE_KEY_PREFIX}${uid}_${yid}`
  })

  function loadFromStorage() {
    try {
      const raw = localStorage.getItem(storageKey.value)
      if (!raw) {
        groups.value = buildDefaultMenuGroups()
        return
      }
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed) && parsed.length > 0) {
        groups.value = normalizeAndMergeGroups(parsed)
      } else {
        groups.value = buildDefaultMenuGroups()
      }
    } catch {
      groups.value = buildDefaultMenuGroups()
    }
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

  /** 尚未放入左侧菜单栏的功能备选池（按当前用户角色过滤） */
  const hiddenAtoms = computed<DiyMenuLeaf[]>(() =>
    ALL_MENU_ATOMS.filter(
      a => !activeLeafKeys.value.has(a.key) && auth.hasAnyRole(a.roles),
    ),
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
    loadFromStorage,
    saveToStorage,
    updateGroups,
    resetToDefault,
    applyQuickTemplate,
    promoteToTopLevel,
    demoteToSubmenu,
    moveItemInGroup,
    moveChildInFolder,
  }
})
