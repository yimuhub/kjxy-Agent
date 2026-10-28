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
  ChatApprovalMode,
  ChatAttachment,
  ChatCard,
  ChatGroupPolicy,
  ChatMemberEvent,
  ChatMessage,
  ChatParticipant,
  ChatPluginCard,
  ChatSession,
  ChatSkillCard,
  ChatSkillCardMatch,
  ChatStep,
  CreateSessionPayload,
  MentionCandidate,
  MemberEventStatus,
  SendMessageExtras,
  SessionBatchAction,
  SessionPatch,
} from '@/api/chat'
import { extractErrorMessage } from '@/api/client'
import { skillApi, skillApiErrorText, skillLabApi } from '@/api/v3'
import { friendlyErrorMessage } from '@/utils/status'
import { apiTimeMs } from '@/utils/format'
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

/** 技能卡片的按钮级状态（按消息 id 挂在实例 store 上） */
export interface SkillCardOpState {
  /** 正在安装的 item_key；空串表示空闲 */
  installing: string
  submitting: boolean
  retrying: boolean
  /** 卡片内联错误（不弹全局 toast） */
  error: string
}

/** 插件卡片的按钮级状态（按消息 id 挂在实例 store 上） */
export interface PluginCardOpState {
  retrying: boolean
  /** 卡片内联错误（不弹全局 toast） */
  error: string
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
    /** 列表顶部筛选：进行中 / 已归档（两个视图互斥，服务端按 archived 参数分给） */
    const sessionFilter = ref<'active' | 'archived'>('active')
    /** 批量模式：开启后每行出现勾选框，底部出现批量条 */
    const batchMode = ref(false)
    /**
     * 勾选集合用 Set 而不是数组：列表有几百行时，每行都要问一次「我选中没有」，
     * 数组的 includes 会把一次点击放大成 O(行数 × 已选数) 的字符串比较。
     */
    const batchIds = ref<Set<string>>(new Set())
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

    /**
     * 轮次序号：每一轮 send 自增。
     * 收尾时按序号判定「是否仍是当前轮」——上一轮的清理不得覆盖新一轮的发送态。
     */
    let roundSeq = 0

    /**
     * 乐观消息的本地 id 序号：用 `Date.now()` 做 id 在同一毫秒内发两条（脚本连发、
     * 用户连点）会撞出重复的 v-for key，Vue 遇到重复 key 的补丁行为不保证，表现为
     * 气泡错位/丢内容。改成实例内自增，天然唯一。
     */
    let localMsgSeq = 0

    /**
     * 正在新建的会话（点了「新对话」到服务端回包这段窗口）。
     * 建会话要一个来回，窗口里用户就可能按下回车发消息；发消息前先 await 它，
     * 否则消息会落到上一个会话——用户看着像「刚开的对话没接上消息」。
     */
    let creatingSession: Promise<ChatSession | undefined> | null = null

    /**
     * 会话焦点令牌：新建会话、切换会话都会自增。
     * 只有令牌最新的那次操作允许把焦点落到会话上——两次「新建」并发时后点的赢，
     * 建会话在途时用户已经切走别的会话，也不被抢回来。
     */
    let focusToken = 0

    /**
     * 技能卡片（一句话技能）的实例级操作状态：按消息 id 隔离。
     * 必须挂在各自的 store 实例上——同一页面可能并存多个 ChatPanel（主对话 / 弹出面板），
     * 全局单例会让两个面板的「安装中 / 提交中」互相串台。
     */
    const skillCardOps = ref<Record<string, SkillCardOpState>>({})

    /**
     * 插件卡片（一句话插件）的实例级操作状态：按消息 id 隔离。
     * 与技能卡片同口径——同一页面可能并存多个 ChatPanel，状态互不串台。
     */
    const pluginCardOps = ref<Record<string, PluginCardOpState>>({})

    const activeSession = computed(() => sessions.value.find(s => s.id === activeId.value) ?? null)

    /**
     * 列表真正渲染的那份：按当前筛选过滤，再排成「置顶恒在最前（置顶时间倒序），
     * 其余最近更新在前」。
     *
     * 这条排序规则与服务端 ``list_sessions`` 的 order_by 一一对应（pinned_at 非空在前、
     * pinned_at 倒序、updated_at 倒序），在这里本地做而不是每次改标记位都回读整张列表：
     * 会话多时一次回读要为每行统计 Token，点一下「置顶」就卡半秒不值当。
     * E2E 用例 chat-session-ops 会真的检查置顶后落到第一行，规则漂移会立刻失败。
     */
    const visibleSessions = computed(() => {
      const wantArchived = sessionFilter.value === 'archived'
      /*
       * 每行只取一次排序键。以前比较器里现算 `parseApiTime`，排序会把解析放大成
       * O(N log N) 次 Date 构造；再叠加「任何一行改动就整表重算」，几百行时点一下
       * 置顶就要卡一下。
       */
      const keyed: { row: ChatSession; pin: number; upd: number }[] = []
      for (const s of sessions.value) {
        if (!!s.archived !== wantArchived) continue
        keyed.push({
          row: s,
          pin: s.pinned ? apiTimeMs(s.pinned_at) : 0,
          upd: apiTimeMs(s.updated_at),
        })
      }
      keyed.sort((a, b) => b.pin - a.pin || b.upd - a.upd)
      return keyed.map(e => e.row)
    })

    /** 批量勾选数（模板里用 .size 会让每次渲染都重算，这里给一个稳定派生值） */
    const batchCount = computed(() => batchIds.value.size)

    /** 某会话是否被勾选：Set 查询，行内每次渲染只问自己那一个 */
    function isBatchPicked(id: string): boolean {
      return batchIds.value.has(id)
    }

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
     *
     * 但这条守卫不能救「服务端故意不给」的会话：刚被归档/取消归档的那条，本地状态
     * 已经带 archived 标记，服务端这次没有它正是对的——再塞回列表就出现「归档了还在
     * 进行中显示」。只有既没归档、又确实还没被服务端收录的刚建会话才保留。
     */
    async function fetchSessions() {
      sessionsLoading.value = true
      try {
        const res = await chatApi.listSessions(sessionFilter.value === 'archived')
        const list = res.items ?? []
        const pending = sessions.value.find(s => s.id === activeId.value && !s.archived) ?? null
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
      // 用户已经改主意了：在途的建会话回来后不该抢走焦点
      focusToken += 1
      activeId.value = id
      messages.value = []
      attachments.value = []
      streamError.value = ''
      resetRoundView()
      await loadMessages()
      await loadParticipants()
      await clearUnread(id)
    }

    /**
     * 新建空会话并选中（标题留空，首条消息发送后自动生成）。
     * payload 供弹出对话框这类「锁定执行对象」的实例传 target/target_code。
     *
     * 建会话要一个来回，这段窗口里用户就可能按下回车发消息：creatingSession 把窗口
     * 的承诺挂出来，发消息前先 await 它，消息才落进刚开的会话而不是上一个。
     */
    async function newSession(payload: CreateSessionPayload = {}): Promise<ChatSession | undefined> {
      if (sending.value) return undefined
      const token = ++focusToken
      const task = (async () => {
        const session = await chatApi.createSession(payload)
        sessions.value.unshift(session)
        // 等待期间用户已经切到别的会话：不抢焦点，也不清空他正在看的消息
        if (token === focusToken) {
          activeId.value = session.id
          messages.value = []
          attachments.value = []
          streamError.value = ''
          resetRoundView()
          participants.value = []
        }
        return session
      })()
      // 给并发调用者一个永不 reject 的副本：建会话失败由 newSession 的调用方各自处理
      const pending = task.then(s => s, () => undefined)
      creatingSession = pending
      try {
        return await task
      } finally {
        if (creatingSession === pending) creatingSession = null
      }
    }

    async function renameSession(id: string, title: string) {
      const updated = await chatApi.renameSession(id, title)
      const target = sessions.value.find(s => s.id === id)
      if (target) target.title = updated?.title || title
    }

    async function removeSession(id: string) {
      await chatApi.deleteSession(id)
      sessions.value = sessions.value.filter(s => s.id !== id)
      batchIds.value.delete(id)
      if (activeId.value === id) {
        activeId.value = sessions.value[0]?.id ?? ''
        messages.value = []
        attachments.value = []
        resetRoundView()
        if (activeId.value) await loadMessages()
      }
    }

    /* ---------------- 会话列表管理（三点菜单 / 批量 / 分支，0043）---------------- */

    /**
     * 用服务端回包就地替换该行——排序、置顶、授权模式都以服务端为准，
     * 前端不自己猜（避免「显示 full 但实际被降级为 ask」的假状态）。
     */
    function mergeSessionRow(next: ChatSession) {
      const i = sessions.value.findIndex(s => s.id === next.id)
      if (i >= 0) sessions.value.splice(i, 1, next)
    }

    /** 切换进行中 / 已归档；正在聊的会话被切走时只清列表选择，不删任何东西 */
    async function setSessionFilter(next: 'active' | 'archived') {
      if (sessionFilter.value === next) return
      const prev = sessionFilter.value
      sessionFilter.value = next
      exitBatchMode()
      try {
        await fetchSessions()
      } catch (err) {
        /*
         * 拉取失败退回原来的视图并如实抛出：停在「筛选已经切到已归档、列表却还是
         * 进行中那批」的中间态最骗人——用户会以为自己的归档对话丢了。
         */
        sessionFilter.value = prev
        await fetchSessions().catch(() => undefined)
        throw err
      }
    }

    /**
     * 局部更新一个会话的标记位（置顶/归档/未读/授权模式/标题）。
     *
     * 只把服务端回包的那一行就地替换：排序与「归档就从这个视图消失」都交给
     * ``visibleSessions`` 这个视图模型本地完成，不为此回读整张列表——会话多时
     * 一次回读要为每行统计 Token，点一下「置顶」卡半秒不值当。
     *
     * 置顶/归档/未读/标题这四项服务端不会拒绝也不会降级，先落到本地行、请求在
     * 后台走，界面立刻动；失败就整体还原，绝不留下一个没发生过的状态。
     * ``approval_mode`` 特意不参与抢跑——它可能被服务端按角色降级，显示的必须是回包里的生效值。
     */
    async function patchSession(id: string, patch: SessionPatch) {
      const row = sessions.value.find(s => s.id === id) ?? null
      const optimistic: Partial<ChatSession> = {}
      if (patch.title !== undefined) optimistic.title = patch.title
      if (patch.pinned !== undefined) optimistic.pinned = patch.pinned
      if (patch.archived !== undefined) optimistic.archived = patch.archived
      if (patch.unread !== undefined) optimistic.unread = patch.unread
      const snapshot = row && Object.keys(optimistic).length ? { ...row } : null
      if (row && snapshot) Object.assign(row, optimistic)
      try {
        const next = await chatApi.updateSession(id, patch)
        if (next) mergeSessionRow(next)
        return next
      } catch (err) {
        if (row && snapshot) Object.assign(row, snapshot)
        throw err
      }
    }

    /** 打开会话即清除未读（会话私有，不引入已读回执表；失败不打扰用户） */
    async function clearUnread(id: string) {
      const row = sessions.value.find(s => s.id === id)
      if (!row?.unread) return
      try {
        await patchSession(id, { unread: false })
      } catch {
        /* 未读标记没清掉不影响使用，下次进列表自然纠正 */
      }
    }

    function toggleBatchPick(id: string) {
      const picked = batchIds.value
      if (picked.has(id)) picked.delete(id)
      else picked.add(id)
    }

    /** 全选/清空：整体换一个 Set，比逐个 add 少触发几百次依赖更新 */
    function setBatchPicks(ids: string[]) {
      batchIds.value = new Set(ids)
    }

    function exitBatchMode() {
      batchMode.value = false
      batchIds.value = new Set()
    }

    /** 批量动作：本地先落效果，再回读列表（服务端会重新按置顶/更新时间排序） */
    async function runBatch(action: SessionBatchAction) {
      const ids = [...batchIds.value]
      if (!ids.length) return null
      const res = await chatApi.batchSessions(ids, action)
      const affected = res?.affected ?? []
      // 先把标记位落到本地行，再回读列表：否则「归档当前会话」这一条会被
      // fetchSessions 的刚建会话守卫原样塞回来（服务端已经不给了，本地却说没归档）。
      // 索引一次建好：批量 200 条各自做一次整表 find 就是几万次比较。
      const byId = new Map(sessions.value.map(s => [s.id, s]))
      const flag = (write: (s: ChatSession) => void) => {
        for (const id of affected) {
          const row = byId.get(id)
          if (row) write(row)
        }
      }
      if (action === 'archive') flag(s => (s.archived = true))
      if (action === 'unarchive') flag(s => (s.archived = false))
      if (action === 'pin') flag(s => (s.pinned = true))
      if (action === 'unpin') flag(s => (s.pinned = false))
      if (action === 'read') flag(s => (s.unread = false))
      if (action === 'unread') flag(s => (s.unread = true))
      if (action === 'delete' && affected.length) {
        // 删掉的行当场消失，不等回读：否则要点两次才看得出少了
        const gone = new Set(affected)
        sessions.value = sessions.value.filter(s => !gone.has(s.id))
        if (gone.has(activeId.value)) {
          activeId.value = ''
          messages.value = []
          attachments.value = []
        }
      }
      exitBatchMode()
      await fetchSessions()
      if (!activeId.value && sessions.value.length) {
        await selectSession(sessions.value[0].id)
      }
      return res
    }

    /**
     * 分支：从这条回答拉出一个全新会话并立刻切过去。
     * 原会话不受影响；服务端负责复制历史、附件与群聊名单。
     */
    async function forkFromMessage(messageId: string): Promise<ChatSession | null> {
      if (!activeId.value || !messageId) return null
      const branch = await chatApi.forkSession(activeId.value, messageId)
      if (!branch) return null
      if (sessionFilter.value === 'active') sessions.value.unshift(branch)
      activeId.value = branch.id
      messages.value = []
      attachments.value = []
      streamError.value = ''
      resetRoundView()
      await loadMessages()
      await loadParticipants()
      return branch
    }

    /** 设置本会话的高风险工具授权模式（服务端按角色校验，返回的是生效值） */
    async function setApprovalMode(mode: ChatApprovalMode) {
      if (!activeId.value) return null
      return patchSession(activeId.value, { approval_mode: mode })
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
      if (creatingSession) await creatingSession
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

    /** 识别图片（用户手动触发，明确计费）；失败向上抛出由调用方提示 */
    async function recognizeAttachment(id: string): Promise<string> {
      const att = attachments.value.find(a => a.id === id)
      if (!att) return ''
      const res = await chatApi.recognizeImage(att.session_id, id)
      attachments.value = attachments.value.map(a => (a.id === id ? res.attachment : a))
      return res.text
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

    /**
     * 知识问答模式（v3.13）：单轮检索增强回答，不路由助手/班组。
     * 与通用对话共用消息表：刷新历史后问答记录仍在，引用可回看。
     */
    async function sendQa(content: string, topK = 5): Promise<boolean> {
      const question = content.trim()
      if (!question || sending.value) return false
      if (creatingSession) {
        const created = await creatingSession
        if (!created) return false
      }
      if (!activeId.value) {
        try {
          await newSession()
        } catch {
          return false
        }
      }
      const sid = activeId.value
      const localId = `local-qa-${Date.now()}`
      sending.value = true
      liveSteps.value = []
      streamError.value = ''
      resetRoundView()
      messages.value.push({
        id: localId,
        role: 'user',
        content: question,
        created_at: new Date().toISOString(),
      })
      try {
        const res = await chatApi.askKnowledge(sid, question, topK)
        const i = messages.value.findIndex(m => m.id === localId)
        if (i >= 0) messages.value.splice(i, 1, res.user_message)
        else messages.value.push(res.user_message)
        messages.value.push(res.assistant_message)
        await fetchSessions().catch(() => undefined)
        return true
      } catch (err) {
        messages.value = messages.value.filter(m => m.id !== localId)
        toastError(err)
        return false
      } finally {
        sending.value = false
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
        const seq = roundSeq

        const finish = (outcome: StreamOutcome) => {
          if (settled) return
          settled = true
          roundDone.value = true
          // 本轮已收尾：立刻收起实时步骤条，不等会话列表刷新等收尾整理
          if (seq === roundSeq) {
            sending.value = false
            liveSteps.value = []
          }
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
      // 点「新对话」到会话建好之间有个窗口：这段时间发出的消息必须等会话建好再发，
      // 否则会落到上一个会话——用户看着像「刚开的对话没接上消息」
      if (creatingSession) {
        const created = await creatingSession
        // 建会话失败：消息没有归属，宁可这次不发（草稿留在输入框），也不丢进旧会话
        if (!created) return false
      }
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
      const seq = ++roundSeq
      sending.value = true
      liveSteps.value = []
      streamError.value = ''
      resetRoundView()
      const local: ChatMessage = {
        id: `local-${++localMsgSeq}`,
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
        // 已有新一轮在跑时，上一轮的收尾不得覆盖新一轮的发送态
        if (seq === roundSeq) {
          sending.value = false
          liveSteps.value = []
        }
      }
    }

    /* ---------------- 技能卡片（v3.2 一句话技能）：状态按实例 + 消息 id 隔离 ---------------- */

    function emptySkillCardOp(): SkillCardOpState {
      return { installing: '', submitting: false, retrying: false, error: '' }
    }

    /** 读取某条技能卡片的按钮状态（缺失时返回只读的空状态） */
    function skillCardOp(messageId: string): SkillCardOpState {
      return skillCardOps.value[messageId] ?? emptySkillCardOp()
    }

    function patchSkillCardOp(messageId: string, patch: Partial<SkillCardOpState>) {
      skillCardOps.value[messageId] = { ...skillCardOp(messageId), ...patch }
    }

    /** 取消息上的技能卡片载荷（shell 侧只改本地副本，刷新后以服务端为准） */
    function skillCardPayload(messageId: string): ChatSkillCard | null {
      const msg = messages.value.find(m => m.id === messageId)
      return msg?.payload && msg.payload.type === 'skill_card' ? msg.payload : null
    }

    /** matched 卡片：安装某个候选（成功后按钮置灰「已安装」） */
    async function installSkillFromCard(
      messageId: string,
      match: ChatSkillCardMatch,
    ): Promise<boolean> {
      if (!match.item_key || skillCardOp(messageId).installing) return false
      patchSkillCardOp(messageId, { installing: match.item_key, error: '' })
      try {
        const res = await skillLabApi.install({ item_key: match.item_key })
        const card = skillCardPayload(messageId)
        const target = card?.matches?.find(m => m.item_key === match.item_key)
        if (target) target.installed = true
        const pending =
          res.needs_review === true ||
          res.review_status === 'pending_review' ||
          res.skill_status === 'pending_review'
        ElMessage.success(
          pending
            ? `「${match.name}」已安装，等待专家审核后可在「我的智能体」编辑里绑定`
            : `「${match.name}」已安装，可在「我的智能体」编辑里绑定`,
        )
        return true
      } catch (err) {
        patchSkillCardOp(messageId, { error: skillApiErrorText(err, '技能安装') })
        return false
      } finally {
        patchSkillCardOp(messageId, { installing: '' })
      }
    }

    /** created 卡片：提交小院公共库审核（私有 → pending_review） */
    async function submitSkillCard(messageId: string): Promise<boolean> {
      const card = skillCardPayload(messageId)
      const skill = card?.skill
      if (!skill || skillCardOp(messageId).submitting) return false
      patchSkillCardOp(messageId, { submitting: true, error: '' })
      try {
        const updated = await skillApi.review(skill.id, 'submit_public', undefined, {
          skipErrorToast: true,
        })
        const target = skillCardPayload(messageId)
        if (target?.skill) target.skill.status = updated?.status || 'pending_review'
        ElMessage.success('已提交审核，负责人通过后会转为小院公共')
        return true
      } catch (err) {
        patchSkillCardOp(messageId, { error: skillApiErrorText(err, '提交审核') })
        return false
      } finally {
        patchSkillCardOp(messageId, { submitting: false })
      }
    }

    /** failed 卡片：按原话重试生成，成功则把卡片原地替换为 created */
    async function retrySkillCard(messageId: string): Promise<boolean> {
      const card = skillCardPayload(messageId)
      const utterance = card?.utterance?.trim()
      if (!utterance || skillCardOp(messageId).retrying) return false
      patchSkillCardOp(messageId, { retrying: true, error: '' })
      try {
        const res = await skillLabApi.generate(utterance, true)
        const msg = messages.value.find(m => m.id === messageId)
        if (msg) {
          msg.payload = {
            type: 'skill_card',
            state: 'created',
            utterance,
            skill: res.skill,
            matches: [],
            sources: res.sources ?? [],
            note: res.note ?? '',
          }
        }
        ElMessage.success('已重新生成技能草稿')
        return true
      } catch (err) {
        patchSkillCardOp(messageId, { error: skillApiErrorText(err, '技能生成') })
        return false
      } finally {
        patchSkillCardOp(messageId, { retrying: false })
      }
    }

    /* ---------------- 插件卡片（v3.3 一句话插件）：状态按实例 + 消息 id 隔离 ---------------- */

    function emptyPluginCardOp(): PluginCardOpState {
      return { retrying: false, error: '' }
    }

    /** 读取某条插件卡片的按钮状态（缺失时返回只读的空状态） */
    function pluginCardOp(messageId: string): PluginCardOpState {
      return pluginCardOps.value[messageId] ?? emptyPluginCardOp()
    }

    function patchPluginCardOp(messageId: string, patch: Partial<PluginCardOpState>) {
      pluginCardOps.value[messageId] = { ...pluginCardOp(messageId), ...patch }
    }

    /** 取消息上的插件卡片载荷（shell 侧只改本地副本，刷新后以服务端为准） */
    function pluginCardPayload(messageId: string): ChatPluginCard | null {
      const msg = messages.value.find(m => m.id === messageId)
      return msg?.payload && msg.payload.type === 'plugin_card' ? msg.payload : null
    }

    /** failed 插件卡片：按原话重试生成，成功则把卡片原地替换为 created（仍待审） */
    async function retryPluginCard(messageId: string): Promise<boolean> {
      const card = pluginCardPayload(messageId)
      const utterance = card?.utterance?.trim()
      if (!utterance || pluginCardOp(messageId).retrying) return false
      patchPluginCardOp(messageId, { retrying: true, error: '' })
      try {
        const res = await skillLabApi.generate(utterance, true, 'plugin')
        const msg = messages.value.find(m => m.id === messageId)
        if (msg) {
          msg.payload = {
            type: 'plugin_card',
            state: 'created',
            utterance,
            plugin: res.plugin,
            manifest: res.manifest as unknown as ChatPluginCard['manifest'],
            sources: res.sources ?? [],
            note: res.note ?? '',
          }
        }
        ElMessage.success('已重新生成插件草稿')
        return true
      } catch (err) {
        patchPluginCardOp(messageId, { error: skillApiErrorText(err, '插件生成') })
        return false
      } finally {
        patchPluginCardOp(messageId, { retrying: false })
      }
    }

    return {
      scope: instanceScope,
      sessions,
      visibleSessions,
      sessionFilter,
      batchMode,
      batchCount,
      isBatchPicked,
      setBatchPicks,
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
      /* 会话列表管理（0043）：筛选 / 三点菜单 / 批量 / 分支 / 授权模式 */
      setSessionFilter,
      patchSession,
      clearUnread,
      toggleBatchPick,
      exitBatchMode,
      runBatch,
      forkFromMessage,
      setApprovalMode,
      uploadAttachment,
      recognizeAttachment,
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
      sendQa,
      sendViaStream,
      /* 技能卡片：状态与动作都在实例内，多面板互不串台 */
      skillCardOp,
      installSkillFromCard,
      submitSkillCard,
      retrySkillCard,
      /* 插件卡片（v3.3）：同口径按实例隔离 */
      pluginCardOp,
      retryPluginCard,
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
