/** 初始化向导（v3.69）。 */
import { api } from './client'
import type { ApiResponse } from '@/types/api'

export interface SetupStatus {
  initialized: boolean
}

export interface SetupInitializePayload {
  username: string
  password: string
  display_name: string
  email?: string | null
  yard_name: string
  province?: string
  city?: string
  profile_types: string[]
  yard_description?: string
  app_name?: string
  primary_color?: string
  search_enabled: boolean
  agent_generate_enabled: boolean
  agent_generate_daily_limit: number
  journal_enabled: boolean
  deployment: string
}

export interface SetupInitializeResult {
  user_id: string
  yard_id: string
  username: string
  access_token: string
}

/** 初始化向导 API（/setup，匿名；一次性安全闸在后端）。 */
export const setupApi = {
  status(): Promise<SetupStatus> {
    return api
      .get<ApiResponse<SetupStatus>>('/setup/status', { params: {}, skipErrorToast: true } as never)
      .then(res => res.data.data)
  },
  initialize(payload: SetupInitializePayload): Promise<SetupInitializeResult> {
    return api
      .post<ApiResponse<SetupInitializeResult>>('/setup/initialize', payload)
      .then(res => res.data.data)
  },
}
