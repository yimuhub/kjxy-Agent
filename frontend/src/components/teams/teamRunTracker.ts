import type { TeamRun } from '@/api/modules'

interface StreamHandle { close(): void }
interface TrackerOptions {
  getRun(id: string): Promise<TeamRun>
  onRun(run: TeamRun): void
  onLoading(loading: boolean): void
  onError(message: string): void
  onConnection(message: string): void
  subscribe?(id: string, refresh: () => void, disconnected: () => void): StreamHandle
  intervalMs?: number
}

/** 一个抽屉只观察一个运行。关闭仅取消观察，绝不取消服务端任务。 */
export function createTeamRunTracker(options: TrackerOptions) {
  let generation = 0
  let selectedId = ''
  let active = false
  let pending: Promise<void> | undefined
  let timer: ReturnType<typeof setTimeout> | undefined
  let stream: StreamHandle | undefined
  const isActive = (run: TeamRun) => run.status === 'queued' || run.status === 'running'

  function clearTimer() {
    if (timer !== undefined) clearTimeout(timer)
    timer = undefined
  }

  function closeStream() {
    stream?.close()
    stream = undefined
  }

  function stop() {
    generation += 1
    selectedId = ''
    active = false
    pending = undefined
    clearTimer()
    closeStream()
    options.onLoading(false)
  }

  function schedule() {
    clearTimer()
    if (selectedId && active) timer = setTimeout(() => void refresh(), options.intervalMs ?? 5000)
  }

  function follow(run: TeamRun) {
    active = isActive(run)
    if (!active) {
      clearTimer()
      closeStream()
      options.onConnection('')
      return
    }
    if (!stream && options.subscribe) {
      const version = generation
      stream = options.subscribe(run.id, () => {
        if (version === generation) void refresh()
      }, () => {
        if (version === generation) options.onConnection('实时连接已断开，正在每 5 秒刷新；也可手动刷新。')
      })
    }
    schedule()
  }

  function refresh(): Promise<void> {
    if (!selectedId) return Promise.resolve()
    if (pending) return pending
    clearTimer()
    const id = selectedId
    const version = generation
    options.onLoading(true)
    const request = (async () => {
      try {
        const run = await options.getRun(id)
        if (version !== generation) return
        options.onRun(run)
        options.onError('')
        follow(run)
      } catch {
        if (version !== generation) return
        options.onError('运行详情加载失败或连接中断，请刷新重试；关闭窗口不会取消后台运行。')
        schedule()
      } finally {
        if (version === generation) {
          pending = undefined
          options.onLoading(false)
        }
      }
    })()
    pending = request
    return request
  }

  function open(run: TeamRun) {
    stop()
    selectedId = run.id
    options.onError('')
    options.onConnection('')
    options.onRun(run)
    follow(run)
  }

  async function openId(id: string) {
    stop()
    selectedId = id
    options.onError('')
    options.onConnection('')
    await refresh()
  }

  return { open, openId, refresh, stop }
}
