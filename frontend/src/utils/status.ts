import { AxiosError } from 'axios'

/** 任务状态 -> 彩色 tag 映射 */
export const TASK_STATUS_META: Record<string, { label: string; type: 'info' | 'warning' | 'success' | 'danger' | 'primary' }> = {
  queued: { label: '排队中', type: 'info' },
  running: { label: '运行中', type: 'warning' },
  succeeded: { label: '成功', type: 'success' },
  failed: { label: '失败', type: 'danger' },
  cancelled: { label: '已取消', type: 'info' },
  awaiting_review: { label: '待审核', type: 'primary' },
}

export function taskStatusMeta(status: string) {
  return TASK_STATUS_META[status] ?? { label: status, type: 'info' as const }
}

/** 文档解析状态 -> tag */
export const DOC_STATUS_META: Record<string, { label: string; type: 'info' | 'warning' | 'success' | 'danger' }> = {
  pending: { label: '待解析', type: 'info' },
  parsing: { label: '解析中', type: 'warning' },
  succeeded: { label: '解析成功', type: 'success' },
  failed: { label: '解析失败', type: 'danger' },
}

export function docStatusMeta(status: string) {
  return DOC_STATUS_META[status] ?? { label: status, type: 'info' as const }
}

export type ViewState = 'loading' | 'ready' | 'empty' | 'error' | 'forbidden'

/** 统一把请求错误分类为「失败重试」或「无权限」 */
export function classifyError(err: unknown): 'forbidden' | 'error' {
  return err instanceof AxiosError && err.response?.status === 403 ? 'forbidden' : 'error'
}
