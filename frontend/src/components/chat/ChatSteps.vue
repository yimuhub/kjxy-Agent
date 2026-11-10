<template>
  <div v-if="steps.length" class="steps-wrap">
    <button
      v-if="collapsible && !live"
      class="toggle"
      type="button"
      :aria-expanded="expanded"
      @click="expanded = !expanded"
    >
      <el-icon class="caret" :class="{ open: expanded }">
        <ArrowRight />
      </el-icon>
      过程 · {{ steps.length }} 步
    </button>
    <ul v-if="expanded || live" class="steps" :class="{ live }">
      <li v-for="(s, i) in steps" :key="`${s.phase}-${i}`" class="step" :class="s.status">
        <span class="mark" aria-hidden="true">
          <span v-if="s.status === 'running'" class="spinner" />
          <span v-else-if="s.status === 'failed'" class="cross">✕</span>
          <span v-else class="tick">✓</span>
        </span>
        <span class="text">
          <span class="title">{{ s.title }}</span>
          <span v-if="s.detail" class="detail">{{ s.detail }}</span>
        </span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
  import { ref } from 'vue'
  import { ArrowRight } from '@element-plus/icons-vue'
  import type { ChatStep } from '@/api/chat'

  /**
   * 过程步骤条：把后端的阶段快照渲染成"正在阅读 → 正在思考 → …"的可读序列。
   * live=true 时用于发送中的实时条（带脉冲，常开）；
   * collapsible=true 时用于历史回放（默认折叠，点"过程 · N 步"展开）。
   */
  withDefaults(
    defineProps<{
      steps: ChatStep[]
      live?: boolean
      collapsible?: boolean
    }>(),
    { live: false, collapsible: false },
  )

  const expanded = ref(false)
</script>

<style scoped>
  .steps-wrap {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  .toggle {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    align-self: flex-start;
    padding: 2px 8px;
    font-size: 12px;
    color: var(--ds-text-3);
    background: transparent;
    border: 1px solid var(--ds-border);
    border-radius: 999px;
    cursor: pointer;
  }

  .toggle:hover {
    color: var(--ds-text-2);
    border-color: var(--ds-border-strong);
  }

  .caret {
    transition: transform 0.2s;
  }

  .caret.open {
    transform: rotate(90deg);
  }

  .steps {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .step {
    display: flex;
    align-items: flex-start;
    gap: 7px;
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-3);
  }

  .mark {
    flex: none;
    width: 14px;
    height: 14px;
    margin-top: 3px;
    border-radius: 999px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 9px;
    color: var(--ds-text-3);
    border: 1px solid var(--ds-border);
  }

  .step.done .mark {
    color: var(--ds-success);
    border-color: color-mix(in srgb, var(--ds-success) 55%, transparent);
  }

  .step.failed .mark {
    color: var(--ds-danger);
    border-color: color-mix(in srgb, var(--ds-danger) 55%, transparent);
  }

  .step.running .mark {
    border-color: var(--ds-accent);
  }

  .spinner {
    width: 8px;
    height: 8px;
    border-radius: 999px;
    border: 1.5px solid color-mix(in srgb, var(--ds-accent) 30%, transparent);
    border-top-color: var(--ds-accent);
    animation: step-spin 0.8s linear infinite;
  }

  @keyframes step-spin {
    to {
      transform: rotate(360deg);
    }
  }

  .text {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    min-width: 0;
  }

  .title {
    color: var(--ds-text-2);
  }

  .step.running .title {
    color: var(--ds-accent);
  }

  .detail {
    color: var(--ds-text-3);
  }

  /* 实时条：进行中的那一步做轻微脉冲，暗示"还在动" */
  .live .step.running .title {
    animation: step-pulse 1.4s ease-in-out infinite;
  }

  @keyframes step-pulse {
    0%,
    100% {
      opacity: 1;
    }
    50% {
      opacity: 0.55;
    }
  }
</style>
