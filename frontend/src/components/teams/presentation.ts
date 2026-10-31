import type { TeamRun, TeamRunStatus, TeamSchedule } from '@/api/modules'
import { saveBlobOrDownload } from '../../utils/download.ts'

export function isRunActive(status: TeamRunStatus): boolean {
  return status === 'queued' || status === 'running'
}

export function runStatusLabel(status: TeamRunStatus): string {
  return { queued: '排队中', running: '执行中', completed: '已完成', failed: '已失败' }[status] ?? status
}

export function runStatusTag(status: TeamRunStatus): 'success' | 'warning' | 'danger' | 'info' {
  return status === 'completed' ? 'success' : status === 'failed' ? 'danger' : status === 'queued' ? 'info' : 'warning'
}

/** 不以 document_available 或创建时格式限制导出：历史文字答案也可导出。 */
export function canExportRun(run: Pick<TeamRun, 'status' | 'answer'>): boolean {
  return run.status === 'completed' && !!run.answer?.trim()
}

export function scheduleError(schedule: TeamSchedule): string {
  if (!Number.isInteger(schedule.interval_minutes) || schedule.interval_minutes < 5 || schedule.interval_minutes > 43200) {
    return '运行间隔需为 5–43200 的整数分钟'
  }
  if (schedule.enabled && !schedule.input.trim()) return '启用定时任务前请填写运行问题'
  if (schedule.input.length > 8000) return '运行问题不能超过 8000 字'
  if (!['text', 'md', 'docx'].includes(schedule.output_format)) return '请选择有效的结果格式'
  return ''
}

/** 卡片用可读周期替代"每 1440 分钟"这类难懂的原始分钟数。 */
export function scheduleIntervalLabel(minutes: number): string {
  if (minutes >= 1440 && minutes % 1440 === 0) return `每 ${minutes / 1440} 天`
  if (minutes >= 60 && minutes % 60 === 0) return `每 ${minutes / 60} 小时`
  return `每 ${minutes} 分钟`
}

/** 外链只允许明确的 HTTP(S) 地址，原始 HTML、相对地址与脚本协议均不执行。 */
export function safeHttpUrl(value: string | null | undefined): string | undefined {
  if (!value || !/^https?:\/\//i.test(value)) return undefined
  if (Array.from(value).some(char => char.charCodeAt(0) <= 32 || char.charCodeAt(0) === 127)) return undefined
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) ? url.href : undefined
  } catch {
    return undefined
  }
}

//: 同源海报地址：随机存储键 + 32 位 HMAC 签名（<img> 带不了鉴权头，只能走 capability URL）
const POSTER_PATH = /^\/api\/v1\/growth\/posters\/[a-z0-9/._-]+\?sig=[a-f0-9]{32}$/

/** 图片地址：http(s) 外链，或同源海报签名路径；其余（data:、相对路径、协议混淆）一律拒绝。 */
export function safeImageUrl(value: string | null | undefined): string | undefined {
  const http = safeHttpUrl(value)
  if (http) return http
  if (!value || /[\\\s]/.test(value) || value.includes('..')) return undefined
  return POSTER_PATH.test(value) ? value : undefined
}

export function teamDocumentFilename(name: string | null, runId: string, format: 'md' | 'docx'): string {
  const basename = `${name || '班组运行'}-${runId}`
  const safeName = Array.from(basename).map(char => char.charCodeAt(0) < 32 || /[\\/:*?"<>|]/.test(char) ? '_' : char).join('')
  return `${safeName}.${format}`
}

/** 使用已鉴权请求返回的 Blob：桌面端写下载目录，浏览器端释放临时 URL。 */
export function saveDocumentBlob(blob: Blob, filename: string): void {
  void saveBlobOrDownload(filename, blob)
}
