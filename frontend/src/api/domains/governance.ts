/**
 * 政策模块（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import { api, toParams, unwrap, unwrapList } from '../client'
import type { ListResult } from '../client'
import type {
  ApiResponse,
  ApplicationDraft,
  ApplicationDraftPayload,
  ListResponse,
  Policy,
  PolicyIngestPayload,
  PolicyMatchResult,
  PolicyProfile,
} from '@/types/api'

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
  /** 查看申报材料草案（小院隔离） */
  getDraft(draft_id: string): Promise<ApplicationDraft> {
    return unwrap(
      api.get<ApiResponse<ApplicationDraft>>(`/policies/application-drafts/${draft_id}`),
    )
  },
}
