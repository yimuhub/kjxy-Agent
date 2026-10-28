import type { AxiosRequestConfig, AxiosResponse } from 'axios'
import { api, apiUrl } from './client'
import type { ApiResponse } from '@/types/api'

/**
 * 对话模式 API 模块（docs/DESIGN-v2.md §7 对话模式 / §10 后端契约）。
 *
 * ---------- 对话 API 契约（DESIGN-v2.md §10，前后端共同遵守） ----------
 *
 * - `GET  /api/v1/chat/sessions` → `{items:[{id,title,target,target_code,updated_at,last_excerpt}]}`（当前用户自己的会话，新→旧）
 * - `POST /api/v1/chat/sessions` body `{title?, target?: 'auto'|'agent'|'team', target_code?}` → 会话对象
 * - `PATCH /api/v1/chat/sessions/{id}` body `{title}` → 会话对象
 * - `DELETE /api/v1/chat/sessions/{id}` → 204 语义（ok 包）
 * - `GET  /api/v1/chat/sessions/{id}/messages` → `{items:[{id,role:'user'|'assistant',content,choice?,routed_by?,reason?,created_at}]}`（升序）
 * - `POST /api/v1/chat/sessions/{id}/messages` body `{content}` → `{user_message, assistant_message}`
 *   - target=auto：走 supervisor 三级路由；assistant_message.choice = {kind,code?,name}、routed_by、reason
 *   - target=agent/team：直跑指定对象；routed_by='manual'
 *   - 执行失败：assistant_message.content = 中文错误说明，routed_by='error'（会话不死，可继续问）
 * - 全部登录可用（成员级），跨小院/跨用户会话一律 404
 *
 * ---------- 附件与流式（v1.8） ----------
 *
 * - `POST /api/v1/chat/sessions/{id}/attachments`（multipart `file`）→ 附件对象（201）
 * - `GET  /api/v1/chat/sessions/{id}/attachments` → `{items:[附件]}`
 * - `DELETE /api/v1/chat/sessions/{id}/attachments/{aid}` → ok 包
 * - `POST /api/v1/chat/sessions/{id}/messages` body `{content, attachment_ids?}` → 同步一问一答（流式不可用时的兜底）
 * - `POST /api/v1/chat/sessions/{id}/messages/stream`（SSE）→ 过程步骤 + 最终回答
 *
 * ---------- 点名 / 多助手群聊 / 徽章下发（v2.0，后端并行开发中） ----------
 *
 * - 发送请求体新增两个可选字段：`target_code`（@ 点名对象，可传 id/code/展示名）、
 *   `policy`（群聊本轮策略 all|named|auto）；**都不传 = 现有单聊三级路由，行为不变**。
 * - `GET  /api/v1/chat/mention-candidates`
 *      → `{data:{items:[{kind,id,code,name,display_name,visibility,emoji,blurb}]}}`
 *      （private 助手的 display_name 由后端派生成「原名（用户名私有）」，前端不再拼）
 * - `GET  /api/v1/chat/sessions/{id}/participants` → `{data:{items|list:[参与者]}}`（也可能裸数组）
 * - `PUT  /api/v1/chat/sessions/{id}/participants` body `{agent_ids:[...]}` 有序整表覆盖（1..8 位，超出后端报错）
 * - SSE 新增一帧 `member_event`：`{turn_id, member_index, role_name, agent_id, status, error_text}`
 * - assistant 消息出参新增 `routed_label` / `routed_tone`（后端受控词表；老数据没有这两个字段时前端兜底）
 *
 * ----------------------------------------------------------------------
 * 约定：axios baseURL 已含 /api/v1（见 client.ts），本模块路径省略该前缀。
 */

/** 会话执行对象：auto 走总控三级路由；agent/team 直跑指定对象 */
export type ChatTarget = 'auto' | 'agent' | 'team'

/** 路由来源（诚实标记口径）：规则命中 / 智能分类 / 频率兜底 / 手动指定 / 对话直办 / 执行失败 */
export type ChatRoutedBy = 'rules' | 'llm' | 'fallback' | 'manual' | 'local' | 'error'

/**
 * 徽章色调白名单：与后端 routed_tone 受控词表对齐，同时正好是 el-tag 的合法 type。
 * 白名单而非映射表：新值一律落 'info'，避免未知色调把消息渲染打断。
 */
export type RoutedTone = 'success' | 'primary' | 'warning' | 'info' | 'danger'
const ROUTED_TONES: readonly RoutedTone[] = ['success', 'primary', 'warning', 'info', 'danger']

/** routed_tone 收敛为合法 el-tag type；未知/缺失走 info（v1.8.0 徽章崩溃的根治点之一） */
export function normalizeRoutedTone(tone?: string | null): RoutedTone {
  return ROUTED_TONES.includes(tone as RoutedTone) ? (tone as RoutedTone) : 'info'
}

/** 群聊本轮应答策略：全员答 / 点名答 / 自愿答（named 必须带 target_code） */
export type ChatGroupPolicy = 'all' | 'named' | 'auto'

/** 点名候选对象类型：已发布助手 / 班组 */
export type MentionKind = 'agent' | 'team'

/** @ 点名候选（后端已派生 display_name 与可见性，前端不再加工） */
export interface MentionCandidate {
  kind: MentionKind
  id: string
  code?: string | null
  name?: string | null
  display_name?: string | null
  visibility?: string | null
  emoji?: string | null
  blurb?: string | null
}

/** 点名候选列表响应：{items}（容忍后端改用 list 键或裸数组） */
export interface MentionCandidateList {
  items?: MentionCandidate[]
  list?: MentionCandidate[]
}

/** 会话群聊参与者（GET/PUT participants 出参） */
export interface ChatParticipant {
  id?: string
  session_id?: string
  agent_id?: string
  agent_code?: string | null
  agent_name?: string | null
  position?: number | null
}

/** 参与者列表响应：包 items / 包 list / 裸数组三种形状都可能 */
export type ParticipantListPayload =
  | { items?: ChatParticipant[]; list?: ChatParticipant[] }
  | ChatParticipant[]

/** 成员事件三态（未知取值收敛为 unknown，界面走兜底分支） */
export type MemberEventStatus = 'running' | 'done' | 'error' | 'unknown'

/** member_event 帧载荷 */
export interface ChatMemberEvent {
  turn_id?: string | null
  member_index?: number | string | null
  role_name?: string | null
  agent_id?: string | null
  status?: string | null
  error_text?: string | null
}

/**
 * 后端 MEMBER_RUNNING / MEMBER_DONE / MEMBER_ERROR 等取值 → 前端三态词表。
 * 只认这三态，其余一律 unknown，不崩。
 */
const MEMBER_STATUS_WORDS: Record<string, MemberEventStatus> = {
  running: 'running',
  started: 'running',
  pending: 'running',
  done: 'done',
  completed: 'done',
  finished: 'done',
  ok: 'done',
  success: 'done',
  error: 'error',
  failed: 'error',
  failure: 'error',
}

/** 去掉 MEMBER_ 前缀与分隔符后查三态词表 */
export function normalizeMemberStatus(raw?: string | null): MemberEventStatus {
  const key = String(raw ?? '')
    .trim()
    .toLowerCase()
    .replace(/^member[_-]?/, '')
  return MEMBER_STATUS_WORDS[key] ?? 'unknown'
}

/** 消息执行状态：正常 / 失败 / 被中止 */
export type ChatMessageStatus = 'ok' | 'failed' | 'aborted'

/** 单条消息或会话的 Token 消耗统计 */
export interface ChatTokenUsage {
  input_tokens: number
  output_tokens: number
  total_tokens: number
  model?: string | null
  latency_ms?: number | null
  estimated?: boolean
}

/** 会话对象（列表项与创建/改名返回同构） */
export interface ChatSession {
  id: string
  title: string
  /** 会话固定执行对象：auto=自动路由，agent=锁定某助手，team=班组（见后端 0028） */
  target?: ChatTarget
  target_code?: string | null
  /** target=agent 时后端解析出的助手展示名 */
  target_name?: string | null
  /** 群聊成员数（>0 即群聊；与 target 取值正交） */
  participant_count?: number
  updated_at: string
  last_excerpt?: string | null
  message_count?: number
  input_tokens?: number
  output_tokens?: number
  total_tokens?: number
}

/** 会话列表响应体：{items}，新→旧 */
export interface ChatSessionList {
  items: ChatSession[]
}

/** 实际执行的回答对象标记 */
export interface ChatChoice {
  kind: 'agent' | 'team'
  code?: string | null
  name: string
}

/** 附件类型：图片 / 文档 */
export type ChatAttachmentKind = 'image' | 'document'

/** 附件解析状态：已就绪 / 解析失败 */
export type ChatAttachmentStatus = 'ready' | 'failed'

/** 会话附件对象 */
export interface ChatAttachment {
  id: string
  session_id: string
  filename: string
  mime: string
  size_bytes: number
  kind: ChatAttachmentKind
  status: ChatAttachmentStatus
  chars: number
  error?: string | null
  /** 生成文档的格式（md/docx/xlsx/pptx/pdf）；上传附件为空 */
  doc_format?: string | null
  /** 是否由对话生成（可在线预览） */
  generated?: boolean
  created_at?: string
}

/** 附件列表响应体：{items} */
export interface ChatAttachmentList {
  items: ChatAttachment[]
}

/** 消息里引用的附件摘要（存证用，历史回放可直接展示文件名） */
export interface ChatAttachmentRef {
  id: string
  filename: string
  kind: ChatAttachmentKind
  mime: string
  size_bytes: number
  status: ChatAttachmentStatus
}

/** 过程阶段（与后端 app/core/progress.py 词表一致，只增不改） */
export type ChatStepPhase =
  | 'reading'
  | 'routing'
  | 'retrieving'
  | 'thinking'
  | 'tool_call'
  | 'generating'

/** 步骤状态：进行中 / 已完成 / 失败 */
export type ChatStepStatus = 'running' | 'done' | 'failed'

/** 单条过程步骤 */
export interface ChatStep {
  phase: ChatStepPhase
  title: string
  detail: string
  status: ChatStepStatus
  ts: string
}

/** 卡片动作项 */
export interface ChatCardAction {
  key: string
  label: string
  requires_role?: string
}

/** 能力卡片（对话内生成助手草稿） */
export interface ChatCapabilityCard {
  type: 'capability'
  state: 'created' | 'requested'
  name: string
  description: string
  agent_id: string
  code: string
  risk_level: 'low' | 'high'
  status: string
  tools: string[]
  note: string
  actions: ChatCardAction[]
}

/** 市场卡片（对话内装载能力包） */
export interface ChatMarketCard {
  type: 'market'
  state: 'suggested' | 'requested' | 'installed' | 'failed'
  name: string
  description: string
  items: { code: string; name: string }[]
  note: string
  actions: ChatCardAction[]
}

/** 文档卡片（对话内生成 Word/Excel/PPT/PDF/Markdown） */
export interface ChatDocumentCard {
  type: 'document'
  attachment_id: string
  session_id: string
  filename: string
  /** md | docx | xlsx | pptx | pdf */
  format: string
  mime: string
  size_bytes: number
  previewable: boolean
  actions: ChatCardAction[]
}

/** 技能卡片里的候选（与 /market/agent/request 的 matches 同形） */
export interface ChatSkillCardMatch {
  item_key: string
  name: string
  summary?: string
  source?: string
  license?: string | null
  risk_level?: string
  installed?: boolean
  score?: number
}

/**
 * 技能卡片（v3.2 一句话技能）：
 * matched=找到可安装的候选；created=已生成私有草稿；failed=检索/生成失败（带 note 可重试）。
 */
export interface ChatSkillCard {
  type: 'skill_card'
  state: 'matched' | 'created' | 'failed'
  utterance: string
  skill: {
    id: string
    code: string
    name: string
    visibility: string
    status: string
  } | null
  matches?: ChatSkillCardMatch[]
  sources?: { title: string; url: string }[]
  note?: string
}

/** 插件卡片里的工作流步骤（与 workflow manifest steps 同形） */
export interface ChatPluginCardStep {
  kind: 'tool' | 'llm'
  tool?: string
  args?: Record<string, unknown>
  prompt?: string
  save_as?: string | null
}

/** 插件卡片的 manifest（后端已规范化，只保留已知字段） */
export interface ChatPluginManifest {
  name: string
  description: string
  params: { key: string; label: string; required: boolean }[]
  steps: ChatPluginCardStep[]
}

/**
 * 插件卡片（v3.3 一句话插件）：created=已生成待审工作流；failed=生成失败（带 note 可重试）。
 * 插件没有"私有即用"：created 一律 pending_review，审核通过后才能运行。
 */
export interface ChatPluginCard {
  type: 'plugin_card'
  state: 'created' | 'failed'
  utterance: string
  plugin: {
    plugin_id: string
    name: string
    description: string
    status: string
    step_count: number
  } | null
  manifest?: ChatPluginManifest | null
  sources?: { title: string; url: string }[]
  note?: string
}

/** 结构化载荷（助手消息的 payload 字段） */
export type ChatCard =
  | ChatCapabilityCard
  | ChatMarketCard
  | ChatDocumentCard
  | ChatSkillCard
  | ChatPluginCard

/** 非技能/插件卡片：通用卡片组件（ChatCard）接收的形状，技能与插件卡片各自单独渲染 */
export type ChatStandardCard = Exclude<ChatCard, ChatSkillCard | ChatPluginCard>

/** 回答引用的知识/网页来源（v3.7 可信引用） */
export interface ChatCitation {
  document_id?: string
  chunk_id?: string
  title?: string
  source?: string
  page_number?: number | null
  quote?: string
  score?: number
  trust_level?: string
  url?: string
  snippet?: string
  reference?: string
  kind?: 'knowledge' | 'web' | string
}

/** 消息对象；choice/routed_by/reason 仅助手消息携带 */
export interface ChatMessage {
  id: string
  seq?: number
  role: 'user' | 'assistant'
  content: string
  status?: ChatMessageStatus
  choice?: ChatChoice | null
  routed_by?: ChatRoutedBy | null
  /** 后端受控词表下发的徽章文案（v2.0 起优先于本地映射表） */
  routed_label?: string | null
  /** 后端受控词表下发的徽章色调；未知值由 normalizeRoutedTone 收敛 */
  routed_tone?: string | null
  reason?: string | null
  error_code?: string | null
  payload?: ChatCard | null
  steps?: ChatStep[] | null
  attachments?: ChatAttachmentRef[] | null
  citations?: ChatCitation[] | null
  token_usage?: ChatTokenUsage | null
  created_at?: string
}

/** 消息列表响应体：{items}，时间升序 */
export interface ChatMessageList {
  items: ChatMessage[]
}

/** 发送后同步返回的一问一答 */
export interface ChatSendResult {
  user_message: ChatMessage
  assistant_message: ChatMessage
}

/** 创建会话入参：全部可选，缺省 target=auto */
export interface CreateSessionPayload {
  title?: string
  target?: ChatTarget
  target_code?: string
}

/**
 * 发送消息入参扩展（v2.0）：两个字段都可缺省。
 * 缺省时请求体不出现任何新键，后端按现有单聊三级路由处理，行为完全不变。
 */
export interface SendMessageExtras {
  /** @ 点名的助手：可传 id/code/展示名；named 策略必填 */
  target_code?: string | null
  /** 群聊本轮应答策略：不传 = 单聊三级路由 */
  policy?: ChatGroupPolicy | null
}

/** SSE 帧名：与后端协议保持一致，集中一处便于改 */
export const CHAT_SSE_EVENTS = {
  init: 'chat-init',
  step: 'step',
  card: 'card',
  message: 'message',
  error: 'error',
  /** 群聊成员事件帧（v2.0 新增） */
  memberEvent: 'member_event',
  end: 'chat-end',
} as const

/** @ 候选端点路径（后端为集合资源，不挂在会话下） */
export const MENTION_CANDIDATES_PATH = '/chat/mention-candidates'

/** 群聊成员条上限（与后端一致：1..8，超出后端报错，前端也先拦住） */
export const MAX_GROUP_MEMBERS = 8

/** target_code 取值优先级：code > id > display_name/name（契约说这三种后端都认） */
export function mentionTargetCode(c?: MentionCandidate | null): string {
  return c?.code || c?.id || c?.display_name || c?.name || ''
}

/** 展示名：优先用后端派生好的 display_name（private 助手已带「（xx私有）」后缀） */
export function mentionDisplayName(c?: MentionCandidate | null): string {
  return c?.display_name || c?.name || c?.code || c?.id || ''
}

/** 解包成功信封 {data, request_id} -> data */
function unwrap<T>(promise: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  return promise.then(res => res.data.data)
}

/**
 * 列表载荷形状收敛：后端可能包一层 items / list，也可能直接返回裸数组。
 * 只在这一处兼容，接口上线后若有出入只改这里。
 */
function pickRows<T>(payload: unknown): T[] {
  if (Array.isArray(payload)) return payload as T[]
  if (payload && typeof payload === 'object') {
    const obj = payload as Record<string, unknown>
    for (const key of ['items', 'list']) {
      if (Array.isArray(obj[key])) return obj[key] as T[]
    }
  }
  return []
}

/** 发送请求体构造：单聊（不传新字段）与群聊/点名共用一条通路 */
export function buildSendBody(
  content: string,
  attachmentIds: string[] = [],
  extras: SendMessageExtras = {},
): Record<string, unknown> {
  const body: Record<string, unknown> = { content, attachment_ids: attachmentIds }
  if (extras.target_code) body.target_code = extras.target_code
  if (extras.policy) body.policy = extras.policy
  return body
}

/** 发送失败由页面做「撤回气泡+保留草稿」处理，屏蔽全局重复 toast */
const silentCfg: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }

export const chatApi = {
  /** 当前用户自己的会话列表（新→旧） */
  listSessions(): Promise<ChatSessionList> {
    return unwrap(api.get<ApiResponse<ChatSessionList>>('/chat/sessions'))
  },
  createSession(payload: CreateSessionPayload = {}): Promise<ChatSession> {
    return unwrap(api.post<ApiResponse<ChatSession>>('/chat/sessions', payload))
  },
  /** 改会话标题（标题由首条用户消息前 20 字自动生成，可再改） */
  renameSession(id: string, title: string): Promise<ChatSession> {
    return unwrap(api.patch<ApiResponse<ChatSession>>(`/chat/sessions/${id}`, { title }))
  },
  deleteSession(id: string): Promise<void> {
    return api.delete<ApiResponse<null>>(`/chat/sessions/${id}`).then(() => undefined)
  },
  /** 历史消息（升序） */
  listMessages(id: string): Promise<ChatMessageList> {
    return unwrap(api.get<ApiResponse<ChatMessageList>>(`/chat/sessions/${id}/messages`))
  },
  /** 发送消息：同步等待，返回一问一答；执行失败也返回 200 + routed_by='error' */
  sendMessage(
    id: string,
    content: string,
    attachmentIds: string[] = [],
    extras: SendMessageExtras = {},
  ): Promise<ChatSendResult> {
    return unwrap(
      api.post<ApiResponse<ChatSendResult>>(
        `/chat/sessions/${id}/messages`,
        buildSendBody(content, attachmentIds, extras),
        silentCfg,
      ),
    )
  },
  /** 上传会话附件（照片/文档）：multipart */
  uploadAttachment(id: string, file: File): Promise<ChatAttachment> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(
      api.post<ApiResponse<ChatAttachment>>(`/chat/sessions/${id}/attachments`, form, {
        ...silentCfg,
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
  },
  /** 会话附件列表 */
  listAttachments(id: string): Promise<ChatAttachmentList> {
    return unwrap(api.get<ApiResponse<ChatAttachmentList>>(`/chat/sessions/${id}/attachments`))
  },
  /** 语音转写（v3.11）：音频只在服务端内存中转发上游，不落库 */
  transcribeAudio(file: File): Promise<{ text: string; model: string }> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(
      api.post<ApiResponse<{ text: string; model: string }>>('/chat/transcribe', form, {
        ...silentCfg,
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
  },
  /** 图片理解（v3.11，用户手动触发）：结果写回附件文本，随后随消息进入上下文 */
  recognizeImage(
    sessionId: string,
    attachmentId: string,
  ): Promise<{ text: string; chars: number; model: string; attachment: ChatAttachment }> {
    return unwrap(
      api.post<
        ApiResponse<{ text: string; chars: number; model: string; attachment: ChatAttachment }>
      >(`/chat/sessions/${sessionId}/attachments/${attachmentId}/vision`, {}, silentCfg),
    )
  },
  /** 删附件（连带删存储文件） */
  deleteAttachment(id: string, attachmentId: string): Promise<void> {
    return api
      .delete<ApiResponse<null>>(`/chat/sessions/${id}/attachments/${attachmentId}`, silentCfg)
      .then(() => undefined)
  },
  /** 生成文档（DocModel → md/docx/xlsx/pptx/pdf），返回文档卡片与预览/下载地址 */
  createDocument(
    sessionId: string,
    format: string,
    model: Record<string, unknown>,
  ): Promise<ChatDocumentCard> {
    return unwrap(
      api.post<ApiResponse<ChatDocumentCard>>(
        `/chat/sessions/${sessionId}/documents`,
        { format, model },
        silentCfg,
      ),
    )
  },
  /** 在线预览（后端返回自包含 HTML；前端交给无脚本沙箱 iframe 的 srcdoc 渲染） */
  async previewDocument(sessionId: string, attachmentId: string): Promise<string> {
    const res = await api.get<string>(
      `/chat/sessions/${sessionId}/attachments/${attachmentId}/preview`,
      { ...silentCfg, responseType: 'text' },
    )
    return typeof res.data === 'string' ? res.data : String(res.data)
  },
  /** 下载原文件（带登录态，越权 404） */
  async downloadDocument(sessionId: string, attachmentId: string): Promise<Blob> {
    const res = await api.get<Blob>(
      `/chat/sessions/${sessionId}/attachments/${attachmentId}/download`,
      { ...silentCfg, responseType: 'blob' },
    )
    return res.data
  },
  /** 流式发送的完整地址（fetch/SSE 用） */
  streamUrl(id: string): string {
    return apiUrl(`/chat/sessions/${id}/messages/stream`)
  },
  /**
   * @ 点名候选：本小院可见的已发布助手 + 班组。
   * 后端尚未上线：任何失败都只让候选为空（输入区不崩、不弹 toast）。
   */
  async mentionCandidates(): Promise<MentionCandidate[]> {
    try {
      const res = await api.get<ApiResponse<MentionCandidateList>>(
        MENTION_CANDIDATES_PATH,
        silentCfg,
      )
      return pickRows<MentionCandidate>(res.data.data)
    } catch {
      /* 端点不可用时只让候选为空：输入区不崩、不弹 toast */
      return []
    }
  },
  /** 会话群聊成员（按 position 排序；升序由后端保证，前端再排一次防乱序） */
  async listParticipants(sessionId: string): Promise<ChatParticipant[]> {
    const payload = await unwrap<ParticipantListPayload>(
      api.get<ApiResponse<ParticipantListPayload>>(
        `/chat/sessions/${sessionId}/participants`,
        silentCfg,
      ),
    )
    return pickRows<ChatParticipant>(payload).sort(
      (a, b) => (a.position ?? 0) - (b.position ?? 0),
    )
  },
  /** 成员整表覆盖：有序 agent_ids（1..8 位），超出后端报错并走 toast */
  saveParticipants(sessionId: string, agentIds: string[]): Promise<ChatParticipant[]> {
    return unwrap<ParticipantListPayload>(
      api.put<ApiResponse<ParticipantListPayload>>(
        `/chat/sessions/${sessionId}/participants`,
        { agent_ids: agentIds },
        silentCfg,
      ),
    ).then(payload => pickRows<ChatParticipant>(payload))
  },
}
