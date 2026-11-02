import type { AxiosResponse } from 'axios'
import { api } from './client'
import type { ApiResponse } from '@/types/api'

/**
 * 指挥官 Agent（v3.38）契约：设置、运行详情与计划/汇总载荷。
 *
 * 所有接口都走统一 client（baseURL 已含 /api/v1，自动带 Bearer 与错误 toast）：
 * - GET/PUT /yards/current/settings/commander
 * - GET    /commander/stats?days=30 → {stats:{...}}
 * - PUT    /commander/runs/{run_id}/plan → {run:{...}}
 * - POST   /commander/runs/{run_id}/execute → 202
 * - GET    /commander/runs/{run_id} → {run:{...}}
 * - POST   /commander/runs/{run_id}/retry
 * - POST   /commander/runs/{run_id}/cancel
 */

/** 会话执行模式：普通单助手路由 / 指挥官拆分调度 */
export type ExecutionMode = 'standard' | 'commander'

/** 运行状态词表（后端可能扩充，未知值按字符串透出，界面走兜底文案） */
export type CommanderRunStatus =
  | 'queued'
  | 'running'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'cancelled'
  | string

/** 子任务状态词表：等待 / 进行中 / 完成 / 失败 / 跳过 */
export type CommanderSubtaskStatus =
  | 'pending'
  | 'running'
  | 'ok'
  | 'error'
  | 'skipped'
  | string

export interface CommanderSettings {
  enabled: boolean
  display_name: string
  emoji: string
  /** 新会话是否默认进入指挥官（后端为布尔开关，默认 false） */
  default_mode: boolean
  /** 同时执行的 Agent 数（1..10，默认 3） */
  max_parallel: number
  /** 上游异常时的兜底并发（1..10，默认 2） */
  fallback_parallel: number
  /** 单轮子任务上限（2..12，默认 6） */
  max_subtasks: number
  /** 单子任务超时（秒，30..600） */
  subtask_timeout_seconds: number
  /** 单轮总超时（秒，60..1800） */
  run_timeout_seconds: number
  preview_plan: boolean
  auto_create_agent: boolean
  /** 失败后自主补救：原执行者重试 → 换助手 → 新建助手 → 指挥官带工具亲自执行 */
  auto_remediate: boolean
  /** 单个子任务最多自主尝试轮数（1..10，默认 5；轮尽才报告能力缺口） */
  remediate_rounds: number
  auto_publish_agent: boolean
  critic_enabled: boolean
  /** 目标不明确时先问 1–2 个问题再拆（默认 false：先按最合理假设开跑） */
  clarify_enabled: boolean
  /** 每天自动创建助手的上限（1..50，默认 10；超出回退到最接近的已有助手） */
  daily_agent_limit: number
  token_budget: number
  supplier_id: string | null
  supplier_model_id: string | null
  /** 人格标签：由周度测评判定，也可手动指定 */
  persona: CommanderPersona
  /** 锁定人格后测评只出报告、不自动改画像 */
  persona_locked: boolean
  /** 进化锁：暂停人格自动调整与后续自动进化 */
  evolution_lock: boolean
  /** 每周自动测评 */
  evaluator_enabled: boolean
  /** 测评报告是否允许自动调整人格 */
  evaluator_auto_persona: boolean
  evaluator_supplier_id: string | null
  evaluator_supplier_model_id: string | null
  /** 成功运行自动沉淀标准工作流 */
  auto_learn_workflows: boolean
  /** 从 Worker 成功样本萃取技能片段 */
  auto_extract_skills: boolean
  /** 同场景同错误累计多少次后进入失败黑名单（1..10） */
  failure_blacklist_threshold: number
}

export type CommanderPersona = 'aggressive' | 'steady' | 'balanced'

export const COMMANDER_PERSONA_LABELS: Record<CommanderPersona, string> = {
  aggressive: '进取型',
  steady: '稳健保守型',
  balanced: '平衡型',
}

export const DEFAULT_COMMANDER_SETTINGS: CommanderSettings = {
  enabled: true,
  display_name: '指挥官',
  emoji: '🧭',
  default_mode: false,
  max_parallel: 3,
  fallback_parallel: 2,
  max_subtasks: 6,
  subtask_timeout_seconds: 180,
  run_timeout_seconds: 600,
  preview_plan: false,
  auto_create_agent: true,
  auto_remediate: true,
  remediate_rounds: 5,
  auto_publish_agent: false,
  critic_enabled: false,
  clarify_enabled: false,
  daily_agent_limit: 10,
  token_budget: 120000,
  supplier_id: null,
  supplier_model_id: null,
  persona: 'balanced',
  persona_locked: false,
  evolution_lock: false,
  evaluator_enabled: true,
  evaluator_auto_persona: true,
  evaluator_supplier_id: null,
  evaluator_supplier_model_id: null,
  auto_learn_workflows: true,
  auto_extract_skills: true,
  failure_blacklist_threshold: 2,
}

function intIn(value: unknown, fallback: number, min: number, max: number): number {
  const n = Number(value)
  if (!Number.isFinite(n)) return fallback
  return Math.min(max, Math.max(min, Math.round(n)))
}

function boolOr(value: unknown, fallback: boolean): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function personaOr(value: unknown, fallback: CommanderPersona): CommanderPersona {
  return value === 'aggressive' || value === 'steady' || value === 'balanced'
    ? value
    : fallback
}

function idOrNull(value: unknown): string | null {
  if (value === null || value === undefined) return null
  const text = String(value).trim()
  return text || null
}

/** 设置出参收敛：缺字段走默认值，数值夹紧到合法区间，避免脏设置把表单撑坏 */
export function normalizeCommanderSettings(
  input?: Partial<CommanderSettings> | null,
): CommanderSettings {
  const src = (input ?? {}) as Partial<CommanderSettings>
  const d = DEFAULT_COMMANDER_SETTINGS
  return {
    enabled: boolOr(src.enabled, d.enabled),
    display_name: String(src.display_name ?? d.display_name).trim() || d.display_name,
    emoji: String(src.emoji ?? d.emoji).trim() || d.emoji,
    default_mode: boolOr(src.default_mode, d.default_mode),
    max_parallel: intIn(src.max_parallel, d.max_parallel, 1, 10),
    fallback_parallel: intIn(src.fallback_parallel, d.fallback_parallel, 1, 10),
    max_subtasks: intIn(src.max_subtasks, d.max_subtasks, 2, 12),
    subtask_timeout_seconds: intIn(
      src.subtask_timeout_seconds,
      d.subtask_timeout_seconds,
      30,
      600,
    ),
    run_timeout_seconds: intIn(src.run_timeout_seconds, d.run_timeout_seconds, 60, 1800),
    preview_plan: boolOr(src.preview_plan, d.preview_plan),
    auto_create_agent: boolOr(src.auto_create_agent, d.auto_create_agent),
    auto_remediate: boolOr(src.auto_remediate, d.auto_remediate),
    remediate_rounds: intIn(src.remediate_rounds, d.remediate_rounds, 1, 10),
    auto_publish_agent: boolOr(src.auto_publish_agent, d.auto_publish_agent),
    critic_enabled: boolOr(src.critic_enabled, d.critic_enabled),
    clarify_enabled: boolOr(src.clarify_enabled, d.clarify_enabled),
    daily_agent_limit: intIn(src.daily_agent_limit, d.daily_agent_limit, 1, 50),
    token_budget: intIn(src.token_budget, d.token_budget, 1000, 2_000_000),
    supplier_id: idOrNull(src.supplier_id),
    supplier_model_id: idOrNull(src.supplier_model_id),
    persona: personaOr(src.persona, d.persona),
    persona_locked: boolOr(src.persona_locked, d.persona_locked),
    evolution_lock: boolOr(src.evolution_lock, d.evolution_lock),
    evaluator_enabled: boolOr(src.evaluator_enabled, d.evaluator_enabled),
    evaluator_auto_persona: boolOr(src.evaluator_auto_persona, d.evaluator_auto_persona),
    evaluator_supplier_id: idOrNull(src.evaluator_supplier_id),
    evaluator_supplier_model_id: idOrNull(src.evaluator_supplier_model_id),
    auto_learn_workflows: boolOr(src.auto_learn_workflows, d.auto_learn_workflows),
    auto_extract_skills: boolOr(src.auto_extract_skills, d.auto_extract_skills),
    failure_blacklist_threshold: intIn(
      src.failure_blacklist_threshold,
      d.failure_blacklist_threshold,
      1,
      10,
    ),
  }
}

/** 计划帧里的子任务（轻量字段，运行详情会补全指令/产物） */
export interface CommanderPlanSubtask {
  id: string
  seq?: number
  title?: string
  /** 计划编辑：子任务指令（计划帧可能不带，编辑保存后的回包会补全） */
  instruction?: string
  expected_output?: string
  /** 指派的助手 id；null = 指挥官代执行 */
  agent_id?: string | null
  agent_name?: string
  agent_code?: string
  depends_on?: string[]
  status?: CommanderSubtaskStatus
  tokens?: number
}

/** 子任务自主补救轨迹：失败后换人/建人/自执行的每一步 */
export interface CommanderRemediation {
  round: number
  action: 'retry' | 'alt_agent' | 'new_agent' | 'self' | string
  agent_name?: string
  code?: string
  message?: string
}

/** 运行详情里的子任务（GET /commander/runs/{run_id}） */
export interface CommanderSubtask {
  id: string
  seq?: number
  title?: string
  instruction?: string
  expected_output?: string
  /** 指派的助手 id；null = 指挥官代执行 */
  agent_id?: string | null
  agent_name?: string
  agent_code?: string
  agent_source?: 'existing' | 'generated' | 'commander' | string
  status?: CommanderSubtaskStatus
  depends_on?: string[]
  attempts?: number
  tokens?: number
  output_excerpt?: string
  artifact_json?: unknown
  error_code?: string | null
  remediation?: CommanderRemediation[]
  started_at?: string | null
  finished_at?: string | null
}

export interface CommanderRun {
  id: string
  session_id?: string
  goal?: string
  status?: CommanderRunStatus
  max_parallel?: number
  effective_parallel?: number
  degraded?: boolean
  /** 创建时快照的会话授权模式（full = 完全控制，不再逐次审批） */
  approval_mode?: 'ask' | 'auto' | 'full' | string
  tokens_total?: number
  token_budget?: number
  budget_ratio?: number
  budget_status?: 'ok' | 'warning' | 'exceeded' | string
  budget_remaining?: number
  memory_used?: boolean
  latency_ms?: number
  plan_fallback?: boolean
  /** 降级原因：clarify_disabled / parse_failed / llm_error；空串表示未降级 */
  plan_fallback_reason?: string
  subtasks?: CommanderSubtask[]
}

/** 计划编辑的提交行：depends_on 传「新顺序下的 1-based 位置」 */
export interface CommanderPlanUpdateSubtask {
  title: string
  instruction: string
  expected_output?: string
  /** null = 指挥官代执行 */
  agent_id: string | null
  depends_on: number[]
}

/** 产物：后端可能给文档/图片/链接，字段做宽松收敛，只渲染能安全打开的链接 */
export interface CommanderArtifact {
  kind?: string
  type?: string
  title?: string
  name?: string
  filename?: string
  description?: string
  url?: string
  href?: string
  mime?: string
  format?: string
  attachment_id?: string
  session_id?: string
  size_bytes?: number
}

/** 失败项：seq/title/reason 为主，兼容后端只给 id */
export interface CommanderFailedItem {
  id?: string
  seq?: number
  title?: string
  reason?: string
}

/** assistant 消息里的计划载荷 */
export interface CommanderPlanPayload {
  type: 'commander_plan'
  run_id: string
  goal: string
  subtasks: CommanderPlanSubtask[]
  plan_fallback?: boolean
  /** 降级原因：clarify_disabled / parse_failed / llm_error；空串表示未降级 */
  plan_fallback_reason?: string
  /** 后端未来可能下发；当前实现缺省时由设置 preview_plan 兜底 */
  preview?: boolean
  /** 技能优先：本次从技能库检索并注入的技能（用于界面展示「用了哪些技能」） */
  skills?: Array<{ code: string; name: string }>
  status?: CommanderRunStatus
  degraded?: boolean
  max_parallel?: number
  effective_parallel?: number
  token_usage?: Record<string, unknown>
}

/** 澄清表单的一项：能选就选（chips），不能选就填一句话 */
export interface CommanderClarifyQuestion {
  key: string
  label: string
  /** single=单选 chips；multi=多选；text=自由输入 */
  type: 'single' | 'multi' | 'text'
  options: string[]
  placeholder?: string
  allow_custom?: boolean
  required?: boolean
}

/** assistant 消息里的澄清载荷：用户答完自动续跑，不用重开对话 */
export interface CommanderClarifyPayload {
  type: 'commander_clarify'
  run_id: string
  goal?: string
  display_name?: string
  questions: CommanderClarifyQuestion[]
  feasibility?: Record<string, unknown>
}

/** assistant 消息里的汇总载荷（历史消息可能只带 id，不带 run_id） */
export interface CommanderSummaryPayload {
  type: 'commander_summary'
  run_id?: string
  id?: string
  session_id?: string
  goal?: string
  status?: CommanderRunStatus
  answer: string
  artifacts: CommanderArtifact[]
  failed: CommanderFailedItem[]
  tokens_total?: number
  degraded?: boolean
  max_parallel?: number
  effective_parallel?: number
  latency_ms?: number
  plan_fallback?: boolean
  /** 降级原因：clarify_disabled / parse_failed / llm_error；空串表示未降级 */
  plan_fallback_reason?: string
  subtasks?: CommanderSubtask[]
}

export type CommanderMessagePayload = CommanderPlanPayload | CommanderSummaryPayload

/** SSE 帧名：与后端事件名逐字对齐（连字符，区分既有 member_event 的下划线） */
export const COMMANDER_SSE_EVENTS = {
  plan: 'commander-plan',
  subtaskStatus: 'subtask-status',
  summary: 'commander-summary',
  budget: 'commander-budget',
} as const

/** 预算帧：跨过 80%/100% 时下发，前端即时提示但不阻塞执行 */
export interface CommanderBudgetFrame {
  run_id: string
  tokens_total?: number
  token_budget?: number
  ratio?: number
  status?: 'ok' | 'warning' | 'exceeded' | string
}

export interface CommanderPlanFrame {
  run_id: string
  goal?: string
  plan_fallback?: boolean
  /** 降级原因：clarify_disabled / parse_failed / llm_error；空串表示未降级 */
  plan_fallback_reason?: string
  preview?: boolean
  status?: CommanderRunStatus
  degraded?: boolean
  max_parallel?: number
  effective_parallel?: number
  subtasks?: CommanderPlanSubtask[]
}

export interface CommanderSubtaskStatusFrame {
  run_id: string
  subtask_id: string
  /** 后端服务层原字段（bridge 保留），用于计划帧缺失时仍能拼出可读行 */
  id?: string
  seq?: number
  title?: string
  status?: string
  agent_name?: string
  agent_code?: string
  depends_on?: (string | number)[]
  tokens?: number
  error_code?: string | null
  output_excerpt?: string | null
  started_at?: string | null
  finished_at?: string | null
}

/** 汇总帧：后端实际会把 run_out 的字段一并带上（status/subtasks/并发数等） */
export interface CommanderSummaryFrame {
  run_id?: string
  id?: string
  answer?: string
  artifacts?: CommanderArtifact[]
  failed?: CommanderFailedItem[]
  tokens_total?: number
  degraded?: boolean
  status?: CommanderRunStatus
  max_parallel?: number
  effective_parallel?: number
  latency_ms?: number
  plan_fallback?: boolean
  /** 降级原因：clarify_disabled / parse_failed / llm_error；空串表示未降级 */
  plan_fallback_reason?: string
  subtasks?: CommanderSubtask[]
}

/** 运行概况（GET /commander/stats）：字段缺失一律回 0，界面不必再做空值判断 */
export interface CommanderStatsRuns {
  total: number
  completed: number
  partial: number
  failed: number
  cancelled: number
  queued: number
  running: number
}

export interface CommanderStats {
  runs: CommanderStatsRuns
  plan: {
    /** 计划成功率（0..1） */
    success_rate: number
    /** 计划降级率（0..1） */
    fallback_rate: number
  }
  subtasks: {
    total: number
    ok: number
    error: number
    skipped: number
    /** 子任务成功率（0..1） */
    success_rate: number
  }
  avg_effective_parallel: number
  degraded_runs: number
  auto_created_agents: number
  latency_ms: {
    p50: number
    p90: number
    max: number
  }
}

export function emptyCommanderStats(): CommanderStats {
  return {
    runs: { total: 0, completed: 0, partial: 0, failed: 0, cancelled: 0, queued: 0, running: 0 },
    plan: { success_rate: 0, fallback_rate: 0 },
    subtasks: { total: 0, ok: 0, error: 0, skipped: 0, success_rate: 0 },
    avg_effective_parallel: 0,
    degraded_runs: 0,
    auto_created_agents: 0,
    latency_ms: { p50: 0, p90: 0, max: 0 },
  }
}

function numOr(value: unknown, fallback = 0): number {
  const n = Number(value)
  return Number.isFinite(n) ? n : fallback
}

/**
 * 比率统一收敛成 0..1：后端若直接给 85 这种百分数也能正确显示。
 * 界面只负责 ×100 加百分号。
 */
function rateOr(value: unknown): number {
  const n = numOr(value, 0)
  if (n <= 0) return 0
  return n > 1 ? Math.min(1, n / 100) : n
}

function section(input: unknown): Record<string, unknown> {
  return input && typeof input === 'object' ? (input as Record<string, unknown>) : {}
}

/** 运行概况出参收敛：兼容 {stats:{...}} 与非空包裹，缺字段 / 脏值一律回 0 */
export function normalizeCommanderStats(input?: unknown): CommanderStats {
  const body = section(input)
  const src = section(body.stats ?? body)
  const d = emptyCommanderStats()
  const runs = section(src.runs)
  const plan = section(src.plan)
  const subtasks = section(src.subtasks)
  const latency = section(src.latency_ms)
  return {
    runs: {
      total: numOr(runs.total, d.runs.total),
      completed: numOr(runs.completed, d.runs.completed),
      partial: numOr(runs.partial, d.runs.partial),
      failed: numOr(runs.failed, d.runs.failed),
      cancelled: numOr(runs.cancelled, d.runs.cancelled),
      queued: numOr(runs.queued, d.runs.queued),
      running: numOr(runs.running, d.runs.running),
    },
    plan: {
      success_rate: rateOr(plan.success_rate),
      fallback_rate: rateOr(plan.fallback_rate),
    },
    subtasks: {
      total: numOr(subtasks.total, d.subtasks.total),
      ok: numOr(subtasks.ok, d.subtasks.ok),
      error: numOr(subtasks.error, d.subtasks.error),
      skipped: numOr(subtasks.skipped, d.subtasks.skipped),
      success_rate: rateOr(subtasks.success_rate),
    },
    avg_effective_parallel: numOr(src.avg_effective_parallel, d.avg_effective_parallel),
    degraded_runs: numOr(src.degraded_runs, d.degraded_runs),
    auto_created_agents: numOr(src.auto_created_agents, d.auto_created_agents),
    latency_ms: {
      p50: numOr(latency.p50, d.latency_ms.p50),
      p90: numOr(latency.p90, d.latency_ms.p90),
      max: numOr(latency.max, d.latency_ms.max),
    },
  }
}

function unwrap<T>(promise: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  return promise.then(res => res.data.data)
}

type SettingsEnvelope = Partial<CommanderSettings> & { commander?: Partial<CommanderSettings> }

function pickSettings(data: unknown): Partial<CommanderSettings> | null {
  if (!data || typeof data !== 'object') return null
  const body = data as SettingsEnvelope
  if (body.commander && typeof body.commander === 'object') return body.commander
  return body
}

function pickRun(data: unknown): CommanderRun | null {
  if (!data || typeof data !== 'object') return null
  const body = data as CommanderRun & { run?: CommanderRun }
  const run = body.run && typeof body.run === 'object' ? body.run : body
  return run && typeof run === 'object' && run.id ? run : null
}

function runPath(runId: string, action = ''): string {
  const base = `/commander/runs/${encodeURIComponent(runId)}`
  return action ? `${base}/${action}` : base
}

export const commanderApi = {
  /** 读取指挥官设置（兼容后端把设置包在 commander 键里的形状） */
  async getSettings(): Promise<CommanderSettings> {
    const payload = await unwrap<unknown>(
      api.get<ApiResponse<unknown>>('/yards/current/settings/commander'),
    )
    return normalizeCommanderSettings(pickSettings(payload))
  },
  /** 保存指挥官设置；返回后端归一后的设置（无回包时用提交值） */
  async updateSettings(settings: CommanderSettings): Promise<CommanderSettings> {
    const body = normalizeCommanderSettings(settings)
    const payload = await unwrap<unknown>(
      api.put<ApiResponse<unknown>>('/yards/current/settings/commander', body),
    )
    return normalizeCommanderSettings(pickSettings(payload) ?? body)
  },
  /** 运行详情：{run:{...}}；兼容后端直接返回 run 的形状 */
  async getRun(runId: string): Promise<CommanderRun | null> {
    const payload = await unwrap<unknown>(api.get<ApiResponse<unknown>>(runPath(runId)))
    return pickRun(payload)
  },
  /** 运行概况（近 N 天）；失败由调用方静默兜底，不打断设置页 */
  async getStats(days = 30): Promise<CommanderStats> {
    const span = intIn(days, 30, 1, 365)
    const payload = await unwrap<unknown>(
      api.get<ApiResponse<unknown>>('/commander/stats', { params: { days: span } }),
    )
    return normalizeCommanderStats(payload)
  },
  /** 计划未开始时整表保存子任务（顺序/删除/指令/助手都会生效） */
  async updatePlan(
    runId: string,
    subtasks: CommanderPlanUpdateSubtask[],
  ): Promise<CommanderRun | null> {
    const payload = await unwrap<unknown>(
      api.put<ApiResponse<unknown>>(runPath(runId, 'plan'), { subtasks }),
    )
    return pickRun(payload)
  },
  /** 开始执行预览中的计划（202） */
  async executeRun(runId: string): Promise<void> {
    await api.post<ApiResponse<unknown>>(runPath(runId, 'execute'))
  },
  /** 重试失败/跳过的子任务 */
  async retryRun(runId: string): Promise<CommanderRun | null> {
    const payload = await unwrap<unknown>(
      api.post<ApiResponse<unknown>>(runPath(runId, 'retry')),
    )
    return pickRun(payload)
  },
  /** 取消未开始的子任务（已完成的保留） */
  async cancelRun(runId: string): Promise<CommanderRun | null> {
    const payload = await unwrap<unknown>(
      api.post<ApiResponse<unknown>>(runPath(runId, 'cancel')),
    )
    return pickRun(payload)
  },
}
