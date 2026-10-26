import { AxiosError } from 'axios'

/** 任务状态 -> 彩色 tag 映射 */
export const TASK_STATUS_META: Record<
  string,
  { label: string; type: 'info' | 'warning' | 'success' | 'danger' | 'primary' }
> = {
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
export const DOC_STATUS_META: Record<
  string,
  { label: string; type: 'info' | 'warning' | 'success' | 'danger' }
> = {
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

/**
 * 错误码 -> 中文文案映射（见 contracts/error-codes.md）。
 * 界面上的错误提示一律先过这张表；未收录且后端给的是纯英文时，
 * 兜底为通用中文提示，绝不把原始英文细节直接抛给用户。
 */
export const ERROR_CODE_MESSAGES: Record<string, string> = {
  UNAUTHORIZED: '登录状态已失效，请重新登录',
  INVALID_CREDENTIALS: '用户名或密码错误',
  FORBIDDEN: '当前账号无权执行该操作',
  YARD_REQUIRED: '该账号归属多个小院，请选择后再登录',
  NOT_FOUND: '内容不存在或已被删除',
  CONFLICT: '操作冲突，请刷新后重试',
  VALIDATION_ERROR: '提交的内容不符合要求，请检查后重试',
  RATE_LIMITED: '操作过于频繁，请稍后再试',
  INTERNAL_ERROR: '服务暂时不可用，请稍后重试',
  NETWORK_ERROR: '网络连接失败，请检查网络后重试',
  TIMEOUT: '请求超时，请稍后重试',
  UNKNOWN: '发生未知错误，请稍后重试',
  KNOWLEDGE_INGEST_FAILED: '知识库写入失败，请重试或联系管理员',
  DOCUMENT_TOO_LARGE: '文件超过大小限制，单个文件不超过 50MB',
  UNSUPPORTED_FILE_TYPE: '暂不支持该文件类型，请使用 PDF/DOCX/TXT/MD/CSV',
  DOCUMENT_NOT_REPARSABLE: '仅解析失败的文档支持重新解析',
  TASK_NOT_RETRYABLE: '该任务当前状态不支持重试',
  QUOTA_EXCEEDED: '用量额度已用尽，请联系管理员调整',
  POLICY_DUPLICATE: '该政策已采集，无需重复录入',
  LLM_UNAVAILABLE: '模型服务暂时不可用，请稍后重试',
}

/** 未收录错误码时的统一兜底文案（不暴露后端英文细节） */
export const GENERIC_ERROR_MESSAGE = '操作失败，请稍后重试；若反复失败请联系管理员'

/**
 * 生成面向用户的中文错误提示：
 * 1. 命中错误码映射表 -> 用映射文案；
 * 2. 后端 message 本身含中文 -> 直接用（后端已本地化）；
 * 3. 其余（纯英文/空）-> 通用兜底文案。
 */
export function friendlyErrorMessage(code: string, message?: string): string {
  const mapped = ERROR_CODE_MESSAGES[code]
  if (mapped) return mapped
  if (message && /[\u4e00-\u9fff]/.test(message)) return message
  return GENERIC_ERROR_MESSAGE
}
