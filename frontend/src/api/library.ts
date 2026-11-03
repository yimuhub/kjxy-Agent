import type { AxiosRequestConfig } from 'axios'
import { api } from './client'
import type { ApiResponse } from '@/types/api'

/**
 * 初始化即发的只读探测（状态/源/条目/台账/更新中心）：失败由面板内联态兜底，
 * 不走全局 toast——后端版本落后于前端时，避免一进「插件中心」就连弹原始 404。
 */
const silentCfg: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }

/**
 * 官方技能/插件库（签名源）接口。
 *
 * 后端路由集中在 /library 下：源配置 + 条目目录 + 已安装台账。
 * 所有响应按统一信封 `{ data, request_id }` 解析；字段缺失时按可选处理，
 * 页面侧只依赖本文件的宽松类型，避免并行实现期因字段差异直接崩。
 */
export type LibrarySourceKind = 'official' | 'private'
export type LibraryChannel = 'stable' | 'beta'
export type LibraryItemType = 'skill' | 'plugin'

export interface LibrarySource {
  id: string
  name?: string
  kind?: LibrarySourceKind | string
  base_url?: string
  channel?: LibraryChannel | string
  /** 公钥指纹（客户端展示用；不回明文公钥） */
  key_id?: string | null
  enabled?: boolean
  status?: string
  last_sync_at?: string | null
  last_error?: string | null
  sequence?: number
}

/** /library/status 中精简的源状态（不含 base_url / 公钥等配置细节） */
export interface LibrarySourceStatus {
  id: string
  name?: string
  channel?: LibraryChannel | string
  status?: string
  last_sync_at?: string | null
  update_count?: number
}

export interface LibraryStatus {
  sources?: LibrarySourceStatus[]
  update_count?: number
  offline?: boolean
  last_sync_at?: string | null
}

export interface LibraryItem {
  id: string
  source_id?: string
  item_key?: string
  item_type?: LibraryItemType | string
  code?: string
  name?: string
  version?: string
  min_app_version?: string | null
  max_app_version?: string | null
  description?: string
  category?: string
  risk_level?: string
  license?: string
  author?: string
  sha256?: string
  size_bytes?: number
  status?: string
  changelog?: string
}

export interface LibraryInstall {
  id: string
  item_key?: string
  item_type?: LibraryItemType | string
  installed_version?: string
  previous_version?: string | null
  auto_update?: boolean
  pinned?: boolean
  installed_at?: string | null
  latest_version?: string | null
  update_available?: boolean
  risk_level?: string
  can_auto_update?: boolean
}

export type PluginRisk = 'low' | 'medium' | 'high' | 'critical'

export interface PluginPermission {
  code: string
  label: string
  description: string
  risk: PluginRisk | string
  requires_ack: boolean
}

export interface PluginConsent {
  id: string
  item_key: string
  plugin_id: string
  plugin_version: string
  scope_hash: string
  permissions: string[]
  permission_details?: PluginPermission[]
  risk_level: PluginRisk | string
  ack_version?: string
  accepted_by?: string | null
  accepted_at?: string | null
  revoked_at?: string | null
  active: boolean
}

export interface ConsentRequirement {
  item_key: string
  version: string
  required: boolean
  scope_hash: string
  permissions: PluginPermission[]
  platforms: string[]
  risk_level: PluginRisk | string
  granted: boolean
  consent?: PluginConsent | null
  ack_text: string
  ack_version: string
}

export interface LibraryUpdate extends LibraryInstall {
  name?: string
  description?: string
  published_at?: string | null
  blocked?: boolean
}

export interface LibraryUpdates {
  items: LibraryUpdate[]
  total: number
  auto_updatable: number
  risk_summary: Record<string, number>
}

export interface PluginConsentList {
  items: PluginConsent[]
  total: number
  active: number
  ack_text: string
  ack_version: string
}

export interface LibrarySourceInput {
  name: string
  kind: LibrarySourceKind
  base_url: string
  channel: LibraryChannel
  /** Base64 公钥；编辑时留空表示不更换 */
  public_key_b64?: string
  enabled: boolean
}

export type LibrarySourcePatch = Partial<LibrarySourceInput>

export interface LibrarySyncResult {
  status?: string
  added?: number
  updated?: number
  removed?: number
  skipped?: number
  message?: string
}

export interface LibraryInstallUpdateResult {
  installed_version?: string
  previous_version?: string | null
}

export interface LibraryInstallRollbackResult {
  installed_version?: string
}

export interface LibraryItemQuery {
  source_id?: string
  item_type?: string
  keyword?: string
}

interface LibrarySourcesResponse {
  items?: LibrarySource[]
}

interface LibraryItemsResponse {
  items?: LibraryItem[]
}

interface LibraryInstallsResponse {
  items?: LibraryInstall[]
}

interface LibraryUpdatesResponse {
  items?: LibraryUpdate[]
  total?: number
  auto_updatable?: number
  risk_summary?: Record<string, number>
}

interface PluginConsentsResponse {
  items?: PluginConsent[]
  total?: number
  active?: number
  ack_text?: string
  ack_version?: string
}

/**
 * 解包统一信封：兼容 `{ data, request_id }` 与后端直接返回裸对象两种形态。
 * 列表接口再各自容忍 `items` 缺失。
 */
function unwrap<T>(request: Promise<{ data: ApiResponse<T> | T }>): Promise<T> {
  return request.then(res => {
    const body = res.data as unknown
    if (body && typeof body === 'object' && 'data' in body) {
      return (body as ApiResponse<T>).data
    }
    return body as T
  })
}

function toList<T>(raw: { items?: T[] } | T[] | null | undefined): T[] {
  if (Array.isArray(raw)) return raw
  return Array.isArray(raw?.items) ? raw.items : []
}

export const officialLibraryApi = {
  /** 总览：源状态、可更新数量、离线标记 */
  status(): Promise<LibraryStatus> {
    return unwrap<LibraryStatus>(api.get<ApiResponse<LibraryStatus>>('/library/status', silentCfg))
  },

  sources(): Promise<LibrarySource[]> {
    return unwrap<LibrarySourcesResponse | LibrarySource[]>(
      api.get<ApiResponse<LibrarySourcesResponse | LibrarySource[]>>('/library/sources', silentCfg),
    ).then(res => toList<LibrarySource>(res))
  },

  createSource(payload: LibrarySourceInput): Promise<LibrarySource | null> {
    return unwrap<LibrarySource | null>(
      api.post<ApiResponse<LibrarySource | null>>('/library/sources', payload),
    )
  },

  updateSource(id: string, payload: LibrarySourcePatch): Promise<LibrarySource | null> {
    return unwrap<LibrarySource | null>(
      api.patch<ApiResponse<LibrarySource | null>>(`/library/sources/${id}`, payload),
    )
  },

  removeSource(id: string): Promise<{ deleted?: string } | null> {
    return unwrap<{ deleted?: string } | null>(
      api.delete<ApiResponse<{ deleted?: string } | null>>(`/library/sources/${id}`),
    )
  },

  syncSource(id: string): Promise<LibrarySyncResult> {
    return unwrap<LibrarySyncResult>(
      api.post<ApiResponse<LibrarySyncResult>>(`/library/sources/${id}/sync`),
    )
  },

  items(query: LibraryItemQuery = {}): Promise<LibraryItem[]> {
    const params: Record<string, string> = {}
    if (query.source_id) params.source_id = query.source_id
    if (query.item_type) params.item_type = query.item_type
    if (query.keyword?.trim()) params.keyword = query.keyword.trim()
    return unwrap<LibraryItemsResponse | LibraryItem[]>(
      api.get<ApiResponse<LibraryItemsResponse | LibraryItem[]>>('/library/items', {
        params,
        ...silentCfg,
      }),
    ).then(res => toList<LibraryItem>(res))
  },

  installItem(id: string, version?: string): Promise<LibraryInstall | null> {
    return unwrap<LibraryInstall | null>(
      api.post<ApiResponse<LibraryInstall | null>>(
        `/library/items/${id}/install`,
        version ? { version } : {},
      ),
    )
  },

  installs(): Promise<LibraryInstall[]> {
    return unwrap<LibraryInstallsResponse | LibraryInstall[]>(
      api.get<ApiResponse<LibraryInstallsResponse | LibraryInstall[]>>('/library/installs', silentCfg),
    ).then(res => toList<LibraryInstall>(res))
  },

  updateInstall(id: string): Promise<LibraryInstallUpdateResult> {
    return unwrap<LibraryInstallUpdateResult>(
      api.post<ApiResponse<LibraryInstallUpdateResult>>(`/library/installs/${id}/update`),
    )
  },

  rollbackInstall(id: string): Promise<LibraryInstallRollbackResult> {
    return unwrap<LibraryInstallRollbackResult>(
      api.post<ApiResponse<LibraryInstallRollbackResult>>(`/library/installs/${id}/rollback`),
    )
  },

  pinInstall(id: string, pinned: boolean): Promise<{ pinned?: boolean } | null> {
    return unwrap<{ pinned?: boolean } | null>(
      api.post<ApiResponse<{ pinned?: boolean } | null>>(`/library/installs/${id}/pin`, {
        pinned,
      }),
    )
  },

  setAutoUpdate(id: string, autoUpdate: boolean): Promise<{ auto_update?: boolean } | null> {
    return unwrap<{ auto_update?: boolean } | null>(
      api.post<ApiResponse<{ auto_update?: boolean } | null>>(
        `/library/installs/${id}/auto-update`,
        { auto_update: autoUpdate },
      ),
    )
  },

  updates(): Promise<LibraryUpdates> {
    return unwrap<LibraryUpdatesResponse>(
      api.get<ApiResponse<LibraryUpdatesResponse>>('/library/updates', silentCfg),
    ).then(
      res => ({
        items: Array.isArray(res?.items) ? res.items : [],
        total: res?.total ?? 0,
        auto_updatable: res?.auto_updatable ?? 0,
        risk_summary: res?.risk_summary ?? {},
      }),
    )
  },

  consents(): Promise<PluginConsentList> {
    return unwrap<PluginConsentsResponse>(
      api.get<ApiResponse<PluginConsentsResponse>>('/library/consents', silentCfg),
    ).then(res => ({
      items: Array.isArray(res?.items) ? res.items : [],
      total: res?.total ?? 0,
      active: res?.active ?? 0,
      ack_text: res?.ack_text ?? '',
      ack_version: res?.ack_version ?? '',
    }))
  },

  revokeConsent(id: string): Promise<{ consent?: PluginConsent } | null> {
    return unwrap<{ consent?: PluginConsent } | null>(
      api.delete<ApiResponse<{ consent?: PluginConsent } | null>>(`/library/consents/${id}`),
    )
  },

  itemConsent(id: string): Promise<ConsentRequirement> {
    return unwrap<ConsentRequirement>(
      api.get<ApiResponse<ConsentRequirement>>(`/library/items/${id}/consent`, silentCfg),
    )
  },

  grantConsent(id: string): Promise<{ consent?: PluginConsent; idempotent?: boolean } | null> {
    return unwrap<{ consent?: PluginConsent; idempotent?: boolean } | null>(
      api.post<ApiResponse<{ consent?: PluginConsent; idempotent?: boolean } | null>>(
        `/library/items/${id}/consent`,
        { ack: true },
      ),
    )
  },
}
