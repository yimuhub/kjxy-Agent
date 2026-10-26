import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { ElMessage } from 'element-plus'
import {
  CHAT_SSE_EVENTS,
  MAX_GROUP_MEMBERS,
  buildSendBody,
  chatApi,
  mentionTargetCode,
  normalizeMemberStatus,
} from '@/api/chat'
import type {
  ChatAttachment,
  ChatCard,
  ChatGroupPolicy,
  ChatMemberEvent,
  ChatMessage,
  ChatParticipant,
  ChatSession,
  ChatStep,
  CreateSessionPayload,
  MentionCandidate,
  MemberEventStatus,
  SendMessageExtras,
} from '@/api/chat'
import { extractErrorMessage } from '@/api/client'
import { friendlyErrorMessage } from '@/utils/status'
import { openSseStream, type SseHandle } from '@/utils/sse'

/** 后端未生成标题时的界面占位 */
export const UNTITLED = '新对话'

/** 主对话的 scope：ChatView 不传参时的默认实例，等价于改造前的单例 */
export const MAIN_CHAT_SCOPE = 'main'

/** 一轮对话的流式结局：正常 / 服务端报错 / 连接中断 */
type StreamOutcome = 'ok' | 'failed' | 'broken'

/** 群聊成员本轮视图状态（idle=本轮还没有该成员的事件） */
export type MemberRoundStatus = MemberEventStatus | 'idle'

/** 成员条一行：本轮真实进度 + 未回答原因（诚实口径，绝不允许静默缺席） */
export interface MemberRoundRow {
  key: string
  name: string
  emoji: string
  position: number | null
  status: MemberRoundStatus
  /** 原始 status 取值，未知值原样透出便于排查 */
  raw: string
  /** 可读状态文案 */
  note: string
  /** 是否属于「未回答」需要醒目提示 */
  missing: boolean
  /** 是否不在成员表里（后端多回了一个成员，也要如实显示） */
  extra: boolean
}

/** 成员行的稳定 key：agent_id 优先，退化到 id/code（后端事件按 agent_id 对齐） */
function participantKey(p: ChatParticipant): string {
  return p.agent_id || p.id || p.agent_code || ''
}

/** 状态文案（唯一一处，成员条与实时区共用口径） */
const MEMBER_NOTE: Record<MemberRoundStatus, string> = {
  running: '正在回答…',
  done: '已回答',
  error: '未回答',
  unknown: '状态未知',
  idle: '等待应答',
}

/**
 * 对话模式会话状态：会话列表 + 当前会话消息流 + 待发附件 + 过程步骤。
 *
 * 发送走 SSE（POST /messages/stream）：过程步骤实时上屏，最终回答随 message 帧落地。
 * 流不可用时降级为同步一问一答（REST），不静默丢弃用户输入。
 *
 * 为什么是工厂而不是单例：应用要同时打开两个对话（弹出「使用」对话框、多助手群聊），
 * 单例会让两个面板共用 sending/messages/attachments，互相把对方的状态吹乱。
 * 按 scope 注册 `chat-<scope>` 的 store：逻辑仍只有一份，状态按实例隔离。
 */
function buildChatStore(scope: string) {
  return defineStore(`chat-${scope}`, () => {
    /** 实例标识（暴露给视图，便于断言两份状态确实不是同一个 store） */
    const instanceScope = ref(scope)
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

    /** @ 点名候选（小院级，取一次即可复用） */
    const mentionCandidates = ref<MentionCandidate[]>([])
    const mentionLoading = ref(false)
    /** 本轮点名对象：发完自动清空，下一条回到自动路由 */
    const mention = ref<MentionCandidate | null>(null)

    /** 群聊固定成员（会话级，按 position 有序） */
    const participants = ref<ChatParticipant[]>([])
    const participantsLoading = ref(false)
    /** 本轮应答策略；仅在成员表非空时才随请求发送（单聊行为保持不变） */
    const groupPolicy = ref<ChatGroupPolicy>('auto')
    /** 本轮 member_event 实况：key -> 状态（key 用 agent_id，兜底 role_name/member_index） */
    const liveMembers = ref<Record<string, MemberRoundRow>>({})
    /** 本轮是否已收尾：收尾后仍无事件的成员才能判定为「未回答」 */
    const roundDone = ref(false)

    const activeSession = computed(() => sessions.value.find(s => s.id === activeId.value) ?? null)
    const activeTitle = computed(() => activeSession.value?.title || UNTITLED)

    /** 群聊态：有固定成员才谈策略；空成员表 = 原单聊，发送体里不出现 policy */
    const isGroup = computed(() => participants.value.length > 0)

    /** 成员条本轮视图：按 position 顺序，外加后端多回、成员表里没有的成员 */
    const memberRounds = computed<MemberRoundRow[]>(() => {
      const listed: MemberRoundRow[] = []
      const known = new Set<string>()
      participants.value.forEach((p, i) => {
        const key = participantKey(p) || `idx:${i}`
        known.add(key)
        const live = liveMembers.value[key]
        if (live) {
          listed.push({ ...live, key, extra: false })
          return
        }
        const waiting = sending.value || !roundDone.value
        listed.push({
          key,
          name: p.agent_name || p.agent_code || key,
          emoji: '',
          position: p.position ?? i + 1,
          status: 'idle',
          raw: '',
          // 本轮收尾后仍没有任何事件：如实说明未回答，不做静默缺席
          note: waiting ? '等待应答' : '未回答：本轮后端未返回该成员的应答',
          missing: !waiting,
          extra: false,
        })
      })
      const extras = Object.entries(liveMembers.value)
        .filter(([key]) => !known.has(key))
        .map(([, row]) => ({ ...row, extra: true }))
      return [...listed, ...extras]
    })

    /**
     * 发送受阻原因：群聊 named 策略必须有点名对象，否则后端无从定点。
     * 返回空串表示可以发送。
     */
    const sendBlockReason = computed(() => {
      if (isGroup.value && groupPolicy.value === 'named' && !mention.value) {
        return '点名答需要先用 @ 指定一位助手'
      }
      return ''
    })

    /** 本轮要带上的发送附加字段（单聊且未点名时全为空，请求体保持原样） */
    function currentExtras() {
      return {
        target_code: mention.value ? mentionTargetCode(mention.value) : null,
        policy: isGroup.value ? groupPolicy.value : null,
      }
    }

    /** 发送类请求走了 skipErrorToast，这里统一给一条中文提示 */
    function toastError(err: unknown) {
      const { code, message } = extractErrorMessage(err)
      ElMessage.error(friendlyErrorMessage(code, message))
    }

    /**
     * 拉取会话列表；当前选中项失效时自动切到最新会话。
     *
     * 竞态守卫：请求发出之后本地可能又新建了会话（点「新对话」、空态直接发消息），
     * 这份会话自然不会出现在这次响应里。此时不能把它当成「已失效」切走——否则用户
     * 正在聊的会话会被静默换成一条旧会话，流式回来的回答也因 sid !== activeId 被丢弃。
     */
    async function fetchSessions() {
      sessionsLoading.value = true
      try {
        const res = await chatApi.listSessions()
        const list = res.items ?? []
        const pending = sessions.value.find(s => s.id === activeId.value) ?? null
        // 服务端列表新→旧；本地刚建、服务端还没回的会话留在最前面
        sessions.value = pending && !list.some(s => s.id === pending.id) ? [pending, ...list] : list
        if (!sessions.value.some(s => s.id === activeId.value)) {
          activeId.value = sessions.value[0]?.id ?? ''
          messages.value = []
          attachments.value = []
          resetRoundView()
          if (activeId.value) {
            await loadMessages()
            await loadParticipants()
          } else {
            participants.value = []
          }
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
      resetRoundView()
      await loadMessages()
      await loadParticipants()
    }

    /**
     * 新建空会话并选中（标题留空，首条消息发送后自动生成）。
     * payload 供弹出对话框这类「锁定执行对象」的实例传 target/target_code。
     */
    async function newSession(payload: CreateSessionPayload = {}) {
      if (sending.value) return
      const session = await chatApi.createSession(payload)
      sessions.value.unshift(session)
      activeId.value = session.id
      messages.value = []
      attachments.value = []
      streamError.value = ''
      resetRoundView()
      participants.value = []
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
        resetRoundView()
        if (activeId.value) await loadMessages()
      }
    }

    /* ---------------- @ 点名 ---------------- */

    /** 候选只拉一次（小院级数据）；端点未上线时静默为空，不影响输入区 */
    async function loadMentionCandidates(force = false) {
      if (mentionCandidates.value.length && !force) return
      mentionLoading.value = true
      try {
        mentionCandidates.value = await chatApi.mentionCandidates()
      } finally {
        mentionLoading.value = false
      }
    }

    /** 设定本轮点名对象（发送时转成 target_code） */
    function setMention(candidate: MentionCandidate | null) {
      mention.value = candidate
    }

    /** 发完清除点名态：下一条消息回到后端自动路由 */
    function clearMention() {
      mention.value = null
    }

    /* ---------------- 多助手群聊 ---------------- */

    /** 换会话/重开一轮时清掉上一轮的成员实况 */
    function resetRoundView() {
      liveMembers.value = {}
      roundDone.value = false
    }

    /** 拉成员表（新端点，失败不弹 toast：会话可能还没有成员概念） */
    async function loadParticipants() {
      const sid = activeId.value
      if (!sid) {
        participants.value = []
        return
      }
      participantsLoading.value = true
      try {
        const rows = await chatApi.listParticipants(sid)
        if (sid === activeId.value) participants.value = rows
      } catch {
        if (sid === activeId.value) participants.value = []
      } finally {
        participantsLoading.value = false
      }
    }

    /**
     * 有序整表覆盖（后端语义是 PUT 全量）：本地先落地再回写，失败回滚到服务端实况。
     * 超过 MAX_GROUP_MEMBERS 直接前端拦下，不给后端 400 的机会。
     */
    async function saveParticipantOrder(rows: ChatParticipant[]): Promise<boolean> {
      const sid = activeId.value
      if (!sid) return false
      const ids = rows.map(participantKey).filter(Boolean)
      if (ids.length > MAX_GROUP_MEMBERS) {
        ElMessage.warning(`群聊成员最多 ${MAX_GROUP_MEMBERS} 位`)
        return false
      }
      if (!ids.length) {
        // 清空成员表只能靠 PUT 空数组；后端契约是 1..8，因此退化为「保持空」本地态
        participants.value = []
        groupPolicy.value = 'auto'
        return true
      }
      const before = participants.value.slice()
      participants.value = rows
      try {
        const saved = await chatApi.saveParticipants(sid, ids)
        if (saved.length) participants.value = saved
        return true
      } catch (err) {
        participants.value = before
        toastError(err)
        return false
      }
    }

    /** 候选 → 参与者：优先用 id 作为 agent_id（契约允许 id/code/展示名，成员表要稳定 id） */
    function toParticipant(c: MentionCandidate): ChatParticipant {
      return {
        agent_id: c.id,
        agent_code: c.code ?? null,
        agent_name: c.display_name || c.name || c.code || c.id,
      }
    }

    async function addParticipant(c: MentionCandidate): Promise<boolean> {
      if (participants.value.length >= MAX_GROUP_MEMBERS) {
        ElMessage.warning(`群聊成员最多 ${MAX_GROUP_MEMBERS} 位`)
        return false
      }
      if (participants.value.some(p => participantKey(p) === c.id)) return false
      const rows = [...participants.value, toParticipant(c)].map((p, i) => ({
        ...p,
        position: i + 1,
      }))
      return saveParticipantOrder(rows)
    }

    async function removeParticipant(key: string): Promise<boolean> {
      const rows = participants.value
        .filter(p => participantKey(p) !== key)
        .map((p, i) => ({ ...p, position: i + 1 }))
      if (participants.value.length <= 1) {
        // 最后一位移除即退出群聊：本地清空并回到单聊三级路由
        participants.value = []
        groupPolicy.value = 'auto'
        resetRoundView()
        return true
      }
      return saveParticipantOrder(rows)
    }

    /** 调序：delta=-1 前移，+1 后移（上下按钮比拖拽更好做无障碍，效果等价） */
    async function moveParticipant(key: string, delta: number): Promise<boolean> {
      const rows = participants.value.slice()
      const from = rows.findIndex(p => participantKey(p) === key)
      const to = from + delta
      if (from < 0 || to < 0 || to >= rows.length) return false
      const [moved] = rows.splice(from, 1)
      rows.splice(to, 0, moved)
      return saveParticipantOrder(rows.map((p, i) => ({ ...p, position: i + 1 })))
    }

    function setGroupPolicy(policy: ChatGroupPolicy) {
      groupPolicy.value = policy
    }

    /** member_event 帧落地：按 agent_id → member_index → role_name 依次对齐成员表 */
    function applyMemberEvent(raw: ChatMemberEvent) {
      const index =
        typeof raw.member_index === 'number' ? raw.member_index : Number(raw.member_index)
      const byId = raw.agent_id
        ? participants.value.find(p => participantKey(p) === raw.agent_id)
        : undefined
      const byIndex = Number.isInteger(index) && index >= 0 ? participants.value[index] : undefined
      const byName = raw.role_name
        ? participants.value.find(p => p.agent_name === raw.role_name)
        : undefined
      const matched = byId || byIndex || byName
      const key =
        participantKey(matched ?? {}) || raw.agent_id || raw.role_name || `idx:${raw.member_index}`
      const status = normalizeMemberStatus(raw.status)
      const errorText = String(raw.error_text ?? '')
      const row: MemberRoundRow = {
        key,
        name: matched?.agent_name || raw.role_name || '未具名成员',
        emoji: '',
        position: matched?.position ?? (Number.isInteger(index) ? (index as number) + 1 : null),
        status,
        raw: String(raw.status ?? ''),
        // error 无 error_text 时也要说清「没回答」而不是留白
        note:
          status === 'error'
            ? `${MEMBER_NOTE.error}：${errorText || '后端未给出原因'}`
            : status === 'unknown'
              ? `${MEMBER_NOTE.unknown}（${raw.status || '空状态'}）`
              : MEMBER_NOTE[status],
        missing: status === 'error' || status === 'unknown',
        extra: false,
      }
      liveMembers.value = { ...liveMembers.value, [key]: row }
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
      extras: SendMessageExtras = {},
    ): Promise<StreamOutcome> {
      return new Promise(resolve => {
        let handle: SseHandle | null = null
        let settled = false
        let pendingCard: ChatCard | null = null

        const finish = (outcome: StreamOutcome) => {
          if (settled) return
          settled = true
          roundDone.value = true
          handle?.close()
          resolve(outcome)
        }

        handle = openSseStream({
          url: chatApi.streamUrl(sid),
          method: 'POST',
          body: JSON.stringify(buildSendBody(content, attachmentIds, extras)),
          onFrame: frame => {
            const data = parseFrameData(frame.data)
            if (frame.event === CHAT_SSE_EVENTS.init) {
              const um = data.user_message
              replaceLocalUser(localId, um ? asMessage(um) : undefined)
            } else if (frame.event === CHAT_SSE_EVENTS.step) {
              liveSteps.value = (data.steps as ChatStep[] | undefined) ?? []
            } else if (frame.event === CHAT_SSE_EVENTS.card) {
              pendingCard = (data.payload as ChatCard | undefined) ?? null
            } else if (frame.event === CHAT_SSE_EVENTS.memberEvent) {
              applyMemberEvent(data as ChatMemberEvent)
            } else if (frame.event === CHAT_SSE_EVENTS.message) {
              const msg = asMessage(data)
              if (sid === activeId.value) {
                messages.value.push({
                  ...msg,
                  payload: msg.payload ?? pendingCard,
                })
              }
            } else if (frame.event === CHAT_SSE_EVENTS.error) {
              streamError.value = String(data.message ?? '执行失败')
            } else if (frame.event === CHAT_SSE_EVENTS.end) {
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
      // 本轮的点名与策略在发送瞬间定格：发完即清，下一条回到自动路由
      const extras = currentExtras()
      sending.value = true
      liveSteps.value = []
      streamError.value = ''
      resetRoundView()
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
        const outcome = await sendViaStream(sid, draft, attIds, local.id, extras)
        if (outcome === 'ok') {
          attachments.value = attachments.value.filter(a => !attIds.includes(a.id))
          // 点名是一次性的：本轮已投递给服务端，下一条回到自动路由
          if (extras.target_code) clearMention()
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
          if (extras.target_code) clearMention()
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
      scope: instanceScope,
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
      /* 点名与群聊：全部按实例隔离，弹出面板与主对话互不污染 */
      mentionCandidates,
      mentionLoading,
      mention,
      participants,
      participantsLoading,
      groupPolicy,
      liveMembers,
      roundDone,
      isGroup,
      memberRounds,
      sendBlockReason,
      fetchSessions,
      loadMessages,
      selectSession,
      newSession,
      renameSession,
      removeSession,
      uploadAttachment,
      removeAttachment,
      loadMentionCandidates,
      setMention,
      clearMention,
      loadParticipants,
      addParticipant,
      removeParticipant,
      moveParticipant,
      saveParticipantOrder,
      setGroupPolicy,
      send,
      sendViaStream,
    }
  })
}

type ChatStoreDefinition = ReturnType<typeof buildChatStore>

/** 一个对话实例的状态形状（ChatPanel 以此类型接收 store prop） */
export type ChatStoreInstance = ReturnType<ChatStoreDefinition>

/**
 * scope → store 定义：同 scope 复用同一个实例（跨组件挂载不丢状态），
 * 不同 scope 各自一份 messages/sending/attachments，实现并行对话。
 */
const chatStoreDefinitions = new Map<string, ChatStoreDefinition>()

/**
 * 取一个对话实例的状态。
 * @param scope 实例标识；主对话用默认值，弹出面板/群聊各传自己的 id
 */
export function useChatStore(scope: string = MAIN_CHAT_SCOPE): ChatStoreInstance {
  let definition = chatStoreDefinitions.get(scope)
  if (!definition) {
    definition = buildChatStore(scope)
    chatStoreDefinitions.set(scope, definition)
  }
  return definition()
}
