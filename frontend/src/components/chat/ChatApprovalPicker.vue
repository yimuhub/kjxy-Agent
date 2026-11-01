<template>
  <el-dropdown
    trigger="click"
    placement="top-start"
    popper-class="ap-popper"
    :disabled="disabled || !hasSession"
    @command="onCommand"
  >
    <button
      type="button"
      class="ap-pill"
      :class="[`ap-pill--${modelValue}`, { 'is-off': !hasSession }]"
      :disabled="disabled || !hasSession"
      :title="pillTitle"
      :aria-label="`本对话的操作权限：${meta.label}`"
    >
      <el-icon class="ap-icon">
        <Unlock v-if="modelValue === 'full'" />
        <Operation v-else />
      </el-icon>
      <span class="ap-text">{{ hasSession ? meta.label : '操作权限' }}</span>
      <el-icon class="ap-caret">
        <ArrowDown />
      </el-icon>
    </button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item
          v-for="opt in OPTIONS"
          :key="opt.value"
          :command="opt.value"
          :disabled="opt.adminsOnly && !canEscalate"
        >
          <span
            class="ap-item"
            :class="{ active: opt.value === modelValue }"
          >
            <span class="ap-item-label">
              {{ opt.label }}
              <em
                v-if="opt.value === modelValue"
                class="ap-now"
              >当前</em>
            </span>
            <span class="ap-item-hint">
              {{ opt.adminsOnly && !canEscalate ? '仅小院/平台管理员或专家可开' : opt.hint }}
            </span>
          </span>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup lang="ts">
  import { computed } from 'vue'
  import { ArrowDown, Operation, Unlock } from '@element-plus/icons-vue'
  import { APPROVAL_MODE_META } from '@/api/chat'
  import type { ChatApprovalMode } from '@/api/chat'

  /**
   * 本对话的高风险工具权限档位（输入框处）。
   *
   * 只是「请求改成哪一档」的入口：服务端按角色校验后回包，父级用回包值刷新显示，
   * 所以即便这里选了 auto/full 而被降级为 ask，界面显示的仍是生效值而不是请求值。
   */
  const props = withDefaults(
    defineProps<{
      modelValue: ChatApprovalMode
      /** 当前角色能否开 auto/full（与后端 APPROVAL_ESCALATION_ROLES 同口径） */
      canEscalate?: boolean
      /** 没有选中会话时不可点：档位是会话级属性 */
      hasSession?: boolean
      disabled?: boolean
    }>(),
    { canEscalate: false, hasSession: true, disabled: false },
  )

  const emit = defineEmits<{
    (e: 'update:modelValue', value: ChatApprovalMode): void
  }>()

  const OPTIONS: { value: ChatApprovalMode; label: string; hint: string; adminsOnly: boolean }[] = (
    ['ask', 'auto', 'full'] as ChatApprovalMode[]
  ).map(value => ({ value, ...APPROVAL_MODE_META[value] }))

  const meta = computed(() => APPROVAL_MODE_META[props.modelValue] ?? APPROVAL_MODE_META.ask)

  const pillTitle = computed(() =>
    props.hasSession ? meta.value.hint : '先在左侧选择或新建一个对话，再设置它的操作权限',
  )

  function onCommand(value: ChatApprovalMode) {
    if (value === props.modelValue) return
    emit('update:modelValue', value)
  }
</script>

<style scoped>
  .ap-pill {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    height: 28px;
    padding: 0 8px;
    border: 1px solid transparent;
    border-radius: 8px;
    background: transparent;
    color: var(--ds-text-3);
    font-size: 12px;
    cursor: pointer;
    transition:
      background 0.15s,
      color 0.15s;
  }
  .ap-pill:hover:not(:disabled) {
    background: var(--ds-glass-strong);
    color: var(--ds-text-1);
  }
  .ap-pill:disabled,
  .ap-pill.is-off {
    opacity: 0.5;
    cursor: not-allowed;
  }
  .ap-icon,
  .ap-caret {
    font-size: 12px;
  }
  .ap-text {
    white-space: nowrap;
  }
  /* 档位越高越醒目：只改文字色，不额外加边框，保持中性底 */
  .ap-pill--auto {
    color: var(--ds-text-2);
  }
  .ap-pill--full {
    color: var(--ds-warning);
    background: color-mix(in srgb, var(--ds-warning) 12%, transparent);
  }

  .ap-item {
    display: flex;
    flex-direction: column;
    gap: 2px;
    max-width: 260px;
    padding: 2px 0;
  }
  .ap-item.active .ap-item-label {
    color: var(--ds-accent-text);
  }
  .ap-item-label {
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .ap-now {
    font-style: normal;
    font-size: 10px;
    padding: 0 4px;
    border-radius: 3px;
    background: color-mix(in srgb, var(--ds-accent) 16%, transparent);
  }
  .ap-item-hint {
    font-size: 11px;
    line-height: 1.45;
    color: var(--ds-text-3);
    white-space: normal;
  }

  /* 窄屏压缩，给右侧模式与发送留空间 */
  @media (max-width: 1280px) {
    .ap-pill {
      padding: 0 6px;
    }
    .ap-text {
      font-size: 11.5px;
    }
  }
</style>
