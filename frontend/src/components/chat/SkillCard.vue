<template>
  <div
    class="skill-card"
    :class="card.state"
  >
    <div class="head">
      <span class="kind">技能卡片</span>
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

    <!-- matched：候选列表 + 安装 -->
    <template v-if="card.state === 'matched'">
      <ul
        v-if="card.matches?.length"
        class="match-list"
      >
        <li
          v-for="m in card.matches"
          :key="m.item_key"
          class="match-item"
        >
          <div class="match-info">
            <div class="match-name">
              {{ m.name }}
            </div>
            <p
              v-if="m.summary"
              class="match-summary"
            >
              {{ m.summary }}
            </p>
            <div class="match-meta">
              <span>来源：{{ sourceLabel(m.source) }}</span>
              <span>许可：{{ m.license || '未声明' }}</span>
              <span>
                风险：
                <em :class="riskClass(m.risk_level)">{{ riskLabel(m.risk_level) }}</em>
              </span>
              <span v-if="typeof m.score === 'number'">
                匹配度：{{ Math.round(m.score * 100) }}%
              </span>
            </div>
          </div>
          <el-button
            size="small"
            type="primary"
            :loading="op.installing === m.item_key"
            :disabled="m.installed"
            :aria-label="`${m.installed ? '已安装' : '安装'} ${m.name}`"
            @click="install(m)"
          >
            {{ m.installed ? '已安装' : '安装' }}
          </el-button>
        </li>
      </ul>
      <p
        v-else
        class="note"
      >
        {{ card.note || '没有找到可安装的现成技能' }}
      </p>
      <p
        v-if="card.note && card.matches?.length"
        class="note"
      >
        {{ card.note }}
      </p>
    </template>

    <!-- created：生成结果 + 绑定/提交审核 -->
    <template v-else-if="card.state === 'created' && card.skill">
      <div class="created-head">
        <div class="created-title">
          {{ card.skill.name }}
        </div>
        <div class="badges">
          <el-tag
            size="small"
            type="info"
            effect="plain"
          >
            {{ visibilityLabel(card.skill.visibility) }}
          </el-tag>
          <el-tag
            size="small"
            :type="skillStatusTagType(card.skill.status)"
            effect="plain"
          >
            {{ skillStatusLabel(card.skill.status) }}
          </el-tag>
        </div>
      </div>
      <div class="meta">
        <span class="kv"><i>编码</i>{{ card.skill.code }}</span>
      </div>

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
          size="small"
          type="primary"
          @click="goBind"
        >
          去绑定助手
        </el-button>
        <el-button
          size="small"
          :loading="op.submitting"
          :disabled="submitted"
          @click="submit"
        >
          {{ submitted ? '已提交审核' : '提交审核' }}
        </el-button>
      </div>
    </template>

    <!-- created 但后端未下发 skill（形状不完整）：诚实提示，不白屏 -->
    <template v-else-if="card.state === 'created'">
      <p class="note">
        技能已生成，但卡片缺少技能信息，请到「插件与技能库 → 我的」查看。
      </p>
      <div class="actions">
        <el-button
          size="small"
          type="primary"
          @click="goBind"
        >
          去我的智能体
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
  import type { ChatSkillCard, ChatSkillCardMatch } from '@/api/chat'
  import type { ChatStoreInstance } from '@/stores/chat'
  import { riskLabel, sourceLabel } from '@/components/library/libraryMeta'
  import { skillStatusLabel, skillStatusTagType, visibilityLabel } from '@/components/library/skillMeta'

  /**
   * 对话内技能卡片（v3.2 一句话技能）。
   *
   * 所有按钮状态都从 props.store（各自 ChatPanel 实例的 store）读取，
   * 组件自己不持有任何跨实例共享状态——多个面板同时开卡片也不会互相影响。
   */
  const props = defineProps<{
    card: ChatSkillCard
    store: ChatStoreInstance
    messageId: string
  }>()

  const router = useRouter()

  const op = computed(() => props.store.skillCardOp(props.messageId))

  const submitted = computed(
    () => props.card.skill?.status === 'pending_review' || props.card.skill?.status === 'published',
  )

  const stateLabel = computed(() => {
    if (props.card.state === 'matched') {
      return props.card.matches?.length ? `找到 ${props.card.matches.length} 个候选` : '暂无候选'
    }
    return props.card.state === 'created' ? '已生成草稿' : '生成失败'
  })

  const stateTagType = computed(() => {
    if (props.card.state === 'created') return 'success'
    return props.card.state === 'failed' ? 'danger' : 'primary'
  })

  function riskClass(risk?: string): string {
    if (risk === 'low') return 'low'
    if (risk === 'high') return 'high'
    return ''
  }

  function install(match: ChatSkillCardMatch) {
    if (match.installed) return
    void props.store.installSkillFromCard(props.messageId, match)
  }

  function submit() {
    void props.store.submitSkillCard(props.messageId)
  }

  function retry() {
    void props.store.retrySkillCard(props.messageId)
  }

  function goBind() {
    void router.push('/agents/custom')
  }
</script>

<style scoped>
  .skill-card {
    margin-top: 8px;
    padding: 12px 14px;
    border-radius: 12px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    display: flex;
    flex-direction: column;
    gap: 8px;
    /* 候选列表需要横向空间，避免被短气泡挤成窄条 */
    min-width: min(460px, 100%);
  }
  .skill-card.created {
    border-color: color-mix(in srgb, var(--ds-accent) 45%, transparent);
    background: color-mix(in srgb, var(--ds-accent) 8%, var(--ds-glass));
  }
  .skill-card.failed {
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
  .match-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .match-item {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
    padding: 8px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control, 8px);
  }
  .match-info {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .match-name {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .match-summary {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--ds-text-2);
  }
  .match-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
  .match-meta em {
    font-style: normal;
    padding: 1px 6px;
    border-radius: 6px;
    border: 1px solid var(--ds-border);
  }
  .match-meta em.low {
    color: var(--ds-success, #3f9d68);
  }
  .match-meta em.high {
    color: var(--ds-danger);
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
  .badges {
    display: flex;
    gap: 6px;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 12px;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .kv i {
    font-style: normal;
    color: var(--ds-text-3);
    margin-right: 4px;
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
