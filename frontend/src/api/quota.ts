import { api } from './client'
import type { ApiResponse } from '@/types/api'

export type QuotaScopeType = 'yard' | 'agent' | 'agent_group' | 'model' | 'supplier'
export type QuotaMetric = 'tokens' | 'calls' | 'cost'
export type QuotaWindow = 'day' | 'month'
export type QuotaOnExceed = 'warn' | 'block' | 'approve'

/** 对话被额度拦下时带回的策略信息（用于弹「申请提额」） */
export interface QuotaBlockInfo {
  policyId: string
  scopeType: string
  scopeKey: string
  metric: string
  window: string
  used: number
  limit: number
  requestId: string
}

export interface QuotaPolicyItem {
  id: string
  scope_type: QuotaScopeType
  scope_type_label: string
  scope_key: string
  metric: QuotaMetric
  metric_label: string
  window: QuotaWindow
  window_label: string
  limit_value: number
  /** 显式无限制（limit_value<0）：effective_limit/remaining 为 null，不设上限 */
  unlimited: boolean
  granted: number
  effective_limit: number | null
  used: number
  remaining: number | null
  usage_percent: number
  on_exceed: QuotaOnExceed
  on_exceed_label: string
  enabled: boolean
  source: string
  note?: string | null
  window_start: string
}

export interface QuotaPolicyPayload {
  scope_type: QuotaScopeType
  scope_key?: string
  metric: QuotaMetric
  window: QuotaWindow
  limit_value: number
  on_exceed: QuotaOnExceed
  note?: string | null
  enabled?: boolean
}

export interface QuotaRequestItem {
  id: string
  policy_id: string
  scope_type: QuotaScopeType
  scope_type_label: string
  scope_key: string
  metric: QuotaMetric
  metric_label: string
  window: QuotaWindow
  window_label: string
  current_limit: number
  requested_limit: number
  reason: string
  status: 'pending' | 'approved' | 'rejected' | 'cancelled' | 'expired'
  requester_id: string
  requester_name?: string | null
  decided_by?: string | null
  decided_at?: string | null
  decision_note?: string | null
  valid_days: number
  created_at?: string | null
}

export interface QuotaEnvironment {
  key: string
  label: string
  description: string
  policies: Record<string, unknown>[]
}

export interface QuotaOverview {
  environment: string
  environments: QuotaEnvironment[]
  legacy_calls_mode: string
  default_grant_days: number
  policies: QuotaPolicyItem[]
  pending_requests: number
  options: {
    agents: { code: string; name: string; group: string }[]
    models: string[]
  }
  can_manage?: boolean
  can_approve?: boolean
  can_request?: boolean
}

function unwrap<T>(p: Promise<{ data: ApiResponse<T> }>): Promise<T> {
  return p.then(res => res.data.data)
}

export const quotaApi = {
  overview(): Promise<QuotaOverview> {
    return unwrap(api.get<ApiResponse<QuotaOverview>>('/quota/overview'))
  },
  upsertPolicy(payload: QuotaPolicyPayload): Promise<QuotaPolicyItem> {
    return unwrap(api.put<ApiResponse<QuotaPolicyItem>>('/quota/policies', payload))
  },
  deletePolicy(id: string): Promise<{ deleted: string }> {
    return unwrap(api.delete<ApiResponse<{ deleted: string }>>(`/quota/policies/${id}`))
  },
  resetPolicy(id: string): Promise<{ policy_id: string; used: number }> {
    return unwrap(api.post<ApiResponse<{ policy_id: string; used: number }>>(`/quota/policies/${id}/reset`, {}))
  },
  setEnvironment(environment: string): Promise<QuotaOverview> {
    return unwrap(api.put<ApiResponse<QuotaOverview>>('/quota/environment', { environment }))
  },
  listRequests(status?: string): Promise<{ items: QuotaRequestItem[]; can_approve: boolean }> {
    return unwrap(
      api.get<ApiResponse<{ items: QuotaRequestItem[]; can_approve: boolean }>>(
        '/quota/requests',
        { params: status ? { status } : {} },
      ),
    )
  },
  createRequest(payload: {
    policy_id: string
    requested_limit: number
    reason?: string
    valid_days?: number
  }): Promise<QuotaRequestItem> {
    return unwrap(api.post<ApiResponse<QuotaRequestItem>>('/quota/requests', payload))
  },
  approve(
    id: string,
    payload: { note?: string; approved_limit?: number },
  ): Promise<{ request: QuotaRequestItem; grant: { id: string; amount: number; expires_at: string } }> {
    return unwrap(
      api.post<
        ApiResponse<{
          request: QuotaRequestItem
          grant: { id: string; amount: number; expires_at: string }
        }>
      >(`/quota/requests/${id}/approve`, payload),
    )
  },
  reject(id: string, note = ''): Promise<QuotaRequestItem> {
    return unwrap(api.post<ApiResponse<QuotaRequestItem>>(`/quota/requests/${id}/reject`, { note }))
  },
}
