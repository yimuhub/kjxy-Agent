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
            {{ s.title || UNTITLED }}
          </div>
          <div class="s-meta">
            <span class="s-time">{{ timeAgo(s.updated_at) }}</span>
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

    <!-- 右：主对话区（顶栏 + 消息流 max-820 + 底部输入） -->
    <section class="chat-main">
      <header class="chat-top">
        <div class="t-left">
          <span class="t-title">{{ store.activeId ? store.activeTitle : UNTITLED }}</span>
          <el-button
            v-if="store.activeId"
            class="t-edit"
            link
            title="重命名"
            @click="onRename"
          >
            <el-icon><EditPen /></el-icon>
          </el-button>
        </div>
        <el-tooltip
          content="按问题内容自动挑选专业助手或协作班组：规则命中 → 智能分类 → 频率兜底，每条回答都会标注实际来源。"
          placement="bottom"
        >
          <span class="t-route">模型路由：自动</span>
        </el-tooltip>
      </header>

      <div
        ref="streamRef"
        v-loading="store.messagesLoading"
        class="chat-stream"
      >
        <!-- 空态：大标题 + 四个建议卡（点击填入输入框） -->
        <div
          v-if="!store.messages.length && !store.sending"
          class="chat-empty"
        >
          <h3 class="empty-title">
            直接说要办的事
          </h3>
          <p class="empty-sub">
            助手与班组会自动接住你的问题，回答标注实际来源，不确定可转专家。
          </p>
          <div class="suggest-grid">
            <button
              v-for="(tip, i) in SUGGESTIONS"
              :key="i"
              class="suggest-card"
              @click="useSuggestion(tip)"
            >
              {{ tip }}
            </button>
          </div>
        </div>

        <div
          v-else
          class="stream-inner"
        >
          <div
            v-for="m in store.messages"
            :key="m.id"
            class="msg-row"
            :class="m.role"
          >
            <div class="msg-col">
              <!-- 助手诚实徽章：由谁回答 + 路由来源（tooltip=reason） -->
              <div
                v-if="m.role === 'assistant' && (m.choice || m.routed_by)"
                class="msg-badge"
              >
                <span
                  v-if="m.choice"
                  class="badge-from"
                >↳ 由 {{ m.choice.name }} 回答</span>
                <el-tooltip
                  v-if="m.routed_by"
                  :content="m.reason || routedMeta(m.routed_by).tip"
                  placement="top"
                >
                  <el-tag
                    size="small"
                    :type="routedMeta(m.routed_by).type"
                  >
                    {{ routedMeta(m.routed_by).label }}
                  </el-tag>
                </el-tooltip>
              </div>
              <!-- 纯文本 pre-wrap，不引 markdown 库 -->
              <div
                class="bubble"
                :class="m.role"
              >
                {{ m.content }}
              </div>
            </div>
          </div>

          <!-- 等待回答：三点波浪 -->
          <div
            v-if="store.sending"
            class="msg-row assistant"
          >
            <div class="bubble assistant typing">
              <span class="dot" /><span class="dot" /><span class="dot" />
            </div>
          </div>
        </div>
      </div>

      <footer class="chat-input">
        <div class="input-inner">
          <el-input
            ref="inputRef"
            v-model="draft"
            type="textarea"
            :autosize="{ minRows: 1, maxRows: 6 }"
            :maxlength="2000"
            resize="none"
            placeholder="直接说要办的事，Enter 发送，Shift+Enter 换行"
            @keydown.enter.exact.prevent="onSend"
          />
          <!-- 发送中仅样式变为「停止」并禁用（v1 不支持中断） -->
          <button
            class="send-btn"
            :class="{ stop: store.sending }"
            :disabled="store.sending || !draft.trim()"
            :aria-label="store.sending ? '回答生成中' : '发送'"
            @click="onSend"
          >
            <el-icon>
              <VideoPause v-if="store.sending" />
              <Promotion v-else />
            </el-icon>
          </button>
        </div>
      </footer>
    </section>
  </div>
</template>

<script setup lang="ts">
  import { nextTick, onMounted, ref } from 'vue'
  import { ElInput, ElMessageBox } from 'element-plus'
  import { Delete, EditPen, Plus, Promotion, VideoPause } from '@element-plus/icons-vue'
  import { UNTITLED, useChatStore } from '@/stores/chat'
  import type { ChatRoutedBy } from '@/api/chat'

  /** 路由来源徽章（诚实标记口径，§7：rules=规则命中/success，llm=智能分类/accent，fallback=兜底/warning） */
  const ROUTED_META: Record<
    ChatRoutedBy,
    { label: string; type: 'success' | 'primary' | 'warning' | 'info' | 'danger'; tip: string }
  > = {
    rules: { label: '规则命中', type: 'success', tip: '按关键词规则直接命中该对象' },
    llm: { label: '智能分类', type: 'primary', tip: '由模型分类挑选该对象' },
    fallback: { label: '兜底', type: 'warning', tip: '未命中规则与分类，按近期频率兜底' },
    manual: { label: '手动指定', type: 'info', tip: '用户手动指定的执行对象' },
    error: { label: '执行失败', type: 'danger', tip: '执行遇到问题，可换个说法再问' },
  }

  /** 空态建议卡文案（点击填入输入框） */
  const SUGGESTIONS = [
    '帮我诊断水稻叶片发黄，可能是什么问题？',
    '整理一下本周设备上报的异常数据',
    '写一份小麦赤霉病的防治方案',
    '有哪些适合家庭农场的惠农政策？',
  ]

  const store = useChatStore()
  const draft = ref('')
  const streamRef = ref<HTMLElement | null>(null)
  const inputRef = ref<InstanceType<typeof ElInput> | null>(null)

  function routedMeta(routed?: ChatRoutedBy | null) {
    return ROUTED_META[routed ?? 'error']
  }

  /** 列表时间：刚刚 / N分钟前 / N小时前 / 月日 */
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

  function scrollToBottom() {
    nextTick(() => {
      const el = streamRef.value
      if (el) el.scrollTop = el.scrollHeight
    })
  }

  async function onNew() {
    try {
      await store.newSession()
    } catch {
      /* 错误 toast 已由 client 拦截器统一处理 */
    }
  }

  async function onSelect(id: string) {
    try {
      await store.selectSession(id)
      scrollToBottom()
    } catch {
      /* 已 toast */
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
      /* 取消或失败（已 toast） */
    }
  }

  async function onDelete(id: string) {
    try {
      await store.removeSession(id)
    } catch {
      /* 已 toast */
    }
  }

  function useSuggestion(tip: string) {
    draft.value = tip
    inputRef.value?.focus()
  }

  async function onSend() {
    const text = draft.value.trim()
    if (!text || store.sending) return
    // 发送失败保留草稿便于重发；成功才清空
    if (await store.send(text)) {
      draft.value = ''
    }
    scrollToBottom()
  }

  onMounted(() => {
    store.fetchSessions().catch(() => undefined)
    scrollToBottom()
  })
</script>

<style scoped>
  /* 三段式骨架：左会话列表 260 + 右主区（玻璃面板配方，DESIGN-v2 §3/§7） */
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

  /* 新对话：品牌渐变动作位（§4 允许处之一） */
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
    font-size: 13px;
    color: var(--ds-text-1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .s-meta {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-top: 2px;
  }

  .s-time {
    font-size: 12px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }

  /* 悬浮删除：默认隐藏，hover/选中现形 */
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

  .t-route {
    flex: none;
    font-size: 12px;
    color: var(--ds-text-3);
    cursor: default;
  }

  .chat-stream {
    flex: 1;
    overflow-y: auto;
    padding: 20px;
  }
  .chat-main :deep(.el-loading-mask),
  .chat-sessions :deep(.el-loading-mask) {
    background-color: var(--ds-bg-1);
  }

  .stream-inner {
    max-width: 820px;
    margin: 0 auto;
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .msg-row {
    display: flex;
  }
  .msg-row.user {
    justify-content: flex-end;
  }
  .msg-col {
    max-width: 78%;
    display: flex;
    flex-direction: column;
    align-items: flex-start;
  }
  .msg-row.user .msg-col {
    align-items: flex-end;
  }

  .bubble {
    padding: 10px 14px;
    border-radius: 14px;
    font-size: 14px;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
  }
  /* 用户气泡：accent 深底右对齐 */
  .bubble.user {
    background: color-mix(in srgb, var(--ds-accent) 30%, var(--ds-bg-1));
    border: 1px solid color-mix(in srgb, var(--ds-accent) 45%, transparent);
    color: var(--ds-text-1);
  }
  /* 助手气泡：玻璃底左对齐 */
  .bubble.assistant {
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    color: var(--ds-text-1);
  }

  .msg-badge {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: 0 2px 6px;
  }
  .badge-from {
    font-size: 12px;
    color: var(--ds-text-3);
  }

  /* 等待回答：三点波浪 */
  .bubble.typing {
    display: flex;
    align-items: center;
    gap: 5px;
    padding: 14px 16px;
  }
  .bubble.typing .dot {
    width: 6px;
    height: 6px;
    border-radius: 999px;
    background: var(--ds-text-3);
    animation: typing-wave 0.9s ease-in-out infinite;
  }
  .bubble.typing .dot:nth-child(2) {
    animation-delay: 0.15s;
  }
  .bubble.typing .dot:nth-child(3) {
    animation-delay: 0.3s;
  }
  @keyframes typing-wave {
    0%,
    60%,
    100% {
      transform: translateY(0);
      opacity: 0.5;
    }
    30% {
      transform: translateY(-4px);
      opacity: 1;
    }
  }

  /* 空态 */
  .chat-empty {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 24px;
  }
  .empty-title {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ds-text-1);
  }
  .empty-sub {
    margin: 0 0 10px;
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .suggest-grid {
    width: 100%;
    max-width: 720px;
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 12px;
  }
  /* 建议卡：可点玻璃卡 hover 配方（§3） */
  .suggest-card {
    text-align: left;
    padding: 14px 16px;
    font-size: 13px;
    line-height: 1.6;
    color: var(--ds-text-2);
    cursor: pointer;
    background: var(--ds-glass);
    backdrop-filter: blur(18px) saturate(1.2);
    border: 1px solid var(--ds-border);
    border-radius: 14px;
    transition:
      border-color 0.2s,
      background 0.2s,
      transform 0.2s;
  }
  .suggest-card:hover {
    border-color: var(--ds-border-strong);
    background: var(--ds-glass-strong);
    color: var(--ds-text-1);
    transform: translateY(-2px);
  }

  /* 底部输入区：粘性玻璃，随消息流同宽 820 */
  .chat-input {
    flex: none;
    border-top: 1px solid var(--ds-border);
    background: var(--ds-glass);
    backdrop-filter: blur(18px) saturate(1.2);
    padding: 12px 20px 16px;
  }
  .input-inner {
    max-width: 820px;
    margin: 0 auto;
    display: flex;
    align-items: flex-end;
    gap: 10px;
  }
  .input-inner :deep(.el-textarea__inner) {
    box-shadow: 0 0 0 1px var(--ds-border) inset;
    background: var(--ds-bg-1);
    color: var(--ds-text-1);
    border-radius: 10px;
    padding: 8px 12px;
    line-height: 22px;
  }
  .input-inner :deep(.el-textarea__inner:focus) {
    box-shadow:
      0 0 0 1px color-mix(in srgb, var(--ds-accent) 30%, transparent) inset,
      0 0 0 3px color-mix(in srgb, var(--ds-accent) 30%, transparent);
  }

  /* 发送圆钮：品牌渐变动作位；发送中仅禁用 */
  .send-btn {
    flex: none;
    width: 34px;
    height: 34px;
    border: none;
    border-radius: 999px;
    background: var(--ds-grad-brand);
    color: #fff;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: filter 0.2s;
  }
  .send-btn:hover:not(:disabled) {
    filter: brightness(1.1);
  }
  .send-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  /* 滚动条 8px 圆头（§6） */
  .session-list::-webkit-scrollbar,
  .chat-stream::-webkit-scrollbar {
    width: 8px;
  }
  .session-list::-webkit-scrollbar-thumb,
  .chat-stream::-webkit-scrollbar-thumb {
    background: var(--ds-border-strong);
    border-radius: 999px;
  }
  .session-list::-webkit-scrollbar-track,
  .chat-stream::-webkit-scrollbar-track {
    background: transparent;
  }

  /* 窄屏收敛：隐藏侧栏由外层布局控制，这里仅收窄留白 */
  @media (max-width: 900px) {
    .chat-sessions {
      width: 200px;
    }
    .suggest-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
