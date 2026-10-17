import { ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi, tenantApi } from '@/api/modules'
import { REFRESH_TOKEN_KEY, TOKEN_KEY } from '@/api/client'
import type { Tenant, User } from '@/types/api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) ?? '')
  const user = ref<User | null>(null)
  const tenant = ref<Tenant | null>(null)
  const tenants = ref<Tenant[]>([])
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
    if (roles.value.length === 0) return true
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
    tenant.value = tenants.value.find(t => t.id === tenant_id) ?? tenants.value[0] ?? null
    // 登录响应只带 user.role 时先粗填一个角色，完整角色以 /auth/me 为准
    roles.value = res.user?.role ? [res.user.role] : []
    meLoaded.value = true
  }

  async function fetchMe() {
    const me = await authApi.me()
    user.value = me.user
    tenants.value = me.tenants ?? []
    roles.value = me.roles?.length ? me.roles : me.user?.role ? [me.user.role] : []
    if (!tenant.value) {
      tenant.value = tenants.value[0] ?? null
    }
    meLoaded.value = true
  }

  async function fetchTenant() {
    tenant.value = await tenantApi.current()
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
    fetchMe,
    fetchTenant,
    logout,
    clear,
  }
})
