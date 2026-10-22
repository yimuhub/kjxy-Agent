import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import { chatApi } from '@/api/chat'
import type { ChatMessage, ChatSession } from '@/api/chat'
import { extractErrorMessage } from '@/api/client'
import { friendlyErrorMessage } from '@/utils/status'

/** 后端未生成标题时的界面占位 */
export const UNTITLED = '新对话'

/**
 * 对话模式会话状态：会话列表 + 当前会话消息流。
 * v1 后端同步返回一问一答（无流式），发送期间 sending 常驻用于打字气泡与输入锁定。
 */
export const useChatStore = defineStore('chat', () => {
  const sessions = ref<ChatSession[]>([])
  const activeId = ref('')
  const messages = ref<ChatMessage[]>([])
  const sessionsLoading = ref(false)
  const messagesLoading = ref(false)
  const sending = ref(false)

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
    await loadMessages()
  }

  /** 新建空会话并选中（标题留空，首条消息发送后自动生成） */
  async function newSession() {
    if (sending.value) return
    const session = await chatApi.createSession({})
    sessions.value.unshift(session)
    activeId.value = session.id
    messages.value = []
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
      if (activeId.value) await loadMessages()
    }
  }

  /**
   * 发送一条消息：乐观上屏 + 同步等待回答。
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
    sending.value = true
    const local: ChatMessage = {
      id: `local-${Date.now()}`,
      role: 'user',
      content: draft,
      created_at: new Date().toISOString(),
    }
    messages.value.push(local)
    try {
      const res = await chatApi.sendMessage(sid, draft)
      // 会话未切换时才回写消息流；乐观气泡换成服务端一问一答
      if (sid === activeId.value) {
        const i = messages.value.findIndex(m => m.id === local.id)
        if (i >= 0) messages.value.splice(i, 1)
        messages.value.push(res.user_message, res.assistant_message)
      }
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
    } catch (err) {
      const i = messages.value.findIndex(m => m.id === local.id)
      if (sid === activeId.value && i >= 0) messages.value.splice(i, 1)
      toastError(err)
      return false
    } finally {
      sending.value = false
    }
  }

  return {
    sessions,
    activeId,
    messages,
    sessionsLoading,
    messagesLoading,
    sending,
    activeSession,
    activeTitle,
    fetchSessions,
    loadMessages,
    selectSession,
    newSession,
    renameSession,
    removeSession,
    send,
  }
})
