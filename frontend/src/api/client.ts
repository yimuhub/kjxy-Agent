import axios, {
  AxiosError,
  type AxiosRequestConfig,
  type AxiosResponse,
  type InternalAxiosRequestConfig,
} from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiError, ApiResponse, ListResponse, PageMeta } from '@/types/api'
import { classifyResponseError, friendlyErrorMessage } from '@/utils/status'
import { getMockAdapter, isMockEnabled } from './mock'

/** 全局提示层级：必须高于页面悬浮伙伴（z-index 2601） */
const TOAST_Z_INDEX = 2700

export const TOKEN_KEY = 'ky_token'
export const REFRESH_TOKEN_KEY = 'ky_refresh_token'

/** axios 实例：baseURL /api/v1，自动带 Bearer 与 X-Request-ID */
/** 错误 toast 同文案去重（挂载期并发失败只弹一条） */
const _lastToast: { text: string; at: number } = { text: '', at: 0 }

function toastOnce(text: string): void {
  const now = Date.now()
  if (text === _lastToast.text && now - _lastToast.at < 2500) return
  _lastToast.text = text
  _lastToast.at = now
  // 悬浮指挥官伙伴常驻在 2600/2601，Element Plus 提示默认基数只有 2000：
  // 不抬层级时，页面右下角报错会被小人盖住，用户以为「点了没反应」。
  ElMessage.error({ message: text, zIndex: TOAST_Z_INDEX })
}

/** 请求没有拿到响应（超时/断网/被取消）：写操作结果未知，调用方需回查（审查 F3） */
export function isNoResponseError(err: unknown): boolean {
  return err instanceof AxiosError && !err.response && err.code !== 'ERR_CANCELED'
}

/**
 * 解包成功信封 ``{data, request_id} → data``（v3.82 统一 API 层共用）。
 * 此前 modules.ts 与 v3.ts 各自定义了一份同名实现，随双 API 层合并一并收敛。
 */
export function unwrap<T>(promise: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  return promise.then(res => res.data.data)
}

/** 静默请求选项：跳过拦截器的错误 toast（写操作自带错误兜底时使用） */
export const silent: AxiosRequestConfig & { skipErrorToast?: boolean } = {
  skipErrorToast: true,
}

/** 分页列表信封（items + meta） */
export interface ListResult<T> {
  items: T[]
  meta: PageMeta
}

/** 解包分页信封 ``{data, meta} → {items, meta}`` */
export function unwrapList<T>(
  promise: Promise<AxiosResponse<ListResponse<T>>>,
): Promise<ListResult<T>> {
  return promise.then(res => ({ items: res.data.data, meta: res.data.meta }))
}

/** 查询参数清理：剔除 undefined / null / 空串 */
export function toParams(params: object): Record<string, string | number> {
  const out: Record<string, string | number> = {}
  for (const [k, v] of Object.entries(params as Record<string, unknown>)) {
    if (v !== undefined && v !== null && v !== '') {
      out[k] = v as string | number
    }
  }
  return out
}

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
const AUTH_ENDPOINTS = ['/auth/login', '/auth/refresh', '/auth/sso/exchange']

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

/** 公开页（与 router meta.public 对齐）：页面自身的探测请求 401 时不能被踢去登录 */
const PUBLIC_PATHS = ['/login', '/signup', '/setup', '/share', '/invite', '/sso', '/pet']

function isPublicPath(pathname: string): boolean {
  return PUBLIC_PATHS.some(path => pathname === path || pathname.startsWith(`${path}/`))
}

function redirectToLogin(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(REFRESH_TOKEN_KEY)
  if (isPublicPath(window.location.pathname)) return
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
      // 无响应（超时/断网）：写操作结果未知，必须提示用户去核对，不能静默
      // 让「点了没反应」诱导重复提交（审查 F3）。
      if (!config?.skipErrorToast && !error.response && error.code !== 'ERR_CANCELED') {
        const method = (config?.method || 'get').toUpperCase()
        const isWrite = ['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)
        toastOnce(
          isWrite
            ? '请求超时或网络中断，结果未确认；请刷新确认是否已生效，避免重复提交'
            : '网络连接中断，请检查网络后重试',
        )
        return Promise.reject(error)
      }
      // 统一错误 toast：错误码优先映射为中文文案，未命中且为纯英文时兜底，
      // 不把原始英文细节抛给用户；opt-out 见 config.skipErrorToast
      if (!config?.skipErrorToast && error.response) {
        const { code, message } = extractErrorMessage(error)
        const text = friendlyErrorMessage(code, message)
        toastOnce(text)
      }
    }
    return Promise.reject(error)
  },
)
