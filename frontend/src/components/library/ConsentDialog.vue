<template>
  <el-dialog
    :model-value="modelValue"
    append-to-body
    width="640px"
    class="consent-dialog"
    :close-on-click-modal="!loading"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <template #header>
      <div class="consent-head">
        <span
          class="consent-icon"
          aria-hidden="true"
        >🛡️</span>
        <div>
          <h3>插件权限授权</h3>
          <p>{{ requirement?.item_key || '未知插件' }} · 版本 {{ requirement?.version || '—' }}</p>
        </div>
      </div>
    </template>

    <div
      v-if="requirement"
      class="consent-body"
    >
      <div
        class="risk-banner"
        :class="`risk-${requirement.risk_level}`"
      >
        <el-tag
          :type="riskTagType(requirement.risk_level)"
          effect="dark"
          size="small"
        >
          {{ riskLabel(requirement.risk_level) }}
        </el-tag>
        <span>该插件将在这台电脑上运行；请确认你了解以下权限范围。</span>
      </div>

      <div class="meta-line">
        <span class="meta-label">支持平台</span>
        <span>{{ platformText }}</span>
      </div>

      <ul class="permission-list">
        <li
          v-for="permission in requirement.permissions"
          :key="permission.code"
          class="permission-item"
        >
          <span
            class="risk-dot"
            :class="`risk-${permission.risk}`"
            aria-hidden="true"
          />
          <div class="permission-main">
            <div class="permission-title">
              <strong>{{ permission.label }}</strong>
              <el-tag
                size="small"
                effect="plain"
                :type="riskTagType(permission.risk)"
              >
                {{ riskLabel(permission.risk) }}
              </el-tag>
            </div>
            <p>{{ permission.description }}</p>
          </div>
        </li>
      </ul>

      <el-alert
        type="warning"
        :closable="false"
        show-icon
      >
        <template #title>
          授权后，插件可调用上述本机能力。
        </template>
        <p class="ack-text">
          {{ requirement.ack_text }}
        </p>
      </el-alert>

      <el-checkbox
        v-model="acknowledged"
        class="ack-checkbox"
      >
        我已阅读并知晓上述权限与风险，自愿授权并自行承担使用后果
      </el-checkbox>
    </div>

    <template #footer>
      <el-button @click="emit('update:modelValue', false)">
        取消
      </el-button>
      <el-button
        type="primary"
        :loading="loading"
        :disabled="!acknowledged"
        @click="emit('confirm')"
      >
        确认授权
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  import { computed, ref, watch } from 'vue'
  import type { ConsentRequirement } from '@/api/library'
  import { riskLabel, riskTagType } from '@/components/library/libraryMeta'

  const props = withDefaults(
    defineProps<{
      modelValue: boolean
      requirement: ConsentRequirement | null
      loading?: boolean
    }>(),
    { loading: false },
  )
  const emit = defineEmits<{
    (event: 'update:modelValue', value: boolean): void
    (event: 'confirm'): void
  }>()

  const acknowledged = ref(false)

  watch(
    () => [props.modelValue, props.requirement?.scope_hash],
    () => {
      if (props.modelValue) acknowledged.value = false
    },
  )

  const platformText = computed(() => {
    const platforms = props.requirement?.platforms || []
    if (!platforms.length) return '全平台'
    const labels: Record<string, string> = {
      macos: 'macOS',
      windows: 'Windows',
      linux: 'Linux',
    }
    return platforms.map(item => labels[item] || item).join(' · ')
  })
</script>

<style scoped>
  .consent-head {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .consent-head h3 {
    margin: 0;
    font-size: 17px;
    color: var(--ds-text-1);
  }
  .consent-head p {
    margin: 2px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .consent-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 40px;
    height: 40px;
    border-radius: 12px;
    background: linear-gradient(135deg, rgba(99, 102, 241, 0.25), rgba(56, 189, 248, 0.18));
    font-size: 20px;
  }
  .consent-body {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .risk-banner {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border-radius: 10px;
    border: 1px solid var(--ds-border);
    background: rgba(255, 255, 255, 0.04);
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .risk-banner.risk-critical {
    border-color: rgba(239, 68, 68, 0.45);
    background: rgba(239, 68, 68, 0.08);
  }
  .risk-banner.risk-high {
    border-color: rgba(249, 115, 22, 0.4);
    background: rgba(249, 115, 22, 0.07);
  }
  .meta-line {
    display: flex;
    gap: 10px;
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .meta-label {
    color: var(--ds-text-3);
  }
  .permission-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin: 0;
    padding: 0;
    list-style: none;
    max-height: 300px;
    overflow-y: auto;
  }
  .permission-item {
    display: flex;
    gap: 12px;
    padding: 12px;
    border: 1px solid var(--ds-border);
    border-radius: 12px;
    background: rgba(255, 255, 255, 0.03);
  }
  .permission-main {
    flex: 1;
    min-width: 0;
  }
  .permission-title {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .permission-title strong {
    font-size: 14px;
    color: var(--ds-text-1);
  }
  .permission-main p {
    margin: 4px 0 0;
    font-size: 12.5px;
    line-height: 1.5;
    color: var(--ds-text-3);
  }
  .risk-dot {
    flex: none;
    width: 8px;
    height: 8px;
    margin-top: 7px;
    border-radius: 50%;
    background: var(--el-color-info);
  }
  .risk-dot.risk-medium {
    background: var(--el-color-warning);
  }
  .risk-dot.risk-high {
    background: var(--el-color-danger);
  }
  .risk-dot.risk-critical {
    background: #dc2626;
    box-shadow: 0 0 0 4px rgba(220, 38, 38, 0.16);
  }
  .ack-text {
    margin: 4px 0 0;
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }
  .ack-checkbox {
    align-self: flex-start;
    font-size: 13px;
  }
  @media (max-width: 720px) {
    .consent-body {
      gap: 10px;
    }
  }
</style>
