import type { AxiosRequestConfig } from 'axios'
import { api } from './client'
import type { ApiResponse } from '@/types/api'

/** 系统日志（v3.41）：负责人查看脱敏后的运行日志。 */

export type SystemLogSource = 'app' | 'engine'
export type SystemLogLevel = '' | 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

export interface SystemLogItem {
  ts: string
  level: string
  logger: string
  request_id: string
  message: string
}

export interface SystemLogFileMeta {
  name: string
  exists: boolean
  size_bytes: number
  mtime: string | null
}

export interface SystemLogMeta {
  source: SystemLogSource
  path: string
  exists: boolean
  size_bytes: number
  mtime: string | null
  scanned_bytes: number
  truncated: boolean
  matched: number
  sources: SystemLogFileMeta[]
}

export interface SystemLogPage {
  items: SystemLogItem[]
  meta: SystemLogMeta
}

export interface SystemLogQuery {
  source: SystemLogSource
  level?: SystemLogLevel
  q?: string
  limit?: number
  offset?: number
}

const silentCfg: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }

export const systemLogsApi = {
  async list(params: SystemLogQuery): Promise<SystemLogPage> {
    const res = await api.get<ApiResponse<SystemLogPage>>('/system/logs', {
      params,
      ...(silentCfg as AxiosRequestConfig),
    })
    return res.data.data
  },

  async download(source: SystemLogSource): Promise<Blob> {
    const res = await api.get<Blob>('/system/logs/download', {
      params: { source },
      responseType: 'blob',
      timeout: 60_000,
    })
    return res.data
  },
}
