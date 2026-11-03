/** 工作日志/月报（角色工作台 P0）+ 跨小院监管看板（P1）。 */
import { api } from './client'
import type { ApiResponse } from '@/types/api'

function unwrap<T>(promise: Promise<{ data: ApiResponse<T> }>): Promise<T> {
  return promise.then(res => res.data.data)
}

export interface JournalRow {
  id: string
  kind: 'daily' | 'monthly' | string
  period_key: string
  entry_date?: string | null
  title: string
  content_md: string
  status: 'draft' | 'submitted' | 'approved' | string
  author_user_id: string
  reviewed_by?: string | null
  review_note?: string | null
  created_at?: string | null
}

export interface JournalStats {
  month: string
  daily_total: number
  daily_submitted: number
  daily_approved: number
  monthly_total: number
  active_days: number
}

export interface OversightItem {
  yard_id: string
  yard_name: string
  journal_count: number
  active_authors: number
  last_updated_at: string | null
  stale: boolean
}

import type { AxiosRequestConfig } from 'axios'

const silent: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }

export const journalApi = {
  draft(kind: 'daily' | 'monthly', date = ''): Promise<JournalRow> {
    return unwrap(api.post<ApiResponse<JournalRow>>('/journal/draft', { kind, date }, silent))
  },
  list(month = '', scope: 'mine' | 'yard' = 'mine'): Promise<{ items: JournalRow[] }> {
    return unwrap(
      api.get<ApiResponse<{ items: JournalRow[] }>>('/journal', {
        params: { ...(month ? { month } : {}), scope },
        ...silent,
      }),
    )
  },
  save(id: string, content_md: string, title?: string): Promise<JournalRow> {
    return unwrap(
      api.put<ApiResponse<JournalRow>>(`/journal/${id}`, { content_md, title }, silent),
    )
  },
  submit(id: string): Promise<JournalRow> {
    return unwrap(api.post<ApiResponse<JournalRow>>(`/journal/${id}/submit`, {}, silent))
  },
  review(id: string, action: 'approve' | 'reject', note?: string): Promise<JournalRow> {
    return unwrap(
      api.post<ApiResponse<JournalRow>>(`/journal/${id}/review`, { action, note }, silent),
    )
  },
  stats(month = ''): Promise<JournalStats> {
    return unwrap(api.get<ApiResponse<JournalStats>>('/journal/stats', { params: { month }, ...silent }))
  },
  /** 月报导出 Word（blob 下载） */
  async exportMonthly(month: string): Promise<void> {
    const res = await api.get<Blob>('/journal/monthly/export', {
      params: { month },
      responseType: 'blob',
      timeout: 120_000,
    })
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `工作月报-${month}.docx`
    a.click()
    URL.revokeObjectURL(url)
  },
  trainingNotify(title: string, timeText: string, place: string): Promise<{ sent: string }> {
    return unwrap(
      api.post<ApiResponse<{ sent: string }>>(
        '/journal/training/notify',
        { title, time_text: timeText, place },
        silent,
      ),
    )
  },
  oversight(month = ''): Promise<{ month: string; stale_days: number; items: OversightItem[] }> {
    return unwrap(
      api.get<ApiResponse<{ month: string; stale_days: number; items: OversightItem[] }>>(
        '/journal/aggregate',
        { params: { month }, ...silent },
      ),
    )
  },
}
