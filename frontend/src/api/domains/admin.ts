/**
 * 审计 / 平台管理 / 多集群节点 / 负责人驾驶舱（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import { api, toParams, unwrap, unwrapList } from '../client'
import type { ListResult } from '../client'
import type { SearchStatus } from './knowledge'
import type {
  AdminStats,
  AdminYardList,
  ApiResponse,
  AuditLog,
} from '@/types/api'

// ---------- 审计 / 管理 ----------

export const auditApi = {
  logs(query: { page?: number; page_size?: number } = {}): Promise<ListResult<AuditLog>> {
    return unwrapList(api.get('/audit/logs', { params: toParams(query) }))
  },
}

export const adminApi = {
  stats(): Promise<AdminStats> {
    return unwrap(api.get<ApiResponse<AdminStats>>('/admin/stats'))
  },
  yards(): Promise<AdminYardList> {
    return unwrap(api.get<ApiResponse<AdminYardList>>('/admin/yards'))
  },
}


// ---------- 多集群节点（平台管理员） ----------

export interface ClusterNode {
  id: string
  name: string
  base_url: string
  note: string
  status: 'never' | 'online' | 'degraded' | 'offline' | string
  status_label: string
  stale: boolean
  version: string
  migration_head: string
  latency_ms: number
  last_checked_at?: string | null
  last_ok_at?: string | null
  last_error: string
  created_at?: string | null
}

export interface ClusterNodeSummary {
  total: number
  online: number
  degraded: number
  offline: number
  never: number
}

export interface ClusterNodeList {
  items: ClusterNode[]
  summary: ClusterNodeSummary
}

export interface ClusterNodeInput {
  name: string
  base_url: string
  note?: string
}

export const clusterApi = {
  list(): Promise<ClusterNodeList> {
    return unwrap(api.get<ApiResponse<ClusterNodeList>>('/cluster/nodes'))
  },
  create(payload: ClusterNodeInput): Promise<{ node: ClusterNode }> {
    return unwrap(
      api.post<ApiResponse<{ node: ClusterNode }>>('/cluster/nodes', {
        ...payload,
        note: payload.note ?? '',
      }),
    )
  },
  update(
    id: string,
    payload: Partial<ClusterNodeInput>,
  ): Promise<{ node: ClusterNode }> {
    return unwrap(
      api.put<ApiResponse<{ node: ClusterNode }>>(
        `/cluster/nodes/${encodeURIComponent(id)}`,
        payload,
      ),
    )
  },
  remove(id: string): Promise<{ deleted: boolean }> {
    return unwrap(
      api.delete<ApiResponse<{ deleted: boolean }>>(
        `/cluster/nodes/${encodeURIComponent(id)}`,
      ),
    )
  },
  probe(id: string): Promise<{ node: ClusterNode }> {
    return unwrap(
      api.post<ApiResponse<{ node: ClusterNode }>>(
        `/cluster/nodes/${encodeURIComponent(id)}/probe`,
        undefined,
        { timeout: 20_000 },
      ),
    )
  },
  probeAll(): Promise<ClusterNodeList> {
    return unwrap(
      api.post<ApiResponse<ClusterNodeList>>('/cluster/probe-all', undefined, {
        timeout: 200_000,
      }),
    )
  },
}


// ---------------------------------------------------------------------------
// 负责人驾驶舱
// ---------------------------------------------------------------------------

export interface OwnerOverview {
  yard_id?: string
  generated_at?: string
  members_total: number
  active_today: number
  agents_count: number
  sessions_today: number
  messages_today: number
  tokens_today: number
  tokens_today_input?: number
  tokens_today_output?: number
  tokens_7d: number
  tokens_7d_input?: number
  tokens_7d_output?: number
  failed_messages_today: number
  /** v3.7 可信度与成本：引用覆盖率、7 日模型成功率和估算成本 */
  trust?: {
    assistant_messages_7d: number
    cited_messages_7d: number
    citation_coverage_7d: number | null
    failed_messages_7d: number
    llm_calls_7d: number
    llm_success_rate_7d: number | null
    llm_cost_7d: number
  } | null
  pending_reviews: {
    total: number
    knowledge_candidates: number
    skills: number
    plugins: number
  }
  network?: SearchStatus | null
}

export interface MemberUsageRow {
  user_id: string
  username: string
  display_name?: string | null
  role?: string
  sessions: number
  messages: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  last_active?: string | null
  email?: string | null
  phone?: string | null
  agents_used?: string[]
  agents_used_count?: number
}

export interface DailyUsageMember {
  user_id: string
  username: string
  display_name?: string | null
  messages: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface DailyUsageRow {
  date: string
  sessions: number
  messages: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
  active_members: number
  by_member: DailyUsageMember[]
}

export interface DailyUsageTotals {
  sessions: number
  messages: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface AgentUsageRow {
  code: string
  name: string
  calls: number
  failed: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface OwnerSessionRow {
  id: string
  title: string
  created_by: string
  created_by_username?: string | null
  created_by_display_name?: string | null
  created_at?: string
  updated_at?: string
  message_count?: number
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
  target?: string
  target_name?: string | null
  participant_count?: number
}

export interface GrantRow {
  user_id: string
  username: string
  display_name?: string | null
  grants: string[]
}

/** 投入产出看板（v3.71）：效率/质量/成本/人时节省聚合 */
export interface RoiDashboard {
  window_days: number
  work_orders: {
    total: number
    closed: number
    active: number
    overdue: number
    close_rate: number | null
    avg_close_hours: number | null
  }
  diagnoses: { total: number; reviewed: number; avg_review_hours: number | null }
  commander_runs: {
    total: number
    completed: number
    success_rate: number | null
    tokens: number
  }
  cost: { yuan: number; tokens_in: number; tokens_out: number; calls: number }
  assets: { farm_records: number; knowledge_docs: number }
  saved_hours_estimate: number
  saved_hours_assumptions: {
    closed_order_hours: number
    reviewed_diagnosis_hours: number
    completed_run_hours: number
  }
}

export const ownerApi = {
  overview(): Promise<OwnerOverview> {
    return unwrap(api.get<ApiResponse<OwnerOverview>>('/yards/current/owner/overview'))
  },
  roi(days = 30): Promise<RoiDashboard> {
    return unwrap(
      api.get<ApiResponse<RoiDashboard>>('/yards/current/owner/roi', { params: { days } }),
    )
  },
  memberUsage(
    days = 7,
  ): Promise<{ items: MemberUsageRow[]; summary: Record<string, number>; sensitive?: boolean }> {
    return unwrap(
      api.get<
        ApiResponse<{
          items: MemberUsageRow[]
          summary: Record<string, number>
          sensitive?: boolean
        }>
      >('/yards/current/owner/member-usage', { params: { days } }),
    )
  },
  dailyUsage(days = 7): Promise<{ items: DailyUsageRow[]; totals: DailyUsageTotals }> {
    return unwrap(
      api.get<ApiResponse<{ items: DailyUsageRow[]; totals: DailyUsageTotals }>>(
        '/yards/current/owner/daily-usage',
        { params: { days } },
      ),
    )
  },
  agentUsage(days = 7): Promise<{ items: AgentUsageRow[] }> {
    return unwrap(
      api.get<ApiResponse<{ items: AgentUsageRow[] }>>('/yards/current/owner/agent-usage', {
        params: { days },
      }),
    )
  },
  sessions(query: {
    page?: number
    page_size?: number
    user_id?: string
    keyword?: string
  } = {}): Promise<{ items: OwnerSessionRow[]; total: number }> {
    return api
      .get<ApiResponse<OwnerSessionRow[]> & { meta?: { total?: number } }>(
        '/yards/current/owner/sessions',
        { params: query },
      )
      .then(res => ({
        items: (res.data.data ?? []) as unknown as OwnerSessionRow[],
        total: Number(res.data.meta?.total ?? 0),
      }))
  },
  sessionMessages(
    sessionId: string,
    reason: string,
  ): Promise<{ session: Record<string, unknown>; items: Record<string, unknown>[] }> {
    return unwrap(
      api.get<
        ApiResponse<{ session: Record<string, unknown>; items: Record<string, unknown>[] }>
      >(`/yards/current/owner/sessions/${sessionId}/messages`, { params: { reason } }),
    )
  },
  grants(): Promise<{ items: GrantRow[]; grantable: string[] }> {
    return unwrap(
      api.get<ApiResponse<{ items: GrantRow[]; grantable: string[] }>>(
        '/yards/current/owner/grants',
      ),
    )
  },
  updateGrants(
    userId: string,
    grants: string[],
  ): Promise<{ user_id: string; grants: string[]; effective_permissions: string[] }> {
    return unwrap(
      api.put<ApiResponse<{ user_id: string; grants: string[]; effective_permissions: string[] }>>(
        `/yards/current/owner/grants/${userId}`,
        { grants },
      ),
    )
  },
  transfer(userId: string): Promise<{ ok: boolean }> {
    return unwrap(
      api.post<ApiResponse<{ ok: boolean }>>('/yards/current/owner/transfer', {
        user_id: userId,
      }),
    )
  },
}
