/**
 * 专业 Agent / 声明式智能体 / Agent 工厂 / 市场批量装载 / 监督复核 / 可绑定扩展 / 一句话生成智能体 / A2A 协作（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import { isAxiosError, type AxiosRequestConfig } from 'axios'
import { api, extractErrorMessage, silent, unwrap } from '../client'
import type { TeamDefinition } from './teams'
import type {
  AgentDefinition,
  AgentRunResult,
  ApiResponse,
} from '@/types/api'
import { friendlyErrorMessage } from '@/utils/status'

// ---------- 专业 Agent ----------

export const agentApi = {
  list(): Promise<AgentDefinition[]> {
    return unwrap(api.get<ApiResponse<{ agents: AgentDefinition[] }>>('/agents')).then(
      r => r.agents,
    )
  },
  run(code: string, input: Record<string, unknown>): Promise<AgentRunResult> {
    // 后端 Agent 截止时间 120s，客户端留余量，避免同步运行被 30s 默认超时中断
    return unwrap(
      api.post<ApiResponse<AgentRunResult>>(`/agents/${code}/run`, { input }, { timeout: 150_000 }),
    )
  },
}


export type AgentVisibility = 'public' | 'private'

export interface AgentTokenStats {
  calls: number
  input_tokens: number
  output_tokens: number
  total_tokens: number
}

export interface DeclarativeAgent {
  id: string
  yard_id: string
  agent_code: string
  /** 干净原名：提交与重名比对都用它 */
  name: string
  /** 私有助手由后端派生「原名（用户名私有）」，前端只展示、绝不自行拼后缀 */
  display_name: string
  visibility: AgentVisibility
  description: string | null
  emoji: string | null
  /** 首图地址；为空时卡片回落 emoji 大字渲染 */
  cover_image_url?: string | null
  persona_prompt: string
  task_prompt_template: string
  allowed_tools: string[]
  bound_skills?: string[]
  knowledge_scopes: string[]
  supplier_id: string | null
  model_name?: string | null
  group_name?: string | null
  risk_level: 'low' | 'high'
  status: 'draft' | 'pending_platform_review' | 'published' | 'deprecated'
  version: string
  created_from: string
  created_by?: string | null
  updated_by?: string | null
  created_at: string
  updated_at: string
  token_stats?: AgentTokenStats
  /** v3.5：一句话生成的蓝图框架（使命/边界/工作流/护栏等）；非生成 agent 为 null */
  blueprint?: Record<string, unknown> | null
  /** v3.5：待补技能清单（生成时未匹配到绑定的能力）；非生成草稿为空数组 */
  skill_requirements?:
    | { need: string; keywords?: string[]; reason?: string; status?: string }[]
    | null
}

/** 409 DUPLICATE_NAME 的 details：指向本小院里那条已存在的助手 */
export interface DuplicateNameDetails {
  id: string
  agent_code: string
  name: string
  status: string
}

export interface DeclarativeAgentCreate {
  agent_code: string
  name: string
  description?: string
  emoji?: string
  persona_prompt: string
  task_prompt_template: string
  allowed_tools?: string[]
  bound_skills?: string[]
  knowledge_scopes?: string[]
  supplier_id?: string | null
  model_name?: string | null
  group_name?: string | null
  visibility?: AgentVisibility
}

/** 编辑入参：编码不可改（版本化由新建版本行实现），故不带 agent_code */
export interface DeclarativeAgentUpdate {
  name?: string
  description?: string
  emoji?: string
  persona_prompt?: string
  task_prompt_template?: string
  allowed_tools?: string[]
  bound_skills?: string[]
  knowledge_scopes?: string[]
  supplier_id?: string | null
  model_name?: string | null
  group_name?: string | null
  visibility?: AgentVisibility
  /** v3.5：待补技能清单允许人工维护（删除已补齐项） */
  skill_requirements?: { need: string; keywords?: string[]; reason?: string; status?: string }[]
}

/** 「使用该助手」建的会话（只需这两项即可挂进对话实例） */
export interface AgentChatSession {
  id: string
  title: string
  /** 会话锁定对象：建会话时就固定，后续消息不再自动路由 */
  target?: 'auto' | 'agent' | 'team'
  target_code?: string | null
  target_name?: string | null
  created_at?: string
  updated_at?: string
}

/** 只用于「重名/降级」判定的静默请求：错误由页面按码分流，不抢全局 toast */
const agentSilentCfg: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }

export const declarativeApi = {
  list(): Promise<DeclarativeAgent[]> {
    return unwrap(
      api.get<ApiResponse<{ agents: DeclarativeAgent[] }>>('/agents/declarative'),
    ).then(r => r.agents)
  },
  /** 重名 409 由页面弹三选，toast 交给调用方决定，故关全局提示 */
  create(payload: DeclarativeAgentCreate): Promise<DeclarativeAgent> {
    return unwrap(
      api.post<ApiResponse<DeclarativeAgent>>('/agents/declarative', payload, agentSilentCfg),
    )
  },
  update(id: string, payload: DeclarativeAgentUpdate): Promise<DeclarativeAgent> {
    return unwrap(
      api.patch<ApiResponse<DeclarativeAgent>>(`/agents/declarative/${id}`, payload, agentSilentCfg),
    )
  },
  /** 复制：新编码与「原名-N」由服务端生成，副本一律 draft */
  duplicate(id: string): Promise<DeclarativeAgent> {
    return unwrap(api.post<ApiResponse<DeclarativeAgent>>(`/agents/declarative/${id}/duplicate`))
  },
  publish(id: string): Promise<{ status: string }> {
    return unwrap(api.post<ApiResponse<{ status: string }>>(`/agents/declarative/${id}/publish`))
  },
  deprecate(id: string): Promise<{ status: string }> {
    return unwrap(api.post<ApiResponse<{ status: string }>>(`/agents/declarative/${id}/deprecate`))
  },
  /** 软删除：status='deleted'，列表隐藏、历史对话仍可读 */
  remove(id: string): Promise<DeclarativeAgent> {
    return unwrap(api.delete<ApiResponse<DeclarativeAgent>>(`/agents/declarative/${id}`))
  },
  /** 上传首图（multipart，2MB 内、白名单图片类型）；出参带回新 cover_image_url */
  uploadCover(id: string, file: File): Promise<DeclarativeAgent> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(
      api.post<ApiResponse<DeclarativeAgent>>(`/agents/declarative/${id}/cover`, form),
    )
  },
  /** 清除首图（回退 emoji 大字） */
  clearCover(id: string): Promise<DeclarativeAgent> {
    return unwrap(api.delete<ApiResponse<DeclarativeAgent>>(`/agents/declarative/${id}/cover`))
  },
  /**
   * 建「与该助手对话」的会话：按 §10 契约带 target/target_code 锁定执行对象。
   * 后端尚未放行 target 字段时退回仅标题建会话——弹窗打不开比会话没锁定更糟。
   */
  async openChatSession(agentCode: string, title: string): Promise<AgentChatSession> {
    try {
      return await unwrap(
        api.post<ApiResponse<AgentChatSession>>(
          '/chat/sessions',
          { title, target: 'agent', target_code: agentCode },
          agentSilentCfg,
        ),
      )
    } catch {
      return unwrap(
        api.post<ApiResponse<AgentChatSession>>('/chat/sessions', { title }, agentSilentCfg),
      )
    }
  },
  reviewQueue(): Promise<DeclarativeAgent[]> {
    return unwrap(
      api.get<ApiResponse<{ agents: DeclarativeAgent[] } | DeclarativeAgent[]>>(
        '/agents/admin/agent-review-queue',
      ),
    ).then(r => (Array.isArray(r) ? r : r.agents))
  },
  review(id: string, decision: 'approve' | 'reject'): Promise<{ status: string }> {
    return unwrap(
      api.post<ApiResponse<{ status: string }>>(`/agents/admin/agent-review/${id}/${decision}`),
    )
  },
}


// ---------- Agent 工厂（行业描述 → 蓝图 → 采纳发布，ADR-0005） ----------

export type FactoryRunStatus = 'claimed' | 'planning' | 'generating' | 'completed' | 'failed'

/** 进度里程碑（SSE task-event 的 payload.milestone） */
export type FactoryMilestone = 'planning' | 'blueprint_ready' | 'generating' | 'completed' | 'failed'

/** 运行列表摘要（GET /agents/factory/runs） */
export interface FactoryRunSummary {
  id: string
  status: FactoryRunStatus
  industry_brief: string
  generated_by: string | null
  error: string | null
  created_by: string | null
  approved_by: string | null
  created_at: string | null
  updated_at: string | null
  role_count: number | null
  needs_knowledge_count: number | null
  research_used?: boolean
  research_sources?: number
}

/** 蓝图内单个角色（blueprint_json.agents 元素） */
export interface FactoryBlueprintAgent {
  agent_code: string
  name: string
  emoji: string | null
  description: string | null
  persona_prompt: string
  task_prompt_template: string
  allowed_tools: string[]
  knowledge_scopes: string[]
  suggested_questions: string[]
  agent_id: string | null
  knowledge_coverage: number
  needs_knowledge: boolean
  smoke_questions: string[]
  risk_level: 'low' | 'high'
  status: string
  /** 工作流阶段：analysis / plan / execution / delivery / review */
  stage?: string
  framework?: string
  /** 建议自动绑定的办公技能 code */
  suggested_skills?: string[]
}

/** 蓝图工作流：按阶段排序的接力顺序 */
export interface FactoryWorkflow {
  mode: string
  handoff: string
  stages: Array<{ key: string; label: string; roles: string[] }>
}

/** 联网情报（used=false 时 reason 说明降级原因） */
export interface FactoryResearch {
  used: boolean
  reason?: string
  queries?: string[]
  provider?: string
  sources?: Array<{ title: string; url: string; snippet: string; source: string }>
  summary?: string
  fetched_at?: string | null
}

/** 蓝图（schema: agent_factory_blueprint.v1） */
export interface FactoryBlueprint {
  schema: string
  industry_brief: string
  focus: string | null
  generated_by: string
  role_count: number
  workflow?: FactoryWorkflow
  research?: FactoryResearch
  agents: FactoryBlueprintAgent[]
}

/** 运行详情：摘要 + 完整蓝图（未生成时为 null） */
export interface FactoryRunDetail extends FactoryRunSummary {
  blueprint_json: FactoryBlueprint | null
}

/** POST /generate 返回的任务句柄 */
export interface FactoryGenerateResult {
  task_id: string
  run_id: string
}

/** 采纳逐项结果 */
export interface FactoryApplyItem {
  agent_code: string
  agent_id: string | null
  ok: boolean
  status: string | null
  error?: string
}

/** 采纳汇总 */
export interface FactoryApplyResult {
  run_id: string
  publish: boolean
  results: FactoryApplyItem[]
  requested: number
  applied: number
  failed: number
  published: number
  pending_review: number
  draft: number
  // W2 装配即组队：as_team 时后端回传建好的班组；建队失败只回错误码、不回滚采纳结果
  team?: TeamDefinition | null
  team_error?: string | null
}

export const factoryApi = {
  /** 发起生成（yard_admin；202 返回任务句柄；超配额 429 QUOTA_EXCEEDED） */
  generate(payload: { industry_brief: string; focus?: string }): Promise<FactoryGenerateResult> {
    return unwrap(api.post<ApiResponse<FactoryGenerateResult>>('/agents/factory/generate', payload))
  },
  /** 本小院运行列表（新→旧，不含蓝图正文） */
  runs(): Promise<FactoryRunSummary[]> {
    return unwrap(
      api.get<ApiResponse<{ runs: FactoryRunSummary[] }>>('/agents/factory/runs'),
    ).then(r => r.runs)
  },
  /** 运行详情（含 blueprint_json） */
  run(run_id: string): Promise<FactoryRunDetail> {
    return unwrap(api.get<ApiResponse<FactoryRunDetail>>(`/agents/factory/runs/${run_id}`))
  },
  /** 人工采纳：仅创建者或 yard_admin；run 非 completed 时 409；as_team 装配即组队 */
  apply(
    run_id: string,
    codes: string[],
    publish: boolean,
    as_team?: { name: string; description?: string; model_hint?: string },
  ): Promise<FactoryApplyResult> {
    return unwrap(
      api.post<ApiResponse<FactoryApplyResult>>(`/agents/factory/runs/${run_id}/apply`, {
        codes,
        publish,
        ...(as_team ? { as_team } : {}),
      }),
    )
  },
}


// ---------- Agent 市场（平台精选 → 一键装载到本小院） ----------

/** 精选目录的分类域（固定八类） */
export const MARKET_DOMAINS = [
  '种植技术',
  '棚室管理',
  '植保',
  '农机',
  '畜牧水产',
  '政策惠农',
  '经营营销',
  '科研助理',
] as const

export type MarketDomain = (typeof MARKET_DOMAINS)[number]

/** 目录卡片（GET /market/catalog → data.items 元素） */
export interface MarketAgent {
  agent_code: string
  name: string
  emoji: string | null
  description: string | null
  domain: MarketDomain
  risk_level: 'low' | 'high'
  installed: boolean
}

/** 装载结果（POST /market/install → data） */
export interface MarketInstallResult {
  installed: string[]
  skipped: string[]
  failed: { code: string; error: string }[]
}

export const marketApi = {
  /** 平台精选目录（含本小院 installed 标记） */
  catalog(): Promise<MarketAgent[]> {
    return unwrap(api.get<ApiResponse<{ items: MarketAgent[] }>>('/market/catalog')).then(
      r => r.items,
    )
  },
  /** 批量装载所选助手到本小院 */
  install(codes: string[]): Promise<MarketInstallResult> {
    return unwrap(api.post<ApiResponse<MarketInstallResult>>('/market/install', { codes }))
  },
}


export interface SupervisorChoice {
  kind: 'agent' | 'team'
  id: string
  code: string | null
  name: string
}
export interface SupervisorAskResult {
  choice: SupervisorChoice
  routed_by: 'rules' | 'llm' | 'fallback'
  reason: string
  result: { kind: string; answer?: string; status?: string; run_id?: string }
}
export interface SupervisorCandidate {
  kind: 'agent' | 'team'
  id: string
  code: string | null
  name: string
  blurb: string
  recent_calls: number
}
export const supervisorApi = {
  ask(question: string): Promise<SupervisorAskResult> {
    return unwrap(api.post<ApiResponse<SupervisorAskResult>>('/agents/supervisor/ask', { question }))
  },
  candidates(): Promise<{ candidates: SupervisorCandidate[] }> {
    return unwrap(api.get<ApiResponse<{ candidates: SupervisorCandidate[] }>>('/agents/supervisor/candidates'))
  },
}


/* ---------- 进化域 v1.3：AI 总结候选 + 声明式插件（DESIGN-v3 C 契约） ---------- */

/** workflow 插件入参定义（运行小对话框按此渲染） */

// ---------------------------------------------------------------------------
// 智能体可绑定扩展（v3.1：技能 / 插件分区绑定）
// ---------------------------------------------------------------------------

/** 可绑定的扩展技能；code 即绑定存储值（skill:<id>） */
export interface BindableSkill {
  code: string
  name: string
  summary: string
  source: 'builtin' | 'market' | 'generated' | 'imported'
  visibility: 'platform' | 'yard' | 'private'
  risk_level: 'low' | 'medium' | 'high'
  bound_count: number
}

/** 可绑定的扩展插件；code 即绑定存储值（plugin:<plugin_id>） */
export interface BindablePlugin {
  code: string
  name: string
  summary: string
  source: 'builtin' | 'market' | 'local'
  status: string
  risk_level: 'low' | 'medium' | 'high'
  bound_count: number
}

export interface BindableExtensions {
  skills: BindableSkill[]
  plugins: BindablePlugin[]
}

export const agentsApi = {
  /** 当前用户可绑定到智能体的扩展技能与插件（含来源 / 风险 / 已绑数量）；失败由调用方在区块内兜底 */
  bindableExtensions(): Promise<BindableExtensions> {
    return unwrap(
      api.get<ApiResponse<BindableExtensions>>('/agents/bindable-extensions', silent),
    )
  },
  /** 局部更新声明式智能体（v3.5：一句话生成后勾选技能 / 删除待补项用，不动其余字段） */
  updateDeclarative(
    id: string,
    payload: {
      bound_skills?: string[]
      skill_requirements?: { need: string; keywords?: string[]; reason?: string; status?: string }[]
    },
  ): Promise<Record<string, unknown>> {
    return unwrap(
      api.patch<ApiResponse<Record<string, unknown>>>(
        `/agents/declarative/${id}`,
        payload,
        silent,
      ),
    )
  },
}


// ---------------------------------------------------------------------------
// 一句话生成智能体（v3.5）：预览 → 202 异步任务（SSE 追进度）→ 草稿预览
// ---------------------------------------------------------------------------

/** 生成配额快照（向导第一步顶部展示；remaining=-1 表示不限） */
export interface AgentGeneratePolicy {
  enabled: boolean
  use_web: boolean
  used: number
  limit: number
  remaining: number
}

/** 预览命中的现有技能/插件候选（code 已带 skill:/plugin: 前缀） */
export interface AgentPreviewMatch {
  code: string
  name: string
  kind: 'skill' | 'plugin' | string
  summary: string
  source: string
  score: number
}

/** POST /agents/generate/preview：同步轻量预览（不调 LLM） */
export interface AgentPreviewResult {
  utterance: string
  keywords: string[]
  matches: AgentPreviewMatch[]
  /** 可直接自动绑定的最低分（后端 MATCH_THRESHOLD，避免两边口径漂移） */
  threshold?: number
  note: string
  policy: AgentGeneratePolicy
}

/** 生成结果里的已匹配项：生成时自动绑定，取消勾选走 PATCH bound_skills */
export interface AgentMatchedSkill {
  need: string
  code: string
  name: string
  kind: 'skill' | 'plugin' | string
  summary: string
  source: string
  score: number
  reason: string
}

/** 待补技能：生成时未匹配到，可编辑/删除（PATCH skill_requirements） */
export interface AgentSkillRequirement {
  need: string
  keywords: string[]
  reason: string
  status: string
}

/** 任务成功后 result_json 的形状（GET /tasks/{task_id}） */
export interface AgentGenerateTaskSummary {
  agent_id: string
  agent_code: string
  name: string
  status: string
  risk_level: string
  visibility: string
  matched: AgentMatchedSkill[]
  skill_requirements: AgentSkillRequirement[]
  sources: { title: string; url: string }[]
  offline: boolean
  note: string
  token_usage: { input_tokens?: number; output_tokens?: number; total_tokens?: number }
}

/** 轮询任务行时关心的字段（TaskOut 子集，其余原样透传） */
export interface AgentGenerateTaskRow {
  id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string
  result_json?: AgentGenerateTaskSummary | null
  error_message?: string | null
  [key: string]: unknown
}

/** 全部静默：配额/预览失败只在向导内提示，不弹全局错误 */
export const agentGenerateApi = {
  preview(utterance: string): Promise<AgentPreviewResult> {
    return unwrap(
      api.post<ApiResponse<AgentPreviewResult>>('/agents/generate/preview', { utterance }, silent),
    )
  },
  /** 后端返回 202 {task_id}；axios 把 2xx 一律当成功，无需特殊处理 */
  generate(payload: {
    utterance: string
    visibility: 'private' | 'public'
    use_web: boolean
    supplier_id?: string
  }): Promise<{ task_id: string }> {
    return unwrap(api.post<ApiResponse<{ task_id: string }>>('/agents/generate', payload))
  },
  policy(): Promise<AgentGeneratePolicy> {
    return unwrap(api.get<ApiResponse<AgentGeneratePolicy>>('/agents/generate/policy', silent))
  },
  /** SSE 断线降级轮询用：看 status 与 result_json */
  task(taskId: string): Promise<AgentGenerateTaskRow> {
    return unwrap(api.get<ApiResponse<AgentGenerateTaskRow>>(`/tasks/${taskId}`, silent))
  },
}

/**
 * 接口尚未上线（404 / 501）：v3.2 后端并行开发期间用于优雅降级，
 * 页面按「加载失败，重试」处理，向导/卡片展示原因而不是整页报错。
 */
export function isServiceNotReady(err: unknown): boolean {
  if (!isAxiosError(err)) return false
  const status = err.response?.status
  return status === 404 || status === 501
}

/** 技能相关接口的统一中文错误文案：服务未就绪时明确说明，其余走错误码映射 */
export function skillApiErrorText(err: unknown, feature = '该功能'): string {
  if (isServiceNotReady(err)) {
    return `${feature}暂未就绪（后端接口尚未上线），可稍后重试，或改用「上传 Skill 文件」导入现成技能。`
  }
  const { code, message } = extractErrorMessage(err)
  return friendlyErrorMessage(code, message)
}


// ---------------------------------------------------------------------------
// A2A 协作（v3.20）：对外名片、入站令牌、协作方与任务
// ---------------------------------------------------------------------------

export type A2aTaskStatus = 'submitted' | 'working' | 'completed' | 'failed' | 'canceled'

export interface A2aSkillInfo {
  id: string
  name: string
  description: string
}

export interface A2aCardSummary {
  name?: string | null
  description?: string
  url?: string | null
  version?: string | null
  skill_count: number
  skills: { id: string; name: string }[]
}

export interface A2aEndpointInfo {
  enabled: boolean
  token_prefix: string
  last_used_at?: string | null
}

export interface A2aStatus {
  enabled: boolean
  card_url: string
  rpc_url: string
  endpoint?: A2aEndpointInfo | null
  skills: A2aSkillInfo[]
}

export interface A2aPeer {
  id: string
  name: string
  base_url: string
  enabled: boolean
  token_configured: boolean
  card?: A2aCardSummary | null
  card_fetched_at?: string | null
  last_error?: string | null
  created_at?: string | null
}

export interface A2aPeerPayload {
  name: string
  base_url: string
  token?: string
  clear_token?: boolean
  enabled?: boolean
}

export interface A2aTask {
  id: string
  direction: 'inbound' | 'outbound'
  peer_id?: string | null
  skill_id?: string | null
  status: A2aTaskStatus
  error_code?: string | null
  error_message?: string | null
  remote_task_id?: string | null
  result_text?: string
  created_by?: string | null
  created_at?: string | null
  finished_at?: string | null
}

export const a2aApi = {
  status(): Promise<A2aStatus> {
    return unwrap(api.get<ApiResponse<A2aStatus>>('/settings/a2a'))
  },
  rotateToken(): Promise<{ token: string; token_prefix: string }> {
    return unwrap(
      api.post<ApiResponse<{ token: string; token_prefix: string }>>('/settings/a2a/token', {}),
    )
  },
  setInbound(enabled: boolean): Promise<{ enabled: boolean }> {
    return unwrap(api.patch<ApiResponse<{ enabled: boolean }>>('/settings/a2a', { enabled }))
  },
  listPeers(): Promise<A2aPeer[]> {
    return unwrap(api.get<ApiResponse<A2aPeer[]>>('/settings/a2a/peers'))
  },
  createPeer(payload: A2aPeerPayload): Promise<A2aPeer> {
    return unwrap(api.post<ApiResponse<A2aPeer>>('/settings/a2a/peers', payload))
  },
  updatePeer(id: string, payload: Partial<A2aPeerPayload>): Promise<A2aPeer> {
    return unwrap(api.patch<ApiResponse<A2aPeer>>(`/settings/a2a/peers/${id}`, payload))
  },
  removePeer(id: string): Promise<{ deleted: string }> {
    return unwrap(api.delete<ApiResponse<{ deleted: string }>>(`/settings/a2a/peers/${id}`))
  },
  probe(id: string): Promise<{ ok: boolean; card: A2aCardSummary; card_url: string }> {
    return unwrap(
      api.post<ApiResponse<{ ok: boolean; card: A2aCardSummary; card_url: string }>>(
        `/settings/a2a/peers/${id}/probe`,
        {},
      ),
    )
  },
  sendTask(id: string, payload: { skill_id: string; input_text: string }): Promise<A2aTask> {
    return unwrap(
      api.post<ApiResponse<A2aTask>>(`/settings/a2a/peers/${id}/tasks`, payload),
    )
  },
  tasks(params: { direction?: string; status?: string; limit?: number }): Promise<A2aTask[]> {
    return unwrap(api.get<ApiResponse<A2aTask[]>>('/settings/a2a/tasks', { params }))
  },
  task(id: string): Promise<A2aTask> {
    return unwrap(api.get<ApiResponse<A2aTask>>(`/settings/a2a/tasks/${id}`))
  },
}
