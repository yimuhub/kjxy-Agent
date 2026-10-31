import { ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi, yardApi } from '@/api/modules'
import { REFRESH_TOKEN_KEY, TOKEN_KEY } from '@/api/client'
import type { LoginResponse, Yard, YardBrief, User } from '@/types/api'

/** 登录/me 的 YardBrief 与 /yards/current 的 YardOut 归一为同一形状 */
function toYard(brief: YardBrief): Yard {
  return { yard_id: brief.yard_id, name: brief.yard_name, roles: brief.roles }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) ?? '')
  const user = ref<User | null>(null)
  const yard = ref<Yard | null>(null)
  const yards = ref<YardBrief[]>([])
  /** 当前用户角色码（yard_admin / expert / operator / viewer / platform_admin） */
  const roles = ref<string[]>([])
  /** 当前小院有效权限（后端 /auth/me 下发，角色默认 ∪ 逐人授权） */
  const permissions = ref<string[]>([])
  const isOwner = ref(false)
  /** 权限是否已从后端加载（加载前不隐藏入口，避免整站闪空） */
  const permissionsLoaded = ref(false)
  /** /auth/me 是否已加载完成（用于区分"未知"和"确实无小院"） */
  const meLoaded = ref(false)

  const isLoggedIn = () => !!token.value

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
    roles,
    permissions,
    isOwner,
    permissionsLoaded,
    meLoaded,
    isLoggedIn,
    hasAnyRole,
    hasPermission,
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
