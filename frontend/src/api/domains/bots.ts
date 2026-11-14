/**
 * IM 机器人网关（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import { api, unwrap } from '../client'
import type {
  ApiResponse,
} from '@/types/api'

// ---------- IM 机器人网关（/bots） ----------

export interface BotOut {
  id: string
  platform: string
  name: string
  enabled: boolean
  status: string
  status_reason: string | null
  credentials_masked: Record<string, string>
  binding_mode: string
  bound_agent_id: string | null
  require_approval: boolean
  consecutive_failures: number
  created_at: string
}

export interface BotConversationOut {
  id: string
  bot_config_id: string
  platform: string
  external_conv_type: string
  display_name: string | null
  approved_at: string
  revoked_at: string
  last_msg_at: string
  msg_count: number
}

export interface BotMessageOut {
  id: string
  direction: string
  content_sha12: string
  length: number
  routed_by: string | null
  reason_summary: string | null
  ok: boolean
  created_at: string
}

export const botsApi = {
  list(): Promise<BotOut[]> {
    return unwrap(api.get<ApiResponse<BotOut[]>>('/bots'))
  },
  create(payload: {
    platform: string
    name: string
    credentials: Record<string, string>
    binding_mode?: string
    bound_agent_id?: string | null
    require_approval?: boolean
  }): Promise<BotOut> {
    return unwrap(api.post<ApiResponse<BotOut>>('/bots', payload))
  },
  update(
    id: string,
    payload: Partial<{
      name: string
      credentials: Record<string, string>
      binding_mode: string
      bound_agent_id: string | null
      require_approval: boolean
      enabled: boolean
    }>,
  ): Promise<BotOut> {
    return unwrap(api.patch<ApiResponse<BotOut>>(`/bots/${id}`, payload))
  },
  remove(id: string): Promise<{ deleted: string }> {
    return unwrap(api.delete<ApiResponse<{ deleted: string }>>(`/bots/${id}`))
  },
  test(id: string, credentials?: Record<string, string>): Promise<{ ok: boolean; problems: string[] }> {
    return unwrap(api.post<ApiResponse<{ ok: boolean; problems: string[] }>>(`/bots/${id}/test`, credentials ? { credentials } : {}))
  },
  status(id: string): Promise<{ status: string; status_reason: string | null; enabled: boolean; consecutive_failures: number; connected_here: boolean }> {
    return unwrap(api.get(`/bots/${id}/status`))
  },
  conversations(limit = 50): Promise<BotConversationOut[]> {
    return unwrap(api.get<ApiResponse<BotConversationOut[]>>('/bots/conversations', { params: { limit } }))
  },
  messages(conversationId: string, limit = 30): Promise<BotMessageOut[]> {
    return unwrap(api.get<ApiResponse<BotMessageOut[]>>(`/bots/conversations/${conversationId}/messages`, { params: { limit } }))
  },
  approve(conversationId: string): Promise<BotConversationOut> {
    return unwrap(api.post<ApiResponse<BotConversationOut>>(`/bots/conversations/${conversationId}/approve`))
  },
  revoke(conversationId: string): Promise<BotConversationOut> {
    return unwrap(api.post<ApiResponse<BotConversationOut>>(`/bots/conversations/${conversationId}/revoke`))
  },
}
