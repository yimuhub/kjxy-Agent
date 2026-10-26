/**
 * SSE（text/event-stream）手解流工具。
 *
 * 为什么不用 EventSource：EventSource 不支持自定义请求头，而平台鉴权走
 * Authorization: Bearer（见 src/api/client.ts），因此用 fetch + ReadableStream
 * 手工解析 SSE 帧。
 *
 * 契约（与后端 app/modules/tasks/stream.py 对齐）：
 * - 帧格式：`id: <id>\nevent: <名>\ndata: <JSON>\n\n`；本工具解析 id/event/data，
 *   忽略注释帧（`:` 开头，用于服务端心跳保活）与 retry: 字段。
 * - 断线自动重连：连续失败重试 ≤ maxRetries（默认 3）次，退避间隔递增；
 *   重连携带 Last-Event-ID，后端可从断点续传。
 * - 空闲超时：超过 idleTimeoutMs 未收到任何字节（含心跳）则主动断开重连，
 *   避免半开连接永久挂起。
 * - 重试仍失败时调用 onFallback 降级回调（调用方降级回轮询）。
 * - close() 通过 AbortController 断流：只是停止“看”，服务端任务继续执行，
 *   取消任务必须显式调用 REST 取消 API。
 */
import { TOKEN_KEY } from '@/api/client'

export interface SseFrame {
  event: string
  data: string
}

export interface SseHandle {
  /** 主动断流（关抽屉/组件卸载）；不再重连、不触发降级回调 */
  close: () => void
}

export interface SseOptions {
  url: string
  /** 请求方法，默认 GET；POST 用于"发消息即开流"这类一次性请求 */
  method?: 'GET' | 'POST'
  /** POST 请求体（字符串；与 Content-Type: application/json 配套） */
  body?: string
  /** 额外请求头（覆盖同名默认值） */
  headers?: Record<string, string>
  /** 每收到一个完整事件帧回调一次 */
  onFrame: (frame: SseFrame) => void
  /** 连续重连失败后的降级回调（调用方切回轮询） */
  onFallback?: () => void
  /** 最大重连次数（不含首次连接），默认 3；POST 默认不重连 */
  maxRetries?: number
  /** 空闲超时（毫秒），默认 45s；服务端心跳间隔 15s */
  idleTimeoutMs?: number
}

const DEFAULT_MAX_RETRIES = 3
const BASE_RETRY_DELAY_MS = 1000
const MAX_RETRY_DELAY_MS = 15_000
const DEFAULT_IDLE_TIMEOUT_MS = 45_000

interface ParsedBlock {
  frame: SseFrame
  id?: string
}

/** 解析单个 SSE 块（以空行分隔）；无 event 也无 data 时返回 null */
function parseBlock(block: string): ParsedBlock | null {
  let event = 'message'
  let id: string | undefined
  const dataLines: string[] = []
  for (const raw of block.split('\n')) {
    const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('id:')) {
      const value = line.slice('id:'.length).trim()
      if (value) id = value
    } else if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      // SSE 规范：data 前恰好一个空格是协议空格，需剥掉
      dataLines.push(line.slice('data:'.length).replace(/^ /, ''))
    }
  }
  if (!dataLines.length && event === 'message') return null
  return { frame: { event, data: dataLines.join('\n') }, id }
}

function retryDelay(failures: number): number {
  const base = Math.min(BASE_RETRY_DELAY_MS * 2 ** (failures - 1), MAX_RETRY_DELAY_MS)
  // 抖动避免多标签页同时重连打爆后端
  return base + Math.floor(Math.random() * 300)
}

/** 打开 SSE 订阅；返回控制句柄（close 断流）。 */
export function openSseStream(options: SseOptions): SseHandle {
  const method = options.method ?? 'GET'
  const maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES
  const idleTimeoutMs = options.idleTimeoutMs ?? DEFAULT_IDLE_TIMEOUT_MS
  const outer = new AbortController()
  const onFallback = options.onFallback
  // POST 是一次性请求：重连会重复提交，故默认不重连，失败直接降级
  const allowRetry = method === 'GET'
  let closed = false
  let consecutiveFailures = 0
  let lastEventId: string | null = null
  let retryTimer: number | undefined

  function sleep(ms: number): Promise<void> {
    return new Promise(resolve => {
      retryTimer = window.setTimeout(resolve, ms)
    })
  }

  async function connect(): Promise<void> {
    while (!closed && consecutiveFailures <= maxRetries) {
      const conn = new AbortController()
      let idleTimer: number | undefined
      let received = false
      const abortOnOuter = () => conn.abort()
      outer.signal.addEventListener('abort', abortOnOuter, { once: true })

      const armIdle = () => {
        if (idleTimer !== undefined) window.clearTimeout(idleTimer)
        idleTimer = window.setTimeout(() => conn.abort(), idleTimeoutMs)
      }

      try {
        armIdle()
        const token = localStorage.getItem(TOKEN_KEY)
        const headers: Record<string, string> = { Accept: 'text/event-stream' }
        if (token) headers.Authorization = `Bearer ${token}`
        if (lastEventId) headers['Last-Event-ID'] = lastEventId
        if (method === 'POST') headers['Content-Type'] = 'application/json'
        Object.assign(headers, options.headers)
        const res = await fetch(options.url, {
          headers,
          signal: conn.signal,
          method,
          body: method === 'POST' ? options.body : undefined,
        })
        if (!res.ok || !res.body) throw new Error(`SSE 连接失败：HTTP ${res.status}`)
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          received = true
          armIdle()
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
          const parts = buffer.split('\n\n')
          // 末段可能是半帧，留在 buffer 等下一块
          buffer = parts.pop() ?? ''
          for (const block of parts) {
            const parsed = parseBlock(block)
            if (!parsed) continue
            if (parsed.id) lastEventId = parsed.id
            options.onFrame(parsed.frame)
          }
        }
        // 服务端主动结束连接（重启/网关空闲超时等）：按断线走重连逻辑
        throw new Error('SSE 连接被服务端关闭')
      } catch (err) {
        if (closed || outer.signal.aborted) return
        if (err instanceof DOMException && err.name === 'AbortError' && closed) return
        if (received) consecutiveFailures = 0
        consecutiveFailures += 1
        if (!allowRetry || consecutiveFailures > maxRetries) {
          onFallback?.()
          return
        }
        await sleep(retryDelay(consecutiveFailures))
      } finally {
        if (idleTimer !== undefined) window.clearTimeout(idleTimer)
        outer.signal.removeEventListener('abort', abortOnOuter)
        conn.abort()
      }
    }
  }

  void connect()

  return {
    close() {
      closed = true
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
      outer.abort()
    },
  }
}
