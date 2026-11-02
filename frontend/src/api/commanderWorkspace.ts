import type { AxiosResponse } from 'axios'
import { api } from './client'
import type { ApiResponse } from '@/types/api'
import type { CommanderRun, CommanderStats } from './commander'

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
  }
  stats: CommanderStats
  cost: CommanderCost
  memory: {
    counts: CommanderMemoryCounts
    latest_summary: CommanderMemoryItem | null
  }
  runs: CommanderRun[]
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
      api.delete<ApiResponse<{ deleted: boolean }>>(`/commander/memories/${encodeURIComponent(id)}`),
    )
  },
}
