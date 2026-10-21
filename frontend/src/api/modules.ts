import type { AxiosRequestConfig, AxiosResponse } from 'axios'
import { api } from './client'
import type {
  AddMemberPayload,
  AdminStats,
  AdminTenantList,
  AgentDefinition,
  AgentRunResult,
  ApiResponse,
  AuditLog,
  BatchReviewResult,
  Candidate,
  ChunkHit,
  CreateTaskPayload,
  DocumentReparseResult,
  Feedback,
  KnowledgeDocument,
  KnowledgeSearchResult,
  KnowledgeUploadResult,
  ListResponse,
  LlmStatus,
  LoginResponse,
  Member,
  PageMeta,
  Policy,
  PolicyIngestPayload,
  PolicyMatchResult,
  PolicyProfile,
  ApplicationDraft,
  ApplicationDraftPayload,
  QAAnswerRequest,
  QAAnswerResult,
  QAOutput,
  ReviewCandidatePayload,
  ReviewSkillPayload,
  Skill,
  SubmitFeedbackPayload,
  Task,
  TaskEvent,
  Tenant,
  ToolApproval,
  ToolDefinition,
  MeResponse,
} from '@/types/api'

/** 解包成功信封 {data, request_id} -> data */
function unwrap<T>(promise: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  return promise.then(res => res.data.data)
}

export interface ListResult<T> {
  items: T[]
  meta: PageMeta
}

function unwrapList<T>(promise: Promise<AxiosResponse<ListResponse<T>>>): Promise<ListResult<T>> {
  return promise.then(res => ({ items: res.data.data, meta: res.data.meta }))
}

function toParams(params: object): Record<string, string | number> {
  const out: Record<string, string | number> = {}
  for (const [k, v] of Object.entries(params as Record<string, unknown>)) {
    if (v !== undefined && v !== null && v !== '') {
      out[k] = v as string | number
    }
  }
  return out
}

// ---------- 认证 ----------

export const authApi = {
  login(username: string, password: string, tenant_id?: string): Promise<LoginResponse> {
    return unwrap(
      api.post<ApiResponse<LoginResponse>>('/auth/login', { username, password, tenant_id }),
    )
  },
  refresh(refresh_token: string): Promise<{ access_token: string; refresh_token: string }> {
    return unwrap(api.post('/auth/refresh', { refresh_token }))
  },
  logout(): Promise<unknown> {
    return unwrap(api.post('/auth/logout'))
  },
  me(): Promise<MeResponse> {
    return unwrap(api.get<ApiResponse<MeResponse>>('/auth/me'))
  },
}

// ---------- 租户 ----------

export const tenantApi = {
  current(): Promise<Tenant> {
    return unwrap(api.get<ApiResponse<Tenant>>('/tenants/current'))
  },
  members(): Promise<ListResult<Member>> {
    return unwrapList(api.get<ListResponse<Member>>('/tenants/current/members'))
  },
  addMember(payload: AddMemberPayload): Promise<Member> {
    return unwrap(api.post<ApiResponse<Member>>('/tenants/current/members', payload))
  },
  removeMember(user_id: string): Promise<void> {
    return api.delete(`/tenants/current/members/${user_id}`).then(() => undefined)
  },
  /** 当前租户配额状态 */
  getQuota(): Promise<any> {
    return unwrap(api.get<ApiResponse<any>>('/tenants/quota'))
  },
  /** 公开：租户自助注册，返回可直接登录的 access_token */
  signup(payload: TenantSignupPayload): Promise<TenantSignupResult> {
    return unwrap(api.post<ApiResponse<TenantSignupResult>>('/tenants/signup', payload))
  },
}

// ---------- 租户自助注册 ----------

export interface TenantSignupPayload {
  tenant_name: string
  admin_username: string
  password: string
  contact?: string
}

export interface TenantSignupResult {
  tenant_id: string
  user_id: string
  access_token: string
  token_type: string
}

// ---------- 任务 ----------

export interface TaskListQuery {
  page?: number
  page_size?: number
  status?: string
}

export const taskApi = {
  create(payload: CreateTaskPayload): Promise<Task> {
    const { idempotencyKey, ...body } = payload
    return unwrap(
      api.post<ApiResponse<Task>>('/tasks', body, {
        headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
      }),
    )
  },
  list(query: TaskListQuery = {}): Promise<ListResult<Task>> {
    return unwrapList(api.get<ListResponse<Task>>('/tasks', { params: toParams(query) }))
  },
  get(task_id: string): Promise<Task> {
    return unwrap(api.get<ApiResponse<Task>>(`/tasks/${task_id}`))
  },
  events(task_id: string): Promise<TaskEvent[]> {
    return unwrap(api.get<ApiResponse<TaskEvent[]>>(`/tasks/${task_id}/events`))
  },
  cancel(task_id: string): Promise<Task> {
    return unwrap(api.post<ApiResponse<Task>>(`/tasks/${task_id}/cancel`))
  },
  retry(task_id: string): Promise<Task> {
    return unwrap(api.post<ApiResponse<Task>>(`/tasks/${task_id}/retry`))
  },
}

// ---------- 高风险工具审批 ----------

export const approvalApi = {
  list(
    query: { status?: string; page?: number; page_size?: number } = {},
  ): Promise<ListResult<ToolApproval>> {
    return unwrapList(
      api.get<ListResponse<ToolApproval>>('/tool-approvals', { params: toParams(query) }),
    )
  },
  approve(
    approval_id: string,
    note?: string,
  ): Promise<{ approval: ToolApproval; tool_result: unknown }> {
    return unwrap(
      api.post<ApiResponse<{ approval: ToolApproval; tool_result: unknown }>>(
        `/tool-approvals/${approval_id}/approve`,
        { note },
      ),
    )
  },
  reject(approval_id: string, note?: string): Promise<{ approval: ToolApproval }> {
    return unwrap(
      api.post<ApiResponse<{ approval: ToolApproval }>>(`/tool-approvals/${approval_id}/reject`, {
        note,
      }),
    )
  },
}

// ---------- 知识库 ----------

export const knowledgeApi = {
  upload(file: File, title?: string): Promise<KnowledgeUploadResult> {
    const form = new FormData()
    form.append('file', file)
    if (title) form.append('title', title)
    return unwrap(api.post<ApiResponse<KnowledgeUploadResult>>('/knowledge/documents', form))
  },
  list(query: { page?: number; page_size?: number } = {}): Promise<ListResult<KnowledgeDocument>> {
    return unwrapList(api.get('/knowledge/documents', { params: toParams(query) }))
  },
  get(document_id: string): Promise<KnowledgeDocument> {
    return unwrap(api.get(`/knowledge/documents/${document_id}`))
  },
  remove(document_id: string): Promise<void> {
    return api.delete(`/knowledge/documents/${document_id}`).then(() => undefined)
  },
  /** 重新解析：仅 failed 文档可调，返回新解析任务 */
  reparse(document_id: string): Promise<DocumentReparseResult> {
    return unwrap(
      api.post<ApiResponse<DocumentReparseResult>>(`/knowledge/documents/${document_id}/reparse`),
    )
  },
  search(query: string, top_k = 5): Promise<ChunkHit[]> {
    return unwrap<KnowledgeSearchResult>(
      api.post<ApiResponse<KnowledgeSearchResult>>('/knowledge/search', { query, top_k }),
    ).then(r => r.hits)
  },
  candidates(
    query: { status?: string; page?: number; page_size?: number } = { status: 'pending_review' },
  ): Promise<ListResult<Candidate>> {
    return unwrapList(api.get('/knowledge/candidates', { params: toParams(query) }))
  },
  reviewCandidate(candidate_id: string, payload: ReviewCandidatePayload): Promise<Candidate> {
    return unwrap(api.post(`/knowledge/candidates/${candidate_id}/review`, payload))
  },
  /** 批量审核候选知识：一次提交多个 ID 的通过/驳回 */
  batchReview(
    ids: string[],
    action: 'approve' | 'reject',
    review_note?: string,
  ): Promise<BatchReviewResult> {
    return unwrap(
      api.post<ApiResponse<BatchReviewResult>>('/knowledge/candidates/batch-review', {
        ids,
        action,
        review_note,
      }),
    )
  },
}

// ---------- 问答 ----------

export const qaApi = {
  async answer(payload: QAAnswerRequest): Promise<QAAnswerResult> {
    const data = await unwrap<Record<string, unknown>>(api.post('/qa/answers', payload))
    if (data && typeof data === 'object' && 'answer' in data) {
      return { kind: 'sync', output: data as unknown as QAOutput }
    }
    const task_id = (data as { task_id?: string })?.task_id ?? ''
    return { kind: 'async', task_id }
  },
}

// ---------- 反馈 ----------

export const feedbackApi = {
  submit(payload: SubmitFeedbackPayload): Promise<Feedback> {
    return unwrap(api.post<ApiResponse<Feedback>>('/feedback', payload))
  },
  list(query: { page?: number; page_size?: number } = {}): Promise<ListResult<Feedback>> {
    return unwrapList(api.get('/feedback', { params: toParams(query) }))
  },
}

// ---------- Skill ----------

export const skillApi = {
  list(status?: string): Promise<Skill[]> {
    return unwrapList<Skill>(api.get('/skills', { params: toParams({ status }) })).then(
      r => r.items,
    )
  },
  create(payload: Record<string, unknown>): Promise<Skill> {
    return unwrap(api.post<ApiResponse<Skill>>('/skills', payload))
  },
  review(skill_id: string, payload: ReviewSkillPayload): Promise<Skill> {
    return unwrap(api.post(`/skills/${skill_id}/review`, payload))
  },
}

// ---------- 模型 / 工具 ----------

export const llmApi = {
  status(): Promise<LlmStatus> {
    return unwrap(api.get<ApiResponse<LlmStatus>>('/llm/status'))
  },
}

export const toolApi = {
  list(): Promise<ToolDefinition[]> {
    return unwrapList<ToolDefinition>(api.get('/tools')).then(r => r.items)
  },
}

// ---------- 模型供应商（架构补强 A 组） ----------

export interface LlmSupplier {
  id: string
  tenant_id: string | null
  name: string
  provider_type: string
  base_url: string
  model_id: string
  api_key_masked: string
  models: string | null
  priority: number
  status: number // 1=启用 2=禁用
  is_active: boolean
  // function-calling 能力位：true 才允许该供应商的助手进入工具调用循环（后端恒返回）
  supports_tools?: boolean
  consecutive_failures: number
  test_time: string | null
  response_time_ms: number | null
  last_error: string | null
  // 后端权限信号：tenant_admin 视角下平台全局行=true（只可选用不可改）；缺省按非只读兼容
  readonly?: boolean
  created_at: string
  updated_at: string
}

export interface LlmSupplierCreate {
  name: string
  provider_type: string
  base_url: string
  model_id: string
  api_key: string
  models?: string
  priority?: number
  scope?: 'tenant' | 'global'
}

export interface LlmSupplierUpdate {
  name?: string
  provider_type?: string
  base_url?: string
  model_id?: string
  api_key?: string
  models?: string
  priority?: number
  // function-calling 能力位开关（PUT 语义：传则覆盖）
  supports_tools?: boolean
}

export interface LlmTestResult {
  ok: boolean
  latency_ms: number
  models: string[]
  note: string
}

export interface LlmProviderTemplate {
  provider_type: string
  label: string
  base_url: string
  model_id: string
  note: string
}

export const llmSupplierApi = {
  /** 供应商列表（Key 已打码） */
  list(): Promise<LlmSupplier[]> {
    return unwrap(api.get<ApiResponse<LlmSupplier[]>>('/llm/providers'))
  },
  create(payload: LlmSupplierCreate): Promise<LlmSupplier> {
    return unwrap(api.post<ApiResponse<LlmSupplier>>('/llm/providers', payload))
  },
  update(id: string, payload: LlmSupplierUpdate): Promise<LlmSupplier> {
    return unwrap(api.put<ApiResponse<LlmSupplier>>(`/llm/providers/${id}`, payload))
  },
  remove(id: string): Promise<void> {
    return api.delete(`/llm/providers/${id}`).then(() => undefined)
  },
  enable(id: string): Promise<LlmSupplier> {
    return unwrap(api.post<ApiResponse<LlmSupplier>>(`/llm/providers/${id}/enable`))
  },
  disable(id: string): Promise<LlmSupplier> {
    return unwrap(api.post<ApiResponse<LlmSupplier>>(`/llm/providers/${id}/disable`))
  },
  /** 未落库候选配置的连通性测试 */
  testCandidate(payload: {
    provider_type: string
    base_url: string
    model_id: string
    api_key: string
  }): Promise<LlmTestResult> {
    return unwrap(api.post<ApiResponse<LlmTestResult>>('/llm/providers/test', payload))
  },
  /** 已存供应商测试（结果写回） */
  test(id: string): Promise<{ supplier: LlmSupplier; result: LlmTestResult }> {
    return unwrap(api.post(`/llm/providers/${id}/test`))
  },
  /** 设为当前生效供应商（租户级） */
  setActive(supplier_id: string): Promise<LlmSupplier> {
    return unwrap(api.post<ApiResponse<LlmSupplier>>('/llm/providers/active', { supplier_id }))
  },
  /** 预置模板 */
  templates(): Promise<LlmProviderTemplate[]> {
    return unwrap(api.get<ApiResponse<LlmProviderTemplate[]>>('/llm/provider-templates'))
  },
}

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
  tenants(): Promise<AdminTenantList> {
    return unwrap(api.get<ApiResponse<AdminTenantList>>('/admin/tenants'))
  },
}

// ===== 协作班组（W2 契约，字段以 2026-10-21 实测为准） =====

/** 班组成员定义（有序数组，2..8 个；顺序即接力顺序） */
export interface TeamMemberSpec {
  agent_id: string
  role_name: string
  instruction: string
  is_critic: boolean
}

/** 班组定义（GET/POST/PATCH /agents/teams* 的 data 形状） */
export interface TeamDefinition {
  id: string
  tenant_id: string
  name: string
  model_hint: string | null
  description: string | null
  members: TeamMemberSpec[]
  enabled: boolean
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

export type TeamRunStatus = 'running' | 'completed' | 'failed'
export type TeamMemberRunStatus = 'pending' | 'running' | 'ok' | 'error'

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
}

/** 运行详情（POST run / GET team-runs/{id} / POST retry 的 data 共同形状） */
export interface TeamRun {
  id: string
  team_id: string
  tenant_id: string
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
}

/** 运行列表摘要（GET /agents/teams/{id}/runs 元素，无 answer/members） */
export interface TeamRunSummary {
  id: string
  team_id: string
  status: TeamRunStatus
  input_preview: string
  current_member: number
  member_count: number
  last_error: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
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
  /** 班组列表（租户成员均可读） */
  list(): Promise<TeamDefinition[]> {
    return unwrap(api.get<ApiResponse<{ teams: TeamDefinition[] }>>('/agents/teams')).then(
      r => r.teams,
    )
  },
  /** 创建（tenant_admin；成员 2..8；同租户重名 409） */
  create(payload: TeamCreatePayload): Promise<TeamDefinition> {
    return unwrap(api.post<ApiResponse<TeamDefinition>>('/agents/teams', payload))
  },
  /** 编辑（tenant_admin；members 传则整体替换） */
  update(teamId: string, payload: TeamUpdatePayload): Promise<TeamDefinition> {
    return unwrap(api.patch<ApiResponse<TeamDefinition>>(`/agents/teams/${teamId}`, payload))
  },
  /** 删除班组定义（tenant_admin；历史运行台账保留） */
  remove(teamId: string): Promise<void> {
    return api.delete(`/agents/teams/${teamId}`).then(() => undefined)
  },
  /** 同步跑一次接力（全员可用；失败时 HTTP 抛原始错误、run 停在 failed 断点） */
  run(teamId: string, input: string): Promise<TeamRun> {
    return unwrap(api.post<ApiResponse<TeamRun>>(`/agents/teams/${teamId}/run`, { input }))
  },
  /** 本班组运行列表（摘要，最新在前） */
  runs(teamId: string): Promise<TeamRunSummary[]> {
    return unwrap(
      api.get<ApiResponse<{ runs: TeamRunSummary[] }>>(`/agents/teams/${teamId}/runs`),
    ).then(r => r.runs)
  },
  /** 运行详情（含成员接力明细） */
  runDetail(runId: string): Promise<TeamRun> {
    return unwrap(api.get<ApiResponse<TeamRun>>(`/agents/team-runs/${runId}`))
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

// ---------- 专业 Agent ----------

export const agentApi = {
  list(): Promise<AgentDefinition[]> {
    return unwrap(api.get<ApiResponse<{ agents: AgentDefinition[] }>>('/agents')).then(
      r => r.agents,
    )
  },
  run(code: string, input: Record<string, unknown>): Promise<AgentRunResult> {
    return unwrap(api.post<ApiResponse<AgentRunResult>>(`/agents/${code}/run`, { input }))
  },
}

// ---------- 政策模块（第三期） ----------

export interface PolicyListQuery {
  page?: number
  page_size?: number
  keyword?: string
  province?: string
  policy_type?: string
}

export const policyApi = {
  /** 政策列表（分页） */
  list(query: PolicyListQuery = {}): Promise<ListResult<Policy>> {
    return unwrapList(api.get<ListResponse<Policy>>('/policies', { params: toParams(query) }))
  },
  /** 政策详情 */
  get(policy_id: string): Promise<Policy> {
    return unwrap(api.get<ApiResponse<Policy>>(`/policies/${policy_id}`))
  },
  /** 政策采集入库；重复返回 409 POLICY_DUPLICATE */
  ingest(payload: PolicyIngestPayload): Promise<Policy> {
    return unwrap(api.post<ApiResponse<Policy>>('/policies', payload))
  },
  /** 政策规则匹配：只做规则比对，不做成功率预测 */
  match(profile: PolicyProfile): Promise<PolicyMatchResult[]> {
    return unwrap(api.post<ApiResponse<PolicyMatchResult[]>>('/policies/match', profile))
  },
  /** 生成申报材料草案（恒为 draft） */
  createDraft(policy_id: string, payload: ApplicationDraftPayload): Promise<ApplicationDraft> {
    return unwrap(
      api.post<ApiResponse<ApplicationDraft>>(`/policies/${policy_id}/application-drafts`, payload),
    )
  },
  /** 查看申报材料草案（租户隔离） */
  getDraft(draft_id: string): Promise<ApplicationDraft> {
    return unwrap(
      api.get<ApiResponse<ApplicationDraft>>(`/policies/application-drafts/${draft_id}`),
    )
  },
}

// ---------- 自生长管理（第四期） ----------

export const evolutionApi = {
  /** Skill 列表 */
  listSkills(): Promise<any[]> {
    return unwrap(api.get<any>('/skills')).then((r: any) => r.data ?? r)
  },
  /** 设置灰度 */
  setRollout(skill_id: string, payload: Record<string, unknown>): Promise<unknown> {
    return unwrap(api.post(`/skills/${skill_id}/rollout`, payload))
  },
  /** 查询灰度状态 */
  getRollout(skill_id: string): Promise<unknown> {
    return unwrap(api.get(`/skills/${skill_id}/rollout`))
  },
  /** 灰度全量 */
  promoteRollout(skill_id: string): Promise<unknown> {
    return unwrap(api.post(`/skills/${skill_id}/rollout/promote`, {}))
  },
  /** 暂停灰度 */
  haltRollout(skill_id: string): Promise<unknown> {
    return unwrap(api.post(`/skills/${skill_id}/rollout/halt`, {}))
  },
  /** 门禁历史 */
  listEvalRuns(skill_id: string): Promise<any[]> {
    return unwrap(api.get<any>(`/skills/${skill_id}/eval-runs`)).then((r: any) => r.data ?? r)
  },
  /** 插件列表 */
  listPlugins(): Promise<any[]> {
    return unwrap(api.get<any>('/plugins')).then((r: any) => r.data ?? r.plugins ?? r)
  },
  /** 扫描插件 */
  scanPlugins(): Promise<unknown> {
    return unwrap(api.post('/plugins/scan', {}))
  },
  /** 启用插件 */
  enablePlugin(plugin_id: string): Promise<unknown> {
    return unwrap(api.post(`/plugins/${plugin_id}/enable`, {}))
  },
  /** 禁用插件 */
  disablePlugin(plugin_id: string): Promise<unknown> {
    return unwrap(api.post(`/plugins/${plugin_id}/disable`, {}))
  },
}

// ===== 声明式 Agent 与 Agent 工厂（能力中心，ADR-0005） =====
export interface DeclarativeAgent {
  id: string
  tenant_id: string
  agent_code: string
  name: string
  description: string | null
  emoji: string | null
  persona_prompt: string
  task_prompt_template: string
  allowed_tools: string[]
  knowledge_scopes: string[]
  supplier_id: string | null
  risk_level: 'low' | 'high'
  status: 'draft' | 'pending_platform_review' | 'published' | 'deprecated'
  version: string
  created_from: string
  created_at: string
  updated_at: string
}

export interface DeclarativeAgentCreate {
  agent_code: string
  name: string
  description?: string
  emoji?: string
  persona_prompt: string
  task_prompt_template: string
  allowed_tools?: string[]
  knowledge_scopes?: string[]
  supplier_id?: string | null
}

export const declarativeApi = {
  list(): Promise<DeclarativeAgent[]> {
    return unwrap(
      api.get<ApiResponse<{ agents: DeclarativeAgent[] }>>('/agents/declarative'),
    ).then(r => r.agents)
  },
  create(payload: DeclarativeAgentCreate): Promise<DeclarativeAgent> {
    return unwrap(api.post<ApiResponse<DeclarativeAgent>>('/agents/declarative', payload))
  },
  update(id: string, payload: Partial<DeclarativeAgentCreate>): Promise<DeclarativeAgent> {
    return unwrap(api.patch<ApiResponse<DeclarativeAgent>>(`/agents/declarative/${id}`, payload))
  },
  publish(id: string): Promise<{ status: string }> {
    return unwrap(api.post<ApiResponse<{ status: string }>>(`/agents/declarative/${id}/publish`))
  },
  deprecate(id: string): Promise<{ status: string }> {
    return unwrap(api.post<ApiResponse<{ status: string }>>(`/agents/declarative/${id}/deprecate`))
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
}

/** 蓝图（schema: agent_factory_blueprint.v1） */
export interface FactoryBlueprint {
  schema: string
  industry_brief: string
  focus: string | null
  generated_by: string
  role_count: number
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
  /** 发起生成（tenant_admin；202 返回任务句柄；超配额 429 QUOTA_EXCEEDED） */
  generate(payload: { industry_brief: string; focus?: string }): Promise<FactoryGenerateResult> {
    return unwrap(api.post<ApiResponse<FactoryGenerateResult>>('/agents/factory/generate', payload))
  },
  /** 本租户运行列表（新→旧，不含蓝图正文） */
  runs(): Promise<FactoryRunSummary[]> {
    return unwrap(
      api.get<ApiResponse<{ runs: FactoryRunSummary[] }>>('/agents/factory/runs'),
    ).then(r => r.runs)
  },
  /** 运行详情（含 blueprint_json） */
  run(run_id: string): Promise<FactoryRunDetail> {
    return unwrap(api.get<ApiResponse<FactoryRunDetail>>(`/agents/factory/runs/${run_id}`))
  },
  /** 人工采纳：仅创建者或 tenant_admin；run 非 completed 时 409；as_team 装配即组队 */
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

// ---------- Agent 市场（平台精选 → 一键装载到本租户） ----------

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
  /** 平台精选目录（含本租户 installed 标记） */
  catalog(): Promise<MarketAgent[]> {
    return unwrap(api.get<ApiResponse<{ items: MarketAgent[] }>>('/market/catalog')).then(
      r => r.items,
    )
  },
  /** 批量装载所选助手到本租户 */
  install(codes: string[]): Promise<MarketInstallResult> {
    return unwrap(api.post<ApiResponse<MarketInstallResult>>('/market/install', { codes }))
  },
}

// ---------- 设备中心（物联网接入，A3） ----------

/** 指标字典条目 */
export interface DeviceMetric {
  code: string
  name: string
  unit: string
}

/** 通用 value/name 字典项（设备类型、接入方式、阈值方向） */
export interface DeviceDictItem {
  value: string
  name: string
}

export interface DeviceCapabilities {
  metrics: DeviceMetric[]
  device_types: DeviceDictItem[]
  source_types: DeviceDictItem[]
  ops?: DeviceDictItem[]
}

/** 连接器字段映射条目（厂商字段 → 统一指标） */
export interface DeviceFieldMapEntry {
  metric: string
  unit?: string | null
}

/** 连接器配置（写入形态：headers 为明文，入库加密） */
export interface DeviceConnectorIn {
  url: string
  method: 'GET' | 'POST'
  headers?: Record<string, string> | null
  body_template?: Record<string, unknown> | null
  interval_seconds: number
  json_path: string
  field_map: Record<string, DeviceFieldMapEntry>
  timeout_seconds: number
  time_field?: string | null
}

/** 连接器配置（读出形态：headers 一律掩码） */
export interface DeviceConnectorOut extends Omit<DeviceConnectorIn, 'headers'> {
  headers_masked: Record<string, string>
}

/** 每指标最新一条读数（仅 GET /devices/{id} 详情返回；列表接口没有） */
export interface DeviceLatestItem {
  metric: string
  metric_name: string
  value: number | null
  text: string | null
  unit: string | null
  collected_at: string
}

export interface Device {
  id: string
  tenant_id: string
  name: string
  device_type: string
  source_type: string
  model_hint: string | null
  longitude: number | null
  latitude: number | null
  location_note: string | null
  enabled: boolean
  created_by: string | null
  created_at: string
  updated_at: string
  has_ingest_token: boolean
  connector: DeviceConnectorOut | null
  last_polled_at: string | null
  consecutive_failures: number
  last_error: string | null
  /** 仅详情接口返回 */
  latest?: DeviceLatestItem[]
}

export interface DeviceCreatePayload {
  name: string
  device_type: string
  source_type: 'webhook' | 'connector'
  model_hint?: string
  location_note?: string
  longitude?: number | null
  latitude?: number | null
  connector?: DeviceConnectorIn | null
}

export interface DevicePatchPayload {
  name?: string
  device_type?: string
  model_hint?: string | null
  longitude?: number | null
  latitude?: number | null
  location_note?: string | null
  enabled?: boolean
  connector?: DeviceConnectorIn | null
}

/** 读数组（GET readings → data[:] 裸数组，时间升序），raw_json 体积大不拉回 */
export interface DeviceReading {
  id: string
  metric: string
  value: number | null
  text: string | null
  unit: string | null
  collected_at: string
}

export interface DeviceAlertRule {
  id: string
  tenant_id: string
  device_id: string | null
  metric: string
  op: 'lt' | 'gt'
  threshold: number
  enabled: boolean
  notify_note: string | null
  created_at: string
  updated_at: string
}

export interface DeviceAlertRuleCreate {
  metric: string
  op: 'lt' | 'gt'
  threshold: number
  device_id?: string | null
  enabled?: boolean
  notify_note?: string | null
}

export interface DeviceAlert {
  id: string
  tenant_id: string
  rule_id: string | null
  device_id: string
  device_name: string | null
  metric: string
  metric_name: string | null
  value: number | null
  triggered_at: string
  notified_text: string | null
  /** 一键工单：tasks 任务 id；null=尚未开工单（workorder_no 为展示编号，当前同值） */
  workorder_id: string | null
  workorder_no: string | null
}

export interface AlertWorkorder {
  workorder_id: string
  workorder_no: string
  status: string
  created: boolean
  link: string
  message: string
}

export const deviceApi = {
  /** 能力字典：指标 18 项 + 设备类型 + 接入方式 + 阈值方向 */
  capabilities(): Promise<DeviceCapabilities> {
    return unwrap(api.get<ApiResponse<DeviceCapabilities>>('/devices/capabilities'))
  },
  /** 设备列表（无 latest；最新值需逐台调 detail 补齐） */
  list(): Promise<Device[]> {
    return unwrap(api.get<ApiResponse<Device[]>>('/devices'))
  },
  /** 设备详情（含 latest + 连接器掩码配置） */
  detail(device_id: string): Promise<Device> {
    return unwrap(api.get<ApiResponse<Device>>(`/devices/${device_id}`))
  },
  /** 新建：webhook 时 data.device_token 一次性明文，仅本次响应出现 */
  create(payload: DeviceCreatePayload): Promise<Device & { device_token?: string }> {
    return unwrap(api.post<ApiResponse<Device & { device_token?: string }>>('/devices', payload))
  },
  patch(device_id: string, payload: DevicePatchPayload): Promise<Device> {
    return unwrap(api.patch<ApiResponse<Device>>(`/devices/${device_id}`, payload))
  },
  remove(device_id: string): Promise<void> {
    return api.delete(`/devices/${device_id}`).then(() => undefined)
  },
  /** 重新生成设备令牌：data.device_token 同样只展示这一次 */
  rotateToken(device_id: string): Promise<{ device_token: string }> {
    return unwrap(api.post<ApiResponse<{ device_token: string }>>(`/devices/${device_id}/rotate-token`))
  },
  /** 读数（时间升序裸数组；limit 上限 1000） */
  readings(
    device_id: string,
    opts: { metric?: string; start?: string; end?: string; limit?: number } = {},
  ): Promise<DeviceReading[]> {
    return unwrap(
      api.get<ApiResponse<DeviceReading[]>>(`/devices/${device_id}/readings`, {
        params: toParams(opts),
      }),
    )
  },
  rules(): Promise<DeviceAlertRule[]> {
    return unwrap(api.get<ApiResponse<DeviceAlertRule[]>>('/devices/alert-rules'))
  },
  createRule(payload: DeviceAlertRuleCreate): Promise<DeviceAlertRule> {
    return unwrap(api.post<ApiResponse<DeviceAlertRule>>('/devices/alert-rules', payload))
  },
  patchRule(rule_id: string, payload: Partial<DeviceAlertRuleCreate>): Promise<DeviceAlertRule> {
    return unwrap(api.patch<ApiResponse<DeviceAlertRule>>(`/devices/alert-rules/${rule_id}`, payload))
  },
  removeRule(rule_id: string): Promise<void> {
    return api.delete(`/devices/alert-rules/${rule_id}`).then(() => undefined)
  },
  alerts(limit = 50): Promise<DeviceAlert[]> {
    return unwrap(
      api.get<ApiResponse<DeviceAlert[]>>('/devices/alerts', { params: { limit } }),
    )
  },
  /** 告警一键开工单（全员可用；幂等——已开过的返回既有工单号 created=false） */
  createAlertWorkorder(alert_id: string): Promise<AlertWorkorder> {
    return unwrap(
      api.post<ApiResponse<AlertWorkorder>>(`/devices/alerts/${alert_id}/create-workorder`),
    )
  },
}
