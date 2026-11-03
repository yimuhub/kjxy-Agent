import type { AxiosRequestConfig } from 'axios'
import { api } from './client'
import type { ApiResponse } from '@/types/api'

/** 本机能力通道（v3.60）：桥状态 + 插件授权/动作清单。 */
export interface RuntimeAction {
  code: string
  label: string
  risk: string
  permission: string
  description: string
}

export interface RuntimePlugin {
  plugin_id: string
  name: string
  version: string
  type: string
  status: string
  platforms: string[]
  platform_supported: boolean
  permissions: string[]
  scope_hash: string
  consent_granted: boolean
  actions: RuntimeAction[]
}

export interface RuntimeStatus {
  platform: string
  bridge: {
    available: boolean
    /** v3.62：逐动作可用性；平台工具缺失时为 false */
    capabilities?: Record<string, boolean>
    actions: RuntimeAction[]
  }
  plugins: RuntimePlugin[]
  limit_per_minute: number
}

export const localRuntimeApi = {
  /** 状态探测失败由调用方静默降级（无权限/后端未升级时不弹全局 toast） */
  async status(): Promise<RuntimeStatus> {
    const res = await api.get<ApiResponse<RuntimeStatus>>('/local-runtime/status', {
      skipErrorToast: true,
    } as AxiosRequestConfig & { skipErrorToast?: boolean })
    return res.data.data
  },
}
