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
 * - 全部登录可用（成员级），跨租户/跨用户会话一律 404
 *
 * ---------- 附件与流式（v1.8） ----------
 *
 * - `POST /api/v1/chat/sessions/{id}/attachments`（multipart `file`）→ 附件对象（201）
 * - `GET  /api/v1/chat/sessions/{id}/attachments` → `{items:[附件]}`
 * - `DELETE /api/v1/chat/sessions/{id}/attachments/{aid}` → ok 包
 * - `POST /api/v1/chat/sessions/{id}/messages` body `{content, attachment_ids?}` → 同步一问一答（流式不可用时的兜底）
 * - `POST /api/v1/chat/sessions/{id}/messages/stream`（SSE）→ 过程步骤 + 最终回答
 *
 * ----------------------------------------------------------------------
 * 约定：axios baseURL 已含 /api/v1（见 client.ts），本模块路径省略该前缀。
 */

/** 会话执行对象：auto 走总控三级路由；agent/team 直跑指定对象 */
export type ChatTarget = 'auto' | 'agent' | 'team'

/** 路由来源（诚实标记口径）：规则命中 / 智能分类 / 频率兜底 / 手动指定 / 对话直办 / 执行失败 */
export type ChatRoutedBy = 'rules' | 'llm' | 'fallback' | 'manual' | 'local' | 'error'

/** 消息执行状态：正常 / 失败 / 被中止 */
export type ChatMessageStatus = 'ok' | 'failed' | 'aborted'

/** 会话对象（列表项与创建/改名返回同构） */
export interface ChatSession {
  id: string
  title: string
  target: ChatTarget
  target_code?: string | null
  updated_at: string
  last_excerpt?: string | null
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

/** 结构化载荷（助手消息的 payload 字段） */
export type ChatCard = ChatCapabilityCard | ChatMarketCard

/** 消息对象；choice/routed_by/reason 仅助手消息携带 */
export interface ChatMessage {
  id: string
  seq?: number
  role: 'user' | 'assistant'
  content: string
  status?: ChatMessageStatus
  choice?: ChatChoice | null
  routed_by?: ChatRoutedBy | null
  reason?: string | null
  error_code?: string | null
  payload?: ChatCard | null
  steps?: ChatStep[] | null
  attachments?: ChatAttachmentRef[] | null
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

/** 解包成功信封 {data, request_id} -> data */
function unwrap<T>(promise: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  return promise.then(res => res.data.data)
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
  sendMessage(id: string, content: string, attachmentIds: string[] = []): Promise<ChatSendResult> {
    return unwrap(
      api.post<ApiResponse<ChatSendResult>>(
        `/chat/sessions/${id}/messages`,
        { content, attachment_ids: attachmentIds },
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
  /** 删附件（连带删存储文件） */
  deleteAttachment(id: string, attachmentId: string): Promise<void> {
    return api
      .delete<ApiResponse<null>>(`/chat/sessions/${id}/attachments/${attachmentId}`, silentCfg)
      .then(() => undefined)
  },
  /** 流式发送的完整地址（fetch/SSE 用） */
  streamUrl(id: string): string {
    return apiUrl(`/chat/sessions/${id}/messages/stream`)
  },
}
