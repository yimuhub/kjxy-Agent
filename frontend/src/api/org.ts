import { api } from './client'
import type { ApiResponse } from '@/types/api'

/** 组织架构（v3.57）：组织树、岗位与 Agent 绑定。 */

export type OrgUnitKind = 'company' | 'coop' | 'department' | 'team'
export type OrgUnitStatus = 'active' | 'archived'

export interface OrgAgentSummary {
  id: string
  agent_code: string
  name: string
  emoji?: string
  status?: string
}

export interface OrgPosition {
  id: string
  org_unit_id: string
  title: string
  responsibilities: string
  headcount: number
  agent_id: string | null
  agent: OrgAgentSummary | null
  sort_order: number
  status: OrgUnitStatus
  created_at?: string
  updated_at?: string
}

export interface OrgUnit {
  id: string
  parent_id: string | null
  code: string
  name: string
  kind: OrgUnitKind
  kind_label: string
  description: string
  sort_order: number
  status: OrgUnitStatus
  source: string
  created_at?: string
  updated_at?: string
  children: OrgUnit[]
  positions: OrgPosition[]
}

export interface OrgFlatUnit {
  id: string
  parent_id: string | null
  name: string
  kind: OrgUnitKind
  kind_label: string
  depth: number
  path: string
}

export interface OrgTree {
  items: OrgUnit[]
  flat: OrgFlatUnit[]
  summary: {
    units: number
    departments: number
    positions: number
    agents_bound: number
    roots: number
  }
  include_archived: boolean
}

export interface OrgUnitPayload {
  name: string
  parent_id?: string | null
  code?: string | null
  kind?: OrgUnitKind
  description?: string | null
  sort_order?: number
  status?: OrgUnitStatus
}

export interface OrgPositionPayload {
  org_unit_id: string
  title: string
  responsibilities?: string | null
  headcount?: number
  agent_id?: string | null
  sort_order?: number
  status?: OrgUnitStatus
}

function unwrap<T>(promise: Promise<{ data: ApiResponse<T> }>): Promise<T> {
  return promise.then(res => res.data.data)
}

export const orgApi = {
  tree(includeArchived = false): Promise<OrgTree> {
    return unwrap(
      api.get<ApiResponse<OrgTree>>('/org/tree', {
        params: includeArchived ? { include_archived: true } : {},
      }),
    )
  },
  unit(id: string): Promise<OrgUnit> {
    return unwrap(api.get<ApiResponse<OrgUnit>>(`/org/units/${id}`))
  },
  createUnit(payload: OrgUnitPayload): Promise<OrgUnit> {
    return unwrap(api.post<ApiResponse<OrgUnit>>('/org/units', payload))
  },
  updateUnit(id: string, payload: Partial<OrgUnitPayload>): Promise<OrgUnit> {
    return unwrap(api.put<ApiResponse<OrgUnit>>(`/org/units/${id}`, payload))
  },
  deleteUnit(id: string): Promise<{ deleted: boolean }> {
    return unwrap(api.delete<ApiResponse<{ deleted: boolean }>>(`/org/units/${id}`))
  },
  createPosition(payload: OrgPositionPayload): Promise<OrgPosition> {
    return unwrap(api.post<ApiResponse<OrgPosition>>('/org/positions', payload))
  },
  updatePosition(id: string, payload: Partial<OrgPositionPayload>): Promise<OrgPosition> {
    return unwrap(api.put<ApiResponse<OrgPosition>>(`/org/positions/${id}`, payload))
  },
  deletePosition(id: string): Promise<{ deleted: boolean }> {
    return unwrap(api.delete<ApiResponse<{ deleted: boolean }>>(`/org/positions/${id}`))
  },
}
