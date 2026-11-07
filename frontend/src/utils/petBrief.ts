/**
 * 桌宠轮播内容源（v3.70）：
 * 在固定问候/场景提示之外，周期性拉取小院实况——天气摘要、待处理工单、
 * 未处理警报、后台进行中任务——生成短句列表，交给悬浮伙伴与桌宠窗口轮播。
 *
 * 铁律：轮播是陪伴不是功能依赖，任何请求失败都静默跳过；
 * 只输出短句与数量，不带正文、不带密钥；无权限时整源跳过（不打 403）。
 */

import { api } from '@/api/client'
import { farmAgentApi, workOrderApi } from '@/api/modules'
import {
  alertLine,
  overdueOrderLine,
  weatherLine,
  workOrderLine,
  workOrderPriorityRank,
} from '@/utils/petBriefLines'

/** 是否有权限拉取农业数据（工单/警报/天气）；未提供探针时默认尝试、失败静默 */
export type PermProbe = (permission: string) => boolean

const ACTIVE_ORDER_STATUS = ['open', 'assigned', 'in_progress'] as const

interface TaskListMeta {
  total?: number
}

interface TaskListEnvelope {
  data?: unknown[]
  meta?: TaskListMeta
}

/** 后台进行中任务 → 短句（没有在跑的返回空串） */
export async function runningTaskLine(): Promise<string> {
  const res = await api.get<TaskListEnvelope>('/tasks', {
    params: { status: 'running', page: 1, page_size: 1 },
  })
  const total = Number(res.data?.meta?.total ?? 0)
  if (!Number.isFinite(total) || total <= 0) return ''
  return total === 1
    ? '后台有 1 个任务在跑，出结果我会喊你。'
    : `后台有 ${total} 个任务在跑，出结果我会喊你。`
}

/** 待处理工单：拉回来按紧急度排，再拼短句；逾期单独提醒 */
async function pendingWorkOrderLine(): Promise<string> {
  const res = await workOrderApi.list({ page: 1, page_size: 20 })
  const active = res.items
    .filter(item => (ACTIVE_ORDER_STATUS as readonly string[]).includes(item.status))
    .sort((a, b) => workOrderPriorityRank(a.priority) - workOrderPriorityRank(b.priority))
  const today = new Date().toISOString().slice(0, 10)
  return overdueOrderLine(res.items, today) || workOrderLine(active)
}

/** 一次数据源盘点：天气/工单/警报/后台任务 → 短句列表（失败源静默跳过） */
export async function collectPetLines(canRead?: PermProbe): Promise<string[]> {
  const lines: string[] = []

  const farmAllowed = typeof canRead !== 'function' || canRead('farm:read')
  if (farmAllowed) {
    const settled = await Promise.allSettled([
      farmAgentApi.weatherBrief().then(value => weatherLine(value)),
      pendingWorkOrderLine(),
      workOrderApi.alerts({ status: 'new', page: 1, page_size: 10 }).then(res => alertLine(res.items)),
    ])
    for (const item of settled) {
      if (item.status === 'fulfilled' && item.value) lines.push(item.value)
    }
  }

  try {
    const tasks = await runningTaskLine()
    if (tasks) lines.push(tasks)
  } catch {
    /* 任务列表拉不到就不说这句 */
  }
  return lines
}
