/** 供应商模型缓存与模态匹配（v3.37）。
 *
 * 「供应商 → 模型」级联选择在模型中心的多个入口复用，模型列表按供应商批量拉取，
 * 同一轮会话内缓存，避免每开一个弹窗就打一轮上游接口。
 */
import { supplierModelApi } from '@/api/modelChannels'
import type { SupplierModel } from '@/api/modelChannels'
import type { LlmSupplier } from '@/api/modules'

const cache = new Map<string, SupplierModel[]>()
const inflight = new Map<string, Promise<SupplierModel[]>>()

/** 供应商增删改模型后调用，保证下拉看到的是最新列表 */
export function clearSupplierModelCache(supplierId?: string): void {
  if (supplierId) {
    cache.delete(supplierId)
    inflight.delete(supplierId)
    return
  }
  cache.clear()
  inflight.clear()
}

export async function loadSupplierModels(
  supplierId: string,
  force = false,
): Promise<SupplierModel[]> {
  if (!supplierId) return []
  if (!force && cache.has(supplierId)) return cache.get(supplierId) ?? []
  const pending = inflight.get(supplierId)
  if (!force && pending) return pending
  const task = supplierModelApi
    .list(supplierId)
    .then(models => {
      cache.set(supplierId, models)
      return models
    })
    .catch(() => [] as SupplierModel[])
    .finally(() => inflight.delete(supplierId))
  inflight.set(supplierId, task)
  return task
}

export function modelEnabled(model: SupplierModel): boolean {
  const status = model.status
  if (status === undefined || status === null || status === '') return true
  if (typeof status === 'number') return status === 1
  return ['1', 'active', 'enabled', 'ok'].includes(String(status).toLowerCase())
}

export function modelLabel(model: SupplierModel): string {
  return (
    model.display_name || model.model_key || model.upstream_model_id || model.id || '未命名模型'
  )
}

function modalitySet(model: SupplierModel): Set<string> {
  const out = new Set<string>()
  const own = (model.modality || '').toLowerCase()
  if (own) out.add(own)
  for (const value of [...(model.input_modalities ?? []), ...(model.output_modalities ?? [])]) {
    if (value) out.add(String(value).toLowerCase())
  }
  if (out.has('multimodal')) {
    out.add('text')
  }
  return out
}

/** 模态匹配：wanted 为空表示不过滤；图像/视频同时认 multimodal 与输入输出模态 */
export function modalityMatches(model: SupplierModel, wanted: string[]): boolean {
  if (!wanted.length) return true
  const set = modalitySet(model)
  if (!set.size) return false
  return wanted.some(item => {
    const key = item.toLowerCase()
    if (key === 'audio') return set.has('asr') || set.has('tts') || set.has('audio')
    if (key === 'text') return set.has('text') || set.has('multimodal')
    return set.has(key)
  })
}

export interface ModelOption {
  supplier: LlmSupplier
  model: SupplierModel
}

/** 一次绑定的选择结果：供应商 + 具体模型行 */
export interface ModelChoice {
  supplier_id: string
  supplier_model_id: string
  model_name: string
  supplier_name: string
}

/** 拉齐一批供应商的模型：只保留启用且模态匹配的模型，供应商按原顺序 */
export async function loadModelOptions(
  suppliers: LlmSupplier[],
  modalities: string[] = [],
): Promise<ModelOption[]> {
  const usable = suppliers.filter(s => s.status === 1)
  const groups = await Promise.all(
    usable.map(async supplier => {
      const models = await loadSupplierModels(supplier.id)
      return models
        .filter(model => model.id && modelEnabled(model) && modalityMatches(model, modalities))
        .map(model => ({ supplier, model }))
    }),
  )
  return groups.flat()
}
