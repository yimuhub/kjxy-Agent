import type { AxiosResponse } from 'axios'
import { api } from './client'
import type {
  AddMemberPayload,
  AdminStats,
  AdminTenantList,
  AgentDefinition,
  AgentRunResult,
  ApiResponse,
  AuditLog,
  Candidate,
  ChunkHit,
  CreateTaskPayload,
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
    return unwrap(api.post<ApiResponse<LoginResponse>>('/auth/login', { username, password, tenant_id }))
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
  list(query: { status?: string; page?: number; page_size?: number } = {}): Promise<ListResult<ToolApproval>> {
    return unwrapList(api.get<ListResponse<ToolApproval>>('/tool-approvals', { params: toParams(query) }))
  },
  approve(approval_id: string, note?: string): Promise<{ approval: ToolApproval; tool_result: unknown }> {
    return unwrap(api.post<ApiResponse<{ approval: ToolApproval; tool_result: unknown }>>(`/tool-approvals/${approval_id}/approve`, { note }))
  },
  reject(approval_id: string, note?: string): Promise<{ approval: ToolApproval }> {
    return unwrap(api.post<ApiResponse<{ approval: ToolApproval }>>(`/tool-approvals/${approval_id}/reject`, { note }))
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
    return unwrapList<Skill>(api.get('/skills', { params: toParams({ status }) })).then(r => r.items)
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
  consecutive_failures: number
  test_time: string | null
  response_time_ms: number | null
  last_error: string | null
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
    return unwrap(api.get<ApiResponse<ApplicationDraft>>(`/policies/application-drafts/${draft_id}`))
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
