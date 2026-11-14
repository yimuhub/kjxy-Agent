/** 时间与耗时格式化工具（全站共用） */

/**
 * 解析后端时间戳。
 *
 * 后端各服务的 `created_at/updated_at` 都是 **UTC 裸时间**（`isoformat()` 不带 +00:00/Z），
 * 直接 `new Date()` 会被浏览器当成本地时间，国内环境整体早 8 小时。
 * 这里统一补上 UTC 标记；已带时区（Z 或 ±HH:MM）的原样解析，前端自己生成的时间不受影响。
 */
export function parseApiTime(t?: string | null): Date | null {
  if (!t) return null
  const raw = String(t).trim()
  // 纯日期（2026-10-28）本身就是按 UTC 解析的，补 Z 反而解析失败
  if (!raw.includes('T')) {
    const plain = new Date(raw)
    return isNaN(plain.getTime()) ? null : plain
  }
  const aware = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(raw) ? raw : `${raw}Z`
  const d = new Date(aware)
  return isNaN(d.getTime()) ? null : d
}

const pad = (n: number) => String(n).padStart(2, '0')

/**
 * 时间戳 -> 本地毫秒，带一层缓存。
 *
 * 长列表的排序与展示会反复读同一批时间串（308 行每次数十次），`new Date()` 与正则
 * 每次都重做；后端这些字符串是不可变的，缓存命中即可。超过上限整体丢弃，不无界增长。
 */
const EPOCH_CACHE_LIMIT = 4096
const epochCache = new Map<string, number>()

export function apiTimeMs(t?: string | null): number {
  if (!t) return 0
  const key = String(t)
  const hit = epochCache.get(key)
  if (hit !== undefined) return hit
  const ms = parseApiTime(t)?.getTime() ?? 0
  if (epochCache.size >= EPOCH_CACHE_LIMIT) epochCache.clear()
  epochCache.set(key, ms)
  return ms
}

/** ISO 时间 -> 本地化展示；无效值显示占位符 */
export function formatTime(t?: string | null): string {
  const d = parseApiTime(t)
  return d ? d.toLocaleString('zh-CN') : t || '—'
}

/** 列表用的完整时间：年-月-日 时:分（本地时区）；无效值显示占位符 */
export function formatDateTime(t?: string | null): string {
  const d = parseApiTime(t)
  if (!d) return '—'
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/**
 * 金额（元）格式化：千分位。整数价默认不带小数；传 digits 可保留小数位。
 * 计费面板与运营套餐管理共用同一口径，避免"同一价格两页两种显示"。
 */
export function formatPriceYuan(value: number | null | undefined, digits = 0): string {
  return Number(value ?? 0).toLocaleString('zh-CN', { maximumFractionDigits: digits })
}

/**
 * 额度数字格式化（计费口径）：Token/次数用千分位取整；算力金额（metric=cost）保留两位。
 * BillingPanel 与 PlanAdminPanel 共用。
 */
export function formatQuotaAmount(value: number | null | undefined, metric?: string): string {
  const num = Number(value ?? 0)
  if (metric === 'cost') {
    return num.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
  }
  return num.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}

/**
 * 由起止时间计算耗时，返回值单位秒的展示文本（如 12.3s）。
 * 任一时间缺失/无效则显示占位符；running 中的任务不计算。
 */
export function formatDuration(start?: string | null, end?: string | null): string {
  if (!start || !end) return '—'
  const s = parseApiTime(start)?.getTime() ?? NaN
  const e = parseApiTime(end)?.getTime() ?? NaN
  if (isNaN(s) || isNaN(e) || e < s) return '—'
  const seconds = (e - s) / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  const rest = Math.round(seconds % 60)
  if (minutes < 60) return `${minutes}m${rest}s`
  return `${Math.floor(minutes / 60)}h${minutes % 60}m`
}
