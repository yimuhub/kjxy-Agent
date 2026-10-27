// 与 contracts/openapi.yaml 同步，修改契约时同步更新。
// 字段来源：contracts/openapi.yaml、contracts/json-schema/*.json、
// contracts/examples/*.json、contracts/error-codes.md。
// openapi.yaml 中仅声明为 object 的响应体，其字段标注为"契约未细化"的约定字段，
// 均为可选；后端实现后以契约为准补充。

/** 成功响应信封：单对象 */
export interface ApiResponse<T> {
  data: T
  request_id: string
}

/** 成功响应信封：列表 */
export interface ListResponse<T> {
  data: T[]
  meta: PageMeta
  request_id: string
}

/** 列表分页元信息 */
export interface PageMeta {
  page: number
  page_size: number
  total: number
}

/** 错误响应信封（见 contracts/error-codes.md） */
export interface ApiError {
  error: {
    code: string
    message: string
    details?: Record<string, unknown>
  }
  request_id?: string
}

// ---------- 认证 ----------

export interface User {
  /** 后端 UserOut：/auth/login、/auth/me 均返回 */
  id?: string
  user_id?: string
  username?: string
  email?: string | null
  phone?: string | null
  display_name?: string
  role?: string
  status?: string
  must_change_password?: boolean
  last_login_at?: string | null
  created_at?: string
}

/** 登录/me 返回的小院归属（后端 YardBrief） */
export interface YardBrief {
  yard_id: string
  yard_name: string
  roles: string[]
}

/** /yards/current 返回的小院详情（后端 YardOut） */
export interface Yard {
  yard_id: string
  name: string
  roles?: string[]
  status?: string
  province?: string | null
  city?: string | null
  scene_type?: string | null
}

export interface LoginResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: User
  /** 登录响应附带调用者角色（平台管理员 bootstrap 后为最终角色） */
  roles?: string[]
  yards: YardBrief[]
}

/** /auth/me 返回：当前用户 + 角色 + 小院列表 */
export interface MeResponse {
  user: User
  roles: string[]
  yards: YardBrief[]
}

/** 高风险工具审批单 */
export interface ToolApproval {
  id?: string
  yard_id?: string
  tool_code?: string
  params?: Record<string, unknown>
  status?: 'pending' | 'approved' | 'rejected' | 'cancelled'
  requested_by?: string
  task_id?: string | null
  reason?: string | null
  decided_by?: string | null
  decided_at?: string | null
  decision_note?: string | null
  created_at?: string
}

/** 小院成员；契约仅声明端点存在，字段为约定字段 */
export interface Member {
  user_id?: string
  username?: string
  display_name?: string
  email?: string | null
  role?: MemberRole
  status?: string
  joined_at?: string
}

export type MemberRole = 'yard_admin' | 'expert' | 'operator' | 'viewer'

export interface AddMemberPayload {
  username: string
  role: MemberRole
  display_name?: string
}

/** 成员邀请链接（管理端列表项；invite_path 仅创建响应返回一次） */
export interface Invitation {
  id: string
  role: MemberRole
  note: string | null
  status: 'pending' | 'accepted' | 'revoked' | 'expired'
  expires_at: string
  created_at: string
  created_by?: string | null
  accepted_at?: string | null
  invite_path?: string | null
}

/** 公开邀请预览（接受页展示小院名与角色） */
export interface InvitationPreview {
  yard_id: string
  yard_name: string
  role: MemberRole
  note: string | null
  expires_at: string
}

/** 批量添加成员结果：逐条独立成败 */
export interface BatchAddMembersResult {
  added: Member[]
  failed: Array<{ username: string; error_code: string; message: string }>
}

// ---------- 任务 ----------

export type TaskStatus =
  'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'awaiting_review'

export type TaskPriority = 'low' | 'normal' | 'high'

/** 契约仅声明 create 的请求体字段；响应字段为约定字段 */
export interface Task {
  id: string
  task_type: string
  status: TaskStatus
  priority?: TaskPriority
  input?: Record<string, unknown>
  output?: Record<string, unknown> | null
  progress?: number | null
  error_code?: string | null
  error_message?: string | null
  yard_id?: string
  created_by?: string
  created_at?: string
  updated_at?: string
  started_at?: string | null
  finished_at?: string | null
}

export interface CreateTaskPayload {
  task_type: string
  input: Record<string, unknown>
  priority?: TaskPriority
  idempotencyKey?: string
}

/** 任务进度事件；契约仅声明端点，字段为约定字段 */
export interface TaskEvent {
  id?: string
  task_id?: string
  seq?: number
  event_type?: string
  message?: string
  progress?: number | null
  payload?: Record<string, unknown> | null
  created_at?: string
}

// ---------- 知识库 ----------

export type DocumentStatus = 'pending' | 'parsing' | 'succeeded' | 'failed' | string

/** 契约未细化文档字段，以下为约定字段 */
export interface KnowledgeDocument {
  id: string
  title?: string
  filename?: string
  file_type?: string
  status: DocumentStatus
  error_message?: string | null
  chunk_count?: number | null
  token_count?: number | null
  page_count?: number | null
  created_at?: string
  updated_at?: string
}

export interface KnowledgeChunkPreview {
  id: string
  chunk_index: number
  content: string
  title_path?: string | null
  page_number?: number | null
  token_count: number
  trust_level?: string
}

export interface KnowledgeUploadResult {
  /** 契约仅声明 201（已创建解析任务），字段为约定字段 */
  document_id?: string
  task_id?: string
}

/** 检索命中，与 contracts/json-schema/knowledge-search-output.schema.json 一一对应 */
export interface ChunkHit {
  chunk_id: string
  document_id: string
  title: string
  source: string
  quote: string
  score: number
  trust_level: string
  page_number: number | null
  token_count?: number | null
  title_path?: string | null
}

export interface KnowledgeSearchResult {
  hits: ChunkHit[]
}

export type CandidateStatus = 'pending_review' | 'approved' | 'rejected' | string

/** 待审核知识；契约未细化字段，以下为约定字段 */
export interface Candidate {
  id: string
  title?: string
  content?: string
  source?: string
  status: CandidateStatus
  review_note?: string | null
  reviewed_by?: string | null
  created_at?: string
}

export type ReviewAction = 'approve' | 'reject'

export interface ReviewCandidatePayload {
  action: ReviewAction
  note?: string
}

/** 批量审核结果：approved/rejected 为成功候选 ID，failed 为逐条失败原因 */
export interface BatchReviewResult {
  approved: string[]
  rejected: string[]
  failed: Array<{ id: string; error_code: string }>
}

/** 文档重新解析结果（POST /knowledge/documents/{id}/reparse，仅 failed 文档可调） */
export interface DocumentReparseResult {
  task_id: string
  status: string
}

// ---------- 问答 ----------

export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  rag_tokens?: number
  model?: string | null
  latency_ms?: number | null
  estimated?: boolean
}

/** 与 contracts/json-schema/qa-output.schema.json 一一对应 */
export interface QAOutput {
  answer: string
  citations: Citation[]
  warnings: string[]
  needs_expert_review: boolean
  token_usage?: TokenUsage | null
}

/** 与 qa-output.schema.json 中 citations 元素一一对应 */
export interface Citation {
  chunk_id: string
  document_id: string
  title: string
  source: string
  quote: string
  page_number: number | null
}

export interface QAAnswerRequest {
  question: string
  top_k?: number
  mode?: 'sync' | 'async'
  crop_type?: string
}

/** /qa/answers 200：sync 返回结构化答案，async 返回任务 ID */
export type QAAnswerResult = { kind: 'sync'; output: QAOutput } | { kind: 'async'; task_id: string }

// ---------- 反馈 ----------

/** 契约仅声明请求体字段，响应字段为约定字段 */
export interface Feedback {
  id?: string
  task_id?: string | null
  rating?: 'up' | 'down' | null
  feedback_text?: string | null
  corrected_answer?: string | null
  created_by?: string
  created_at?: string
}

export interface SubmitFeedbackPayload {
  task_id?: string
  rating?: 'up' | 'down'
  feedback_text?: string
  corrected_answer?: string
}

// ---------- Skill ----------

/** 技能归属：私有（仅创建者/负责人）/ 小院共享 / 平台共享（v3.2 归属模型） */
export type SkillVisibility = 'private' | 'yard' | 'platform'

/**
 * 契约未细化 Skill 字段，以下为约定字段；状态流转 submit/approve/reject/rollback。
 * v3.2 起 SkillOut 增加归属与审核痕迹字段：名称/摘要/风险等业务字段仍放在 definition 内。
 */
export interface Skill {
  id: string
  name?: string
  code?: string
  /** 后端 SkillOut 的业务编码（与 code 并存：历史字段名差异） */
  skill_code?: string
  version?: string
  description?: string
  status?: string
  /** 技能定义（SkillOut 的 definition_json，别名为 definition）：name/summary/risk 都在这里 */
  definition?: Record<string, unknown> | null
  definition_json?: Record<string, unknown> | null
  /** 可见性（v3.2）；老后端未下发时按 yard 处理 */
  visibility?: SkillVisibility
  /** 创建者 user_id；私有技能归属判定用 */
  owner_user_id?: string | null
  /** 来源：seed 内置 / manual 手工 / ai 生成 / market 市场 / market_import 本地导入 等 */
  origin?: string | null
  created_from?: string | null
  reviewed_by?: string | null
  reviewed_at?: string | null
  review_note?: string | null
  created_at?: string
  updated_at?: string
}

/** submit_public：私有技能提交小院公共库（v3.2） */
export type SkillReviewAction = 'submit' | 'submit_public' | 'approve' | 'reject' | 'rollback'

export interface ReviewSkillPayload {
  action: SkillReviewAction
  note?: string
}

// ---------- 模型 / 工具 ----------

/** /llm/status：不返回密钥；字段与后端 LLMStatusOut 对齐 */
export interface LlmStatus {
  provider?: string
  model?: string
  configured?: boolean
  /** mock | live | unknown */
  mode?: 'mock' | 'live' | 'unknown'
  /** 选择该提供方的可读原因（不含密钥） */
  reason?: string
}

/** 与 contracts/json-schema/tool-definition.schema.json 一一对应 */
export interface ToolDefinition {
  code: string
  version: string
  name: string
  description: string
  input_schema: Record<string, unknown>
  output_schema?: Record<string, unknown>
  required_permissions: string[]
  risk_level: 'read_only' | 'draft' | 'write_reversible' | 'external_side_effect'
  side_effect: boolean
  timeout_seconds: number
}

// ---------- 审计 / 管理 ----------

/** Agent 出生自带技能（GET /agents 的 skills 字段，精简版） */
export interface AgentSkill {
  code: string
  name: string
  description: string
  risk_level: 'read_only' | 'draft' | 'write_reversible' | 'external_side_effect'
}

/** 专业 Agent 定义（GET /agents 返回） */
export interface AgentDefinition {
  code: string
  name: string
  version: string
  description: string
  input_schema_ref?: string
  output_schema_ref?: string
  allowed_tools?: string[]
  risk_level?: string
  max_steps?: number
  timeout_seconds?: number
  enabled?: boolean
  skills?: AgentSkill[]
}

/** Agent 运行结果（POST /agents/{code}/run 返回） */
export interface AgentRunResult {
  status: 'succeeded' | 'failed' | 'awaiting_review'
  output: Record<string, unknown>
  citations?: Array<Record<string, unknown>>
  warnings?: string[]
  confidence?: number | null
}

/** 契约未细化字段，以下为约定字段 */
export interface AuditLog {
  id?: string
  actor?: string
  action?: string
  resource_type?: string
  resource_id?: string
  detail?: Record<string, unknown> | null
  created_at?: string
}

/** /admin/stats：调用量/失败原因/任务耗时/模型成本概况；字段为约定字段 */
export interface AdminStats {
  scope?: 'global' | 'yard'
  tasks?: {
    total: number
    by_status: Record<string, number>
    top_failure_reasons: { error_code: string; count: number }[]
  }
  llm?: {
    total_calls: number
    total_input_tokens: number
    total_output_tokens: number
    total_estimated_cost: number
    by_provider: Record<string, number>
  }
  knowledge?: { documents_by_status: Record<string, number> }
  members?: { total: number }
}

export interface AdminYard {
  id: string
  name: string
  status: string
  province?: string | null
  scene_type?: string | null
  active_members: number
  task_total: number
  created_at?: string | null
}

export interface AdminYardList {
  count: number
  yards: AdminYard[]
}

// ---------- 政策模块（第三期） ----------

/** 惠农/农业政策条目 */
export interface Policy {
  id: string
  yard_id?: string | null
  scope: string
  province: string | null
  city: string | null
  department: string | null
  policy_name: string
  publish_department: string | null
  published_at: string | null
  apply_start_at: string | null
  apply_end_at: string | null
  policy_type: string | null
  support_direction: string | null
  apply_conditions_json: Record<string, unknown> | null
  material_requirements_json: Record<string, unknown> | null
  content: string | null
  source_url: string | null
  source_hash: string | null
  first_crawled_at: string | null
  last_checked_at: string | null
  verified_at: string | null
  status: string
  review_status: string
  created_at: string
}

/** 政策采集输入 */
export interface PolicyIngestPayload {
  policy_name: string
  publish_department: string
  published_at?: string | null
  scope?: 'national' | 'province' | 'city'
  province?: string | null
  city?: string | null
  department?: string | null
  policy_type?: string | null
  support_direction?: string | null
  apply_conditions_json?: Record<string, unknown> | null
  material_requirements_json?: Record<string, unknown> | null
  content?: string | null
  source_url?: string | null
  apply_start_at?: string | null
  apply_end_at?: string | null
}

/** 待匹配画像 */
export interface PolicyProfile {
  crops?: string[] | null
  area_mu?: number | null
  province?: string | null
  city?: string | null
  applicant_type?: string | null
}

export type PolicyMatchLevel = 'eligible' | 'partial' | 'insufficient' | 'ineligible'

/** 单条政策的规则匹配结果 */
export interface PolicyMatchResult {
  policy_id: string
  policy_name: string
  match_level: PolicyMatchLevel
  matched_conditions: string[]
  unmet_conditions: string[]
  unknown_conditions: string[]
  source_url: string | null
  verification_status: string
}

/** 申报材料清单条目 */
export interface ChecklistItem {
  name: string
  required: boolean
  source: string
}

/** 生成申报草案输入 */
export interface ApplicationDraftPayload {
  applicant_name: string
  applicant_type?: string | null
  contact_info?: Record<string, unknown> | null
  form_data?: Record<string, unknown> | null
}

/** 申报材料草案 */
export interface ApplicationDraft {
  id: string
  draft_id: string
  yard_id: string
  policy_id: string
  applicant_name: string
  applicant_type: string | null
  contact_info: Record<string, unknown> | null
  form_data: Record<string, unknown> | null
  material_checklist: ChecklistItem[] | null
  missing_materials: string[] | null
  notes: string | null
  status: string
  created_at: string
  verification_reminder: string
}
