<template>
  <el-dialog
    :model-value="modelValue"
    class="agent-use-dialog"
    :title="dialogTitle"
    :fullscreen="isMobile"
    width="780px"
    top="6vh"
    append-to-body
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div
      v-loading="sessionBusy"
      class="use-body"
    >
      <div class="use-head">
        <template v-if="isMultiGroup">
          <div class="use-group-avatars">
            <span
              v-for="a in activeAgents"
              :key="a.id"
              class="use-group-chip"
            >
              <span>{{ a.emoji || '🤖' }}</span>
              <span>{{ a.display_name }}</span>
            </span>
          </div>
          <div class="use-facts">
            <el-tag
              size="small"
              type="success"
            >
              多智能体群聊协同（{{ activeAgents.length }} 位）
            </el-tag>
            <span class="use-model">支持全员齐答 / @点名答 / 总控自动选</span>
          </div>
        </template>
        <template v-else>
          <span
            class="use-emoji"
            aria-hidden="true"
          >{{ agent.cover_image_url ? '🖼️' : agent.emoji || '🤖' }}</span>
          <div class="use-facts">
            <span class="use-model">大模型：{{ modelText }}</span>
            <span
              v-if="agent.visibility === 'private'"
              class="use-private"
            >私有 · 仅自己可用</span>
            <span
              v-if="sessionTokens > 0"
              class="use-token-badge"
            >⚡ 本会话已用 {{ sessionTokens.toLocaleString() }} tokens</span>
          </div>
        </template>

        <div class="use-head-actions">
          <el-button
            v-if="store.activeId"
            size="small"
            link
            type="primary"
            @click="openInMainChat"
          >
            在主对话页展开 ↗
          </el-button>
        </div>
      </div>

      <!-- 会话建好前不渲染输入区：避免用户抢在锁定会话落地前发送，落到未锁定的临时会话 -->
      <div
        v-if="sessionBusy"
        v-loading="true"
        element-loading-text="正在准备对话…"
        class="use-preparing"
      />
      <!-- 独立 scope 的对话实例：支持单智能体对话与多智能体群聊 -->
      <ChatPanel
        v-else
        :store="store"
        :panel-id="panelId"
        :label="dialogTitle"
        :suggestions="suggestions"
        :empty-title="emptyTitle"
        :empty-sub="emptySub"
        :mentionable="isMultiGroup"
        :groupable="isMultiGroup"
        :locked-label="isMultiGroup ? '' : props.agent.display_name"
        placeholder="输入问题，Enter 发送，Shift+Enter 换行；群聊支持输入 @ 点名特定智能体"
        max-width="100%"
        @card-published="emit('card-published')"
      />
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
  import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage } from 'element-plus'
  import ChatPanel from '@/components/chat/ChatPanel.vue'
  import { chatApi } from '@/api/chat'
  import { declarativeApi, type DeclarativeAgent } from '@/api/modules'
  import { extractErrorMessage } from '@/api/client'
  import { useChatStore } from '@/stores/chat'
  import { friendlyErrorMessage } from '@/utils/status'

  const props = defineProps<{
    modelValue: boolean
    agent: DeclarativeAgent
    /** 多智能体群聊时传入 2..8 个智能体 */
    agents?: DeclarativeAgent[]
    /** supplier_id 解析后的模型名（未绑定给「跟随小院默认」） */
    modelText: string
  }>()

  const emit = defineEmits<{
    (e: 'update:modelValue', value: boolean): void
    /** 对话里发布了能力：父级刷新助手列表 */
    (e: 'card-published'): void
  }>()

  const router = useRouter()

  const activeAgents = computed<DeclarativeAgent[]>(() =>
    props.agents && props.agents.length > 1 ? props.agents : [props.agent],
  )
  const isMultiGroup = computed(() => activeAgents.value.length > 1)

  const panelId = computed(() =>
    isMultiGroup.value
      ? `group-${activeAgents.value.map(a => a.id.slice(0, 6)).join('-')}`
      : `agent-${props.agent.id}`,
  )
  const store = useChatStore(panelId.value)
  const sessionBusy = ref(false)

  const dialogTitle = computed(() =>
    isMultiGroup.value
      ? `多智能体群聊（${activeAgents.value.map(a => a.display_name).join('、')}）`
      : `使用：${props.agent.display_name}`,
  )

  const emptyTitle = computed(() =>
    isMultiGroup.value
      ? `与 ${activeAgents.value.length} 位智能体群聊研讨`
      : `问问「${props.agent.display_name}」`,
  )

  const emptySub = computed(() =>
    isMultiGroup.value
      ? '可切换「全员齐答 / 点名答(@) / 自动选」，每位智能体会按各自绑定的大模型与技能独立作答并展示 Token 消耗。'
      : props.agent.description || '直接说要办的事，回答会标注实际来源与 Token 消耗。',
  )

  const suggestions = computed(() =>
    isMultiGroup.value
      ? [
          '请各位从自身专业角度分析当前作物叶片发黄的可能原因与处置建议',
          '结合本小院最新知识库，为我们制定一份本周农事与防灾协同方案',
        ]
      : [
          '介绍一下你的能力和你最擅长的场景',
          '结合本小院资料给个建议',
        ],
  )

  const sessionTokens = computed(() =>
    store.messages.reduce((sum, m) => sum + (m.token_usage?.total_tokens ?? 0), 0),
  )

  const mql = window.matchMedia('(max-width: 768px)')
  const isMobile = ref(mql.matches)
  function onViewportChange(e: MediaQueryListEvent) {
    isMobile.value = e.matches
  }
  mql.addEventListener('change', onViewportChange)
  onBeforeUnmount(() => mql.removeEventListener('change', onViewportChange))

  function openInMainChat() {
    const sid = store.activeId
    emit('update:modelValue', false)
    router.push(sid ? { path: '/chat', query: { session_id: sid } } : { path: '/chat' })
  }

  onMounted(async () => {
    if (store.activeId) return
    sessionBusy.value = true
    try {
      if (isMultiGroup.value) {
        const names = activeAgents.value.map(a => a.display_name).join('、')
        const session = await chatApi.createSession({
          title: `群聊：${names.slice(0, 40)}`,
        })
        store.activeId = session.id
        await store.saveParticipantOrder(
          activeAgents.value.map(a => ({
            agent_id: a.id,
            agent_code: a.agent_code ?? null,
            agent_name: a.display_name,
          })),
        )
        store.setGroupPolicy('all')
        await store.loadMentionCandidates()
      } else {
        const session = await declarativeApi.openChatSession(
          props.agent.agent_code,
          `与「${props.agent.display_name}」的对话`,
        )
        store.activeId = session.id
        // 让本实例也知道会话被锁定：发送时带同一个 target_code，界面显示绑定提示
        if (!store.sessions.some(s => s.id === session.id)) {
          const now = new Date().toISOString()
          store.sessions.unshift({
            id: session.id,
            title: session.title,
            target: session.target,
            target_code: session.target_code,
            target_name: session.target_name,
            created_at: session.created_at || now,
            updated_at: session.updated_at || now,
          })
        }
      }
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message))
    } finally {
      sessionBusy.value = false
    }
  })
</script>

<style scoped>
  .use-body {
    height: min(66vh, 600px);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .use-preparing {
    flex: 1;
    min-height: 260px;
  }

  .use-head {
    flex: none;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    padding: 0 0 10px;
    border-bottom: 1px solid var(--ds-border);
  }
  .use-emoji {
    font-size: 20px;
    line-height: 1;
  }
  .use-group-avatars {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .use-group-chip {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 8px;
    border-radius: 999px;
    font-size: 12px;
    background: var(--ds-glass-strong);
    border: 1px solid var(--ds-border);
    color: var(--ds-text-1);
  }
  .use-facts {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
    font-size: 12.5px;
    color: var(--ds-text-2);
  }
  .use-private {
    color: var(--ds-warning);
  }
  .use-token-badge {
    padding: 1px 8px;
    border-radius: 999px;
    font-size: 11.5px;
    color: var(--ds-success);
    background: rgba(16, 185, 129, 0.14);
    border: 1px solid rgba(16, 185, 129, 0.3);
  }
  .use-head-actions {
    margin-left: auto;
  }

  @media (max-width: 768px) {
    .use-body {
      height: calc(100vh - 116px);
    }
  }
</style>
