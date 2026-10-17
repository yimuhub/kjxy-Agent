/** 时间与耗时格式化工具（全站共用） */

/** ISO 时间 -> 本地化展示；无效值显示占位符 */
export function formatTime(t?: string | null): string {
  if (!t) return '—'
  const d = new Date(t)
  return isNaN(d.getTime()) ? t : d.toLocaleString('zh-CN')
}

/**
 * 由起止时间计算耗时，返回值单位秒的展示文本（如 12.3s）。
 * 任一时间缺失/无效则显示占位符；running 中的任务不计算。
 */
export function formatDuration(start?: string | null, end?: string | null): string {
  if (!start || !end) return '—'
  const s = new Date(start).getTime()
  const e = new Date(end).getTime()
  if (isNaN(s) || isNaN(e) || e < s) return '—'
  const seconds = (e - s) / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (minutes < 60) return `${minutes}m${rest}s`
  return `${Math.floor(minutes / 60)}h${minutes % 60}m`
}
