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
  /** 当前用户在该枢纽的角色（manager/viewer/yard_manager） */
  my_role?: 'manager' | 'viewer' | 'yard_manager' | string
  created_by: string
}

export interface HubOverviewItem {
  yard_id: string
  yard_name: string
  journal_count: number
  active_authors: number
  last_updated_at: string | null
  stale: boolean
  /** v3.66：小院特性（三种模式分组与横向对比用） */
  province?: string | null
  city?: string | null
  scene_type?: string | null
  attributes?: Record<string, unknown>
  /** 当前枢纽模式下该院的分组键（培养单位/县域/类型） */
  group_key?: string
  /** v3.67：本枢纽与该院的关系口径 */
  relation_type?: 'manage' | 'review' | 'observe' | string
  relation_label?: string
}

export interface HubInsightGroup {
  key: string
  yards: number
  journal_count: number
  compliant: number
}

export interface HubInsights {
  mode: string
  group_by: string
  group_label: string
  /** 制度锚点：年 100 篇 → 月 9 篇 */
  anchor_annual: number
  anchor_monthly: number
  groups: HubInsightGroup[]
  summary: {
    yards: number
    groups: number
    journal_count: number
    compliant: number
    compliance_rate: number
    stale_count: number
    /** v3.67：分组键仍为占位（档案没填）的小院数，前端提示去补档案 */
    unlabeled?: number
  }
}

const silent = { skipErrorToast: true } as Record<string, unknown>

export interface HubRelation {
  hub_id: string
  hub_name: string | null
  relation_type: 'manage' | 'review' | 'observe' | string
  relation_label: string
}

export interface AttachableYard {
  id: string
  name: string
  /** 主归属枢纽（历史上的一院一枢纽） */
  hub_id: string | null
  hub_name: string | null
  /** v3.67：该院全部枢纽关系（可同时有管理/评审/观察） */
  relations: HubRelation[]
  /** 是否已关联到当前请求的枢纽 */
  in_current_hub: boolean
}

export const hubsApi = {
  list(): Promise<{ items: HubRow[] }> {
    return unwrap(api.get<ApiResponse<{ items: HubRow[] }>>('/hubs', silent))
  },
  /** 可关联到枢纽的小院清单（带 hubId 时标出 in_current_hub） */
  attachableYards(hubId?: string): Promise<AttachableYard[]> {
    return unwrap(
      api.get<ApiResponse<AttachableYard[]>>('/hubs/attachable-yards', {
        ...silent,
        params: hubId ? { hub_id: hubId } : undefined,
      }),
    )
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
    insights: HubInsights
  }> {
    return unwrap(
      api.get<
        ApiResponse<{
          hub: HubRow
          stale_days: number
          stale_count: number
          items: HubOverviewItem[]
          insights: HubInsights
        }>
      >(`/hubs/${hubId}/overview`, silent),
    )
  },
  /** 关联小院：manage=日常管理 / review=评审归口 / observe=观察 */
  attach(
    hubId: string,
    yardId: string,
    relationType: 'manage' | 'review' | 'observe' = 'manage',
  ): Promise<{ moved: boolean; primary: boolean; relation_type: string }> {
    return unwrap(
      api.post<ApiResponse<{ moved: boolean; primary: boolean; relation_type: string }>>(
        `/hubs/${hubId}/yards`,
        { yard_id: yardId, relation_type: relationType },
        silent,
      ),
    )
  },
  /** 解除本枢纽与该院的关系（其他枢纽的关联不受影响） */
  detach(hubId: string, yardId: string): Promise<{ hub_id: string | null }> {
    return unwrap(
      api.delete<ApiResponse<{ hub_id: string | null }>>(
        `/hubs/${hubId}/yards/${yardId}`,
        silent,
      ),
    )
  },
  /** 旧签名兼容：按主归属推断枢纽 */
  detachByYard(yardId: string): Promise<{ hub_id: string | null }> {
    return unwrap(api.delete<ApiResponse<{ hub_id: string | null }>>(`/hubs/yards/${yardId}`, silent))
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
