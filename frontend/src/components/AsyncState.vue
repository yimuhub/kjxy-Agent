<template>
  <div
    class="async-state"
    aria-live="polite"
  >
    <el-skeleton
      v-if="state === 'loading'"
      :rows="4"
      animated
    />
    <el-empty
      v-else-if="state === 'empty'"
      :description="emptyText"
    >
      <template #image>
        <!-- 品牌化空态:文档 + 检索的轻量线稿,双主题走设计 token;
             替换 Element 默认深色盒子插画(与「浮层」设计语言不符) -->
        <svg
          class="ds-empty-art"
          viewBox="0 0 160 104"
          width="148"
          height="96"
          aria-hidden="true"
        >
          <rect
            x="30" y="34" width="88" height="54" rx="10"
            fill="var(--ds-glass)" stroke="var(--ds-border-strong)"
          />
          <rect
            x="44" y="18" width="62" height="48" rx="9"
            fill="var(--ds-bg-2)" stroke="var(--ds-border-strong)"
          />
          <line x1="54" y1="32" x2="94" y2="32" stroke="var(--ds-border-strong)" stroke-width="2.5" stroke-linecap="round" />
          <line x1="54" y1="42" x2="86" y2="42" stroke="var(--ds-border-strong)" stroke-width="2.5" stroke-linecap="round" />
          <circle cx="100" cy="52" r="12" fill="none" stroke="var(--ds-accent)" stroke-width="3" />
          <line x1="109" y1="61" x2="119" y2="71" stroke="var(--ds-accent)" stroke-width="3" stroke-linecap="round" />
        </svg>
      </template>
      <slot name="empty-action">
        <el-button
          v-if="emptyActionText"
          type="primary"
          plain
          @click="$emit('action')"
        >
          {{ emptyActionText }}
        </el-button>
      </slot>
    </el-empty>
    <el-result
      v-else-if="state === 'error'"
      icon="error"
      title="加载失败"
      :sub-title="errorText || '请求出错，请稍后重试。'"
    >
      <template #extra>
        <el-button
          type="primary"
          @click="$emit('retry')"
        >
          重试
        </el-button>
      </template>
    </el-result>
    <el-result
      v-else-if="state === 'forbidden'"
      icon="warning"
      title="无权限"
      sub-title="当前账号无权查看此内容，请联系管理员开通权限。"
    />
    <slot v-else />
  </div>
</template>

<script setup lang="ts">
  import type { ViewState } from '@/utils/status'

  withDefaults(
    defineProps<{
      state: ViewState
      emptyText?: string
      emptyActionText?: string
      errorText?: string
    }>(),
    { emptyText: '暂无数据', emptyActionText: '', errorText: '' },
  )

  defineEmits<{
    (e: 'retry'): void
    (e: 'action'): void
  }>()
</script>

<style scoped>
  .async-state {
    padding: 24px 0;
  }
  .ds-empty-art {
    display: block;
    margin: 0 auto 4px;
    opacity: 0.9;
  }
</style>
