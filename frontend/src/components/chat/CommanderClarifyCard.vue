<script setup lang="ts">
  /** 消息内的澄清卡片：回看历史时可以重新作答，答案按原 run 续跑。 */
  import CommanderClarifyForm from '@/components/chat/CommanderClarifyForm.vue'
  import type { CommanderClarifyPayload } from '@/api/commander'

  const props = defineProps<{
    card: CommanderClarifyPayload
    submitting?: boolean
    /** 已提交时传入答案文本，卡片转为只读回显 */
    submitted?: string[] | null
  }>()

  const emit = defineEmits<{
    (e: 'submit', payload: CommanderClarifyPayload, answers: Array<{ key: string; label: string; value: string }>): void
    (e: 'skip', payload: CommanderClarifyPayload): void
  }>()
</script>

<template>
  <section class="clarify-card">
    <header class="cc-head">
      <span class="cc-title">{{ card.display_name || '指挥官' }}需要确认几项</span>
      <span
        v-if="card.goal"
        class="cc-goal"
      >{{ card.goal }}</span>
    </header>
    <CommanderClarifyForm
      :questions="card.questions"
      :submitting="submitting"
      :submitted="submitted"
      @submit="answers => emit('submit', props.card, answers)"
      @skip="emit('skip', props.card)"
    />
  </section>
</template>

<style scoped>
  .clarify-card {
    margin-top: 10px;
    padding: 14px 16px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
    background: var(--ds-glass);
  }

  .cc-head {
    display: flex;
    flex-direction: column;
    gap: 3px;
    margin-bottom: 12px;
  }

  .cc-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .cc-goal {
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.5;
  }
</style>
