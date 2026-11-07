/**
 * 桌宠轮播短句的纯文案函数（v3.70）：
 * 天气/工单/警报的结构化数据 → 一句能读懂的话。
 * 只做拼句，不发请求（取数编排见 petBrief.ts），可被 node 单测直跑。
 */

import type { FarmAlert, WorkOrder } from '@/api/modules'

function round1(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-'
  return String(Math.round(value * 10) / 10)
}

/** 天气摘要的 brief 字段（与后端 /farm/weather/brief 对齐） */
export interface WeatherBriefPayload {
  configured: boolean
  brief: {
    temp_max: number | null
    temp_min: number | null
    rain_mm: number | null
    wind_max_ms: number | null
    humidity_mean: number | null
    tomorrow_temp_min: number | null
    generated_at: string | null
  } | null
  warning: string | null
}

/** 天气摘要 → 短句（未配置或字段缺失返回空串，不占轮播位） */
export function weatherLine(source: WeatherBriefPayload): string {
  if (!source.configured || !source.brief) return ''
  const b = source.brief
  if (typeof b.temp_max !== 'number' && typeof b.temp_min !== 'number') return ''
  const parts: string[] = []
  parts.push(`未来24h ${round1(b.temp_min)}~${round1(b.temp_max)}℃`)
  if (typeof b.rain_mm === 'number' && b.rain_mm >= 0.5) {
    parts.push(`降水约${round1(b.rain_mm)}mm，下地记得带伞`)
  } else {
    parts.push('基本无雨')
  }
  const tomorrow =
    typeof b.tomorrow_temp_min === 'number' && b.tomorrow_temp_min <= 5
      ? `明天最低${round1(b.tomorrow_temp_min)}℃，注意保暖`
      : ''
  return `${[parts.join('，'), tomorrow].filter(Boolean).join('。')}。`
}

const PRIORITY_LABEL: Record<string, string> = {
  low: '不急',
  normal: '普通',
  high: '急',
  critical: '特急',
}

/** 待处理工单（已按紧急度排序）→ 短句（没有待办返回空串） */
export function workOrderLine(orders: WorkOrder[]): string {
  if (orders.length === 0) return ''
  const first = orders[0]
  const label = PRIORITY_LABEL[first.priority] ?? ''
  const head = label ? `一张${label}的` : '一张'
  if (orders.length === 1) return `有 1 张工单待处理：${head}「${first.title}」。`
  return `有 ${orders.length} 张工单待处理，最急一张：「${first.title}」。`
}

const SEVERITY_LABEL: Record<string, string> = {
  low: '一般',
  normal: '一般',
  high: '高级别',
  critical: '紧急',
}

/** 未处理警报 → 短句（critical 提到最前、换语气；没有警报返回空串） */
export function alertLine(alerts: FarmAlert[]): string {
  if (alerts.length === 0) return ''
  const critical = alerts.find(item => item.severity === 'critical')
  if (critical) return `⚠ 有紧急警报：「${critical.title}」，建议尽快处理。`
  const label = SEVERITY_LABEL[alerts[0]?.severity ?? 'normal'] ?? ''
  return `有 ${alerts.length} 条未处理${label}警报，别忘了看一眼。`
}

/** 工单紧急度排序权重（critical 最先） */
export function workOrderPriorityRank(priority: string): number {
  const order = ['critical', 'high', 'normal', 'low']
  return order.indexOf(priority) === -1 ? order.length : order.indexOf(priority)
}

/** 逾期工单 → 短句（没有逾期返回空串；today 传 YYYY-MM-DD 便于单测） */
export function overdueOrderLine(
  orders: { due_on: string | null; status: string; title: string }[],
  today: string,
): string {
  const active = orders.filter(item => ['open', 'assigned', 'in_progress'].includes(item.status))
  const overdue = active.filter(
    item => typeof item.due_on === 'string' && item.due_on !== '' && item.due_on < today,
  )
  if (overdue.length === 0) return ''
  if (overdue.length === 1) return `有 1 张工单已逾期：「${overdue[0].title}」，抓紧处理。`
  return `有 ${overdue.length} 张工单已逾期，最急的一张：「${overdue[0].title}」。`
}
