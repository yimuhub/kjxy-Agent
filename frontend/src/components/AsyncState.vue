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
</style>
