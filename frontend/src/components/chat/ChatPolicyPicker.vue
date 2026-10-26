<template>
  <div
    class="policy-picker"
    :class="{ blocked: !!blockReason }"
  >
    <span
      :id="labelId"
      class="picker-label"
    >本轮答法</span>
    <el-radio-group
      :model-value="modelValue"
      size="small"
      :disabled="disabled"
      :aria-labelledby="labelId"
      @update:model-value="onPick"
    >
      <el-radio-button
        v-for="opt in POLICY_OPTIONS"
        :key="opt.value"
        :value="opt.value"
      >
        {{ opt.label }}
      </el-radio-button>
    </el-radio-group>
    <p
      class="picker-hint"
      :aria-live="blockReason ? 'assertive' : 'off'"
    >
      <template v-if="blockReason">
        {{ blockReason }}
      </template>
      <template v-else>
        {{ currentHint }}
      </template>
    </p>
  </div>
</template>

<script setup lang="ts">
  import { computed } from 'vue'
  import { ElRadioGroup } from 'element-plus'
  import type { ChatGroupPolicy } from '@/api/chat'

  /**
   * 群聊本轮应答策略选择器：全员答 / 点名答 / 自愿答。
   * named 时父级会把「必须点名」的原因放进 blockReason，这里只负责如实说明。
   */
  const props = withDefaults(
    defineProps<{
      modelValue: ChatGroupPolicy
      disabled?: boolean
      /** 不可发送的原因（如 named 缺少点名对象）；空串表示无阻断 */
      blockReason?: string
    }>(),
    { disabled: false, blockReason: '' },
  )

  const emit = defineEmits<{
    (e: 'update:modelValue', value: ChatGroupPolicy): void
  }>()

  const POLICY_OPTIONS: { value: ChatGroupPolicy; label: string; hint: string }[] = [
    { value: 'all', label: '全员答', hint: '本轮每位成员都会各答一条' },
    { value: 'named', label: '点名答', hint: '只回答你 @ 点名的那一位' },
    { value: 'auto', label: '自愿答', hint: '由成员自行判断要不要接这个问题' },
  ]

  const labelId = `policy-label-${Math.random().toString(36).slice(2, 8)}`

  const currentHint = computed(
    () => POLICY_OPTIONS.find(o => o.value === props.modelValue)?.hint ?? '',
  )

  function onPick(value: string | number | boolean | undefined) {
    emit('update:modelValue', value as ChatGroupPolicy)
  }
</script>

<style scoped>
  .policy-picker {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 6px 8px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
  }

  .picker-label {
    flex: none;
    font-size: 12px;
    color: var(--ds-text-3);
  }

  .picker-hint {
    flex: 1 1 160px;
    margin: 0;
    min-width: 0;
    font-size: 11.5px;
    line-height: 1.5;
    color: var(--ds-text-3);
  }

  .policy-picker.blocked {
    border-color: color-mix(in srgb, var(--ds-warning) 45%, transparent);
  }

  .policy-picker.blocked .picker-hint {
    color: var(--ds-warning);
  }

  .policy-picker :deep(.el-radio-button__inner) {
    background: var(--ds-bg-1);
    border-color: var(--ds-border);
    color: var(--ds-text-2);
    box-shadow: none !important;
  }

  .policy-picker :deep(.el-radio-button__original-radio:checked + .el-radio-button__inner) {
    background: var(--ds-bg-2);
    border-color: var(--ds-border-strong);
    color: var(--ds-accent-text);
  }
</style>
