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
    assets?: CommanderAssetCounts
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
  source: 'manual' | 'persona' | 'restore' | 'monthly' | string
  persona?: CommanderPersona | null
  created_by?: string | null
  created_at?: string | null
}

export interface CommanderAssetCounts {
  workflows: number
  failures: number
  skills: number
}

export interface CommanderWorkflowStep {
  title: string
  instruction: string
  expected_output?: string
  agent_code?: string
}

export interface CommanderWorkflow {
  id: string
  code: string
  name: string
  scene: string
  preconditions: string
  steps: CommanderWorkflowStep[]
  cost_baseline_tokens: number
  success_count: number
  failure_count: number
  version: number
  status: 'active' | 'draft' | 'archived' | string
  source: 'auto' | 'manual' | string
  last_used_at?: string | null
  created_at?: string | null
}

export interface CommanderFailureCase {
  id: string
  signature: string
  scene: string
  error_code: string
  error_message: string
  root_cause: string
  avoidance: string
  occurrences: number
  status: 'watching' | 'active' | 'resolved' | string
  last_seen_at?: string | null
  created_at?: string | null
}

export interface CommanderSkillSnippet {
  id: string
  code: string
  name: string
  scene: string
  content: string
  source_agent_code: string
  sample_count: number
  success_count: number
  success_rate: number
  status: 'candidate' | 'active' | 'archived' | string
  source: 'auto' | 'manual' | string
  version: number
  evidence: Array<Record<string, unknown>>
  created_at?: string | null
}

export interface CommanderAssets {
  counts: CommanderAssetCounts
  workflows: CommanderWorkflow[]
  failures: CommanderFailureCase[]
  skills: CommanderSkillSnippet[]
}

export interface CommanderPromptModule {
  code: 'delivery' | 'cost' | 'concise' | 'risk' | string
  title: string
  content: string
  enabled: boolean
}

export interface CommanderPromptPreview {
  system: string
  chars: number
  tokens: number
  max_chars: number
  modules: CommanderPromptModule[]
}

export interface CommanderDailyReport {
  day: string
  period: { start: string; end: string }
  markdown: string
  metrics: Record<string, unknown>
}

export interface CommanderWeeklyReport {
  week_key: string
  period: { start: string; end: string }
  markdown: string
  metrics: Record<string, unknown>
  evaluation: CommanderEvaluation | null
}

export interface CommanderIteration {
  id: string
  month_key: string
  version_label: string
  persona: CommanderPersona
  summary: string
  report_md: string
  metrics: Record<string, unknown>
  changes: Record<string, unknown>
  snapshot_id?: string | null
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
  assets(): Promise<CommanderAssets> {
    return unwrap(api.get<ApiResponse<CommanderAssets>>('/commander/assets'))
  },
  scanAssets(): Promise<CommanderAssets & { runs_scanned: number; workflows_created: number; skills_seen: number }> {
    return unwrap(
      api.post<
        ApiResponse<
          CommanderAssets & { runs_scanned: number; workflows_created: number; skills_seen: number }
        >
      >('/commander/assets/scan'),
    )
  },
  createWorkflow(body: {
    name: string
    scene: string
    preconditions?: string
    steps: CommanderWorkflowStep[]
  }): Promise<{ workflow: CommanderWorkflow }> {
    return unwrap(
      api.post<ApiResponse<{ workflow: CommanderWorkflow }>>('/commander/assets/workflows', body),
    )
  },
  updateWorkflow(
    id: string,
    body: {
      status?: 'active' | 'draft' | 'archived'
      name?: string
      scene?: string
      preconditions?: string
      steps?: CommanderWorkflowStep[]
    },
  ): Promise<{ workflow: CommanderWorkflow }> {
    return unwrap(
      api.put<ApiResponse<{ workflow: CommanderWorkflow }>>(
        `/commander/assets/workflows/${encodeURIComponent(id)}`,
        body,
      ),
    )
  },
  updateFailure(
    id: string,
    status: 'watching' | 'active' | 'resolved',
  ): Promise<{ failure: CommanderFailureCase }> {
    return unwrap(
      api.put<ApiResponse<{ failure: CommanderFailureCase }>>(
        `/commander/assets/failures/${encodeURIComponent(id)}`,
        { status },
      ),
    )
  },
  updateSkill(
    id: string,
    status: 'candidate' | 'active' | 'archived',
  ): Promise<{ skill: CommanderSkillSnippet }> {
    return unwrap(
      api.put<ApiResponse<{ skill: CommanderSkillSnippet }>>(
        `/commander/assets/skills/${encodeURIComponent(id)}`,
        { status },
      ),
    )
  },
  promptPreview(): Promise<CommanderPromptPreview> {
    return unwrap(api.get<ApiResponse<CommanderPromptPreview>>('/commander/prompt-preview'))
  },
  dailyReport(day?: string): Promise<CommanderDailyReport> {
    return unwrap(
      api.get<ApiResponse<CommanderDailyReport>>('/commander/reports/daily', {
        params: day ? { day } : {},
      }),
    )
  },
  weeklyReport(week?: string): Promise<CommanderWeeklyReport> {
    return unwrap(
      api.get<ApiResponse<CommanderWeeklyReport>>('/commander/reports/weekly', {
        params: week ? { week } : {},
      }),
    )
  },
  listIterations(limit = 12): Promise<{ items: CommanderIteration[] }> {
    return unwrap(
      api.get<ApiResponse<{ items: CommanderIteration[] }>>('/commander/iterations', {
        params: { limit },
      }),
    )
  },
  latestIteration(): Promise<{ iteration: CommanderIteration | null }> {
    return unwrap(
      api.get<ApiResponse<{ iteration: CommanderIteration | null }>>(
        '/commander/iterations/latest',
      ),
    )
  },
  runIteration(month?: string): Promise<{ iteration: CommanderIteration; created: boolean }> {
    return unwrap(
      api.post<ApiResponse<{ iteration: CommanderIteration; created: boolean }>>(
        '/commander/iterations/run',
        month ? { month } : {},
      ),
    )
  },
}
