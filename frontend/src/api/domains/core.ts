/**
 * 认证 / 小院 / 成员与角色 / 小院自助注册 / 推送通道 / SSO / 版本握手 / 工作台菜单授权（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import { api, silent, unwrap, unwrapList } from '../client'
import type { ListResult } from '../client'
import type {
  AddMemberPayload,
  ApiResponse,
  BatchAddMembersResult,
  Invitation,
  InvitationPreview,
  ListResponse,
  LoginResponse,
  MeResponse,
  Member,
  MemberRole as ApiMemberRole,
  Yard,
} from '@/types/api'

// ---------- 认证 ----------

export const authApi = {
  login(username: string, password: string, yard_id?: string): Promise<LoginResponse> {
    return unwrap(
      api.post<ApiResponse<LoginResponse>>('/auth/login', { username, password, yard_id }),
    )
  },
  refresh(refresh_token: string): Promise<{ access_token: string; refresh_token: string }> {
    return unwrap(api.post('/auth/refresh', { refresh_token }))
  },
  logout(refresh_token?: string): Promise<unknown> {
    // 带上刷新令牌后服务端会精确吊销该会话
    return unwrap(api.post('/auth/logout', refresh_token ? { refresh_token } : {}))
  },
  me(): Promise<MeResponse> {
    return unwrap(api.get<ApiResponse<MeResponse>>('/auth/me'))
  },
  /** 桌面安装包启动时的静默登录（令牌仅由本机壳注入） */
  desktopBootstrap(token: string): Promise<LoginResponse> {
    return unwrap(api.post<ApiResponse<LoginResponse>>('/auth/desktop-bootstrap', { token }))
  },
}


// ---------- 小院 ----------

export interface QuotaItem {
  /** 后端下发的中文名（模型调用 / 知识库文档 / 任务） */
  label?: string
  limit: number
  used: number
  remaining: number
  usage_percent: number
}

export interface QuotaStatus {
  yard_id: string
  period: string
  quotas: Record<string, QuotaItem>
  warnings: { kind: string; label?: string; level: string; message: string }[]
}

export interface QuotaLimitPatch {
  llm_calls_limit?: number
  knowledge_docs_limit?: number
  tasks_limit?: number
}

export const yardApi = {
  current(): Promise<Yard> {
    return unwrap(api.get<ApiResponse<Yard>>('/yards/current'))
  },
  members(): Promise<ListResult<Member>> {
    return unwrapList(api.get<ListResponse<Member>>('/yards/current/members'))
  },
  addMember(payload: AddMemberPayload): Promise<Member> {
    return unwrap(api.post<ApiResponse<Member>>('/yards/current/members', payload))
  },
  batchAddMembers(payload: {
    members: AddMemberPayload[]
  }): Promise<BatchAddMembersResult> {
    return unwrap(
      api.post<ApiResponse<BatchAddMembersResult>>(
        '/yards/current/members/batch',
        payload,
      ),
    )
  },
  removeMember(user_id: string): Promise<void> {
    return api.delete(`/yards/current/members/${user_id}`).then(() => undefined)
  },
  invitations(): Promise<Invitation[]> {
    return unwrap(api.get<ApiResponse<Invitation[]>>('/yards/current/invitations'))
  },
  createInvitation(payload: {
    role: ApiMemberRole
    expires_in_days?: number
    note?: string
  }): Promise<Invitation> {
    return unwrap(
      api.post<ApiResponse<Invitation>>('/yards/current/invitations', payload),
    )
  },
  revokeInvitation(id: string): Promise<Invitation> {
    return unwrap(api.delete<ApiResponse<Invitation>>(`/yards/current/invitations/${id}`))
  },
  /** 公开：邀请预览（接受页，无需登录） */
  previewInvitation(token: string): Promise<InvitationPreview> {
    return unwrap(api.get<ApiResponse<InvitationPreview>>(`/invitations/${token}`))
  },
  /** 公开：接受邀请并直接返回登录令牌 */
  acceptInvitation(
    token: string,
    payload: { username: string; password: string; display_name?: string },
  ): Promise<LoginResponse> {
    return unwrap(
      api.post<ApiResponse<LoginResponse>>(`/invitations/${token}/accept`, payload),
    )
  },
  /** 当前小院配额状态 */
  getQuota(): Promise<QuotaStatus> {
    return unwrap(api.get<ApiResponse<QuotaStatus>>('/yards/quota'))
  },
  /** 调整小院配额上限（quota:manage）：上限为 0 表示该额度禁用 */
  updateQuota(yardId: string, payload: QuotaLimitPatch): Promise<QuotaStatus> {
    return unwrap(
      api.patch<ApiResponse<QuotaStatus>>(`/admin/quotas/${yardId}`, payload),
    )
  },
  /** 清零已用量（不动上限）：本地部署没有 Redis 月度任务时靠这里恢复 */
  resetQuota(yardId: string, kinds: string[] = []): Promise<QuotaStatus> {
    return unwrap(
      api.post<ApiResponse<QuotaStatus>>(`/admin/quotas/${yardId}/reset`, { kinds }),
    )
  },
  /** 公开：小院自助注册，返回可直接登录的 access_token */
  signup(payload: YardSignupPayload): Promise<YardSignupResult> {
    return unwrap(api.post<ApiResponse<YardSignupResult>>('/yards/signup', payload))
  },
}


// ---------- 小院自助注册 ----------

export interface YardSignupPayload {
  yard_name: string
  admin_username: string
  password: string
  contact?: string
}

export interface YardSignupResult {
  yard_id: string
  user_id: string
  access_token: string
  token_type: string
}


// ---------- 推送通道（v3.28 前端入口；后端 /settings/push-channels） ----------

export type PushChannelKind = 'wecom_robot' | 'dingtalk' | 'generic_webhook'

export interface PushChannel {
  id: string
  name: string
  kind: PushChannelKind
  url_masked: string | null
  events: string[]
  enabled: boolean
  consecutive_failures: number
  created_at: string | null
  updated_at: string | null
}

export const PUSH_EVENT_OPTIONS: { value: string; label: string }[] = [
  { value: 'farm_alert', label: '农事预警（Agent 自动执行）' },
  { value: 'farm_agent_run', label: 'Agent 自动执行摘要' },
  { value: 'workorder_created', label: '工单已创建' },
  { value: 'device_alert', label: '设备告警' },
  { value: 'self_improve_run', label: '自我总结运行完成' },
  { value: 'team_run_failed', label: '班组运行失败' },
  { value: 'skill_force_published', label: '私有技能被转为公共' },
]

export const pushChannelApi = {
  list(): Promise<PushChannel[]> {
    return unwrap(api.get<ApiResponse<PushChannel[]>>('/settings/push-channels'))
  },
  create(payload: {
    name: string
    kind: PushChannelKind
    url?: string
    events: string[]
    enabled?: boolean
  }): Promise<PushChannel> {
    return unwrap(api.post<ApiResponse<PushChannel>>('/settings/push-channels', payload))
  },
  update(
    channelId: string,
    payload: Partial<{ name: string; kind: PushChannelKind; url: string; events: string[]; enabled: boolean }>,
  ): Promise<PushChannel> {
    return unwrap(api.patch<ApiResponse<PushChannel>>(`/settings/push-channels/${channelId}`, payload))
  },
  remove(channelId: string): Promise<unknown> {
    return unwrap(api.delete(`/settings/push-channels/${channelId}`))
  },
}

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
  /** 当前操作者是否可编辑（后端按负责人/管理员边界计算） */
  can_edit?: boolean
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
  permission_labels?: Record<string, string>
  protected_permissions?: string[]
  grantable_permissions?: string[]
}

/** 工作台视图（菜单模式/角色预设）共享配置 */
export type MenuMode = 'minimal' | 'standard' | 'full'

export interface MenuProfile {
  id: string
  name: string
  mode: MenuMode
  groups: Array<{
    label: string
    items: Array<{
      key: string
      title: string
      path?: string
      iconName?: string
      permission?: string
    }>
  }>
}

export interface MenuConfig {
  default_mode: MenuMode
  role_modes: Record<string, MenuMode>
  profiles: MenuProfile[]
  allow_member_customize: boolean
}

/** 成员级权限总览与逐人授权 */
export interface MemberPermissionPayload {
  user_id: string
  username: string
  display_name: string
  role: string
  role_name: string
  role_permissions: string[]
  grants: string[]
  effective_permissions: string[]
  is_owner: boolean
  permission_groups: PermissionGroup[]
  permission_labels: Record<string, string>
  protected_permissions: string[]
  grantable_permissions: string[]
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

/** v3.33：免密钥内置搜索源（零配置可用，可单独关闭） */
export type BuiltinSearchSource =
  | 'brave_web'
  | 'baidu'
  | 'sogou'
  | 'so360'
  | 'bing_news'
  | 'bing'
  | 'google_news'
  | 'duckduckgo'

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
  /** v3.58：小院画像（官方技能库按此推荐，类型见 official_catalog.YARD_TYPES）
   *  v3.67：扩为小院档案——培养单位/县域/主导产业/标签，枢纽工作台分组依据 */
  yard_profile: {
    types: string[]
    unit?: string
    county?: string
    industry?: string
    tags?: string[]
  }
  /** v3.23：设备数据智能分析（每日定时 + 立即分析） */
  device_analysis: {
    enabled: boolean
    schedule: { preset: string }
    explain_with_llm: boolean
    daily_budget_tokens: number
    window_hours: number
  }
  /** v3.73：对话内图片/视频生成（开关、按人额度、视频时长与保留期） */
  media?: {
    image: {
      enabled: boolean
      member_access: boolean
      daily_limit: number
      max_per_request: number
      default_size: '1024x1024' | '1024x1536' | '1536x1024'
    }
    video: {
      enabled: boolean
      member_access: boolean
      daily_limit: number
      max_seconds: number
      admin_max_seconds: number
      retention_days: number
    }
  }
  /** v3.24：工作台视图（默认模式 + 按角色默认 + 共享预设） */
  menu?: MenuConfig
  /** v3.32：额度环境与旧调用额度模式 */
  quota?: {
    environment: string
    legacy_calls_mode: 'enforce' | 'warn' | 'off'
    default_grant_days: number
  }
  /** v3.30：语音转写通道（auto/local/online/off + 本机模型） */
  asr?: {
    mode: 'auto' | 'local' | 'online' | 'off'
    local_model_id: string
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
  updateRole(
    code: string,
    payload: { name?: string; permissions?: string[]; confirm_protected?: boolean },
  ): Promise<RoleDef> {
    return unwrap(api.put<ApiResponse<RoleDef>>(`/yards/current/roles/${code}`, payload))
  },
  createRole(payload: {
    code: string
    name: string
    permissions: string[]
    confirm_protected?: boolean
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
      | 'agent_generate'
      | 'device_analysis'
      | 'farm_agent'
      | 'media'
      | 'menu'
      | 'asr'
      | 'quota'
      | 'yard_profile',
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
// 工作台视图与成员逐人授权（v3.24）
// ---------------------------------------------------------------------------

export const menuApi = {
  get(): Promise<MenuConfig> {
    return unwrap(api.get<ApiResponse<MenuConfig>>('/yards/current/menu'))
  },
  update(payload: Partial<MenuConfig>): Promise<MenuConfig> {
    return unwrap(api.put<ApiResponse<MenuConfig>>('/yards/current/menu', payload))
  },
}

export const memberPermissionApi = {
  get(userId: string): Promise<MemberPermissionPayload> {
    return unwrap(
      api.get<ApiResponse<MemberPermissionPayload>>(
        `/yards/current/members/${userId}/permissions`,
      ),
    )
  },
  update(
    userId: string,
    payload: { grants: string[]; confirm_protected?: boolean },
  ): Promise<MemberPermissionPayload> {
    return unwrap(
      api.put<ApiResponse<MemberPermissionPayload>>(
        `/yards/current/members/${userId}/permissions`,
        payload,
      ),
    )
  },
}


// ---------------------------------------------------------------------------
// 版本握手（v3.21）：后端版本与数据库迁移 head
// ---------------------------------------------------------------------------

export interface BackendMeta {
  version: string
  migration_head: string | null
}

export const metaApi = {
  get(): Promise<BackendMeta> {
    return unwrap(api.get<ApiResponse<BackendMeta>>('/meta', silent))
  },
}

// ---------------------------------------------------------------------------
// 单点登录 SSO/OIDC（v3.18）
// ---------------------------------------------------------------------------

/** 登录页可见的第三方登录方式（不含端点与密钥细节） */
export interface SsoPublicProvider {
  id: string
  name: string
  yard_id: string
  yard_name: string
  issuer_host: string
}

/** 管理端 IdP 配置（密钥只回传 configured 布尔） */
export interface SsoProviderAdmin {
  id: string
  yard_id: string
  yard_name?: string | null
  name: string
  issuer: string
  client_id: string
  client_secret_configured: boolean
  redirect_uri: string
  frontend_redirect_uri?: string | null
  scopes: string
  group_claim: string
  role_mapping: Record<string, string>
  default_role: string
  email_domains: string[]
  enabled: boolean
  auto_create_users: boolean
  link_by_email: boolean
  require_email_verified: boolean
  last_test_at?: string | null
  last_error?: string | null
  created_at?: string
}

export interface SsoProviderPayload {
  name: string
  issuer: string
  client_id: string
  client_secret?: string
  redirect_uri: string
  frontend_redirect_uri?: string | null
  scopes?: string
  group_claim?: string
  role_mapping?: Record<string, string>
  default_role?: string
  email_domains?: string[]
  enabled?: boolean
  auto_create_users?: boolean
  link_by_email?: boolean
  require_email_verified?: boolean
}

/** 一次性 code 换回的登录响应：附带 IdP 归属小院，供多小院账号落地 */
export interface SsoExchangeResult extends LoginResponse {
  yard_id: string
}

export interface SsoTestResult {
  issuer: string
  authorization_endpoint: string
  token_endpoint: string
  jwks_uri: string
  signing_keys: number
  supported_algorithms: string[]
}

export const ssoApi = {
  /** 登录页：可用第三方登录方式（全局开关关闭时为空） */
  publicProviders(): Promise<{ enabled: boolean; providers: SsoPublicProvider[] }> {
    return unwrap(api.get<ApiResponse<{ enabled: boolean; providers: SsoPublicProvider[] }>>(
      '/auth/sso/providers',
      silent,
    ))
  },
  /** 发起授权：返回 authorize_url（PKCE verifier 由后端写 HttpOnly Cookie） */
  start(providerId: string): Promise<{ authorize_url: string; state: string }> {
    return unwrap(
      api.get<ApiResponse<{ authorize_url: string; state: string }>>(
        `/auth/sso/${providerId}/start`,
        silent,
      ),
    )
  },
  /** 回调页：一次性 code 换正式登录响应 */
  exchange(code: string): Promise<SsoExchangeResult> {
    return unwrap(
      api.post<ApiResponse<SsoExchangeResult>>('/auth/sso/exchange', { code }, silent),
    )
  },
  list(): Promise<SsoProviderAdmin[]> {
    return unwrap(api.get<ApiResponse<SsoProviderAdmin[]>>('/settings/sso-providers'))
  },
  create(payload: SsoProviderPayload): Promise<SsoProviderAdmin> {
    return unwrap(api.post<ApiResponse<SsoProviderAdmin>>('/settings/sso-providers', payload))
  },
  update(id: string, payload: Partial<SsoProviderPayload>): Promise<SsoProviderAdmin> {
    return unwrap(
      api.patch<ApiResponse<SsoProviderAdmin>>(`/settings/sso-providers/${id}`, payload),
    )
  },
  remove(id: string): Promise<{ deleted: string }> {
    return unwrap(api.delete<ApiResponse<{ deleted: string }>>(`/settings/sso-providers/${id}`))
  },
  test(id: string): Promise<SsoTestResult> {
    return unwrap(api.post<ApiResponse<SsoTestResult>>(`/settings/sso-providers/${id}/test`, {}))
  },
}
