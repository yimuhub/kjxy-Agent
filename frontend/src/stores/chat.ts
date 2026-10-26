import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { chatApi } from '@/api/chat'
import type {
  ChatAttachment,
  ChatCard,
  ChatMessage,
  ChatSession,
  ChatStep,
} from '@/api/chat'
import { extractErrorMessage } from '@/api/client'
import { friendlyErrorMessage } from '@/utils/status'
import { openSseStream, type SseHandle } from '@/utils/sse'

/** 后端未生成标题时的界面占位 */
export const UNTITLED = '新对话'

/** 一轮对话的流式结局：正常 / 服务端报错 / 连接中断 */
type StreamOutcome = 'ok' | 'failed' | 'broken'

/**
 * 对话模式会话状态：会话列表 + 当前会话消息流 + 待发附件 + 过程步骤。
 *
 * 发送走 SSE（POST /messages/stream）：过程步骤实时上屏，最终回答随 message 帧落地。
 * 流不可用时降级为同步一问一答（REST），不静默丢弃用户输入。
 */
export const useChatStore = defineStore('chat', () => {
  const sessions = ref<ChatSession[]>([])
  const activeId = ref('')
  const messages = ref<ChatMessage[]>([])
  const sessionsLoading = ref(false)
  const messagesLoading = ref(false)
  const sending = ref(false)
  /** 待发附件（已上传、尚未随消息引用） */
  const attachments = ref<ChatAttachment[]>([])
  /** 附件上传中 */
  const uploading = ref(false)
  /** 本轮实时步骤（发送中） */
  const liveSteps = ref<ChatStep[]>([])
  /** 流中断提示（诚实告知，不假装成功） */
  const streamError = ref('')

  const activeSession = computed(() => sessions.value.find(s => s.id === activeId.value) ?? null)
  const activeTitle = computed(() => activeSession.value?.title || UNTITLED)

  /** 发送类请求走了 skipErrorToast，这里统一给一条中文提示 */
  function toastError(err: unknown) {
    const { code, message } = extractErrorMessage(err)
    ElMessage.error(friendlyErrorMessage(code, message))
  }

  /** 拉取会话列表；当前选中项失效时自动切到最新会话 */
  async function fetchSessions() {
    sessionsLoading.value = true
    try {
      const res = await chatApi.listSessions()
      sessions.value = res.items ?? []
      if (!sessions.value.some(s => s.id === activeId.value)) {
        activeId.value = sessions.value[0]?.id ?? ''
        messages.value = []
        attachments.value = []
        if (activeId.value) await loadMessages()
      }
    } finally {
      sessionsLoading.value = false
    }
  }

  async function loadMessages() {
    if (!activeId.value) return
    messagesLoading.value = true
    try {
      const res = await chatApi.listMessages(activeId.value)
      messages.value = res.items ?? []
    } finally {
      messagesLoading.value = false
    }
  }

  async function selectSession(id: string) {
    if (id === activeId.value) return
    activeId.value = id
    messages.value = []
    attachments.value = []
    streamError.value = ''
    await loadMessages()
  }

  /** 新建空会话并选中（标题留空，首条消息发送后自动生成） */
  async function newSession() {
    if (sending.value) return
    const session = await chatApi.createSession({})
    sessions.value.unshift(session)
    activeId.value = session.id
    messages.value = []
    attachments.value = []
    streamError.value = ''
  }

  async function renameSession(id: string, title: string) {
    const updated = await chatApi.renameSession(id, title)
    const target = sessions.value.find(s => s.id === id)
    if (target) target.title = updated?.title || title
  }

  async function removeSession(id: string) {
    await chatApi.deleteSession(id)
    sessions.value = sessions.value.filter(s => s.id !== id)
    if (activeId.value === id) {
      activeId.value = sessions.value[0]?.id ?? ''
      messages.value = []
      attachments.value = []
      if (activeId.value) await loadMessages()
    }
  }

  /** 上传附件到当前会话（无会话则先建）；成功后进入待发列表 */
  async function uploadAttachment(file: File): Promise<boolean> {
    if (sending.value) return false
    if (!activeId.value) {
      try {
        await newSession()
      } catch {
        return false
      }
    }
    const sid = activeId.value
    uploading.value = true
    try {
      const att = await chatApi.uploadAttachment(sid, file)
      attachments.value.push(att)
      return true
    } catch (err) {
      toastError(err)
      return false
    } finally {
      uploading.value = false
    }
  }

  /** 移除待发附件（同时删服务端文件，避免留垃圾） */
  async function removeAttachment(id: string) {
    const sid = activeId.value
    attachments.value = attachments.value.filter(a => a.id !== id)
    if (!sid) return
    try {
      await chatApi.deleteAttachment(sid, id)
    } catch (err) {
      toastError(err)
    }
  }

  function parseFrameData(raw: string): Record<string, unknown> {
    try {
      const parsed = JSON.parse(raw)
      return parsed && typeof parsed === 'object' ? (parsed as Record<string, unknown>) : {}
    } catch {
      return {}
    }
  }

  /** 帧数据 → 消息对象（字段由后端 message_out 保证，这里只做形状收敛） */
  function asMessage(data: unknown): ChatMessage {
    return data as ChatMessage
  }

  /** 用服务端返回的用户消息替换乐观气泡 */
  function replaceLocalUser(localId: string, server: ChatMessage | undefined) {
    const i = messages.value.findIndex(m => m.id === localId)
    if (i < 0) return
    if (server && server.id) {
      messages.value.splice(i, 1, { ...messages.value[i], ...server })
    } else {
      messages.value.splice(i, 1)
    }
  }

  /**
   * SSE 一轮对话：过程步骤实时上屏，最终回答与卡片随 message 帧落地。
   * 断流不重试（POST 重试会重复提交），改为以服务端记录为准降级。
   */
  function sendViaStream(
    sid: string,
    content: string,
    attachmentIds: string[],
    localId: string,
  ): Promise<StreamOutcome> {
    return new Promise(resolve => {
      let handle: SseHandle | null = null
      let settled = false
      let pendingCard: ChatCard | null = null

      const finish = (outcome: StreamOutcome) => {
        if (settled) return
        settled = true
        handle?.close()
        resolve(outcome)
      }

      handle = openSseStream({
        url: chatApi.streamUrl(sid),
        method: 'POST',
        body: JSON.stringify({ content, attachment_ids: attachmentIds }),
        onFrame: frame => {
          const data = parseFrameData(frame.data)
          if (frame.event === 'chat-init') {
            const um = data.user_message
            replaceLocalUser(localId, um ? asMessage(um) : undefined)
          } else if (frame.event === 'step') {
            liveSteps.value = (data.steps as ChatStep[] | undefined) ?? []
          } else if (frame.event === 'card') {
            pendingCard = (data.payload as ChatCard | undefined) ?? null
          } else if (frame.event === 'message') {
            const msg = asMessage(data)
            if (sid === activeId.value) {
              messages.value.push({
                ...msg,
                payload: msg.payload ?? pendingCard,
              })
            }
          } else if (frame.event === 'error') {
            streamError.value = String(data.message ?? '执行失败')
          } else if (frame.event === 'chat-end') {
            finish(data.status === 'ok' ? 'ok' : 'failed')
          }
        },
        onFallback: () => finish('broken'),
      })
    })
  }

  /**
   * 发送一条消息：乐观上屏 + 流式执行。
   * 无当前会话时先自动建会话；失败撤回乐观气泡（视图保留草稿便于重发）。
   * 返回是否发送成功，供输入区决定是否清空草稿。
   */
  async function send(content: string): Promise<boolean> {
    const draft = content.trim()
    if (!draft || sending.value) return false
    // 无会话先创建一个（失败已由拦截器 toast），保证「空态直接说要办的事」可用
    if (!activeId.value) {
      try {
        await newSession()
      } catch {
        return false
      }
    }
    const sid = activeId.value
    const sentAttachments = attachments.value.slice()
    const attIds = sentAttachments.map(a => a.id)
    sending.value = true
    liveSteps.value = []
    streamError.value = ''
    const local: ChatMessage = {
      id: `local-${Date.now()}`,
      role: 'user',
      content: draft,
      created_at: new Date().toISOString(),
      attachments: sentAttachments.map(a => ({
        id: a.id,
        filename: a.filename,
        kind: a.kind,
        mime: a.mime,
        size_bytes: a.size_bytes,
        status: a.status,
      })),
    }
    messages.value.push(local)
    try {
      const outcome = await sendViaStream(sid, draft, attIds, local.id)
      if (outcome === 'ok') {
        attachments.value = attachments.value.filter(a => !attIds.includes(a.id))
        // 标题兜底：首条用户消息前 20 字自动生成（后端已生成时 PATCH 也幂等）
        const session = sessions.value.find(s => s.id === sid)
        if (session && !session.title) {
          const title = draft.slice(0, 20)
          try {
            await chatApi.renameSession(sid, title)
            session.title = title
          } catch {
            /* 标题仅展示增强，失败不阻塞对话 */
          }
        }
        // 列表排序与摘要以服务端为准，静默刷新
        await fetchSessions().catch(() => undefined)
        return true
      }
      // 降级：以服务端记录为准（失败/中止的助手消息已落库，诚实呈现）
      await loadMessages().catch(() => undefined)
      const persisted = messages.value.some(
        m => m.role === 'user' && m.content === draft && m.id !== local.id,
      )
      if (persisted) {
        attachments.value = attachments.value.filter(a => !attIds.includes(a.id))
        return true
      }
      // 服务端没收到这次发言：撤回乐观气泡并保留草稿
      const i = messages.value.findIndex(m => m.id === local.id)
      if (i >= 0) messages.value.splice(i, 1)
      if (!streamError.value) streamError.value = '连接中断，回答未完成，可重新发送。'
      return false
    } catch (err) {
      const i = messages.value.findIndex(m => m.id === local.id)
      if (sid === activeId.value && i >= 0) messages.value.splice(i, 1)
      toastError(err)
      return false
    } finally {
      sending.value = false
      liveSteps.value = []
    }
  }

  return {
    sessions,
    activeId,
    messages,
    sessionsLoading,
    messagesLoading,
    sending,
    attachments,
    uploading,
    liveSteps,
    streamError,
    activeSession,
    activeTitle,
    fetchSessions,
    loadMessages,
    selectSession,
    newSession,
    renameSession,
    removeSession,
    uploadAttachment,
    removeAttachment,
    send,
  }
})
