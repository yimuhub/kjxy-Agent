<script setup lang="ts">
  /**
   * 指挥官澄清表单：把「缺什么信息」变成点一下就能答的选项。
   *
   * 设计口径：
   * - 能列选项的题一律给 chips（single/multi），用户点一下即可；
   * - 允许自定义的题保留输入框，选项与自由输入互为补充；
   * - 必填项未答时不禁用提交，但会提示——宁可带着假设跑，也不要卡住用户；
   * - 「按最合理假设执行」永远可用：不想答就跳过，指挥官照常干活。
   */
  import { computed, reactive, watch } from 'vue'
  import type { CommanderClarifyQuestion } from '@/api/commander'

  const props = defineProps<{
    questions: CommanderClarifyQuestion[]
    submitting?: boolean
    /** 已提交过的答案（历史消息回看时展示） */
    submitted?: string[] | null
  }>()

  const emit = defineEmits<{
    (e: 'submit', answers: Array<{ key: string; label: string; value: string }>): void
    (e: 'skip'): void
  }>()

  /** 每题的当前答案：单选/文本用 string；多选用数组 */
  const picked = reactive<Record<string, string | string[]>>({})
  const custom = reactive<Record<string, string>>({})

  function reset() {
    for (const q of props.questions) {
      picked[q.key] = q.type === 'multi' ? [] : ''
      custom[q.key] = ''
    }
  }
  watch(() => props.questions, reset, { immediate: true, deep: true })

  function isPicked(q: CommanderClarifyQuestion, option: string): boolean {
    const value = picked[q.key]
    return Array.isArray(value) ? value.includes(option) : value === option
  }

  function toggle(q: CommanderClarifyQuestion, option: string) {
    if (q.type === 'multi') {
      const list = Array.isArray(picked[q.key]) ? [...(picked[q.key] as string[])] : []
      const index = list.indexOf(option)
      if (index >= 0) list.splice(index, 1)
      else list.push(option)
      picked[q.key] = list
      return
    }
    picked[q.key] = isPicked(q, option) ? '' : option
    if (picked[q.key]) custom[q.key] = ''
  }

  function onCustomInput(q: CommanderClarifyQuestion, value: string) {
    custom[q.key] = value
    if (value.trim() && q.type !== 'multi') picked[q.key] = ''
  }

  function answerOf(q: CommanderClarifyQuestion): string {
    const free = (custom[q.key] || '').trim()
    const value = picked[q.key]
    if (Array.isArray(value)) {
      const list = [...value]
      if (free) list.push(free)
      return list.join('、')
    }
    return free || (value || '').trim()
  }

  const missing = computed(() =>
    props.questions.filter(q => q.required && !answerOf(q)).map(q => q.label),
  )

  function submit() {
    const answers = props.questions
      .map(q => ({ key: q.key, label: q.label, value: answerOf(q) }))
      .filter(item => item.value)
    emit('submit', answers)
  }
</script>

<template>
  <div class="clarify-form">
    <div
      v-if="submitted && submitted.length"
      class="cf-done"
    >
      <span class="cf-done-tag">已补充</span>
      <span
        v-for="(item, index) in submitted"
        :key="index"
        class="cf-done-item"
      >{{ item }}</span>
    </div>

    <template v-else>
      <div
        v-for="(q, index) in questions"
        :key="q.key"
        class="cf-item"
      >
        <div class="cf-label">
          <span class="cf-index">{{ index + 1 }}</span>
          <span>{{ q.label }}</span>
          <span
            v-if="q.required"
            class="cf-req"
          >必填</span>
        </div>

        <div
          v-if="q.type !== 'text' && q.options.length"
          class="cf-options"
        >
          <button
            v-for="option in q.options"
            :key="option"
            type="button"
            class="cf-chip"
            :class="{ on: isPicked(q, option) }"
            :aria-pressed="isPicked(q, option)"
            :disabled="submitting"
            @click="toggle(q, option)"
          >
            {{ option }}
          </button>
        </div>

        <input
          v-if="q.type === 'text' || q.allow_custom !== false"
          class="cf-input"
          type="text"
          :value="custom[q.key]"
          :placeholder="q.placeholder || (q.type === 'text' ? '直接输入' : '也可以自己填')"
          :disabled="submitting"
          :aria-label="q.label"
          @input="onCustomInput(q, ($event.target as HTMLInputElement).value)"
          @keydown.enter.prevent="submit"
        >
      </div>

      <p
        v-if="missing.length"
        class="cf-hint"
        role="status"
      >
        还有 {{ missing.length }} 项没填（{{ missing.join('、') }}），不填就按最合理假设执行。
      </p>

      <div class="cf-actions">
        <button
          type="button"
          class="cf-btn ghost"
          :disabled="submitting"
          @click="emit('skip')"
        >
          按最合理假设执行
        </button>
        <button
          type="button"
          class="cf-btn primary"
          :disabled="submitting"
          @click="submit"
        >
          {{ submitting ? '提交中…' : '确认并继续' }}
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
  .clarify-form {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }

  .cf-item {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .cf-label {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 13px;
    color: var(--ds-text-1);
    line-height: 1.5;
  }

  .cf-index {
    flex: none;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: var(--ds-field);
    color: var(--ds-text-3);
    font-size: 11px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }

  .cf-req {
    font-size: 11px;
    color: var(--ds-warning);
  }

  .cf-options {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .cf-chip {
    padding: 5px 11px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
    background: transparent;
    color: var(--ds-text-2);
    font-size: 12.5px;
    cursor: pointer;
    transition:
      border-color 0.18s,
      color 0.18s,
      background 0.18s;
  }
  .cf-chip:hover:not(:disabled) {
    border-color: var(--ds-border-strong);
    color: var(--ds-text-1);
  }
  .cf-chip.on {
    background: var(--ds-bg-2);
    border-color: var(--ds-border-strong);
    color: var(--ds-text-1);
    font-weight: 500;
  }
  .cf-chip:disabled {
    opacity: 0.55;
    cursor: not-allowed;
  }

  .cf-input {
    width: 100%;
    padding: 8px 11px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    background: var(--ds-bg-1);
    color: var(--ds-text-1);
    font-size: 13px;
    outline: none;
    transition: border-color 0.18s;
  }
  .cf-input:focus {
    border-color: var(--ds-border-strong);
  }
  .cf-input::placeholder {
    color: var(--ds-text-3);
  }

  .cf-hint {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }

  .cf-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }

  .cf-btn {
    height: 32px;
    padding: 0 14px;
    border-radius: var(--ds-radius-pill);
    font-size: 13px;
    cursor: pointer;
    transition:
      background 0.18s,
      border-color 0.18s;
  }
  .cf-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .cf-btn.ghost {
    border: 1px solid var(--ds-border);
    background: transparent;
    color: var(--ds-text-2);
  }
  .cf-btn.ghost:hover:not(:disabled) {
    border-color: var(--ds-border-strong);
    color: var(--ds-text-1);
  }
  .cf-btn.primary {
    border: 1px solid transparent;
    background: var(--ds-text-1);
    color: var(--ds-bg-1);
    font-weight: 500;
  }
  .cf-btn.primary:hover:not(:disabled) {
    filter: brightness(0.92);
  }

  .cf-done {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 7px;
    font-size: 12.5px;
    color: var(--ds-text-2);
  }
  .cf-done-tag {
    padding: 2px 8px;
    border-radius: var(--ds-radius-pill);
    background: var(--ds-field);
    color: var(--ds-text-3);
    font-size: 11px;
  }
  .cf-done-item {
    padding: 2px 9px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
  }
</style>
