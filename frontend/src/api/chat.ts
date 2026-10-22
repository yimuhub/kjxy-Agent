import type { AxiosRequestConfig, AxiosResponse } from 'axios'
import { api } from './client'
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
 * ----------------------------------------------------------------------
 * 约定：axios baseURL 已含 /api/v1（见 client.ts），本模块路径省略该前缀。
 */

/** 会话执行对象：auto 走总控三级路由；agent/team 直跑指定对象 */
export type ChatTarget = 'auto' | 'agent' | 'team'

/** 路由来源（诚实标记口径）：规则命中 / 智能分类 / 频率兜底 / 手动指定 / 执行失败 */
export type ChatRoutedBy = 'rules' | 'llm' | 'fallback' | 'manual' | 'error'

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

/** 消息对象；choice/routed_by/reason 仅助手消息携带 */
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  choice?: ChatChoice | null
  routed_by?: ChatRoutedBy | null
  reason?: string | null
  created_at?: string
}

/** 消息列表响应体：{items}，时间升序 */
export interface ChatMessageList {
  items: ChatMessage[]
}

/** 发送后同步返回的一问一答（v1 无流式） */
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
  sendMessage(id: string, content: string): Promise<ChatSendResult> {
    return unwrap(
      api.post<ApiResponse<ChatSendResult>>(
        `/chat/sessions/${id}/messages`,
        { content },
        silentCfg,
      ),
    )
  },
}
