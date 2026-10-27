<template>
  <div
    class="plugin-card"
    :class="card.state"
  >
    <div class="head">
      <span class="kind">插件卡片</span>
      <el-tag
        size="small"
        :type="stateTagType"
        effect="plain"
      >
        {{ stateLabel }}
      </el-tag>
    </div>

    <p
      v-if="card.utterance"
      class="utterance"
    >
      「{{ card.utterance }}」
    </p>

    <!-- created：插件摘要 + 工作流步骤预览 -->
    <template v-if="card.state === 'created' && card.plugin">
      <div class="created-head">
        <div class="created-title">
          {{ card.plugin.name }}
        </div>
        <div class="badges">
          <el-tag
            size="small"
            :type="pluginStatusTagType(card.plugin.status)"
            effect="plain"
          >
            {{ pluginStatusLabel(card.plugin.status) }}
          </el-tag>
        </div>
      </div>
      <p
        v-if="card.plugin.description"
        class="desc"
      >
        {{ card.plugin.description }}
      </p>

      <el-collapse
        v-if="steps.length"
        class="steps"
      >
        <el-collapse-item
          :title="`工作流步骤（${steps.length}）`"
          name="steps"
        >
          <ol class="step-list">
            <li
              v-for="(s, i) in steps"
              :key="i"
              class="step-item"
            >
              <el-tag
                size="small"
                :type="s.kind === 'tool' ? 'warning' : 'info'"
                effect="plain"
              >
                {{ s.kind === 'tool' ? '工具' : '模型' }}
              </el-tag>
              <span class="step-text">
                {{ s.kind === 'tool' ? (s.tool || '未指定工具') : stepPrompt(s) }}
              </span>
            </li>
          </ol>
        </el-collapse-item>
      </el-collapse>

      <el-collapse
        v-if="card.sources?.length"
        class="sources"
      >
        <el-collapse-item
          :title="`引用来源（${card.sources.length}）`"
          name="sources"
        >
          <ul class="source-list">
            <li
              v-for="(s, i) in card.sources"
              :key="`${s.url}-${i}`"
            >
              <a
                :href="s.url"
                target="_blank"
                rel="noopener noreferrer"
              >{{ s.title || s.url }}</a>
            </li>
          </ul>
        </el-collapse-item>
      </el-collapse>

      <p
        v-if="card.note"
        class="note"
      >
        {{ card.note }}
      </p>

      <div class="actions">
        <el-button
          v-if="canReview"
          size="small"
          type="primary"
          @click="goReview"
        >
          去审核启用
        </el-button>
        <p
          v-else
          class="note"
        >
          已提交小院审核，由负责人或管理员在「技能与插件」中审核启用。
        </p>
      </div>
    </template>

    <!-- created 但后端未下发 plugin（形状不完整）：诚实提示，不白屏 -->
    <template v-else-if="card.state === 'created'">
      <p class="note">
        插件已生成，但卡片缺少插件信息，请到「技能与插件 → 插件」查看。
      </p>
      <div
        v-if="canReview"
        class="actions"
      >
        <el-button
          size="small"
          type="primary"
          @click="goReview"
        >
          去审核启用
        </el-button>
      </div>
    </template>

    <!-- failed：note + 重试 -->
    <template v-else>
      <p class="note error">
        {{ card.note || '没有生成成功，可重试一次。' }}
      </p>
      <div class="actions">
        <el-button
          size="small"
          :loading="op.retrying"
          @click="retry"
        >
          重试
        </el-button>
      </div>
    </template>

    <p
      v-if="op.error"
      class="note error"
      role="status"
    >
      {{ op.error }}
    </p>
  </div>
</template>

<script setup lang="ts">
  import { computed } from 'vue'
  import { useRouter } from 'vue-router'
  import type { ChatPluginCard, ChatPluginCardStep } from '@/api/chat'
  import type { ChatStoreInstance } from '@/stores/chat'
  import { useAuthStore } from '@/stores/auth'

  /**
   * 对话内插件卡片（v3.3 一句话插件）。
   *
   * 与技能卡片同构但更严格：插件是可执行工作流，没有"私有即用"——
   * created 一律 pending_review，卡片只做预览与导航，不提供启用入口。
   */
  const props = defineProps<{
    card: ChatPluginCard
    store: ChatStoreInstance
    messageId: string
  }>()

  const router = useRouter()
  const auth = useAuthStore()

  const op = computed(() => props.store.pluginCardOp(props.messageId))

  const steps = computed<ChatPluginCardStep[]>(() => {
    const list = props.card.manifest?.steps
    return Array.isArray(list) ? list : []
  })

  /** 生成的工作流插件走 /plugins 审核（require_roles yard_admin）：负责人/管理员专属，专家不可审 */
  const canReview = computed(
    () =>
      auth.roles.includes('yard_owner') ||
      auth.roles.includes('platform_admin') ||
      auth.roles.includes('yard_admin'),
  )

  const stateLabel = computed(() =>
    props.card.state === 'created' ? '已生成待审' : '生成失败',
  )

  const stateTagType = computed(() => (props.card.state === 'created' ? 'success' : 'danger'))

  function stepPrompt(step: ChatPluginCardStep): string {
    const text = String(step.prompt || '').trim()
    return text.length > 60 ? `${text.slice(0, 60)}…` : text || '（空步骤）'
  }

  function pluginStatusLabel(status: string): string {
    if (status === 'pending_review') return '待审核'
    if (status === 'enabled') return '已启用'
    if (status === 'disabled') return '已停用'
    return status
  }

  function pluginStatusTagType(status: string): 'warning' | 'success' | 'info' {
    if (status === 'pending_review') return 'warning'
    if (status === 'enabled') return 'success'
    return 'info'
  }

  function retry() {
    void props.store.retryPluginCard(props.messageId)
  }

  function goReview() {
    void router.push('/evolution')
  }
</script>

<style scoped>
  .plugin-card {
    margin-top: 8px;
    padding: 12px 14px;
    border-radius: 12px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: min(460px, 100%);
  }
  .plugin-card.created {
    border-color: color-mix(in srgb, var(--ds-warning) 40%, transparent);
    background: color-mix(in srgb, var(--ds-warning) 7%, var(--ds-glass));
  }
  .plugin-card.failed {
    border-style: dashed;
  }
  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .kind {
    font-size: 11px;
    letter-spacing: 0.04em;
    color: var(--ds-text-3);
  }
  .utterance {
    margin: 0;
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--ds-text-2);
    word-break: break-word;
  }
  .created-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }
  .created-title {
    font-size: 14px;
    font-weight: 600;
    color: var(--ds-text-1);
    word-break: break-word;
  }
  .desc {
    margin: 0;
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }
  .badges {
    display: flex;
    gap: 6px;
  }
  .steps :deep(.el-collapse-item__header) {
    font-size: 12px;
    height: 30px;
    line-height: 30px;
  }
  .step-list {
    list-style: none;
    margin: 0;
    padding: 0;
    counter-reset: step;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .step-item {
    display: flex;
    align-items: flex-start;
    gap: 6px;
    font-size: 12px;
    color: var(--ds-text-2);
    counter-increment: step;
  }
  .step-item::before {
    content: counter(step) '.';
    color: var(--ds-text-3);
    flex: none;
  }
  .step-text {
    min-width: 0;
    word-break: break-word;
  }
  .sources :deep(.el-collapse-item__header) {
    font-size: 12px;
    height: 30px;
    line-height: 30px;
  }
  .source-list {
    margin: 0;
    padding-left: 18px;
    display: flex;
    flex-direction: column;
    gap: 3px;
    font-size: 12px;
  }
  .source-list a {
    color: var(--ds-accent-text);
    word-break: break-all;
  }
  .note {
    margin: 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-3);
  }
  .note.error {
    color: var(--ds-danger);
  }
  .actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    margin-top: 2px;
  }
</style>
