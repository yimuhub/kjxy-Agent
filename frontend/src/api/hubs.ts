/** 枢纽/运营模式（通用基座 v3.65）。 */
import { api } from './client'
import type { ApiResponse } from '@/types/api'

function unwrap<T>(promise: Promise<{ data: ApiResponse<T> }>): Promise<T> {
  return promise.then(res => res.data.data)
}

export interface HubRow {
  id: string
  name: string
  mode: 'province' | 'municipal' | 'station' | string
  mode_label: string
  mode_focus: string
  region: string
  note: string
  yard_count: number
  members: { user_id: string; role: 'manager' | 'viewer' | string }[]
  created_by: string
}

export interface HubOverviewItem {
  yard_id: string
  yard_name: string
  journal_count: number
  active_authors: number
  last_updated_at: string | null
  stale: boolean
}

const silent = { skipErrorToast: true } as Record<string, unknown>

export interface AttachableYard {
  id: string
  name: string
  hub_id: string | null
  hub_name: string | null
}

export const hubsApi = {
  list(): Promise<{ items: HubRow[] }> {
    return unwrap(api.get<ApiResponse<{ items: HubRow[] }>>('/hubs', silent))
  },
  /** 可绑定到枢纽的小院清单（platform_admin 全部；普通用户为其管理的小院） */
  attachableYards(): Promise<AttachableYard[]> {
    return unwrap(api.get<ApiResponse<AttachableYard[]>>('/hubs/attachable-yards', silent))
  },
  create(payload: {
    name: string
    mode: string
    region: string
    note: string
  }): Promise<HubRow> {
    return unwrap(api.post<ApiResponse<HubRow>>('/hubs', payload, silent))
  },
  overview(hubId: string): Promise<{
    hub: HubRow
    stale_days: number
    stale_count: number
    items: HubOverviewItem[]
  }> {
    return unwrap(
      api.get<ApiResponse<{ hub: HubRow; stale_days: number; stale_count: number; items: HubOverviewItem[] }>>(
        `/hubs/${hubId}/overview`,
        silent,
      ),
    )
  },
  attach(hubId: string, yardId: string): Promise<{ moved: boolean }> {
    return unwrap(
      api.post<ApiResponse<{ moved: boolean }>>(`/hubs/${hubId}/yards`, { yard_id: yardId }, silent),
    )
  },
  detach(yardId: string): Promise<{ hub_id: null }> {
    return unwrap(api.delete<ApiResponse<{ hub_id: null }>>(`/hubs/yards/${yardId}`, silent))
  },
  /** 可添加为枢纽成员的用户清单（活跃用户，已在本枢纽的排除） */
  memberCandidates(hubId: string): Promise<
    { id: string; username: string; display_name: string; already_member: boolean }[]
  > {
    return unwrap(
      api.get<
        ApiResponse<{ id: string; username: string; display_name: string; already_member: boolean }[]>
      >(`/hubs/${hubId}/member-candidates`, silent),
    )
  },
  addMember(hubId: string, userId: string, role: string): Promise<{ role: string }> {
    return unwrap(
      api.post<ApiResponse<{ role: string }>>(`/hubs/${hubId}/members`, { user_id: userId, role }, silent),
    )
  },
}
