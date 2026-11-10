<template>
  <div class="chat">
    <!-- 左：会话列表（260px，玻璃列表项 + 顶部渐变「新对话」钮） -->
    <aside class="chat-sessions">
      <el-button
        class="new-btn"
        type="primary"
        :disabled="store.sending"
        @click="onNew"
      >
        <el-icon class="new-icon">
          <Plus />
        </el-icon>
        新对话
      </el-button>
      <!-- 顶部筛选（进行中/已归档）+ 批量开关：两个视图互斥，置顶恒在最前 -->
      <div class="s-filter">
        <el-segmented
          v-model="sessionFilter"
          class="s-filter-seg"
          size="small"
          :options="SESSION_FILTER_OPTIONS"
          aria-label="会话列表筛选"
          @change="onFilterChange"
        />
        <el-button
          class="s-batch-btn"
          :class="{ 'is-on': store.batchMode }"
          size="small"
          round
          :type="store.batchMode ? 'primary' : 'default'"
          :aria-pressed="store.batchMode"
          @click="toggleBatchMode"
        >
          <el-icon class="s-batch-ic">
            <Select />
          </el-icon>
          {{ store.batchMode ? '退出' : '批量' }}
        </el-button>
      </div>
      <div
        ref="listRef"
        v-loading="store.sessionsLoading"
        class="session-list"
        :class="{ 'is-batch': store.batchMode }"
        :data-session-total="store.visibleSessions.length"
      >
        <!--
          每一行的重渲染收在「这一行自己变了」的范围内：几百行的列表里点一个勾选框，
          以前会把整表 300+ 行连同各自的三点菜单一起重 patch，主线程直接卡住半秒以上
          （这就是「慢半拍」）。memo 挂在行根节点上而不是行内小块上——挂在里面时
          每次点击仍要重走 60 个行根节点（含重新绑定每行的事件），实测反而慢一个量级。
          批量开关不进依赖：勾选框显隐由容器上的 .is-batch 用 CSS 控制，切换时零行重画。
        -->
        <div
          v-for="s in renderedSessions"
          :key="s.id"
          v-memo="rowMemo(s)"
          class="session-item"
          :data-session-id="s.id"
          :class="{ active: s.id === store.activeId, 'is-pinned': s.pinned, 'is-unread': s.unread }"
          @click="onSelect(s.id)"
        >
          <input
            class="s-check"
            type="checkbox"
            :checked="store.isBatchPicked(s.id)"
            :aria-label="`选择会话 ${s.title || UNTITLED}`"
            @click.stop
            @change="store.toggleBatchPick(s.id)"
          >
          <div class="s-title">
            <el-icon
              v-if="s.pinned"
              class="s-pin"
              title="已置顶"
            >
              <Position />
            </el-icon>
            <span
              class="s-kind"
              :class="sessionKind(s).tone"
            >{{ sessionKind(s).text }}</span>
            <span class="s-name">{{ s.title || UNTITLED }}</span>
            <span
              v-if="s.parent_session_id"
              class="s-branch"
              title="本会话由分支产生"
            >分支</span>
          </div>
          <div class="s-meta">
            <span class="s-time">{{ formatDateTime(s.created_at) }}</span>
            <span
              v-if="(s.total_tokens ?? 0) > 0"
              class="s-tokens"
              :title="`输入 ${s.input_tokens ?? 0} / 输出 ${s.output_tokens ?? 0} tokens`"
            >⚡ {{ formatCompact(s.total_tokens ?? 0) }}</span>
            <!-- 三点菜单：将原先只有一个删除键的位置升级为会话操作集 -->
            <el-dropdown
              trigger="click"
              placement="bottom-end"
              :persistent="false"
              :popper-class="`s-menu-popper s-menu-${s.id}`"
              @command="(cmd: SessionCommand) => onSessionCommand(cmd, s)"
            >
              <el-button
                class="s-more"
                link
                aria-label="会话设置"
                @click.stop
              >
                <el-icon><MoreFilled /></el-icon>
              </el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="pin">
                    {{ s.pinned ? '取消置顶' : '置顶' }}
                  </el-dropdown-item>
                  <el-dropdown-item command="rename">
                    重命名
                  </el-dropdown-item>
                  <el-dropdown-item command="unread">
                    {{ s.unread ? '标记已读' : '标记未读' }}
                  </el-dropdown-item>
                  <el-dropdown-item command="archive">
                    {{ s.archived ? '移回进行中' : '归档' }}
                  </el-dropdown-item>
                  <el-dropdown-item command="window">
                    新窗口打开
                  </el-dropdown-item>
                  <el-dropdown-item
                    command="delete"
                    divided
                  >
                    删除
                  </el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
        </div>
        <!--
          窗口化哨兵：列表只渲染前若干行，滚到底再放下一页。
          一行带一个三点菜单，几百行一次性挂载等于凭空造几千个组件实例。
        -->
        <div
          v-if="hasMoreRows"
          ref="sentinelRef"
          class="s-sentinel"
        >
          继续滚动查看更早的 {{ store.visibleSessions.length - renderedSessions.length }} 条对话
        </div>
        <p
          v-if="!store.visibleSessions.length && !store.sessionsLoading"
          class="s-empty"
        >
          {{ sessionFilter === 'archived' ? '还没有已归档的对话' : '还没有对话，点上方「新对话」开始' }}
        </p>
      </div>
      <!-- 批量条：只对勾选到的会话生效。两行布局，动作按钮等宽铺满，不再挤出侧栏 -->
      <footer
        v-if="store.batchMode"
        class="s-batchbar"
      >
        <div class="s-batch-top">
          <el-checkbox
            class="s-batch-all"
            :model-value="allChecked"
            :indeterminate="store.batchCount > 0 && !allChecked"
            @change="toggleCheckAll"
          >
            全选
          </el-checkbox>
          <span class="s-batch-count">已选 {{ store.batchCount }}</span>
        </div>
        <div class="s-batch-acts">
          <button
            class="s-batch-act"
            type="button"
            :disabled="!store.batchCount"
            @click="onBatch('archive')"
          >
            归档
          </button>
          <button
            class="s-batch-act"
            type="button"
            :disabled="!store.batchCount"
            @click="onBatch('unarchive')"
          >
            移回
          </button>
          <button
            class="s-batch-act"
            type="button"
            :disabled="!store.batchCount"
            @click="onBatch('pin')"
          >
            置顶
          </button>
          <button
            class="s-batch-act s-batch-del"
            type="button"
            :disabled="!store.batchCount"
            @click="onBatchDelete"
          >
            删除
          </button>
        </div>
      </footer>
    </aside>

    <!-- 右：主对话区（顶栏 + 面板实例） -->
    <section class="chat-main">
      <header class="chat-top">
        <div class="t-left">
          <span class="t-title">{{ store.activeId ? store.activeTitle : UNTITLED }}</span>
          <el-button
            v-if="store.activeId"
            class="t-edit"
            link
            title="重命名"
            aria-label="重命名当前会话"
            @click="onRename"
          >
            <el-icon><EditPen /></el-icon>
          </el-button>
        </div>
        <div class="t-right">
          <el-tooltip
            v-if="activeSessionTokens.total > 0"
            :content="`当前会话累计消耗：输入 ${activeSessionTokens.input.toLocaleString()} tokens + 输出 ${activeSessionTokens.output.toLocaleString()} tokens`"
            placement="bottom"
          >
            <span class="t-token-pill">
              ⚡ 本会话 {{ activeSessionTokens.total.toLocaleString() }} tokens
              <span class="t-token-sub">（入 {{ activeSessionTokens.input }} / 出 {{ activeSessionTokens.output }}）</span>
            </span>
          </el-tooltip>

          <!-- 仅 DEV 可见的 E2E 脚手架开关：用于并排挂出第二个面板实例 -->
          <el-segmented
            v-if="harnessAvailable"
            v-model="harnessMode"
            class="t-harness"
            size="small"
            :options="HARNESS_OPTIONS"
            aria-label="并行面板脚手架（仅开发环境）"
          />
          <el-tooltip
            content="按问题内容自动挑选专业助手或协作班组：规则命中 → 智能分类 → 频率兜底，每条回答都会标注实际来源与 Token 消耗。"
            placement="bottom"
          >
            <span class="t-route">模型路由：自动</span>
          </el-tooltip>
        </div>
      </header>

      <!-- 消息流 + 输入区交给面板组件，状态来自 chat-main 这一个 store 实例 -->
      <ChatPanel
        ref="mainPanelRef"
        v-model:qa-mode="qaMode"
        :store="store"
        panel-id="main"
        label="对话"
        mentionable
        groupable
        qa-toggle
        commander-toggle
        :commander-enabled="commanderEnabled"
        :default-execution-mode="commanderDefaultMode"
        :default-preview-plan="commanderPreviewPlan"
      />
    </section>

    <div
      v-if="harnessOpen"
      class="harness-pop"
      role="dialog"
      aria-modal="false"
      :aria-label="HARNESS_LABEL"
    >
      <header class="harness-top">
        <div class="harness-head">
          <span class="harness-title">{{ HARNESS_LABEL }}</span>
          <span
            class="harness-session"
            data-harness-session
          >{{ popupStore.activeId ? popupStore.activeTitle : '未开始会话' }}</span>
        </div>
        <button
          class="harness-close"
          type="button"
          :aria-label="`关闭${HARNESS_LABEL}`"
          @click="harnessMode = 'single'"
        >
          <el-icon><Close /></el-icon>
        </button>
      </header>
      <ChatPanel
        :store="popupStore"
        :panel-id="HARNESS_PANEL_ID"
        :label="HARNESS_LABEL"
        :suggestions="HARNESS_SUGGESTIONS"
        placeholder="测试脚手架：向弹出实例提问，Enter 发送"
        max-width="100%"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
  import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
  import { useRoute } from 'vue-router'
  import { ElMessageBox } from 'element-plus'
  import {
    Close,
    EditPen,
    MoreFilled,
    Plus,
    Position,
    Select,
  } from '@element-plus/icons-vue'
  import ChatPanel from '@/components/chat/ChatPanel.vue'
  import { commanderApi, type ExecutionMode } from '@/api/commander'
  import { formatDateTime } from '@/utils/format'
  import type { ChatSession, SessionBatchAction } from '@/api/chat'
  import { UNTITLED, useChatStore } from '@/stores/chat'

  const HARNESS_SCOPE = 'popup-harness'
  const HARNESS_PANEL_ID = 'popup'
  const HARNESS_LABEL = '弹出对话（测试脚手架）'
  const HARNESS_SUGGESTIONS = ['这个助手能帮我做什么？', '总结一下上次拜访的结论']
  const HARNESS_OPTIONS = [
    { label: '单面板', value: 'single' },
    { label: '双面板（测试）', value: 'dual' },
  ]

  const route = useRoute()
  const store = useChatStore()
  /** 知识问答模式（v3.13）：/qa 重定向与站内切换共用同一开关 */
  const qaMode = ref(route.query.qa === '1')
  /** 指挥官（v3.38）：总开关与默认模式来自设置页；读取失败时按可用/普通模式兜底 */
  const commanderEnabled = ref(true)
  const commanderDefaultMode = ref<ExecutionMode>('standard')
  /** 计划默认先预览：后端 commander-plan 帧未带 preview 时用它兜底 */
  const commanderPreviewPlan = ref(true)

  async function loadCommanderSettings(): Promise<void> {
    try {
      const settings = await commanderApi.getSettings()
      commanderEnabled.value = settings.enabled
      commanderDefaultMode.value = settings.default_mode ? 'commander' : 'standard'
      commanderPreviewPlan.value = settings.preview_plan
    } catch {
      /* 设置读取失败不阻塞对话：保留默认可用与普通模式 */
    }
  }
  const popupStore = useChatStore(HARNESS_SCOPE)
  const mainPanelRef = ref<InstanceType<typeof ChatPanel> | null>(null)

  const harnessAvailable = import.meta.env.DEV
  const harnessMode = ref<string | number | boolean>(
    route.query['panel-harness'] === '1' ? 'dual' : 'single',
  )
  const harnessOpen = computed(() => harnessAvailable && harnessMode.value === 'dual')

  /* ---------------- 会话列表渲染窗口（长列表不再整表挂载） ---------------- */

  /**
   * 一次渲染多少行。
   *
   * 每行自带一个三点菜单（el-dropdown + 菜单项 + 弹出层），整表挂载等于凭空造几千个
   * 组件实例：实测 300+ 会话时点一下「批量」要卡 1.5 秒以上。首屏只挂一页，
   * 滚到底再由哨兵放开下一页；列表容器上的 data-session-total 始终是模型里的真实条数，
   * 不随窗口大小变化（测试与「共 N 条」这类断言都读它）。
   */
  const LIST_PAGE = 60

  const renderLimit = ref(LIST_PAGE)
  const listRef = ref<HTMLElement | null>(null)
  const sentinelRef = ref<HTMLElement | null>(null)

  const renderedSessions = computed(() => store.visibleSessions.slice(0, renderLimit.value))
  const hasMoreRows = computed(() => store.visibleSessions.length > renderedSessions.value.length)

  /**
   * 行级 v-memo 的依赖：只列行内这两块（标题栏 + 元信息栏）真正渲染读到的字段。
   *
   * 刻意不含 store.batchMode——勾选框显隐由容器上的 .is-batch 用 CSS 管，
   * 切一次批量不必重画任何一行；选中态与当前会话则必须进来（它们就画在这一行上）。
   * 更不含会话对象的标识——本地抢跑是直接改对象上的字段，字段本身就在依赖里，
   * 比「换一个新对象才重画」更保险。
   */
  function rowMemo(s: ChatSession): unknown[] {
    return [
      s.id,
      s.title,
      s.pinned,
      s.archived,
      s.unread,
      s.approval_mode,
      s.created_at,
      s.updated_at,
      s.pinned_at,
      s.parent_session_id,
      s.target,
      s.target_name,
      s.participant_count,
      s.execution_mode,
      s.input_tokens,
      s.output_tokens,
      s.total_tokens,
      store.activeId === s.id,
      store.isBatchPicked(s.id),
    ]
  }

  let rowObserver: IntersectionObserver | null = null

  /**
   * 哨兵进入可视区 = 用户滚到当前窗口底部，放下一页。
   * 哨兵是 v-if 渲染的，会随窗口增减反复挂载卸载，所以每次拿到新元素都要重挂观察器。
   */
  function attachRowObserver(el: HTMLElement | null) {
    rowObserver?.disconnect()
    rowObserver = null
    if (!el || !listRef.value || typeof IntersectionObserver === 'undefined') return
    rowObserver = new IntersectionObserver(
      entries => {
        if (entries.some(e => e.isIntersecting)) renderLimit.value += LIST_PAGE
      },
      { root: listRef.value, rootMargin: '120px' },
    )
    rowObserver.observe(el)
  }

  watch(sentinelRef, attachRowObserver)
  onBeforeUnmount(() => rowObserver?.disconnect())

  // 换视图（进行中 / 已归档）回到第一页：两个视图的行完全不同，沿用旧窗口没有意义
  watch(
    () => store.sessionFilter,
    () => {
      renderLimit.value = LIST_PAGE
    },
  )

  /** 当前会话实时累计 Token（优先汇总当前已加载消息的精确 token_usage，兜底用 session 行字段） */
  const activeSessionTokens = computed(() => {
    let input = 0
    let output = 0
    let total = 0
    for (const m of store.messages) {
      if (m.token_usage) {
        input += m.token_usage.input_tokens ?? 0
        output += m.token_usage.output_tokens ?? 0
        total += m.token_usage.total_tokens ?? 0
      }
    }
    if (total === 0 && store.activeSession) {
      input = store.activeSession.input_tokens ?? 0
      output = store.activeSession.output_tokens ?? 0
      total = store.activeSession.total_tokens ?? input + output
    }
    return { input, output, total }
  })

  function formatCompact(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
    return String(n)
  }

  /** 会话类型徽章：群聊 > 锁定助手 > 班组 > 指挥官 > 普通对话（后端下发事实，不做本地猜测） */
  // 色调类名必须带前缀，避免与页面根容器 `.chat` 等通用类名冲突
  function sessionKind(
    s: ChatSession,
  ): {
    text: string
    tone: 's-kind--group' | 's-kind--agent' | 's-kind--team' | 's-kind--commander' | 's-kind--chat'
  } {
    if ((s.participant_count ?? 0) > 0)
      return { text: `群聊 ${s.participant_count}`, tone: 's-kind--group' }
    if (s.target === 'agent') return { text: s.target_name || '智能体', tone: 's-kind--agent' }
    if (s.target === 'team') return { text: '班组', tone: 's-kind--team' }
    if (s.execution_mode === 'commander') return { text: '指挥官', tone: 's-kind--commander' }
    return { text: '对话', tone: 's-kind--chat' }
  }

  /**
   * 三点菜单的会话操作（0043）。
   * 状态一律取服务端回包：归档/置顶/未读/授权模式都可能是「请求了但被降级或忽略」，
   * 界面显示生效值而不是请求值，避免出现假的权限状态。
   */
  type SessionCommand = 'pin' | 'rename' | 'unread' | 'archive' | 'window' | 'delete'

  const SESSION_FILTER_OPTIONS = [
    { label: '进行中', value: 'active' },
    { label: '已归档', value: 'archived' },
  ]

  /** 与 store 里的筛选保持初值一致（弹出面板共用同一个 store 实例时不串台） */
  const sessionFilter = ref<'active' | 'archived'>(store.sessionFilter)

  /**
   * 全选只看当前视图里真正属于这个筛选的那批（已归档视图不会误选进行中的会话）。
   * 勾选判断走 Set，一次比较是 O(1)，不再是「每行 × 已选项」的数组扫描。
   */
  const allChecked = computed(
    () =>
      store.visibleSessions.length > 0 &&
      store.visibleSessions.every(s => store.isBatchPicked(s.id)),
  )

  async function onFilterChange(value: 'active' | 'archived') {
    await store.setSessionFilter(value).catch(() => undefined)
  }

  function toggleBatchMode() {
    if (store.batchMode) {
      store.exitBatchMode()
      return
    }
    store.batchMode = true
  }

  function toggleCheckAll() {
    store.setBatchPicks(allChecked.value ? [] : store.visibleSessions.map(s => s.id))
  }

  async function onBatch(action: SessionBatchAction) {
    try {
      await store.runBatch(action)
    } catch {
      /* toast handled */
    }
  }

  async function onBatchDelete() {
    const n = store.batchCount
    if (!n) return
    try {
      await ElMessageBox.confirm(
        `删除选中的 ${n} 个对话？它们的消息与附件会一并删除，不可恢复。`,
        '批量删除',
        { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return
    }
    await onBatch('delete')
  }

  async function renameOne(s: ChatSession) {
    try {
      const { value } = await ElMessageBox.prompt('会话标题', '重命名', {
        inputValue: s.title || UNTITLED,
        inputValidator: (v: string) => (v && v.trim() ? true : '标题不能为空'),
        confirmButtonText: '保存',
        cancelButtonText: '取消',
      })
      await store.patchSession(s.id, { title: value.trim() })
    } catch {
      /* cancelled */
    }
  }

  async function deleteOne(s: ChatSession) {
    try {
      await ElMessageBox.confirm(
        `删除「${s.title || UNTITLED}」？消息与附件会一并删除，不可恢复。`,
        '删除对话',
        { confirmButtonText: '删除', cancelButtonText: '取消', type: 'warning' },
      )
    } catch {
      return
    }
    try {
      await store.removeSession(s.id)
    } catch {
      /* toast handled */
    }
  }

  /** 新窗口打开：沿用既有 ?session_id 深链，第二个窗口自己会加载该会话 */
  function openInWindow(id: string) {
    window.open(`/chat?session_id=${encodeURIComponent(id)}`, '_blank', 'noopener')
  }

  async function onSessionCommand(cmd: SessionCommand, s: ChatSession) {
    if (cmd === 'rename') {
      await renameOne(s)
      return
    }
    if (cmd === 'delete') {
      await deleteOne(s)
      return
    }
    if (cmd === 'window') {
      openInWindow(s.id)
      return
    }
    try {
      if (cmd === 'pin') await store.patchSession(s.id, { pinned: !s.pinned })
      if (cmd === 'unread') await store.patchSession(s.id, { unread: !s.unread })
      if (cmd === 'archive') {
        await store.patchSession(s.id, { archived: !s.archived })
        // 归档的正是当前会话时切到「已归档」视图，用户不会看见它凭空消失
        if (!s.archived && s.id === store.activeId) {
          sessionFilter.value = 'archived'
          await store.setSessionFilter('archived')
        }
      }
    } catch {
      /* toast handled */
    }
  }

  async function onNew() {
    try {
      await store.newSession()
    } catch {
      /* toast handled */
    }
  }

  async function onSelect(id: string) {
    try {
      await store.selectSession(id)
      mainPanelRef.value?.scrollToBottom()
    } catch {
      /* toast handled */
    }
  }

  async function onRename() {
    if (!store.activeId) return
    try {
      const { value } = await ElMessageBox.prompt('会话标题', '重命名', {
        inputValue: store.activeTitle,
        inputValidator: (v: string) => (v && v.trim() ? true : '标题不能为空'),
        confirmButtonText: '保存',
        cancelButtonText: '取消',
      })
      await store.renameSession(store.activeId, value.trim())
    } catch {
      /* cancelled */
    }
  }

  /** 筛选初值/外部改动（批量删除后 store 可能自己切视图）都同步回分段控件 */
  watch(
    () => store.sessionFilter,
    v => {
      sessionFilter.value = v
    },
  )

  watch(
    () => route.query.session_id,
    async sid => {
      if (typeof sid === 'string' && sid && sid !== store.activeId) {
        await store.selectSession(sid).catch(() => undefined)
        mainPanelRef.value?.scrollToBottom()
      }
    },
  )

  onMounted(async () => {
    void loadCommanderSettings()
    await store.fetchSessions().catch(() => undefined)
    const targetSid = route.query.session_id
    if (typeof targetSid === 'string' && targetSid) {
      await store.selectSession(targetSid).catch(() => undefined)
    }
    // 旧问答页带来的问题：只预填不自动发送（与旧 /qa 行为一致）
    const preset = route.query.q
    if (typeof preset === 'string' && preset.trim()) {
      mainPanelRef.value?.fillDraft(preset.trim())
    }
    mainPanelRef.value?.scrollToBottom()
  })
</script>

<style scoped>
  .chat {
    display: flex;
    gap: 16px;
    height: calc(100vh - 104px);
    min-height: 520px;
  }

  .chat-sessions {
    flex: none;
    width: 260px;
    display: flex;
    flex-direction: column;
    padding: 14px;
    background: var(--ds-glass);
    backdrop-filter: blur(18px) saturate(1.2);
    border: 1px solid var(--ds-border);
    border-radius: 14px;
  }

  .new-btn {
    width: 100%;
    height: 34px;
    border: none;
    border-radius: 10px;
    background: var(--ds-grad-brand);
    color: #fff;
  }
  .new-btn:hover {
    filter: brightness(1.1);
  }
  .new-icon {
    margin-right: 4px;
  }

  .session-list {
    flex: 1;
    overflow-y: auto;
    margin-top: 12px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .session-item {
    position: relative;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid transparent;
    cursor: pointer;
    transition:
      border-color 0.2s,
      background 0.2s;
  }
  .session-item:hover {
    background: var(--ds-glass-strong);
  }
  .session-item.active {
    background: var(--ds-glass-strong);
    border-color: var(--ds-border);
  }

  .s-title {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--ds-text-1);
  }
  .s-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* 会话类型徽章：一眼分清 群聊 / 某个智能体 / 班组 / 普通对话 */
  .s-kind {
    flex: none;
    max-width: 96px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    padding: 0 6px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
    font-size: 11px;
    line-height: 18px;
    color: var(--ds-text-2);
    background: var(--ds-field);
  }
  .s-kind--group {
    color: var(--ds-warning);
    border-color: color-mix(in srgb, var(--ds-warning) 45%, transparent);
    background: color-mix(in srgb, var(--ds-warning) 12%, transparent);
  }
  .s-kind--agent {
    color: var(--ds-accent-text);
    border-color: color-mix(in srgb, var(--ds-accent) 45%, transparent);
    background: color-mix(in srgb, var(--ds-accent) 14%, transparent);
  }
  .s-kind--team {
    color: var(--ds-success);
    border-color: color-mix(in srgb, var(--ds-success) 40%, transparent);
    background: color-mix(in srgb, var(--ds-success) 12%, transparent);
  }
  /* 指挥官：与「某个智能体」的靛蓝区分开，用主题的青色调 */
  .s-kind--commander {
    color: var(--ds-accent-2);
    border-color: color-mix(in srgb, var(--ds-accent-2) 45%, transparent);
    background: color-mix(in srgb, var(--ds-accent-2) 12%, transparent);
  }
  .s-kind--chat {
    color: var(--ds-text-3);
  }

  .s-meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    margin-top: 2px;
  }

  .s-time {
    font-size: 12px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }

  .s-tokens {
    margin-left: auto;
    font-size: 11px;
    color: var(--ds-success);
    font-variant-numeric: tabular-nums;
  }

  /* 三点菜单：常驻低透明度，悬停/选中转亮（触屏没有 hover，不能靠显隐才点得到） */
  .s-more {
    opacity: 0.55;
    color: var(--ds-text-3);
    height: 20px;
    padding: 0;
    transition:
      opacity 0.2s,
      color 0.2s;
  }
  .session-item:hover .s-more,
  .session-item.active .s-more {
    opacity: 1;
  }
  .s-more:hover {
    color: var(--ds-text-1);
  }

  /* 顶部筛选 + 批量开关：做成一整条分段控件，避免两边高低不齐 */
  .s-filter {
    flex: none;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 0 0 8px;
  }
  .s-filter-seg {
    flex: 1;
    min-width: 0;
    --el-segmented-color: var(--ds-text-2);
    --el-segmented-item-selected-color: var(--ds-text-1);
    --el-fill-color-blank: transparent;
  }
  .s-filter-seg :deep(.el-segmented__group) {
    padding: 2px;
  }
  .s-filter-seg :deep(.el-segmented__item) {
    font-size: 12px;
    line-height: 20px;
  }
  .s-batch-btn {
    flex: none;
    height: 26px;
    padding: 0 11px;
    font-size: 12px;
    border-color: var(--ds-border-strong);
    background: var(--ds-glass);
    color: var(--ds-text-2);
    transition:
      color 0.18s,
      border-color 0.18s,
      background 0.18s;
  }
  .s-batch-btn:hover {
    color: var(--ds-text-1);
    border-color: color-mix(in srgb, var(--ds-accent) 55%, transparent);
  }
  .s-batch-btn.is-on {
    color: #fff;
    border-color: transparent;
    background: var(--ds-grad-brand);
  }
  .s-batch-ic {
    margin-right: 3px;
    font-size: 12px;
  }

  /*
   * 勾选框常驻 DOM，只在批量模式（容器带 .is-batch）下显示。
   * 用 v-if 的话切一次批量要给整表每一行挂载/卸载组件并重画；改 CSS 之后
   * 「进入批量」只是根容器换一个类，一行都不重渲染。
   * 定位挂在行左侧（绝对定位 + 批量时加左内边距），行内标题/元信息两块
   * 仍是纵向堆叠的块级元素，不必为了放勾选框改动它们的结构。
   */
  .session-list.is-batch .session-item {
    padding-left: 32px;
  }
  .s-check {
    display: none;
    position: absolute;
    left: 10px;
    top: 50%;
    transform: translateY(-50%);
    width: 16px;
    height: 16px;
    margin: 0;
    appearance: none;
    -webkit-appearance: none;
    border: 1px solid var(--ds-border-strong);
    border-radius: 5px;
    background: var(--ds-field);
    cursor: pointer;
    transition:
      border-color 0.16s,
      background-color 0.16s,
      box-shadow 0.16s;
  }
  .session-list.is-batch .s-check {
    display: block;
  }
  .s-check:hover {
    border-color: color-mix(in srgb, var(--ds-accent) 65%, transparent);
  }
  .s-check:checked {
    border-color: transparent;
    background-color: var(--ds-accent);
    background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Cpath d='M3.6 8.4 6.4 11.2 12.4 5.2' fill='none' stroke='%23fff' stroke-width='2.1' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
    background-position: center;
    background-repeat: no-repeat;
    background-size: 12px 12px;
  }
  .s-check:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--ds-accent) 28%, transparent);
  }
  /* 选中整行给一层描边，一眼看清勾了哪几条 */
  .session-list.is-batch .session-item:has(.s-check:checked) {
    border-color: color-mix(in srgb, var(--ds-accent) 42%, transparent);
    background: color-mix(in srgb, var(--ds-accent) 9%, transparent);
  }
  .s-sentinel {
    padding: 8px 12px;
    font-size: 12px;
    text-align: center;
    color: var(--ds-text-3);
  }
  .s-pin {
    flex: none;
    font-size: 12px;
    color: var(--ds-accent-text);
  }
  .s-branch {
    flex: none;
    padding: 0 5px;
    border: 1px dashed color-mix(in srgb, var(--ds-accent) 45%, transparent);
    border-radius: var(--ds-radius-pill);
    font-size: 10px;
    line-height: 16px;
    color: var(--ds-accent-text);
  }
  /* 未读：标题加粗 + 右侧圆点，列表一眼看出哪条没看过 */
  .session-item.is-unread .s-name {
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .session-item.is-unread .s-time::after {
    content: '';
    display: inline-block;
    width: 6px;
    height: 6px;
    margin-left: 6px;
    border-radius: 50%;
    background: var(--ds-accent);
    vertical-align: middle;
  }

  .s-empty {
    padding: 18px 10px;
    font-size: 12px;
    color: var(--ds-text-3);
    text-align: center;
  }

  /* 批量条：两行（计数行 + 动作行），动作按钮等宽铺满，侧栏窄也不会溢出 */
  .s-batchbar {
    flex: none;
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 9px 10px 10px;
    border-top: 1px solid var(--ds-border);
    background: var(--ds-glass-strong);
    border-radius: 0 0 12px 12px;
  }
  .s-batch-top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .s-batch-all {
    height: auto;
    font-size: 12px;
  }
  .s-batch-count {
    font-size: 12px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }
  .s-batch-acts {
    display: flex;
    align-items: stretch;
    gap: 6px;
  }
  .s-batch-act {
    flex: 1 1 0;
    min-width: 0;
    height: 28px;
    padding: 0 4px;
    border: 1px solid var(--ds-border-strong);
    border-radius: 8px;
    background: var(--ds-glass);
    color: var(--ds-text-2);
    font-size: 12px;
    white-space: nowrap;
    cursor: pointer;
    transition:
      color 0.16s,
      border-color 0.16s,
      background 0.16s;
  }
  .s-batch-act:hover:not([disabled]) {
    color: var(--ds-accent-text);
    border-color: color-mix(in srgb, var(--ds-accent) 55%, transparent);
    background: color-mix(in srgb, var(--ds-accent) 12%, transparent);
  }
  .s-batch-act:focus-visible {
    outline: none;
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--ds-accent) 26%, transparent);
  }
  .s-batch-act[disabled] {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .s-batch-del:hover:not([disabled]) {
    color: var(--ds-danger);
    border-color: color-mix(in srgb, var(--ds-danger) 55%, transparent);
    background: color-mix(in srgb, var(--ds-danger) 12%, transparent);
  }

  .chat-main {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: var(--ds-glass);
    backdrop-filter: blur(18px) saturate(1.2);
    border: 1px solid var(--ds-border);
    border-radius: 14px;
  }

  .chat-top {
    flex: none;
    height: 52px;
    padding: 0 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    border-bottom: 1px solid var(--ds-border);
  }

  .t-left {
    display: flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
  }

  .t-title {
    font-size: 15px;
    font-weight: 600;
    color: var(--ds-text-1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .t-edit {
    color: var(--ds-text-3);
  }
  .t-edit:hover {
    color: var(--ds-accent);
  }

  .t-right {
    flex: none;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .t-token-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 12px;
    color: var(--ds-success);
    background: rgba(16, 185, 129, 0.14);
    border: 1px solid rgba(16, 185, 129, 0.32);
    font-variant-numeric: tabular-nums;
  }
  .t-token-sub {
    color: var(--ds-text-3);
    font-size: 11px;
  }

  .t-route {
    font-size: 12px;
    color: var(--ds-text-3);
    cursor: default;
  }

  .t-harness {
    flex: none;
  }

  .harness-pop {
    position: fixed;
    top: 84px;
    right: 24px;
    z-index: 1900;
    width: 420px;
    height: min(560px, calc(100vh - 140px));
    display: flex;
    flex-direction: column;
    overflow: hidden;
    background: var(--ds-bg-1);
    backdrop-filter: blur(18px) saturate(1.2);
    border: 1px solid var(--ds-border-strong);
    border-radius: var(--ds-radius-card);
    box-shadow: var(--ds-shadow);
  }

  .harness-top {
    flex: none;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--ds-border);
  }

  .harness-head {
    display: flex;
    flex-direction: column;
    min-width: 0;
    gap: 2px;
  }

  .harness-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .harness-session {
    font-size: 11.5px;
    color: var(--ds-text-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .harness-close {
    flex: none;
    width: 24px;
    height: 24px;
    padding: 0;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
    background: transparent;
    color: var(--ds-text-3);
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition:
      color 0.2s,
      border-color 0.2s;
  }
  .harness-close:hover {
    color: var(--ds-danger);
    border-color: var(--ds-border-strong);
  }

  .session-list::-webkit-scrollbar {
    width: 8px;
  }
  .session-list::-webkit-scrollbar-thumb {
    background: var(--ds-border-strong);
    border-radius: 999px;
  }
  .session-list::-webkit-scrollbar-track {
    background: transparent;
  }

  @media (max-width: 900px) {
    .chat-sessions {
      width: 200px;
    }
  }
</style>
