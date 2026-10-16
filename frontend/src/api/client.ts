import axios, { AxiosError, type AxiosRequestConfig } from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiError } from '@/types/api'
import { getMockAdapter, isMockEnabled } from './mock'

export const TOKEN_KEY = 'ky_token'
export const REFRESH_TOKEN_KEY = 'ky_refresh_token'

/** axios 实例：baseURL /api/v1，自动带 Bearer 与 X-Request-ID */
export const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE ?? '/api/v1',
  timeout: 30_000,
})

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
  if (err instanceof AxiosError && err.response?.data) {
    const body = err.response.data as ApiError
    if (body?.error?.message) {
      return { code: body.error.code ?? `HTTP_${err.response.status}`, message: body.error.message }
    }
  }
  if (err instanceof Error) {
    return { code: 'NETWORK_ERROR', message: err.message || '网络请求失败' }
  }
  return { code: 'UNKNOWN', message: '未知错误' }
}

api.interceptors.response.use(
  response => response,
  (error: unknown) => {
    if (error instanceof AxiosError) {
      const status = error.response?.status
      if (status === 401) {
        // 令牌无效/过期：清 token 并跳登录页（错误码 UNAUTHORIZED / INVALID_CREDENTIALS）
        localStorage.removeItem(TOKEN_KEY)
        localStorage.removeItem(REFRESH_TOKEN_KEY)
        if (!window.location.pathname.startsWith('/login')) {
          window.location.assign('/login')
        }
        return Promise.reject(error)
      }
      // 统一错误 toast：取后端 error.message；opt-out 见 config.skipErrorToast
      const config = error.config as AxiosRequestConfig & { skipErrorToast?: boolean }
      if (!config?.skipErrorToast && error.response) {
        const { message } = extractErrorMessage(error)
        ElMessage.error(message)
      }
    }
    return Promise.reject(error)
  },
)
