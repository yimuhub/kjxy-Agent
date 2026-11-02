/**
 * 桌面伙伴陪伴总线（v3.49）：
 * - 主窗把对话/指挥官进度与当前页面场景广播给桌宠窗口；
 * - 浏览器用 BroadcastChannel + storage，桌面壳额外走 Tauri 事件；
 * - 只传状态、场景和短句，不传正文、不传密钥。
 */

export type PetMood = 'idle' | 'thinking' | 'busy' | 'happy' | 'worried' | 'sleepy'
export type PetScene =
  | 'general'
  | 'dashboard'
  | 'chat'
  | 'commander'
  | 'knowledge'
  | 'farm'
  | 'tasks'
  | 'settings'

export interface PetEvent {
  id: string
  type: 'mood' | 'scene' | 'say'
  mood?: PetMood
  scene?: PetScene
  text?: string
  at: number
}

export const MOOD_LABEL: Record<PetMood, string> = {
  idle: '发呆中',
  thinking: '思考中',
  busy: '忙碌中',
  happy: '开心',
  worried: '有点担心',
  sleepy: '打盹中',
}

export const SCENE_LABEL: Record<PetScene, string> = {
  general: '工作台',
  dashboard: '概览',
  chat: '智能对话',
  commander: '指挥官',
  knowledge: '知识库',
  farm: '农业台账',
  tasks: '任务与工单',
  settings: '系统设置',
}

const MOOD_KEY = 'ky_pet_mood'
const SCENE_KEY = 'ky_pet_scene'
const MUTE_KEY = 'ky_pet_mute'
const EVENT_KEY = 'ky_pet_event'
const CHANNEL_NAME = 'ky_pet_copilot'
const TAURI_EVENT = 'pet:copilot'

const TIPS: Record<PetScene, string> = {
  general: '今天想先做哪件事？',
  dashboard: '先看今日行动，把最急的一件挑出来。',
  chat: '有想法直接说，我来帮你拆。',
  commander: '计划卡可以改顺序、换人，再开始执行。',
  knowledge: '知识库里找不到，再来问我。',
  farm: '地块和农事记录都在这里，异常别漏看。',
  tasks: '任务卡住了就点开看证据，别硬猜。',
  settings: '改完设置记得保存；集群节点也在系统设置里。',
}

let eventSeq = 0

function nextEventId(): string {
  eventSeq += 1
  return `${Date.now()}-${eventSeq}-${Math.random().toString(36).slice(2, 7)}`
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* 隐私模式/配额满时静默降级，伙伴仍可运行 */
  }
}

export function isPetMood(value: unknown): value is PetMood {
  return typeof value === 'string' && value in MOOD_LABEL
}

export function isPetScene(value: unknown): value is PetScene {
  return typeof value === 'string' && value in SCENE_LABEL
}

export function readStoredPetMood(): PetMood {
  const raw = localStorage.getItem(MOOD_KEY)
  return isPetMood(raw) ? raw : 'idle'
}

export function readStoredPetScene(): PetScene {
  const raw = localStorage.getItem(SCENE_KEY)
  return isPetScene(raw) ? raw : 'general'
}

export function rememberPetScene(scene: PetScene): void {
  try {
    localStorage.setItem(SCENE_KEY, scene)
  } catch {
    /* 忽略存储失败 */
  }
}

export function rememberPetMood(mood: PetMood): void {
  try {
    localStorage.setItem(MOOD_KEY, mood)
  } catch {
    /* 忽略存储失败 */
  }
}

export function petMuted(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === '1'
  } catch {
    return false
  }
}

export function setPetMuted(muted: boolean): void {
  try {
    localStorage.setItem(MUTE_KEY, muted ? '1' : '0')
  } catch {
    /* 静音偏好写不进去也不影响当前会话 */
  }
}

export function sceneForRoute(name: unknown): PetScene {
  switch (name) {
    case 'Dashboard':
      return 'dashboard'
    case 'Chat':
      return 'chat'
    case 'Commander':
      return 'commander'
    case 'Knowledge':
      return 'knowledge'
    case 'Farm':
    case 'Diagnosis':
      return 'farm'
    case 'Tasks':
    case 'WorkOrders':
      return 'tasks'
    case 'Settings':
    case 'Members':
    case 'ModelCenter':
    case 'Quota':
    case 'Owner':
    case 'Profile':
      return 'settings'
    default:
      return 'general'
  }
}

export function sceneTip(scene: PetScene): string {
  return TIPS[scene] ?? TIPS.general
}

/* ---------------- 时段 / 节气 / 节日问候 ---------------- */

const SOLAR_TERMS: { name: string; month: number; day: number; care: string }[] = [
  { name: '小寒', month: 1, day: 6, care: '最冷的时候，注意防寒' },
  { name: '大寒', month: 1, day: 20, care: '注意保暖，别硬扛' },
  { name: '立春', month: 2, day: 4, care: '春天要来了' },
  { name: '雨水', month: 2, day: 19, care: '雨水渐多，留意墒情' },
  { name: '惊蛰', month: 3, day: 6, care: '万物复苏，注意作息' },
  { name: '春分', month: 3, day: 21, care: '昼夜平分，注意作息' },
  { name: '清明', month: 4, day: 5, care: '雨水多，留意墒情' },
  { name: '谷雨', month: 4, day: 20, care: '雨水多，留意墒情' },
  { name: '立夏', month: 5, day: 6, care: '作物长得快，多看一眼' },
  { name: '小满', month: 5, day: 21, care: '麦粒渐满，注意水分' },
  { name: '芒种', month: 6, day: 6, care: '农忙时节，注意休息' },
  { name: '夏至', month: 6, day: 21, care: '天热了，记得补水' },
  { name: '小暑', month: 7, day: 7, care: '高温，防暑降温' },
  { name: '大暑', month: 7, day: 23, care: '高温，防暑降温' },
  { name: '立秋', month: 8, day: 8, care: '早晚凉了，注意温差' },
  { name: '处暑', month: 8, day: 23, care: '暑气渐退，注意温差' },
  { name: '白露', month: 9, day: 8, care: '露水重，注意保暖' },
  { name: '秋分', month: 9, day: 23, care: '昼夜平分，注意作息' },
  { name: '寒露', month: 10, day: 8, care: '天凉了，添件衣服' },
  { name: '霜降', month: 10, day: 23, care: '天凉了，添件衣服' },
  { name: '立冬', month: 11, day: 7, care: '冬天到了，注意保暖' },
  { name: '小雪', month: 11, day: 22, care: '天冷了，注意保暖' },
  { name: '大雪', month: 12, day: 7, care: '天冷，记得吃热乎的' },
  { name: '冬至', month: 12, day: 22, care: '天冷，记得吃热乎的' },
]

const FIXED_FESTIVALS: Record<string, string> = {
  '1-1': '元旦快乐',
  '5-1': '劳动节快乐',
  '6-1': '儿童节快乐',
  '10-1': '国庆节快乐',
}

function dayOfYear(date: Date): number {
  const start = Date.UTC(date.getFullYear(), 0, 1)
  const current = Date.UTC(date.getFullYear(), date.getMonth(), date.getDate())
  return Math.floor((current - start) / 86_400_000) + 1
}

function daysInYear(year: number): number {
  return new Date(year, 1, 29).getMonth() === 1 ? 366 : 365
}

function solarTermNote(date: Date): string {
  const today = dayOfYear(date)
  const total = daysInYear(date.getFullYear())
  let nearest: { name: string; care: string; distance: number } | null = null
  for (const term of SOLAR_TERMS) {
    const target = dayOfYear(new Date(date.getFullYear(), term.month - 1, term.day))
    const raw = Math.abs(today - target)
    const distance = Math.min(raw, total - raw)
    if (nearest === null || distance < nearest.distance) {
      nearest = { name: term.name, care: term.care, distance }
    }
  }
  if (nearest === null || nearest.distance > 5) return ''
  return nearest.distance === 0
    ? `${nearest.name}，${nearest.care}。`
    : `${nearest.name}将近，${nearest.care}。`
}

function timeGreeting(date: Date): string {
  const hour = date.getHours()
  if (hour < 5) return '夜深了，先喝口水再忙。'
  if (hour < 9) return '早上好，今天先挑最重要的一件事。'
  if (hour < 12) return '上午好，我在这儿帮你盯着进度。'
  if (hour < 14) return '中午好，吃完饭再继续也不迟。'
  if (hour < 18) return '下午好，要不要先把待办清一清？'
  if (hour < 23) return '晚上好，今天辛苦了。'
  return '夜深了，早点休息。'
}

export function greetingFor(date: Date = new Date()): string {
  const festival = FIXED_FESTIVALS[`${date.getMonth() + 1}-${date.getDate()}`]
  const prefix = festival ? `${festival}，` : timeGreeting(date)
  const term = solarTermNote(date)
  return term ? `${prefix}${term}` : prefix
}

/* ---------------- 跨窗口事件 ---------------- */

interface TauriEventApi {
  listen?: (
    event: string,
    handler: (event: { payload: unknown }) => void,
  ) => Promise<() => void>
  emit?: (event: string, payload?: unknown) => Promise<void>
}

function tauriEvents(): TauriEventApi | null {
  if (typeof window === 'undefined') return null
  const api = (window as unknown as { __TAURI__?: { event?: TauriEventApi } }).__TAURI__
  return api?.event ?? null
}

/** 广播一条陪伴事件；调用方只传短句，不传消息正文。 */
export function publishPetEvent(
  event: Omit<PetEvent, 'id' | 'at'> & { id?: string; at?: number },
): void {
  const payload: PetEvent = {
    ...event,
    id: event.id ?? nextEventId(),
    at: event.at ?? Date.now(),
  }
  if (payload.type === 'mood' && payload.mood) {
    rememberPetMood(payload.mood)
  }
  if (payload.type === 'scene' && payload.scene) {
    rememberPetScene(payload.scene)
  }
  writeJson(EVENT_KEY, payload)
  try {
    const channel = new BroadcastChannel(CHANNEL_NAME)
    channel.postMessage(payload)
    channel.close()
  } catch {
    /* 旧 WebView 不支持 BroadcastChannel 时依赖 storage/Tauri 事件 */
  }
  const api = tauriEvents()
  if (api?.emit) {
    void api.emit(TAURI_EVENT, payload).catch(() => undefined)
  }
}

/** 订阅陪伴事件；返回取消订阅函数。 */
export function subscribePetEvents(handler: (event: PetEvent) => void): () => void {
  let closed = false
  const seen = new Set<string>()
  let lastId = ''

  const deliver = (raw: unknown): void => {
    if (closed || !raw || typeof raw !== 'object') return
    const event = raw as Partial<PetEvent>
    if (!event.type || !event.id) return
    if (event.id === lastId || seen.has(event.id)) return
    lastId = event.id
    seen.add(event.id)
    if (seen.size > 50) seen.clear()
    handler(event as PetEvent)
  }

  let channel: BroadcastChannel | null = null
  try {
    channel = new BroadcastChannel(CHANNEL_NAME)
    channel.onmessage = message => deliver(message.data)
  } catch {
    channel = null
  }

  const onStorage = (event: StorageEvent): void => {
    if (event.key === EVENT_KEY && event.newValue) {
      try {
        deliver(JSON.parse(event.newValue))
      } catch {
        /* 损坏的事件直接丢弃 */
      }
    }
  }
  window.addEventListener('storage', onStorage)

  let unlisten: (() => void) | null = null
  const api = tauriEvents()
  if (api?.listen) {
    void api
      .listen(TAURI_EVENT, event => deliver(event.payload))
      .then(stop => {
        if (closed) stop()
        else unlisten = stop
      })
      .catch(() => undefined)
  }

  return () => {
    closed = true
    try {
      channel?.close()
    } catch {
      /* 已关闭 */
    }
    window.removeEventListener('storage', onStorage)
    unlisten?.()
  }
}
