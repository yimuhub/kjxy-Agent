/** 初始化向导（v3.74）：全新安装时创建管理员与小院，不再预置任何账号。 */
import { api } from './client'
import type { ApiResponse } from '@/types/api'

export interface SetupStatus {
  initialized: boolean
}

export interface SetupSkillItem {
  code: string
  name: string
  description: string
  pack: string
}

export interface SetupSkillCatalog {
  items: SetupSkillItem[]
  total: number
}

export interface SetupLlmProvider {
  provider_type: string
  name: string
  base_url: string
  api_key: string
  model_id: string
}

export interface SetupInitializePayload {
  username: string
  password: string
  display_name: string
  email?: string | null
  phone?: string | null
  yard_name: string
  province?: string
  city?: string
  county?: string
  profile_types: string[]
  yard_description?: string
  /** 培养单位（落 yard_profile.unit，v3.78 起真正持久化） */
  school?: string
  /** 主导产业（落 yard_profile.industry，v3.78 起真正持久化） */
  industry?: string
  skill_codes?: string[]
  llm_provider?: SetupLlmProvider | null
  app_name?: string
  primary_color?: string
  search_enabled: boolean
  agent_generate_enabled: boolean
  agent_generate_daily_limit: number
  deployment: string
}

export interface SetupInitializeResult {
  user_id: string
  yard_id: string
  yard_name: string
  username: string
  skill_count: number
  model_configured: boolean
  access_token: string
}

/** 初始化向导 API（/setup，匿名；一次性安全闸在后端）。 */
export const setupApi = {
  status(): Promise<SetupStatus> {
    return api
      .get<ApiResponse<SetupStatus>>('/setup/status', { params: {}, skipErrorToast: true } as never)
      .then(res => res.data.data)
  },
  skills(): Promise<SetupSkillCatalog> {
    return api
      .get<ApiResponse<SetupSkillCatalog>>('/setup/skills', { params: {}, skipErrorToast: true } as never)
      .then(res => res.data.data)
  },
  initialize(payload: SetupInitializePayload): Promise<SetupInitializeResult> {
    return api
      .post<ApiResponse<SetupInitializeResult>>('/setup/initialize', payload)
      .then(res => res.data.data)
  },
}
