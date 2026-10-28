/**
 * 离线写入队列（v3.19）：把台账/工单的写操作先落到本机 IndexedDB。
 *
 * 设计口径：
 * - 只缓存「网络不可达」失败的写操作；服务端明确拒绝（400/403/404/409）不由队列
 *   偷偷重试——409 单独标记为冲突，交给用户选择覆盖还是放弃。
 * - 每个操作自带 `client_op_id`，重放时后端按 (yard_id, client_op_id) 去重，
 *   即便首次请求其实已落库、只是响应丢了，也不会写两遍。
 * - 队列按创建顺序串行重放，遇到网络错误立即停止，避免无意义的重试风暴。
 */

import { isAxiosError, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { api } from '@/api/client'

const DB_NAME = 'kjxy-offline'
const DB_VERSION = 1
const STORE = 'operations'

export type OfflineOpState = 'pending' | 'conflict' | 'failed'

export interface OfflineOp {
  /** 同时作为 client_op_id 使用：本地生成，重放时后端口径一致 */
  id: string
  /** 人类可读的操作名，用于横幅与冲突弹窗 */
  label: string
  method: 'POST' | 'PATCH' | 'DELETE'
  url: string
  body: Record<string, unknown> | null
  createdAt: number
  attempts: number
  state: OfflineOpState
  error?: string
  /** 冲突时服务器当前副本与版本号，供「用我的覆盖」使用 */
  serverCurrent?: Record<string, unknown>
  serverRevision?: number
}

export interface FlushResult {
  synced: number
  conflicts: number
  failed: number
  offline: boolean
}

let dbPromise: Promise<IDBDatabase> | null = null

function openDb(): Promise<IDBDatabase> {
  if (dbPromise) return dbPromise
  dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: 'id' })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error ?? new Error('IndexedDB 打开失败'))
  })
  return dbPromise
}

function txDone(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error ?? new Error('IndexedDB 事务失败'))
    tx.onabort = () => reject(tx.error ?? new Error('IndexedDB 事务中止'))
  })
}

async function withStore<T>(
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> {
  const db = await openDb()
  const tx = db.transaction(STORE, mode)
  const req = run(tx.objectStore(STORE))
  await txDone(tx)
  return req.result
}

export function newOpId(): string {
  const uuid =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2, 12)}`
  return `op-${uuid}`
}

export async function listOps(): Promise<OfflineOp[]> {
  const rows = await withStore<OfflineOp[]>('readonly', store => store.getAll())
  return rows.sort((a, b) => a.createdAt - b.createdAt)
}

export async function pendingCount(): Promise<number> {
  const rows = await listOps()
  return rows.length
}

export async function enqueue(op: OfflineOp): Promise<void> {
  await withStore('readwrite', store => store.put(op))
  emit()
}

export async function updateOp(id: string, patch: Partial<OfflineOp>): Promise<void> {
  const db = await openDb()
  // 读写必须在同一事务的同步回调里完成：await 请求会提前提交事务
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, 'readwrite')
    const store = tx.objectStore(STORE)
    const getReq = store.get(id)
    getReq.onsuccess = () => {
      const current = getReq.result as OfflineOp | undefined
      if (current) store.put({ ...current, ...patch })
    }
    tx.oncomplete = () => resolve()
    tx.onerror = () => reject(tx.error ?? new Error('IndexedDB 事务失败'))
    tx.onabort = () => reject(tx.error ?? new Error('IndexedDB 事务中止'))
  })
  emit()
}

export async function removeOp(id: string): Promise<void> {
  await withStore('readwrite', store => store.delete(id))
  emit()
}

// ---------------------------------------------------------------- 订阅

type Listener = (ops: OfflineOp[]) => void
const listeners = new Set<Listener>()

function emit(): void {
  if (!listeners.size) return
  listOps()
    .then(ops => listeners.forEach(fn => fn(ops)))
    .catch(() => undefined)
}

export function subscribe(fn: Listener): () => void {
  listeners.add(fn)
  listOps().then(ops => fn(ops)).catch(() => undefined)
  return () => listeners.delete(fn)
}

// ---------------------------------------------------------------- 重放

function errorParts(err: unknown): {
  offline: boolean
  status?: number
  code?: string
  message?: string
  details?: Record<string, unknown>
} {
  if (!isAxiosError(err)) return { offline: false, message: String(err) }
  if (!err.response) return { offline: true, message: '网络不可达' }
  const data = err.response.data as
    | { error?: { code?: string; message?: string; details?: Record<string, unknown> } }
    | undefined
  return {
    offline: false,
    status: err.response.status,
    code: data?.error?.code,
    message: data?.error?.message,
    details: data?.error?.details,
  }
}

let flushing: Promise<FlushResult> | null = null

/** 串行重放队列；调用方不需要 await（失败会留在队列里等下次联网）。 */
export function flush(): Promise<FlushResult> {
  if (!flushing) {
    flushing = doFlush().finally(() => {
      flushing = null
    })
  }
  return flushing
}

async function doFlush(): Promise<FlushResult> {
  const result: FlushResult = { synced: 0, conflicts: 0, failed: 0, offline: false }
  for (const op of await listOps()) {
    if (op.state !== 'pending') continue
    try {
      await api.request({
        method: op.method,
        url: op.url,
        data: op.body ?? undefined,
        skipErrorToast: true,
      } as AxiosRequestConfig & { skipErrorToast?: boolean })
      await removeOp(op.id)
      result.synced += 1
    } catch (err) {
      const info = errorParts(err)
      if (info.offline) {
        result.offline = true
        break
      }
      if (info.status === 409 && info.code === 'FARM_REVISION_CONFLICT') {
        result.conflicts += 1
        await updateOp(op.id, {
          state: 'conflict',
          error: info.message,
          serverCurrent: info.details?.current as Record<string, unknown> | undefined,
          serverRevision: Number(info.details?.current_revision ?? 0) || undefined,
          attempts: op.attempts + 1,
        })
        continue
      }
      if (info.status && info.status >= 400 && info.status < 500) {
        result.failed += 1
        await updateOp(op.id, {
          state: 'failed',
          error: info.message ?? `HTTP ${info.status}`,
          attempts: op.attempts + 1,
        })
        continue
      }
      result.offline = true
      await updateOp(op.id, { attempts: op.attempts + 1, error: info.message })
      break
    }
  }
  emit()
  return result
}

/** 冲突解决：拿服务器当前版本覆盖后重放（“以我的为准”）。 */
export async function resolveConflictKeepMine(op: OfflineOp): Promise<void> {
  const revision = op.serverRevision
  if (!revision) return
  const body = { ...(op.body ?? {}), expected_revision: revision }
  await updateOp(op.id, {
    state: 'pending',
    body,
    serverCurrent: undefined,
    serverRevision: undefined,
    error: undefined,
  })
  await flush()
}

/** 冲突解决：放弃本地这次修改。 */
export async function resolveConflictDiscard(op: OfflineOp): Promise<void> {
  await removeOp(op.id)
}

// ---------------------------------------------------------------- 写入入口

function unwrapResponse<T>(res: AxiosResponse): T {
  return (res.data as { data: T }).data
}

export interface QueuedWriteResult<T> {
  queued: boolean
  data?: T
  op?: OfflineOp
}

/**
 * 直接写；网络不可达时把操作落进队列并返回 queued=true。
 * `needsClientOpId` 用于创建类请求：先带幂等键发出，丢了响应也不会写两遍。
 */
export async function writeWithQueue<T>(params: {
  label: string
  method: 'POST' | 'PATCH' | 'DELETE'
  url: string
  body?: Record<string, unknown> | null
  needsClientOpId?: boolean
}): Promise<QueuedWriteResult<T>> {
  const opId = newOpId()
  const body = params.body ? { ...params.body } : null
  if (params.needsClientOpId && body) body.client_op_id = opId
  try {
    const res = await api.request({
      method: params.method,
      url: params.url,
      data: body ?? undefined,
      skipErrorToast: true,
    } as AxiosRequestConfig & { skipErrorToast?: boolean })
    return { queued: false, data: unwrapResponse<T>(res) }
  } catch (err) {
    if (!errorParts(err).offline) throw err
    const op: OfflineOp = {
      id: opId,
      label: params.label,
      method: params.method,
      url: params.url,
      body,
      createdAt: Date.now(),
      attempts: 0,
      state: 'pending',
    }
    await enqueue(op)
    return { queued: true, op }
  }
}

let installed = false

/** 应用启动时调用一次：监听联网事件并尝试重放历史队列。 */
export function installOfflineQueue(): void {
  if (installed || typeof window === 'undefined') return
  installed = true
  window.addEventListener('online', () => {
    void flush()
  })
  void flush()
}
