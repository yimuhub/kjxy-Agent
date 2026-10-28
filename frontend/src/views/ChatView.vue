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
      <div
        v-loading="store.sessionsLoading"
        class="session-list"
      >
        <div
          v-for="s in store.sessions"
          :key="s.id"
          class="session-item"
          :class="{ active: s.id === store.activeId }"
          @click="onSelect(s.id)"
        >
          <div class="s-title">
            <span
              class="s-kind"
              :class="sessionKind(s).tone"
            >{{ sessionKind(s).text }}</span>
            <span class="s-name">{{ s.title || UNTITLED }}</span>
          </div>
          <div class="s-meta">
            <span class="s-time">{{ timeAgo(s.updated_at) }}</span>
            <span
              v-if="(s.total_tokens ?? 0) > 0"
              class="s-tokens"
              :title="`输入 ${s.input_tokens ?? 0} / 输出 ${s.output_tokens ?? 0} tokens`"
            >⚡ {{ formatCompact(s.total_tokens ?? 0) }}</span>
            <el-popconfirm
              title="删除该对话？"
              confirm-button-text="删除"
              cancel-button-text="取消"
              @confirm="onDelete(s.id)"
            >
              <template #reference>
                <el-button
                  class="s-del"
                  link
                  @click.stop
                >
                  <el-icon><Delete /></el-icon>
                </el-button>
              </template>
            </el-popconfirm>
          </div>
        </div>
      </div>
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
  import { computed, onMounted, ref, watch } from 'vue'
  import { useRoute } from 'vue-router'
  import { ElMessageBox } from 'element-plus'
  import { Close, Delete, EditPen, Plus } from '@element-plus/icons-vue'
  import ChatPanel from '@/components/chat/ChatPanel.vue'
  import type { ChatSession } from '@/api/chat'
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
  const popupStore = useChatStore(HARNESS_SCOPE)
  const mainPanelRef = ref<InstanceType<typeof ChatPanel> | null>(null)

  const harnessAvailable = import.meta.env.DEV
  const harnessMode = ref<string | number | boolean>(
    route.query['panel-harness'] === '1' ? 'dual' : 'single',
  )
  const harnessOpen = computed(() => harnessAvailable && harnessMode.value === 'dual')

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

  function timeAgo(t?: string | null): string {
    if (!t) return ''
    const ts = new Date(t).getTime()
    if (isNaN(ts)) return ''
    const diff = Date.now() - ts
    if (diff < 60_000) return '刚刚'
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}分钟前`
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}小时前`
    return new Date(ts).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' })
  }

  /** 会话类型徽章：群聊 > 锁定助手 > 班组 > 普通对话（后端 0028 下发事实，不做本地猜测） */
  // 色调类名必须带前缀，避免与页面根容器 `.chat` 等通用类名冲突
  function sessionKind(
    s: ChatSession,
  ): { text: string; tone: 's-kind--group' | 's-kind--agent' | 's-kind--team' | 's-kind--chat' } {
    if ((s.participant_count ?? 0) > 0)
      return { text: `群聊 ${s.participant_count}`, tone: 's-kind--group' }
    if (s.target === 'agent') return { text: s.target_name || '智能体', tone: 's-kind--agent' }
    if (s.target === 'team') return { text: '班组', tone: 's-kind--team' }
    return { text: '对话', tone: 's-kind--chat' }
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

  async function onDelete(id: string) {
    try {
      await store.removeSession(id)
    } catch {
      /* toast handled */
    }
  }

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
    color: #34d399;
    font-variant-numeric: tabular-nums;
  }

  .s-del {
    opacity: 0;
    color: var(--ds-text-3);
    height: 20px;
    padding: 0;
    transition:
      opacity 0.2s,
      color 0.2s;
  }
  .session-item:hover .s-del,
  .session-item.active .s-del {
    opacity: 1;
  }
  .s-del:hover {
    color: var(--ds-danger);
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
    color: #34d399;
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
