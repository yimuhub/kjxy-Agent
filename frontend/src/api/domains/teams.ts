/**
 * 协作班组（W2 契约）（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import type { AxiosRequestConfig } from 'axios'
import { api, unwrap } from '../client'
import type {
  ApiResponse,
  TokenUsage,
} from '@/types/api'

// ===== 协作班组（W2 契约，字段以 2026-10-21 实测为准） =====

/** 班组成员定义（有序数组，2..8 个；顺序即接力顺序） */
export interface TeamMemberSpec {
  agent_id: string
  role_name: string
  instruction: string
  is_critic: boolean
}

export type TeamOutputFormat = 'text' | 'md' | 'docx'
export type TeamRunTrigger = 'manual' | 'schedule'

export interface TeamSchedule {
  enabled: boolean
  interval_minutes: number
  input: string
  output_format: TeamOutputFormat
}

/** 班组定义（GET/POST/PATCH /agents/teams* 的 data 形状） */
export interface TeamDefinition {
  id: string
  yard_id: string
  name: string
  model_hint: string | null
  description: string | null
  members: TeamMemberSpec[]
  enabled: boolean
  schedule: TeamSchedule | null
  next_run_at: string | null
  created_by: string | null
  created_at: string
  updated_at: string
}

export interface TeamCreatePayload {
  name: string
  members: TeamMemberSpec[]
  model_hint?: string | null
  description?: string | null
  enabled?: boolean
}

/** PATCH 字段全部可选；members 传则整体替换 */
export type TeamUpdatePayload = Partial<TeamCreatePayload>

export type TeamRunStatus = 'queued' | 'running' | 'completed' | 'failed'
export type TeamMemberRunStatus = 'pending' | 'running' | 'ok' | 'error'

export interface TeamResearch {
  knowledge: Array<{
    document_id: string
    chunk_id: string
    title: string
    source: string | null
    page_number: number | null
    quote: string
    score: number | null
    trust_level: string | null
  }>
  web: Array<{ title: string; url: string; snippet: string }>
  warnings: string[]
}

/** 成员接力明细（run_out.members 元素；task_id 可二跳内部步骤） */
export interface TeamRunMember {
  index: number
  role_name: string
  member_agent_id: string
  /** 该成员声明式 run 的 run_id（无步骤行时 steps 端点 404） */
  task_id: string | null
  status: TeamMemberRunStatus
  input_preview: string | null
  output: string | null
  error_text: string | null
  updated_at: string | null
  token_usage?: TokenUsage | null
}

/** 运行详情（POST run / GET team-runs/{id} / POST retry 的 data 共同形状） */
export interface TeamRun {
  id: string
  team_id: string
  team_name: string | null
  created_by: string | null
  trigger: TeamRunTrigger
  output_format: TeamOutputFormat
  document_available: boolean
  research: TeamResearch | null
  yard_id: string
  input: string
  status: TeamRunStatus
  answer: string | null
  current_member: number
  last_error: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
  updated_at: string | null
  member_count: number
  /** 详情/运行/续跑响应带成员明细；列表摘要不带 */
  members?: TeamRunMember[]
  token_usage?: TokenUsage | null
}

/** 运行列表摘要（GET /agents/teams/{id}/runs 元素，无 answer/members） */
export interface TeamRunSummary {
  id: string
  team_id: string
  team_name: string | null
  created_by: string | null
  trigger: TeamRunTrigger
  output_format: TeamOutputFormat
  answer_preview: string | null
  status: TeamRunStatus
  input_preview: string
  current_member: number
  member_count: number
  last_error: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

export interface TeamRunQuery {
  team_id?: string
  status?: TeamRunStatus
  trigger?: TeamRunTrigger
  page?: number
  page_size?: number
}

export interface TeamRunPage {
  runs: TeamRunSummary[]
  total: number
  page: number
  page_size: number
}

/** 成员内部步骤（GET /agents/declarative/runs/{task_id}/steps 元素） */
export interface TeamRunMemberStep {
  step_index: number
  step_type: 'llm_call' | 'tool_call' | 'final'
  status: string
  tool_code: string | null
  duration_ms: number | null
  error_text: string | null
  /** 后端回 JSON 对象（非字符串），展示时自行截断 */
  output_json?: unknown
}

/** SSE member-event 帧 data（GET /agents/team-runs/{run_id}/stream） */
export interface TeamMemberEvent {
  member_index: number
  role_name: string
  status: TeamMemberRunStatus
  agent_run_id: string | null
  error_text: string | null
}

export const teamApi = {
  /** 班组列表（小院成员均可读） */
  list(): Promise<TeamDefinition[]> {
    return unwrap(api.get<ApiResponse<{ teams: TeamDefinition[] }>>('/agents/teams')).then(
      r => r.teams,
    )
  },
  /** 创建（yard_admin；成员 2..8；同小院重名 409） */
  create(payload: TeamCreatePayload): Promise<TeamDefinition> {
    return unwrap(api.post<ApiResponse<TeamDefinition>>('/agents/teams', payload))
  },
  /** 编辑（yard_admin；members 传则整体替换） */
  update(teamId: string, payload: TeamUpdatePayload): Promise<TeamDefinition> {
    return unwrap(api.patch<ApiResponse<TeamDefinition>>(`/agents/teams/${teamId}`, payload))
  },
  /** 删除班组定义（yard_admin；历史运行台账保留） */
  remove(teamId: string): Promise<void> {
    return api.delete(`/agents/teams/${teamId}`).then(() => undefined)
  },
  /** 按间隔配置定时任务，仅管理员可写；不传时默认停用。 */
  schedule(teamId: string, schedule: TeamSchedule): Promise<TeamDefinition> {
    return unwrap(api.put<ApiResponse<TeamDefinition>>(`/agents/teams/${teamId}/schedule`, schedule))
  },
  /** 202 返回完整 queued 记录；答案由 SSE / 详情轮询追踪。 */
  run(teamId: string, input: string, outputFormat: TeamOutputFormat = 'text'): Promise<TeamRun> {
    return unwrap(api.post<ApiResponse<TeamRun>>(`/agents/teams/${teamId}/run`, {
      input,
      output_format: outputFormat,
    }))
  },
  /** 当前用户可见的跨班组台账，包含已删除班组的名称快照。 */
  allRuns(query: TeamRunQuery = {}): Promise<TeamRunPage> {
    const cfg: AxiosRequestConfig & { skipErrorToast?: boolean } = {
      params: query,
      skipErrorToast: true,
    }
    return unwrap(api.get<ApiResponse<TeamRunPage>>('/agents/team-runs', cfg))
  },
  /** 通过鉴权客户端下载真实文档，不能用 window.open 绕过 token。 */
  document(runId: string, format: 'md' | 'docx'): Promise<Blob> {
    return api.get<Blob>(`/agents/team-runs/${runId}/document`, {
      params: { format },
      responseType: 'blob',
    }).then(res => res.data)
  },
  /** 本班组运行列表（摘要，最新在前） */
  runs(teamId: string): Promise<TeamRunSummary[]> {
    return unwrap(
      api.get<ApiResponse<{ runs: TeamRunSummary[] }>>(`/agents/teams/${teamId}/runs`),
    ).then(r => r.runs)
  },
  /** 运行详情（含成员接力明细）；轮询错误由详情内联提示，避免重复 toast。 */
  runDetail(runId: string): Promise<TeamRun> {
    const cfg: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }
    return unwrap(api.get<ApiResponse<TeamRun>>(`/agents/team-runs/${runId}`, cfg))
  },
  /** 断点续跑（已 ok 成员不重跑；completed/running 时 409，错误由调用方按码分流提示） */
  retry(runId: string): Promise<TeamRun> {
    const cfg: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }
    return unwrap(api.post<ApiResponse<TeamRun>>(`/agents/team-runs/${runId}/retry`, undefined, cfg))
  },
  /** 成员内部步骤下钻（单轮无工具成员无步骤行 → 404 RUN_NOT_FOUND，静默处理） */
  memberSteps(taskId: string): Promise<TeamRunMemberStep[]> {
    const cfg: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }
    return unwrap(
      api.get<ApiResponse<{ run_id: string; status: string; steps: TeamRunMemberStep[] }>>(
        `/agents/declarative/runs/${taskId}/steps`,
        cfg,
      ),
    ).then(r => r.steps)
  },
  /** 成员级 SSE 地址（openSseStream 使用） */
  streamUrl(runId: string): string {
    const base = import.meta.env.VITE_API_BASE ?? '/api/v1'
    return `${base}/agents/team-runs/${runId}/stream`
  },
}
