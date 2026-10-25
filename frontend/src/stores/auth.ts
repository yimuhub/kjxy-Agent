import { ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi, tenantApi } from '@/api/modules'
import { REFRESH_TOKEN_KEY, TOKEN_KEY } from '@/api/client'
import type { LoginResponse, Tenant, TenantBrief, User } from '@/types/api'

/** 登录/me 的 TenantBrief 与 /tenants/current 的 TenantOut 归一为同一形状 */
function toTenant(brief: TenantBrief): Tenant {
  return { tenant_id: brief.tenant_id, name: brief.tenant_name, roles: brief.roles }
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) ?? '')
  const user = ref<User | null>(null)
  const tenant = ref<Tenant | null>(null)
  const tenants = ref<TenantBrief[]>([])
  /** 当前用户角色码（tenant_admin / expert / operator / viewer / platform_admin） */
  const roles = ref<string[]>([])
  /** /auth/me 是否已加载完成（用于区分"未知"和"确实无租户"） */
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
    tenant.value = null
    tenants.value = []
    roles.value = []
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

  /** 是否为使用空间管理员或平台管理员（页面内管理控件可见性用） */
  function isSpaceAdmin(): boolean {
    return roles.value.includes('tenant_admin') || roles.value.includes('platform_admin')
  }

  async function login(username: string, password: string, tenant_id?: string) {
    const res = await authApi.login(username, password, tenant_id)
    persist(res.access_token, res.refresh_token)
    user.value = res.user
    tenants.value = res.tenants ?? []
    const brief =
      tenants.value.find(t => t.tenant_id === tenant_id) ?? tenants.value[0] ?? null
    tenant.value = brief ? toTenant(brief) : null
    // 角色以登录响应为准（含平台管理员 bootstrap），/auth/me 会再次校正
    roles.value = res.roles?.length ? res.roles : brief?.roles ?? []
    meLoaded.value = true
  }

  /**
   * 采用外部流程（如接受邀请）返回的登录态。
   * preferredTenantId 用于多空间账号：优先进入被邀请的空间。
   */
  function adoptLogin(res: LoginResponse, preferredTenantId?: string) {
    persist(res.access_token, res.refresh_token)
    user.value = res.user
    tenants.value = res.tenants ?? []
    const brief =
      tenants.value.find(t => t.tenant_id === preferredTenantId) ?? tenants.value[0] ?? null
    tenant.value = brief ? toTenant(brief) : null
    roles.value = res.roles?.length ? res.roles : brief?.roles ?? []
    meLoaded.value = true
  }

  async function fetchMe() {
    const me = await authApi.me()
    user.value = me.user
    tenants.value = me.tenants ?? []
    roles.value = me.roles?.length ? me.roles : me.user?.role ? [me.user.role] : []
    const current = tenants.value.find(t => t.tenant_id === tenant.value?.tenant_id)
    tenant.value = current ? toTenant(current) : tenants.value[0] ? toTenant(tenants.value[0]) : null
    meLoaded.value = true
  }

  async function fetchTenant() {
    const detail = await tenantApi.current()
    tenant.value = { ...detail, roles: tenant.value?.roles ?? [] }
  }

  async function logout() {
    try {
      await authApi.logout()
    } catch {
      // 后端注销失败也不阻塞本地退出
    }
    clear()
  }

  return {
    token,
    user,
    tenant,
    tenants,
    roles,
    meLoaded,
    isLoggedIn,
    hasAnyRole,
    isSpaceAdmin,
    login,
    adoptLogin,
    fetchMe,
    fetchTenant,
    logout,
    clear,
  }
})
