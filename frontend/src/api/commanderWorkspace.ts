import type { AxiosResponse } from 'axios'
import { api } from './client'
import type { ApiResponse } from '@/types/api'
import type { CommanderPersona, CommanderRun, CommanderSettings, CommanderStats } from './commander'

/** 指挥台（v3.43 P0）：概览、成本、记忆检索与日复盘。 */

export interface CommanderCostBucket {
  calls: number
  tokens: number
  cost: number
  purpose?: string
  model?: string
}

export interface CommanderCost {
  window_days: number
  total: {
    calls: number
    input_tokens: number
    output_tokens: number
    tokens: number
    cost: number
  }
  by_purpose: CommanderCostBucket[]
  by_model: CommanderCostBucket[]
  commander: {
    runs: number
    tokens: number
    completed: number
    failed: number
  }
}

export interface CommanderMemoryItem {
  id: string
  kind: 'episode' | 'summary' | 'preference' | 'fact' | string
  title: string
  content: string
  summary?: string | null
  tags: string[]
  importance: number
  confidence: number
  scope_type: string
  scope_key: string
  visibility: string
  source_type: string
  source_id?: string | null
  created_at?: string | null
  last_used_at?: string | null
  score?: number
}

export interface CommanderMemoryCounts {
  total: number
  episode?: number
  summary?: number
  preference?: number
  fact?: number
  [key: string]: number | undefined
}

export interface CommanderOverview {
  commander: {
    display_name: string
    emoji: string
    enabled: boolean
    token_budget: number
    max_parallel: number
    default_mode: boolean
    clarify_enabled: boolean
    auto_remediate: boolean
    persona: CommanderPersona
    persona_locked: boolean
    evolution_lock: boolean
    evaluator_enabled: boolean
  }
  stats: CommanderStats
  cost: CommanderCost
  memory: {
    counts: CommanderMemoryCounts
    latest_summary: CommanderMemoryItem | null
  }
  governance: {
    latest_evaluation: CommanderEvaluation | null
    snapshots: CommanderSnapshot[]
  }
  runs: CommanderRun[]
}

export interface CommanderEvaluation {
  id: string
  week_key: string
  period_start?: string | null
  period_end?: string | null
  persona: CommanderPersona
  persona_label: string
  previous_persona?: CommanderPersona | null
  persona_applied: boolean
  persona_reason?: string | null
  source: 'llm' | 'rule' | string
  model?: string | null
  tokens: number
  scores: Record<string, number>
  total: number
  summary: string
  report_md: string
  strengths: string[]
  shortfalls: string[]
  recommendations: string[]
  metrics: Record<string, unknown>
  created_at?: string | null
}

export interface CommanderSnapshot {
  id: string
  label: string
  reason: string
  source: 'manual' | 'persona' | 'restore' | string
  persona?: CommanderPersona | null
  created_by?: string | null
  created_at?: string | null
}

function unwrap<T>(promise: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  return promise.then(res => res.data.data)
}

export const commanderWorkspaceApi = {
  overview(days = 30): Promise<CommanderOverview> {
    return unwrap(
      api.get<ApiResponse<CommanderOverview>>('/commander/overview', { params: { days } }),
    )
  },
  cost(days = 30): Promise<CommanderCost> {
    return unwrap(api.get<ApiResponse<CommanderCost>>('/commander/cost', { params: { days } }))
  },
  listMemories(params: {
    kind?: string
    scope_type?: string
    limit?: number
    offset?: number
  } = {}): Promise<{ items: CommanderMemoryItem[]; counts: CommanderMemoryCounts }> {
    return unwrap(
      api.get<ApiResponse<{ items: CommanderMemoryItem[]; counts: CommanderMemoryCounts }>>(
        '/commander/memories',
        { params },
      ),
    )
  },
  searchMemories(body: {
    query: string
    top_k?: number
    token_budget?: number
  }): Promise<{ items: CommanderMemoryItem[] }> {
    return unwrap(
      api.post<ApiResponse<{ items: CommanderMemoryItem[] }>>('/commander/memories/search', body),
    )
  },
  summarize(day?: string): Promise<{ created: boolean; memory?: CommanderMemoryItem; reason?: string }> {
    return unwrap(
      api.post<ApiResponse<{ created: boolean; memory?: CommanderMemoryItem; reason?: string }>>(
        '/commander/memories/summary',
        day ? { day } : {},
      ),
    )
  },
  deleteMemory(id: string): Promise<{ deleted: boolean }> {
    return unwrap(
      api.delete<ApiResponse<{ deleted: boolean }>>(
        `/commander/memories/${encodeURIComponent(id)}`,
      ),
    )
  },
  runEvaluation(body: { use_llm?: boolean; force?: boolean } = {}): Promise<{
    evaluation: CommanderEvaluation
    created: boolean
    settings: CommanderSettings
  }> {
    return unwrap(
      api.post<
        ApiResponse<{
          evaluation: CommanderEvaluation
          created: boolean
          settings: CommanderSettings
        }>
      >('/commander/evaluations/run', {
        use_llm: body.use_llm ?? true,
        force: body.force ?? false,
      }),
    )
  },
  listEvaluations(limit = 12): Promise<{ items: CommanderEvaluation[] }> {
    return unwrap(
      api.get<ApiResponse<{ items: CommanderEvaluation[] }>>('/commander/evaluations', {
        params: { limit },
      }),
    )
  },
  listSnapshots(limit = 20): Promise<{ items: CommanderSnapshot[] }> {
    return unwrap(
      api.get<ApiResponse<{ items: CommanderSnapshot[] }>>('/commander/snapshots', {
        params: { limit },
      }),
    )
  },
  createSnapshot(body: { label?: string; reason?: string } = {}): Promise<{
    snapshot: CommanderSnapshot
  }> {
    return unwrap(
      api.post<ApiResponse<{ snapshot: CommanderSnapshot }>>('/commander/snapshots', body),
    )
  },
  restoreSnapshot(id: string): Promise<{ backup: CommanderSnapshot; settings: CommanderSettings }> {
    return unwrap(
      api.post<ApiResponse<{ backup: CommanderSnapshot; settings: CommanderSettings }>>(
        `/commander/snapshots/${encodeURIComponent(id)}/restore`,
      ),
    )
  },
}
