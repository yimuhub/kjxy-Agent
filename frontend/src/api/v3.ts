import { isAxiosError, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { api, extractErrorMessage } from './client'
import type { ApiResponse, Skill, SkillReviewAction, SkillVisibility } from '@/types/api'
import { friendlyErrorMessage } from '@/utils/status'

/** v2.2 / v3.0 新增接口：搜索、市场、身份权限、负责人驾驶舱。 */

function unwrap<T>(promise: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  return promise.then(res => res.data.data)
}

const silent: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }

// ---------------------------------------------------------------------------
// 身份 / 成员 / 角色 / 设置
// ---------------------------------------------------------------------------

export type MemberRole = 'yard_owner' | 'yard_admin' | 'expert' | 'operator' | 'viewer' | string

export interface MemberRow {
  user_id: string
  username: string
  display_name?: string | null
  email?: string | null
  phone?: string | null
  role: MemberRole
  role_name?: string
  status: string
  joined_at?: string
  last_login_at?: string | null
  must_change_password?: boolean
  tokens_7d?: number
}

export interface MemberList {
  items: MemberRow[]
  total: number
}

export interface CreateMemberPayload {
  username: string
  role: MemberRole
  display_name?: string
  email?: string
  phone?: string
  password?: string
  note?: string
}

export interface CreateMemberResult {
  member: MemberRow
  initial_password?: string | null
}

export interface UpdateMemberPayload {
  display_name?: string
  email?: string | null
  phone?: string | null
  role?: MemberRole
  status?: 'active' | 'disabled'
  note?: string
}

export interface RoleDef {
  code: string
  name: string
  permissions: string[]
  is_system: boolean
  is_protected: boolean
}

export interface PermissionGroup {
  key: string
  label: string
  permissions: string[]
}

export interface RolesPayload {
  roles: RoleDef[]
  permission_groups: PermissionGroup[]
  role_labels: Record<string, string>
  assignable_roles: string[]
}

export interface RegistrationRequest {
  id: string
  user_id: string
  username: string
  display_name?: string | null
  email?: string | null
  phone?: string | null
  yard_id?: string | null
  kind: string
  yard_name?: string | null
  note?: string | null
  status: string
  created_at?: string
}

/** v3.1：搜索供应商新增 auto（自动，默认）；builtin_sources 为免密钥内置源 */
export type SearchProvider = 'auto' | 'searxng' | 'tavily' | 'brave'

/** v3.1：免密钥内置搜索源（零配置可用，可单独关闭） */
export type BuiltinSearchSource = 'bing' | 'sogou' | 'so360'

export interface YardSettingsBundle {
  registration: {
    mode: 'closed' | 'approval' | 'open'
    default_role: MemberRole
    require_email: boolean
    require_phone: boolean
    allow_new_yard: boolean
  }
  security: {
    password_min_length: number
    max_login_attempts: number
    lockout_seconds: number
  }
  search: {
    enabled: boolean
    provider: SearchProvider
    searxng_url: string
    daily_limit: number
    timeout_seconds: number
    /** 缺省视为全选（老后端不下发该字段） */
    builtin_sources?: BuiltinSearchSource[]
    tavily_configured?: boolean
    brave_configured?: boolean
    /** 仅提交时使用；留空表示保持原密钥 */
    tavily_api_key?: string
    brave_api_key?: string
  }
  skill_lab: {
    enabled: boolean
    daily_limit: number
    use_web: boolean
    max_sources: number
  }
  self_improve: {
    enabled: boolean
    schedule: { preset: string; custom_value: number; custom_unit: string }
    scope: 'yard' | 'per_member'
    daily_budget_tokens: number
    auto_publish: boolean
    retain_days: number
    notify_owner: boolean
  }
  /** v3.5：一句话生成智能体（每成员每日配额，0 = 不限） */
  agent_generate: {
    enabled: boolean
    daily_limit: number
    use_web: boolean
  }
}

export const governanceApi = {
  listMembers(page = 1, pageSize = 100): Promise<MemberList> {
    // 现有 /yards/current/members 使用统一分页信封：data 为数组、meta.total 为总数
    return api
      .get<ApiResponse<MemberRow[]> & { meta?: { total?: number } }>('/yards/current/members', {
        params: { page, page_size: pageSize },
      })
      .then(res => ({
        items: (res.data.data ?? []) as unknown as MemberRow[],
        total: Number(res.data.meta?.total ?? 0),
      }))
  },
  createMember(payload: CreateMemberPayload): Promise<CreateMemberResult> {
    return unwrap(api.post<ApiResponse<CreateMemberResult>>('/yards/current/members', payload))
  },
  updateMember(userId: string, payload: UpdateMemberPayload): Promise<MemberRow> {
    return unwrap(api.patch<ApiResponse<MemberRow>>(`/yards/current/members/${userId}`, payload))
  },
  resetPassword(userId: string): Promise<{ temporary_password: string }> {
    return unwrap(
      api.post<ApiResponse<{ temporary_password: string }>>(
        `/yards/current/members/${userId}/reset-password`,
      ),
    )
  },
  removeMember(userId: string): Promise<void> {
    return api.delete(`/yards/current/members/${userId}`).then(() => undefined)
  },
  roles(): Promise<RolesPayload> {
    return unwrap(api.get<ApiResponse<RolesPayload>>('/yards/current/roles'))
  },
  updateRole(code: string, payload: { name?: string; permissions?: string[] }): Promise<RoleDef> {
    return unwrap(api.put<ApiResponse<RoleDef>>(`/yards/current/roles/${code}`, payload))
  },
  createRole(payload: {
    code: string
    name: string
    permissions: string[]
  }): Promise<RoleDef> {
    return unwrap(api.post<ApiResponse<RoleDef>>('/yards/current/roles', payload))
  },
  deleteRole(code: string): Promise<void> {
    return api.delete(`/yards/current/roles/${code}`).then(() => undefined)
  },
  settings(): Promise<YardSettingsBundle> {
    return unwrap(api.get<ApiResponse<YardSettingsBundle>>('/yards/current/settings'))
  },
  updateSetting(
    key:
      | 'registration'
      | 'security'
      | 'search'
      | 'skill_lab'
      | 'self_improve'
      | 'agent_generate',
    value: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    return unwrap(
      api.put<ApiResponse<Record<string, unknown>>>(`/yards/current/settings/${key}`, value),
    )
  },
  registrations(status = 'pending'): Promise<RegistrationRequest[]> {
    return unwrap(
      api.get<ApiResponse<RegistrationRequest[]>>('/yards/current/registrations', {
        params: { status },
      }),
    )
  },
  approveRegistration(
    id: string,
  ): Promise<{ request: RegistrationRequest; member: MemberRow }> {
    return unwrap(
      api.post<ApiResponse<{ request: RegistrationRequest; member: MemberRow }>>(
        `/yards/current/registrations/${id}/approve`,
      ),
    )
  },
  rejectRegistration(id: string, reason?: string): Promise<RegistrationRequest> {
    return unwrap(
      api.post<ApiResponse<RegistrationRequest>>(
        `/yards/current/registrations/${id}/reject`,
        { reason },
      ),
    )
  },
}

export const profileApi = {
  updateMe(payload: {
    display_name?: string
    email?: string | null
    phone?: string | null
  }): Promise<unknown> {
    return unwrap(api.patch<ApiResponse<unknown>>('/auth/me', payload))
  },
  changePassword(payload: {
    old_password: string
    new_password: string
  }): Promise<{ access_token: string; refresh_token: string }> {
    return unwrap(
      api.post<ApiResponse<{ access_token: string; refresh_token: string }>>(
        '/auth/change-password',
        payload,
      ),
    )
  },
  register(payload: {
    username: string
    password: string
    email?: string
    phone?: string
    display_name?: string
    yard_id: string
    note?: string
  }): Promise<{ request_id?: string; status: string; message?: string }> {
    return unwrap(api.post<ApiResponse<{ request_id?: string; status: string; message?: string }>>(
      '/auth/register',
      payload,
    ))
  },
}

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
// 联网搜索
// ---------------------------------------------------------------------------

export interface SearchHit {
  title: string
  url: string
  snippet: string
  source: string
  published?: string | null
}

export interface SearchQuota {
  used: number
  limit: number
  remaining: number
}

export interface SearchStatus {
  mode: 'online' | 'degraded' | 'offline'
  /** 设备是否具备互联网访问能力（v3.1 新增；缺失视为未知，按降级处理） */
  internet?: boolean
  provider: string
  checked_at: string
  reason: string
  /** 当前首选供应商（配置项） */
  supplier?: string
  quota?: SearchQuota
}

export const searchApi = {
  status(): Promise<SearchStatus> {
    return unwrap(api.get<ApiResponse<SearchStatus>>('/search/status'))
  },
  test(
    query = '农业 科技小院',
  ): Promise<{
    query: string
    provider: string
    result_count: number
    latency_ms: number
    cached: boolean
    fetched_at: string
    results: SearchHit[]
  }> {
    return unwrap(
      api.post<
        ApiResponse<{
          query: string
          provider: string
          result_count: number
          latency_ms: number
          cached: boolean
          fetched_at: string
          results: SearchHit[]
        }>
      >('/search/test', { query }),
    )
  },
}

// ---------------------------------------------------------------------------
// 插件 / 技能市场
// ---------------------------------------------------------------------------

export interface MarketItem {
  item_key: string
  item_type: 'skill' | 'plugin' | 'agent'
  source: 'builtin' | 'remote' | 'github' | 'local'
  code: string
  name: string
  version: string
  description: string
  source_url?: string | null
  license?: string | null
  author?: string | null
  risk_level: string
  status: string
  verified: boolean
  installed: boolean
  stars?: number
  repo?: string
  tags?: string[]
  attribution?: string
}

export interface MarketItemDetail extends MarketItem {
  definition_json?: Record<string, unknown> | null
  manifest_json?: Record<string, unknown> | null
  metadata_json?: Record<string, unknown> | null
}

export interface MarketSearchResult {
  items: MarketItem[]
  total: number
  /** 联网检索失败时的中文提示；离线回退为内置技能库 */
  warning?: string | null
  offline: boolean
}

export interface MarketInstallResult {
  item_key: string
  item_type: 'skill' | 'plugin' | 'agent'
  source: string
  name: string
  installed_as: string
  /** installed=已启用；pending_review=等待专家审核 */
  review_status: string
  skill_id?: string
  skill_status?: string
  plugin_id?: string
  plugin_status?: string
  checksum_ok?: boolean
}

export interface InstalledSkillRow {
  id: string
  code: string
  name: string
  version: string
  status: string
  item_key?: string | null
  source: string
  created_at: string
}

export interface InstalledPluginRow {
  id: string
  plugin_id: string
  code: string
  name: string
  version: string
  type: string
  status: string
  item_key?: string | null
  source: string
  created_at: string
}

export interface InstalledResult {
  skills: InstalledSkillRow[]
  plugins: InstalledPluginRow[]
  total: number
}

export interface SkillImportResult {
  item_key: string
  item_type: 'skill'
  source: 'local'
  code: string
  name: string
  version: string
  status: string
  skill_id: string
  skill_status: string
  verified: boolean
}

export interface PluginImportResult {
  item_key: string
  item_type: 'plugin'
  source: 'local'
  code: string
  name: string
  version: string
  status: string
  plugin_id: string
  plugin_status: string
  checksum_ok: boolean
  files: string[]
}

export interface GithubPreviewResult {
  repo: string
  path: string
  ref: string
  license?: string | null
  stars: number
  description: string
  name: string
  version: string
  source_url: string
  definition_json: Record<string, unknown>
  /** 预览即落库，可直接安装 */
  item_key?: string
}

export const libraryApi = {
  search(payload: {
    query?: string
    item_type?: 'skill' | 'plugin' | 'agent'
    source?: string
    limit?: number
  }): Promise<MarketSearchResult> {
    return unwrap(api.post<ApiResponse<MarketSearchResult>>('/market/items/search', payload))
  },
  detail(itemKey: string): Promise<MarketItemDetail> {
    return unwrap(api.get<ApiResponse<MarketItemDetail>>(`/market/items/${itemKey}`))
  },
  install(itemKey: string): Promise<MarketInstallResult> {
    return unwrap(
      api.post<ApiResponse<MarketInstallResult>>(`/market/items/${itemKey}/install`, {}),
    )
  },
  installed(): Promise<InstalledResult> {
    return unwrap(api.get<ApiResponse<InstalledResult>>('/market/installed'))
  },
  importSkill(file: File): Promise<SkillImportResult> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(
      api.post<ApiResponse<SkillImportResult>>('/market/skills/import', form, {
        ...silent,
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
  },
  importPlugin(file: File): Promise<PluginImportResult> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(
      api.post<ApiResponse<PluginImportResult>>('/market/plugins/import', form, {
        ...silent,
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
  },
  githubSearch(query: string): Promise<{ items: MarketItem[]; offline: boolean; warning: string | null }> {
    return unwrap(
      api.post<ApiResponse<{ items: MarketItem[]; offline: boolean; warning: string | null }>>(
        '/market/github/search',
        { query },
      ),
    )
  },
  githubPreview(repo: string): Promise<GithubPreviewResult> {
    return unwrap(api.post<ApiResponse<GithubPreviewResult>>('/market/github/preview', { repo }))
  },
}

// ---------------------------------------------------------------------------
// 技能归属（v3.2）：我的 / 小院 / 平台三级视图 + 审核与推广
// ---------------------------------------------------------------------------

/** 技能空间：mine=我创建的，yard=小院共享，platform=平台共享 */
export type SkillScope = 'mine' | 'yard' | 'platform'

export interface SkillListQuery {
  scope?: SkillScope
  /** draft|testing|pending_review|published|deprecated */
  status?: string
  page?: number
  pageSize?: number
}

export interface SkillListResult {
  items: Skill[]
  total: number
  page: number
  pageSize: number
}

// ─────────────────────────────────────────────────────────────────────────────
// 自我总结提升（v3.4）
// ─────────────────────────────────────────────────────────────────────────────

export interface SelfImproveRun {
  id: string
  job_id: string
  trigger: string
  status: 'running' | 'success' | 'failed' | 'skipped'
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  tokens_used: number
  output: Record<string, unknown> | null
  error: string | null
  actor_id: string | null
}

export interface SelfImproveReport {
  settings: Record<string, unknown>
  job: { status: string; next_run_at: string | null; last_run_at: string | null }
  last_run: SelfImproveRun | null
  budget: { daily_budget_tokens: number; used_today: number; remaining: number }
  can_manage: boolean
}

export interface SelfImproveMemory {
  id: string
  user_id: string | null
  summary: string
  tags: string[]
  window_from: string | null
  window_to: string | null
  created_at: string | null
}

export const selfImproveApi = {
  report(): Promise<SelfImproveReport> {
    return unwrap(api.get<ApiResponse<SelfImproveReport>>('/yards/current/self-improve'))
  },
  run(): Promise<SelfImproveRun> {
    return unwrap(api.post<ApiResponse<SelfImproveRun>>('/yards/current/self-improve/run'))
  },
  runs(limit = 20): Promise<{ items: SelfImproveRun[] }> {
    return unwrap(
      api.get<ApiResponse<{ items: SelfImproveRun[] }>>(
        '/yards/current/self-improve/runs',
        { params: { limit } },
      ),
    )
  },
  memories(userId?: string): Promise<{ items: SelfImproveMemory[] }> {
    const params: Record<string, string> = {}
    if (userId) params.user_id = userId
    return unwrap(
      api.get<ApiResponse<{ items: SelfImproveMemory[] }>>(
        '/yards/current/self-improve/memories',
        { params },
      ),
    )
  },
}

/** 上架前评测报告（v3.17） */
export interface SkillEvalCheck {
  code: string
  severity: 'error' | 'warning'
  passed: boolean
  detail: string
}

export interface SkillEvalCase {
  case_id: string
  category: string
  passed: boolean
  reason: string
  latency_ms?: number
  citations_count?: number
}

export interface SkillEvalDataset {
  test_set_id: string
  version?: string
  dataset_sha256?: string | null
  pass_rate: number
  minimum_pass_rate: number
  gate_passed: boolean
  passed: number
  failed: number
  total: number
  failed_case_ids?: string[]
  category_pass_rates?: Record<string, number>
  metrics?: Record<string, number | string | null>
  cases?: SkillEvalCase[]
}

export interface SkillEvalReport {
  report_version: number
  kind: string
  generated_at: string
  skill_code: string
  version: string
  definition_sha256: string
  mode: 'static' | 'static+dataset' | 'static+dataset_skipped'
  pass: boolean
  score: number
  errors: number
  warnings: number
  checks: SkillEvalCheck[]
  dataset?: SkillEvalDataset | null
  notes?: string[]
}

export interface SkillEvalRunBrief {
  id: string
  kind: string
  test_set_id: string
  pass_rate: number
  passed: number
  failed: number
  total: number
  report_sha256?: string | null
  dataset_sha256?: string | null
  created_at: string
  report?: SkillEvalReport | null
}

export interface SkillAttestation {
  skill_id: string
  skill_code: string
  version: string
  status: string
  visibility: string
  definition_sha256?: string | null
  signature?: string | null
  signature_alg?: string | null
  key_id?: string | null
  signed_by?: string | null
  signed_at?: string | null
  verification: {
    valid: boolean
    signed: boolean
    reason: 'ok' | 'unsigned' | 'definition_mismatch' | 'bad_signature' | 'key_mismatch'
    digest: string
    expected_digest?: string | null
  }
  latest_eval_run?: SkillEvalRunBrief | null
}

export interface SkillSigningKeyInfo {
  alg: string
  key_id: string
  public_key: string
  note?: string
}

export interface SkillEvalRunDetail {
  id: string
  skill_code: string
  skill_version: string
  kind: string
  test_set_id: string
  pass_rate: number
  passed: number
  failed: number
  total: number
  report_sha256?: string | null
  dataset_sha256?: string | null
  created_at: string
  report?: SkillEvalReport | null
}

/**
 * 技能接口（v3.2 唯一入口）。
 * 与 modules.ts 的旧 skillApi 合并：list 支持 scope/分页，review 支持 submit_public，新增 promote。
 */
export const skillApi = {
  /** 按技能空间分页拉取；后端暂未支持 scope 时退化为全量列表（不报错） */
  list(query: SkillListQuery = {}): Promise<SkillListResult> {
    const page = query.page && query.page > 0 ? query.page : 1
    const pageSize = query.pageSize && query.pageSize > 0 ? query.pageSize : 20
    return api
      .get<ApiResponse<Skill[]> & { meta?: { total?: number } }>('/skills', {
        params: {
          scope: query.scope,
          status: query.status || undefined,
          page,
          page_size: pageSize,
        },
        ...silent,
      })
      .then(res => ({
        items: (res.data.data ?? []) as unknown as Skill[],
        total: Number(res.data.meta?.total ?? 0),
        page,
        pageSize,
      }))
  },
  create(payload: Record<string, unknown>): Promise<Skill> {
    return unwrap(api.post<ApiResponse<Skill>>('/skills', payload))
  },
  /** 审核动作：submit|submit_public|approve|reject|rollback */
  review(
    id: string,
    action: SkillReviewAction,
    note?: string,
    config: AxiosRequestConfig & { skipErrorToast?: boolean } = {},
  ): Promise<Skill> {
    return unwrap(
      api.post<ApiResponse<Skill>>(`/skills/${id}/review`, { action, note }, config),
    )
  },
  /** 负责人把私有技能转为小院公共（需 skills:publish_any；后端未就绪时 404） */
  promote(id: string, config: AxiosRequestConfig & { skipErrorToast?: boolean } = {}): Promise<Skill> {
    return unwrap(api.post<ApiResponse<Skill>>(`/skills/${id}/promote`, {}, config))
  },
  /** 摘要/签名/验签结论与最近一份评测报告（v3.17） */
  attestation(
    id: string,
    config: AxiosRequestConfig & { skipErrorToast?: boolean } = {},
  ): Promise<SkillAttestation> {
    return unwrap(api.get<ApiResponse<SkillAttestation>>(`/skills/${id}/attestation`, config))
  },
  /** 单次评测报告全文（v3.17） */
  evalRun(
    id: string,
    runId: string,
    config: AxiosRequestConfig & { skipErrorToast?: boolean } = {},
  ): Promise<SkillEvalRunDetail> {
    return unwrap(
      api.get<ApiResponse<SkillEvalRunDetail>>(`/skills/${id}/eval-runs/${runId}`, config),
    )
  },
  /** 签名公钥（Ed25519），用于离线校验（v3.17） */
  signingKey(config: AxiosRequestConfig & { skipErrorToast?: boolean } = {}): Promise<SkillSigningKeyInfo> {
    return unwrap(api.get<ApiResponse<SkillSigningKeyInfo>>('/skills/signing-key', config))
  },
}

// ---------------------------------------------------------------------------
// 一句话生成技能（v3.2）：检索匹配 → 生成 → 安装
// ---------------------------------------------------------------------------

/** 检索命中的候选（市场/内置/GitHub） */
export interface SkillMatch {
  item_key: string
  name: string
  summary?: string
  source?: string
  license?: string | null
  risk_level?: string
  installed?: boolean
  score?: number
}

export interface SkillAgentRequestResult {
  utterance: string
  /** 后端理解出的检索词 */
  query?: string
  recommend: 'install' | 'generate'
  matches: SkillMatch[]
  note?: string
  offline?: boolean
}

/** 生成结果里的技能摘要（一定是私有草稿） */
export interface GeneratedSkillBrief {
  id: string
  code: string
  name: string
  version: string
  visibility: SkillVisibility
  status: string
}

export interface SkillSourceRef {
  title: string
  url: string
}

export interface SkillAgentGenerateResult {
  skill: GeneratedSkillBrief
  /** 生成的 SkillSpec（含 guardrails.risk_level 等），形状由后端模板决定 */
  spec?: Record<string, unknown> | null
  sources?: SkillSourceRef[]
  requires_review?: boolean
  note?: string
  token_usage?: {
    input_tokens?: number
    output_tokens?: number
    total_tokens?: number
  }
}

/** 安装结果形状随安装器：只约定少量公共字段，其余原样透出 */
export interface SkillAgentInstallResult {
  item_key?: string
  skill_id?: string
  installed_as?: string
  review_status?: string
  skill_status?: string
  needs_review?: boolean
  [key: string]: unknown
}

/** 一句话生成插件结果（v3.3）：插件一律 pending_review，审核通过后才能运行 */
export interface PluginAgentGenerateResult {
  plugin: {
    plugin_id: string
    name: string
    description: string
    status: string
    step_count: number
  }
  manifest: Record<string, unknown>
  sources?: SkillSourceRef[]
  note?: string
  token_usage?: {
    input_tokens?: number
    output_tokens?: number
    total_tokens?: number
  }
}

/** 一句话链路：检索/生成/安装全部静默失败，由调用方在区块内展示 note（不弹全局错误） */
export const skillLabApi = {
  request(utterance: string): Promise<SkillAgentRequestResult> {
    return unwrap(
      api.post<ApiResponse<SkillAgentRequestResult>>(
        '/market/agent/request',
        { utterance, kind: 'skill' },
        silent,
      ),
    )
  },
  /** kind 决定返回形状：skill 返回技能草稿，plugin 返回待审工作流插件 */
  generate<K extends 'skill' | 'plugin' = 'skill'>(
    utterance: string,
    useWeb = true,
    kind: K = 'skill' as K,
  ): Promise<K extends 'plugin' ? PluginAgentGenerateResult : SkillAgentGenerateResult> {
    return unwrap(
      api.post<ApiResponse<SkillAgentGenerateResult | PluginAgentGenerateResult>>(
        '/market/agent/generate',
        { utterance, use_web: useWeb, kind },
        // 生成可能触发联网检索 + 模型调用，放宽超时
        { ...silent, timeout: 180_000 },
      ),
    ) as Promise<K extends 'plugin' ? PluginAgentGenerateResult : SkillAgentGenerateResult>
  },
  install(payload: { item_key: string } | { skill_id: string }): Promise<SkillAgentInstallResult> {
    return unwrap(
      api.post<ApiResponse<SkillAgentInstallResult>>('/market/agent/install', payload, silent),
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

export const ownerApi = {
  overview(): Promise<OwnerOverview> {
    return unwrap(api.get<ApiResponse<OwnerOverview>>('/yards/current/owner/overview'))
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
