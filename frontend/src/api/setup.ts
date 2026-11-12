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
  /** 分类键：agri 农技 / growth 增长与服务 / office 通用办公 */
  group: string
  /** 检索关键词（取自技能自身标签） */
  keywords: string[]
  /** 该技能适用的小院画像 */
  scenes: string[]
  /** 是否落在「按产业画像推荐」的预选集合内 */
  recommended: boolean
}

export interface SetupSkillGroup {
  key: string
  label: string
  total: number
  recommended: number
}

export interface SetupSkillCatalog {
  items: SetupSkillItem[]
  total: number
  /** 分类统计（农技 / 增长与服务 / 通用办公） */
  groups: SetupSkillGroup[]
  /** 按产业画像算出的推荐预选集合 */
  recommended_codes: string[]
  recommended_count: number
  profile_types: string[]
  profile_labels: string[]
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
  /** 平台撰写的默认助手数量（装完即可提问，审查 D-01/D-14） */
  agent_count?: number
  model_configured: boolean
  access_token: string
  /** 首启一并下发刷新令牌，避免 30 分钟后被静默踢回登录页（审查 D-16） */
  refresh_token?: string
}

/** 初始化向导 API（/setup，匿名；一次性安全闸在后端）。 */
export const setupApi = {
  status(): Promise<SetupStatus> {
    return api
      .get<ApiResponse<SetupStatus>>('/setup/status', { params: {}, skipErrorToast: true } as never)
      .then(res => res.data.data)
  },
  /**
   * 内置技能清单。传入用户在第 3 步勾选的产业画像（审查 D-13），
   * 服务端据此返回「推荐预选集合」与分类统计，不再一次性全选 35 个。
   */
  skills(profileTypes: string[] = []): Promise<SetupSkillCatalog> {
    return api
      .get<ApiResponse<SetupSkillCatalog>>('/setup/skills', {
        params: { types: profileTypes.join(',') },
        skipErrorToast: true,
      } as never)
      .then(res => res.data.data)
  },
  initialize(payload: SetupInitializePayload): Promise<SetupInitializeResult> {
    return api
      .post<ApiResponse<SetupInitializeResult>>('/setup/initialize', payload)
      .then(res => res.data.data)
  },
}
