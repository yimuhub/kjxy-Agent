import type { AxiosRequestConfig, AxiosResponse } from 'axios'
import { api } from './client'
import type { ApiResponse } from '@/types/api'

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
    provider: 'searxng' | 'tavily' | 'brave'
    searxng_url: string
    daily_limit: number
    timeout_seconds: number
    tavily_configured?: boolean
    brave_configured?: boolean
    /** 仅提交时使用；留空表示保持原密钥 */
    tavily_api_key?: string
    brave_api_key?: string
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
    key: 'registration' | 'security' | 'search',
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
