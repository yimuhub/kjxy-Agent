import { ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi, yardApi } from '@/api/modules'
import { REFRESH_TOKEN_KEY, TOKEN_KEY } from '@/api/client'
import type { HubBrief, LoginResponse, Yard, YardBrief, User } from '@/types/api'

/** 登录/me 的 YardBrief 与 /yards/current 的 YardOut 归一为同一形状 */
function toYard(brief: YardBrief): Yard {
  return { yard_id: brief.yard_id, name: brief.yard_name, roles: brief.roles }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) ?? '')
  const user = ref<User | null>(null)
  /** 初始化向导状态：null=未查；false=未初始化（强制 /setup） */
  const setupChecked = ref(false)
  const setupInitialized = ref(true)

  /** 查询系统是否已完成初始化（仅一次；/setup/status 匿名） */
  async function ensureSetupStatus(): Promise<boolean> {
    if (setupChecked.value) return setupInitialized.value
    try {
      const { setupApi } = await import('@/api/setup')
      setupInitialized.value = (await setupApi.status()).initialized
    } catch {
      setupInitialized.value = true  // 后端不可达时按已初始化，避免全站卡向导
    }
    setupChecked.value = true
    return setupInitialized.value
  }

  /**
   * 初始化向导完成后接管登录态（v3.72 修复）：/setup/initialize 返回的
   * access_token 直接写入本机，并立刻把"已初始化"标记置真，避免路由守卫
   * 用缓存的 false 把用户再次弹回 /setup。
   * 该接口不下发 refresh_token，故清掉旧刷新令牌，过期后走正常登录即可。
   */
  function adoptSetupSession(accessToken: string) {
    token.value = accessToken
    localStorage.setItem(TOKEN_KEY, accessToken)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    setupInitialized.value = true
    setupChecked.value = true
  }
  const yard = ref<Yard | null>(null)
  const yards = ref<YardBrief[]>([])
  /** 当前用户角色码（yard_admin / expert / operator / viewer / platform_admin） */
  const roles = ref<string[]>([])
  /** 当前用户所属/可见的枢纽（v3.66；纯枢纽账号没有 yard 但有 hub） */
  const hubs = ref<HubBrief[]>([])
  /** 当前小院有效权限（后端 /auth/me 下发，角色默认 ∪ 逐人授权） */
  const permissions = ref<string[]>([])
  const isOwner = ref(false)
  /** 权限是否已从后端加载（加载前不隐藏入口，避免整站闪空） */
  const permissionsLoaded = ref(false)
  /** /auth/me 是否已加载完成（用于区分"未知"和"确实无小院"） */
  const meLoaded = ref(false)

  // 以本机持久化令牌为准：401 拦截器只清 localStorage、不动这个 ref，
  // 单看 ref 会在凭据失效后仍报「已登录」，把直接访问 /login 的用户弹离登录框、卡在空白报错页。
  const isLoggedIn = () => !!token.value && !!localStorage.getItem(TOKEN_KEY)

  function persist(newToken: string, refreshToken: string) {
    token.value = newToken
    localStorage.setItem(TOKEN_KEY, newToken)
    localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
  }

  function clear() {
    token.value = ''
    user.value = null
    yard.value = null
    yards.value = []
    roles.value = []
    hubs.value = []
    permissions.value = []
    isOwner.value = false
    permissionsLoaded.value = false
    meLoaded.value = false
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
  }

  /**
   * 角色判定：
   * - required 为空 -> 公开路由，放行；
   * - 平台管理员 -> 全放行；
   * - 角色信息缺失（后端未返回，meLoaded 后仍为空）-> 视为普通成员放行，
   *   避免锁死用户，实际权限由后端接口 403 兜底；
   * - 否则要求与 required 有交集。
   */
  function hasAnyRole(required?: string[]): boolean {
    if (!required || required.length === 0) return true
    if (roles.value.includes('platform_admin')) return true
    // 角色未加载时默认拒绝：路由守卫会先 fetchMe，避免管理入口在加载期间闪现
    return required.some(r => roles.value.includes(r))
  }

  /** 是否为小院管理员或平台管理员（页面内管理控件可见性用） */
  function isYardAdmin(): boolean {
    return (
      roles.value.includes('yard_owner') ||
      roles.value.includes('yard_admin') ||
      roles.value.includes('platform_admin')
    )
  }

  /**
   * 权限判定（千人千面唯一入口，菜单/路由/按钮共用）：
   * - 未加载权限前放行，避免首屏闪空；
   * - 负责人/平台管理员始终放行；
   * - 否则要求命中后端下发的有效权限。
   */
  function hasPermission(required?: string | string[]): boolean {
    if (!required || (Array.isArray(required) && required.length === 0)) return true
    if (!permissionsLoaded.value) return true
    if (isOwner.value || roles.value.includes('platform_admin')) return true
    const list = Array.isArray(required) ? required : [required]
    return list.some(permission => permissions.value.includes(permission))
  }

  async function login(username: string, password: string, yard_id?: string) {
    const res = await authApi.login(username, password, yard_id)
    persist(res.access_token, res.refresh_token)
    user.value = res.user
    yards.value = res.yards ?? []
    const brief =
      yards.value.find(t => t.yard_id === yard_id) ?? yards.value[0] ?? null
    yard.value = brief ? toYard(brief) : null
    // 角色以登录响应为准（含平台管理员 bootstrap），/auth/me 会再次校正
    roles.value = res.roles?.length ? res.roles : brief?.roles ?? []
    permissions.value = []
    isOwner.value = false
    permissionsLoaded.value = false
    meLoaded.value = true
  }

  /**
   * 采用外部流程（如接受邀请）返回的登录态。
   * preferredYardId 用于多小院账号：优先进入被邀请的小院。
   */
  function adoptLogin(res: LoginResponse, preferredYardId?: string) {
    persist(res.access_token, res.refresh_token)
    user.value = res.user
    yards.value = res.yards ?? []
    const brief =
      yards.value.find(t => t.yard_id === preferredYardId) ?? yards.value[0] ?? null
    yard.value = brief ? toYard(brief) : null
    roles.value = res.roles?.length ? res.roles : brief?.roles ?? []
    permissions.value = []
    isOwner.value = false
    permissionsLoaded.value = false
    meLoaded.value = true
  }

  async function fetchMe() {
    const me = await authApi.me()
    user.value = me.user
    yards.value = me.yards ?? []
    hubs.value = me.hubs ?? []
    roles.value = me.roles?.length ? me.roles : me.user?.role ? [me.user.role] : []
    const current = yards.value.find(t => t.yard_id === yard.value?.yard_id)
    yard.value = current ? toYard(current) : yards.value[0] ? toYard(yards.value[0]) : null
    permissions.value = me.permissions ?? []
    isOwner.value = Boolean(me.is_owner)
    permissionsLoaded.value = true
    meLoaded.value = true
  }

  async function fetchYard() {
    const detail = await yardApi.current()
    yard.value = { ...detail, roles: yard.value?.roles ?? [] }
  }

  async function logout() {
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY) ?? undefined
    try {
      await authApi.logout(refreshToken)
    } catch {
      // 后端注销失败也不阻塞本地退出
    }
    // 清掉上个账号的对话内存（会话列表/消息/附件），否则同浏览器换账号会
    // 短暂看到别人的对话（审查 F1）。动态导入避免 store 之间的静态环。
    try {
      const { resetAllChatStores } = await import('./chat')
      resetAllChatStores()
    } catch {
      // 对话状态清理失败不阻塞退出
    }
    clear()
  }

  /** 桌面壳静默登录：仅安装包启动时调用，失败则回落到普通登录页 */
  async function bootstrapDesktop(token: string) {
    const res = await authApi.desktopBootstrap(token)
    adoptLogin(res)
  }

  return {
    token,
    user,
    yard,
    yards,
    hubs,
    roles,
    permissions,
    isOwner,
    permissionsLoaded,
    meLoaded,
    isLoggedIn,
    hasAnyRole,
    hasPermission,
    ensureSetupStatus,
    setupInitialized,
    setupChecked,
    adoptSetupSession,
    isYardAdmin,
    login,
    adoptLogin,
    bootstrapDesktop,
    fetchMe,
    fetchYard,
    logout,
    clear,
  }
})
