import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiError } from '@/types/api'
import { classifyResponseError, friendlyErrorMessage } from '@/utils/status'
import { getMockAdapter, isMockEnabled } from './mock'

export const TOKEN_KEY = 'ky_token'
export const REFRESH_TOKEN_KEY = 'ky_refresh_token'

/** axios 实例：baseURL /api/v1，自动带 Bearer 与 X-Request-ID */
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api/v1',
  timeout: 30_000,
})

/** 拼接完整 API 路径（fetch/SSE 用，不走 axios 拦截器） */
export function apiUrl(path: string): string {
  const base = api.defaults.baseURL ?? '/api/v1'
  return `${base}${path.startsWith('/') ? path : `/${path}`}`
}

// 后端未就绪时用内置 fixture 演示（默认关闭，见 src/api/mock.ts）
if (isMockEnabled()) {
  api.defaults.adapter = getMockAdapter()
}

api.interceptors.request.use(config => {
  const token = localStorage.getItem(TOKEN_KEY)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  config.headers['X-Request-ID'] =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `req_${Date.now()}_${Math.random().toString(36).slice(2)}`
  return config
})

export function extractErrorMessage(err: unknown): { code: string; message: string } {
  if (err instanceof AxiosError) {
    const { code, message } = classifyResponseError(err.response?.status, err.response?.data)
    if (code !== 'NETWORK_ERROR') return { code, message }
  }
  if (err instanceof Error) {
    return { code: 'NETWORK_ERROR', message: err.message || '网络请求失败' }
  }
  return { code: 'UNKNOWN', message: '未知错误' }
}

/** 结构化错误详情（如 YARD_REQUIRED 的 details.yards） */
export function extractErrorDetails(err: unknown): Record<string, unknown> | undefined {
  if (err instanceof AxiosError && err.response?.data) {
    const body = err.response.data as ApiError
    return body?.error?.details
  }
  return undefined
}

interface RetryConfig extends InternalAxiosRequestConfig {
  _retry?: boolean
  skipErrorToast?: boolean
}

/** 登录/刷新自身返回 401 时不触发刷新，避免递归与误跳转 */
const AUTH_ENDPOINTS = ['/auth/login', '/auth/refresh']

function isAuthEndpoint(url?: string): boolean {
  return !!url && AUTH_ENDPOINTS.some(path => url.includes(path))
}

let refreshPromise: Promise<string | null> | null = null

/** 用 refresh_token 换 access_token；并发 401 共享同一个刷新请求。 */
function refreshAccessToken(): Promise<string | null> {
  if (refreshPromise) return refreshPromise
  const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY)
  if (!refreshToken) return Promise.resolve(null)
  const baseURL = api.defaults.baseURL ?? '/api/v1'
  refreshPromise = axios
    .post(`${baseURL}/auth/refresh`, { refresh_token: refreshToken })
    .then(res => {
      const data = (res.data as { data?: { access_token?: string; refresh_token?: string } })?.data
      if (!data?.access_token) return null
      localStorage.setItem(TOKEN_KEY, data.access_token)
      // 刷新令牌轮换：服务端每次刷新都会作废旧令牌，必须同步保存新令牌
      if (data.refresh_token) localStorage.setItem(REFRESH_TOKEN_KEY, data.refresh_token)
      return data.access_token
    })
    .catch(() => null)
    .finally(() => {
      refreshPromise = null
    })
  return refreshPromise
}

function redirectToLogin(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  if (window.location.pathname.startsWith('/login')) return
  const redirect = encodeURIComponent(window.location.pathname + window.location.search)
  window.location.assign(`/login?redirect=${redirect}`)
}

api.interceptors.response.use(
  response => response,
  async (error: unknown) => {
    if (error instanceof AxiosError) {
      const status = error.response?.status
      const config = error.config as RetryConfig | undefined
      if (status === 401) {
        // 访问令牌过期：先尝试用 refresh_token 静默续期并重放原请求
        if (config && !config._retry && !isAuthEndpoint(config.url)) {
          config._retry = true
          const token = await refreshAccessToken()
          if (token) {
            config.headers.Authorization = `Bearer ${token}`
            return api(config)
          }
          redirectToLogin()
          return Promise.reject(error)
        }
        // 登录失败或刷新失败：清空本地凭据；登录页自行展示错误
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem(REFRESH_TOKEN_KEY)
        return Promise.reject(error)
      }
      // 统一错误 toast：错误码优先映射为中文文案，未命中且为纯英文时兜底，
      // 不把原始英文细节抛给用户；opt-out 见 config.skipErrorToast
      if (!config?.skipErrorToast && error.response) {
        const { code, message } = extractErrorMessage(error)
        ElMessage.error(friendlyErrorMessage(code, message))
      }
    }
    return Promise.reject(error)
  },
)
