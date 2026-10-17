import { ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi, tenantApi } from '@/api/modules'
import { REFRESH_TOKEN_KEY, TOKEN_KEY } from '@/api/client'
import type { Tenant, User } from '@/types/api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem(TOKEN_KEY) ?? '')
  const user = ref<User | null>(null)
  const roles = ref<string[]>([])
  const tenant = ref<Tenant | null>(null)
  const tenants = ref<Tenant[]>([])
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
    roles.value = []
    tenant.value = null
    tenants.value = []
    meLoaded.value = false
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
  }

  async function login(username: string, password: string, tenant_id?: string) {
    const res = await authApi.login(username, password, tenant_id)
    persist(res.access_token, res.refresh_token)
    user.value = res.user
    tenants.value = res.tenants ?? []
    tenant.value = tenants.value.find(t => t.id === tenant_id) ?? tenants.value[0] ?? null
    // 登录接口不返回 roles，用 /auth/me 补全（同时刷新 user/tenants）
    try {
      await fetchMe()
    } catch {
      /* me 失败不阻断登录；meLoaded 保持 true，roles 为空按无全局视图处理 */
    }
    meLoaded.value = true
  }

  async function fetchMe() {
    const me = await authApi.me()
    user.value = me.user
    roles.value = me.roles ?? []
    tenants.value = me.tenants ?? []
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

  return { token, user, roles, tenant, tenants, meLoaded, isLoggedIn, login, fetchMe, fetchTenant, logout, clear }
})