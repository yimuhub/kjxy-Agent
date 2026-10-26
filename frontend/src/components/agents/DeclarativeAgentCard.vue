<template>
  <article
    class="da-card"
    :class="{ 'is-focus': focused, 'is-selected': selected }"
    :aria-label="agent.display_name"
  >
    <div class="da-cover">
      <label
        v-if="selectable"
        class="da-select-box"
        :title="selected ? '取消勾选' : '勾选以加入多智能体群聊或班组'"
        @click.stop
      >
        <input
          type="checkbox"
          :checked="selected"
          :aria-label="`选择智能体 ${agent.display_name}`"
          @change="emit('toggle-select', agent)"
        >
        <span class="da-select-label">{{ selected ? '已选' : '群聊多选' }}</span>
      </label>

      <img
        v-if="coverSrc"
        class="da-cover-img"
        :src="coverSrc"
        :alt="`${agent.display_name}首图`"
        loading="lazy"
      />
      <span
        v-else
        class="da-cover-emoji"
        aria-hidden="true"
      >{{ agent.emoji || '🤖' }}</span>

      <div class="da-cover-badges">
        <span class="da-code-pill">{{ agent.agent_code }}</span>
        <span
          v-if="tokenStats.total_tokens > 0 || tokenStats.calls > 0"
          class="da-token-pill"
          :title="`累计调用 ${tokenStats.calls} 次 · 输入 ${tokenStats.input_tokens} / 输出 ${tokenStats.output_tokens} tokens`"
        >
          ⚡ {{ formatCompactTokens(tokenStats.total_tokens) }} tokens · {{ tokenStats.calls }}次
        </span>
      </div>
    </div>

    <div class="da-main">
      <div class="da-title-row">
        <h4 class="da-name">
          {{ agent.display_name }}
        </h4>
        <el-tooltip
          v-if="isPrivate"
          content="仅自己可用"
          placement="top"
        >
          <el-tag
            size="small"
            type="warning"
          >
            私有
          </el-tag>
        </el-tooltip>
        <el-tag
          size="small"
          :type="daStatusType(agent.status)"
        >
          {{ daStatusLabel(agent.status) }}
        </el-tag>
      </div>
      <p class="da-desc">
        {{ agent.description || '未填写功能介绍' }}
      </p>

      <dl class="da-meta">
        <div class="da-meta-row">
          <dt>大模型</dt>
          <dd class="da-model-dd">
            <span class="da-model-chip">{{ modelText }}</span>
          </dd>
        </div>
        <div class="da-meta-row">
          <dt>技能({{ skillNames.length }})</dt>
          <dd class="da-skills">
            <template v-if="skillNames.length">
              <el-tag
                v-for="name in skillNames"
                :key="name"
                size="small"
                type="info"
              >
                {{ name }}
              </el-tag>
            </template>
            <span
              v-else
              class="da-none"
            >未绑定</span>
          </dd>
        </div>
        <div class="da-meta-row">
          <dt>Token</dt>
          <dd class="da-token-dd">
            <span>累计 {{ tokenStats.total_tokens.toLocaleString() }} tokens</span>
            <span class="da-token-sub">（调用 {{ tokenStats.calls }} 次）</span>
          </dd>
        </div>
      </dl>
    </div>

    <footer class="da-actions">
      <el-button
        size="small"
        type="primary"
        @click="emit('use', agent)"
      >
        对话
      </el-button>
      <el-button
        v-if="agent.status === 'draft'"
        size="small"
        type="success"
        @click="emit('publish', agent)"
      >
        发布
      </el-button>
      <el-button
        size="small"
        @click="emit('edit', agent)"
      >
        编辑
      </el-button>
      <el-button
        v-if="showDuplicate"
        size="small"
        @click="emit('duplicate', agent)"
      >
        复制
      </el-button>
      <el-button
        v-if="agent.status === 'published'"
        size="small"
        type="warning"
        @click="emit('deprecate', agent)"
      >
        停用
      </el-button>
      <el-button
        size="small"
        type="danger"
        plain
        :aria-label="`删除助手${agent.display_name}`"
        @click="emit('remove', agent)"
      >
        删除
      </el-button>
    </footer>
  </article>
</template>

<script setup lang="ts">
  import { computed, onUnmounted, ref, watch } from 'vue'
  import type { AxiosRequestConfig } from 'axios'
  import { api } from '@/api/client'
  import type { DeclarativeAgent } from '@/api/modules'

  const props = defineProps<{
    agent: DeclarativeAgent
    /** supplier_id + model_name 解析出的模型名；未绑定时父级给「跟随小院默认」 */
    modelText: string
    /** allowed_tools + bound_skills 解析出的技能名 */
    skillNames: string[]
    /** 重名「复制」后聚焦的那张卡 */
    focused?: boolean
    /** 是否展示多选框（用于多智能体群聊/组队） */
    selectable?: boolean
    /** 是否已被勾选 */
    selected?: boolean
    /** 是否展示「复制」按钮 */
    showDuplicate?: boolean
  }>()

  const emit = defineEmits<{
    (e: 'use', agent: DeclarativeAgent): void
    (e: 'edit', agent: DeclarativeAgent): void
    (e: 'publish', agent: DeclarativeAgent): void
    (e: 'deprecate', agent: DeclarativeAgent): void
    (e: 'remove', agent: DeclarativeAgent): void
    (e: 'duplicate', agent: DeclarativeAgent): void
    (e: 'toggle-select', agent: DeclarativeAgent): void
  }>()

  const STATUS_TYPE: Record<string, 'info' | 'warning' | 'success'> = {
    draft: 'info',
    pending_platform_review: 'warning',
    published: 'success',
    deprecated: 'info',
  }
  const STATUS_LABEL: Record<string, string> = {
    draft: '草稿',
    pending_platform_review: '平台审核中',
    published: '已发布',
    deprecated: '已停用',
  }

  const tokenStats = computed(() => ({
    calls: props.agent.token_stats?.calls ?? 0,
    input_tokens: props.agent.token_stats?.input_tokens ?? 0,
    output_tokens: props.agent.token_stats?.output_tokens ?? 0,
    total_tokens: props.agent.token_stats?.total_tokens ?? 0,
  }))

  function formatCompactTokens(n: number): string {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
    if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
    return String(n)
  }

  const coverSrc = ref<string | null>(null)
  let coverObjectUrl: string | null = null

  function revokeCover(): void {
    if (coverObjectUrl) {
      URL.revokeObjectURL(coverObjectUrl)
      coverObjectUrl = null
    }
    coverSrc.value = null
  }

  async function loadCover(): Promise<void> {
    revokeCover()
    const url = props.agent.cover_image_url
    if (!url) return
    try {
      const res = await api.get<Blob>(url, {
        responseType: 'blob',
        skipErrorToast: true,
      } as AxiosRequestConfig & { skipErrorToast?: boolean })
      if (res.data.type.startsWith('image/')) {
        coverObjectUrl = URL.createObjectURL(res.data)
        coverSrc.value = coverObjectUrl
      }
    } catch {
      /* 404/403 一律静默：回落 emoji 不算错误 */
    }
  }

  watch(
    () => props.agent.id,
    () => {
      void loadCover()
    },
    { immediate: true },
  )
  onUnmounted(revokeCover)

  const isPrivate = computed(() => props.agent.visibility === 'private')
  const daStatusType = (s: string) => STATUS_TYPE[s] ?? 'info'
  const daStatusLabel = (s: string) => STATUS_LABEL[s] ?? s
</script>

<style scoped>
  .da-card {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--ds-glass);
    backdrop-filter: blur(18px) saturate(1.2);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
    overflow: hidden;
    transition:
      border-color 0.2s,
      background 0.2s,
      transform 0.2s;
  }
  .da-card:hover {
    border-color: var(--ds-border-strong);
    background: var(--ds-glass-strong);
    transform: translateY(-2px);
  }
  .da-card.is-focus,
  .da-card.is-selected {
    border-color: var(--ds-accent);
    box-shadow: 0 0 0 1px var(--ds-accent);
  }

  .da-cover {
    position: relative;
    flex: none;
    height: 96px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: color-mix(in srgb, var(--ds-accent) 10%, var(--ds-bg-2));
    border-bottom: 1px solid var(--ds-border);
  }
  .da-select-box {
    position: absolute;
    top: 8px;
    left: 10px;
    z-index: 2;
    display: inline-flex;
    align-items: center;
    gap: 5px;
    padding: 3px 8px;
    border-radius: 999px;
    background: rgba(15, 23, 42, 0.72);
    border: 1px solid var(--ds-border-strong);
    font-size: 11.5px;
    color: var(--ds-text-2);
    cursor: pointer;
    user-select: none;
  }
  .da-select-box input {
    cursor: pointer;
    accent-color: var(--ds-accent);
  }
  .da-cover-badges {
    position: absolute;
    bottom: 6px;
    left: 10px;
    right: 10px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    pointer-events: none;
  }
  .da-code-pill {
    padding: 1px 7px;
    border-radius: 999px;
    font-size: 11px;
    font-family: var(--ds-font-mono);
    background: rgba(15, 23, 42, 0.65);
    color: var(--ds-text-3);
    border: 1px solid var(--ds-border);
  }
  .da-token-pill {
    padding: 1px 8px;
    border-radius: 999px;
    font-size: 11px;
    background: rgba(16, 185, 129, 0.16);
    color: #34d399;
    border: 1px solid rgba(16, 185, 129, 0.35);
    pointer-events: auto;
  }
  .da-cover-img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }
  .da-cover-emoji {
    font-size: 40px;
    line-height: 1;
  }

  .da-main {
    flex: 1;
    padding: 14px 16px 8px;
  }
  .da-title-row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px;
  }
  .da-name {
    margin: 0;
    font-size: 14.5px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .da-desc {
    margin: 8px 0 10px;
    font-size: 13px;
    line-height: 1.6;
    color: var(--ds-text-2);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .da-meta {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .da-meta-row {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    font-size: 12.5px;
  }
  .da-meta-row dt {
    flex: none;
    width: 58px;
    color: var(--ds-text-3);
  }
  .da-meta-row dd {
    margin: 0;
    min-width: 0;
    color: var(--ds-text-1);
  }
  .da-model-chip {
    display: inline-block;
    padding: 1px 8px;
    border-radius: 6px;
    font-size: 12px;
    background: color-mix(in srgb, var(--ds-accent) 14%, transparent);
    color: var(--ds-accent-text);
    border: 1px solid color-mix(in srgb, var(--ds-accent) 30%, transparent);
  }
  .da-skills {
    display: flex;
    flex-wrap: wrap;
    gap: 5px;
  }
  .da-token-dd {
    font-size: 12px;
    color: var(--ds-text-2);
    font-variant-numeric: tabular-nums;
  }
  .da-token-sub {
    color: var(--ds-text-3);
  }
  .da-none {
    color: var(--ds-text-3);
  }

  .da-actions {
    flex: none;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 10px 16px 14px;
    border-top: 1px solid var(--ds-border);
  }
  .da-actions :deep(.el-button + .el-button) {
    margin-left: 0;
  }
</style>
