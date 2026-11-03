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

export type OrgProposalStatus = 'generating' | 'ready' | 'applied' | 'discarded' | 'failed'

export interface OrgProposalPosition {
  title: string
  responsibilities?: string
  headcount?: number
  action?: 'create' | 'existing'
}

export interface OrgProposalUnit {
  name: string
  kind: OrgUnitKind
  parent_name?: string | null
  description?: string
  positions: OrgProposalPosition[]
  agent_suggestion?: {
    name: string
    mission?: string
    reason?: string
  } | null
  action?: 'create' | 'existing'
}

export interface OrgProposalEvidence {
  chunk_id: string
  document_id: string
  title: string
  page_number?: number | null
  quote: string
  score?: number
}

export interface OrgProposal {
  id: string
  title: string
  status: OrgProposalStatus
  instruction: string
  summary: string
  units: OrgProposalUnit[]
  agent_suggestions: {
    name: string
    department: string
    mission?: string
    reason?: string
  }[]
  evidence: OrgProposalEvidence[]
  evidence_count: number
  model: string
  prompt_version: string
  token_usage: { input_tokens?: number; output_tokens?: number }
  diff: {
    items: OrgProposalUnit[]
    counts: {
      create_units?: number
      existing_units?: number
      create_positions?: number
      existing_positions?: number
    }
  }
  applied_result?: Record<string, number> | null
  error_message: string
  task_id?: string | null
  created_at?: string
  applied_at?: string | null
}

export interface OrgProposalSummary {
  id: string
  title: string
  status: OrgProposalStatus
  summary: string
  units: number
  agents: number
  evidence_count: number
  task_id?: string | null
  error_message: string
  created_at?: string
  applied_at?: string | null
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
  createProposal(instruction: string): Promise<{ proposal_id: string; task_id: string }> {
    return unwrap(
      api.post<ApiResponse<{ proposal_id: string; task_id: string }>>('/org/proposals', {
        instruction,
      }),
    )
  },
  listProposals(): Promise<OrgProposalSummary[]> {
    return unwrap(api.get<ApiResponse<OrgProposalSummary[]>>('/org/proposals'))
  },
  getProposal(id: string, parentId?: string | null): Promise<OrgProposal> {
    return unwrap(
      api.get<ApiResponse<OrgProposal>>(`/org/proposals/${id}`, {
        params: parentId ? { parent_id: parentId } : {},
      }),
    )
  },
  applyProposal(
    id: string,
    parentId?: string | null,
  ): Promise<{
    already_applied: boolean
    result: Record<string, number>
    proposal: OrgProposal
  }> {
    return unwrap(
      api.post<ApiResponse<{ already_applied: boolean; result: Record<string, number>; proposal: OrgProposal }>>(
        `/org/proposals/${id}/apply`,
        { parent_id: parentId ?? null },
      ),
    )
  },
  discardProposal(id: string): Promise<OrgProposalSummary> {
    return unwrap(api.post<ApiResponse<OrgProposalSummary>>(`/org/proposals/${id}/discard`))
  },
}
