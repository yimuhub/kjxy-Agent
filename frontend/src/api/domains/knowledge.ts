/**
 * 知识库 / 反馈 / 联网搜索（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import { api, toParams, unwrap, unwrapList } from '../client'
import type { ListResult } from '../client'
import type {
  ApiResponse,
  BatchReviewResult,
  Candidate,
  ChunkHit,
  DocumentReparseResult,
  Feedback,
  KnowledgeChunkPreview,
  KnowledgeDocument,
  KnowledgeSearchResult,
  KnowledgeUploadResult,
  ReviewCandidatePayload,
  SubmitFeedbackPayload,
} from '@/types/api'

// ---------- 知识库 ----------

export const knowledgeApi = {
  upload(file: File, title?: string): Promise<KnowledgeUploadResult> {
    const form = new FormData()
    form.append('file', file)
    if (title) form.append('title', title)
    return unwrap(api.post<ApiResponse<KnowledgeUploadResult>>('/knowledge/documents', form))
  },
  list(query: { page?: number; page_size?: number } = {}): Promise<ListResult<KnowledgeDocument>> {
    return unwrapList(api.get('/knowledge/documents', { params: toParams(query) }))
  },
  get(document_id: string): Promise<KnowledgeDocument> {
    return unwrap(api.get(`/knowledge/documents/${document_id}`))
  },
  /** 查看文档的 MarkItDown 结构化切块与 Token 估算明细 */
  chunks(document_id: string): Promise<KnowledgeChunkPreview[]> {
    // 后端返回 {document_id, chunks, total_tokens}，不是分页列表；按数组解包
    return unwrap(
      api.get<ApiResponse<{ chunks: KnowledgeChunkPreview[]; total_tokens: number }>>(
        `/knowledge/documents/${document_id}/chunks`,
      ),
    ).then(res => res.chunks)
  },
  remove(document_id: string): Promise<void> {
    return api.delete(`/knowledge/documents/${document_id}`).then(() => undefined)
  },
  /** 重新解析：仅 failed 文档可调，返回新解析任务 */
  reparse(document_id: string): Promise<DocumentReparseResult> {
    return unwrap(
      api.post<ApiResponse<DocumentReparseResult>>(`/knowledge/documents/${document_id}/reparse`),
    )
  },
  search(query: string, top_k = 5): Promise<ChunkHit[]> {
    return unwrap<KnowledgeSearchResult>(
      api.post<ApiResponse<KnowledgeSearchResult>>('/knowledge/search', { query, top_k }),
    ).then(r => r.hits)
  },
  candidates(
    query: { status?: string; page?: number; page_size?: number } = { status: 'pending_review' },
  ): Promise<ListResult<Candidate>> {
    return unwrapList(api.get('/knowledge/candidates', { params: toParams(query) }))
  },
  reviewCandidate(candidate_id: string, payload: ReviewCandidatePayload): Promise<Candidate> {
    return unwrap(api.post(`/knowledge/candidates/${candidate_id}/review`, payload))
  },
  /** 批量审核候选知识：一次提交多个 ID 的通过/驳回 */
  batchReview(
    ids: string[],
    action: 'approve' | 'reject',
    review_note?: string,
  ): Promise<BatchReviewResult> {
    return unwrap(
      api.post<ApiResponse<BatchReviewResult>>('/knowledge/candidates/batch-review', {
        ids,
        action,
        review_note,
      }),
    )
  },
}


// ---------- 反馈 ----------

export const feedbackApi = {
  submit(payload: SubmitFeedbackPayload): Promise<Feedback> {
    return unwrap(api.post<ApiResponse<Feedback>>('/feedback', payload))
  },
  list(query: { page?: number; page_size?: number } = {}): Promise<ListResult<Feedback>> {
    return unwrapList(api.get('/feedback', { params: toParams(query) }))
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
  /** v3.33：逐源诊断（code/名称/是否可用/原因） */
  sources?: { code: string; label: string; ok: boolean; reason: string }[]
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
