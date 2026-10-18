/**
 * SSE（text/event-stream）手解流工具。
 *
 * 为什么不用 EventSource：EventSource 不支持自定义请求头，而平台鉴权走
 * Authorization: Bearer（见 src/api/client.ts），因此用 fetch + ReadableStream
 * 手工解析 SSE 帧。
 *
 * 契约（与后端 app/modules/tasks/stream.py 对齐）：
 * - 帧格式：`event: <名>\ndata: <JSON>\n\n`；本工具解析 event:/data: 多行块，
 *   忽略注释（`:` 开头）与 id:/retry: 字段。
 * - 断线自动重连：连续失败重试 ≤ maxRetries（默认 3）次，退避间隔递增；
 *   重试仍失败时调用 onFallback 降级回调（调用方降级回轮询）。
 * - close() 通过 AbortController 断流：只是停止“看”，服务端任务继续执行，
 *   取消任务必须显式调用REST取消 Api。
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
  /** 每收到一个完整事件帧回调一次 */
  onFrame: (frame: SseFrame) => void
  /** 连续重连失败后的降级回调（调用方切回轮询） */
  onFallback?: () => void
  /** 最大重连次数（不含首次连接），默认 3 */
  maxRetries?: number
}

const DEFAULT_MAX_RETRIES = 3
const BASE_RETRY_DELAY_MS = 1000

/** 解析单个 SSE 块（以空行分隔）；无 event 也无 data 时返回 null */
function parseBlock(block: string): SseFrame | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const raw of block.split('\n')) {
    const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw
    if (!line || line.startsWith(':')) continue
    if (line.startsWith('event:')) {
      event = line.slice('event:'.length).trim()
    } else if (line.startsWith('data:')) {
      // SSE 规范：data 前恰好一个空格是协议空格，需剥掉
      dataLines.push(line.slice('data:'.length).replace(/^ /, ''))
    }
  }
  if (!dataLines.length && event === 'message') return null
  return { event, data: dataLines.join('\n') }
}

/** 打开 SSE 订阅；返回控制句柄（close 断流）。 */
export function openSseStream(options: SseOptions): SseHandle {
  const maxRetries = options.maxRetries ?? DEFAULT_MAX_RETRIES
  const controller = new AbortController()
  let closed = false
  let consecutiveFailures = 0
  let retryTimer: number | undefined

  async function connect(): Promise<void> {
    while (!closed && consecutiveFailures <= maxRetries) {
      try {
        const token = localStorage.getItem(TOKEN_KEY)
        const res = await fetch(options.url, {
          headers: {
            Accept: 'text/event-stream',
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal: controller.signal,
        })
        if (!res.ok || !res.body) throw new Error(`SSE 连接失败：HTTP ${res.status}`)
        consecutiveFailures = 0
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        for (;;) {
          const { done, value } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
          const parts = buffer.split('\n\n')
          // 末段可能是半帧，留在 buffer 等下一块
          buffer = parts.pop() ?? ''
          for (const block of parts) {
            const frame = parseBlock(block)
            if (frame) options.onFrame(frame)
          }
        }
        // 服务端主动结束连接（重启/网关空闲超时等）：按断线走重连逻辑
        throw new Error('SSE 连接被服务端关闭')
      } catch (err) {
        if (closed || controller.signal.aborted) return
        if (err instanceof DOMException && err.name === 'AbortError') return
        consecutiveFailures += 1
        if (consecutiveFailures > maxRetries) {
          options.onFallback?.()
          return
        }
        await new Promise<void>(resolve => {
          retryTimer = window.setTimeout(resolve, BASE_RETRY_DELAY_MS * consecutiveFailures)
        })
      }
    }
  }

  void connect()

  return {
    close() {
      closed = true
      options = { ...options, onFallback: undefined }
      if (retryTimer !== undefined) window.clearTimeout(retryTimer)
      controller.abort()
    },
  }
}
