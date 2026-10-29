<template>
  <el-dialog
append-to-body
    :model-value="modelValue"
    class="dup-dialog"
    title="已有同名助手"
    width="460px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="dup-text">
      本小院已有名为「{{ name }}」的助手（编码 {{ existing?.agent_code || '—' }}，{{
        statusLabel
      }}）。
    </p>
    <p class="dup-hint">
      可以直接使用它、复制一份再改，或换个名字继续新建。
    </p>
    <template #footer>
      <div class="dup-actions">
        <el-button @click="emit('rename')">
          重命名
        </el-button>
        <el-button
          :loading="duplicating"
          @click="emit('duplicate')"
        >
          复制一份
        </el-button>
        <el-button
          type="primary"
          @click="emit('reuse')"
        >
          复用该助手
        </el-button>
      </div>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  import { computed } from 'vue'
  import type { DuplicateNameDetails } from '@/api/modules'

  /**
   * 重名三选弹窗：命中 409 DUPLICATE_NAME 时给出「复用 / 复制 / 重命名」三条出路，
   * 只呈现事实（既有助手的编码与状态），是否执行由父级决定。
   */
  const props = defineProps<{
    modelValue: boolean
    /** 用户想用的名字（仅用于文案，展示不参与提交） */
    name: string
    /** 409 details 指向的既有助手 */
    existing?: DuplicateNameDetails | null
    duplicating?: boolean
  }>()

  const emit = defineEmits<{
    (e: 'update:modelValue', value: boolean): void
    (e: 'reuse'): void
    (e: 'duplicate'): void
    (e: 'rename'): void
  }>()

  const STATUS_LABEL: Record<string, string> = {
    draft: '草稿',
    pending_platform_review: '平台审核中',
    published: '已发布',
    deprecated: '已停用',
    deleted: '已删除',
  }

  const statusLabel = computed(() => STATUS_LABEL[props.existing?.status ?? ''] ?? '未知状态')
</script>

<style scoped>
  .dup-text {
    margin: 0;
    font-size: 14px;
    line-height: 1.7;
    color: var(--ds-text-1);
  }
  .dup-hint {
    margin: 6px 0 0;
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .dup-actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
  }
  .dup-actions :deep(.el-button + .el-button) {
    margin-left: 0;
  }
</style>
