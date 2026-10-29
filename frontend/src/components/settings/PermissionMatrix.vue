<template>
  <div class="permission-matrix">
    <section
      v-for="group in groups"
      :key="group.key"
      class="perm-group"
    >
      <header class="perm-group-head">
        <span class="perm-group-title">{{ group.label }}</span>
        <span class="perm-group-count">{{ checkedCount(group) }} / {{ group.permissions.length }}</span>
      </header>
      <el-checkbox-group
        :model-value="modelValue"
        :disabled="disabled"
        class="perm-items"
        @update:model-value="onChange"
      >
        <el-checkbox
          v-for="p in group.permissions"
          :key="p"
          :value="p"
          :disabled="disabled || (!allowProtected && isProtected(p))"
          class="perm-item"
        >
          <span class="perm-label">{{ labelOf(p) }}</span>
          <el-tag
            v-if="isProtected(p)"
            size="small"
            type="info"
            class="perm-tag"
          >
            受保护
          </el-tag>
        </el-checkbox>
      </el-checkbox-group>
    </section>
    <p
      v-if="!groups.length"
      class="perm-empty"
    >
      暂无权限分组
    </p>
  </div>
</template>

<script setup lang="ts">
  import type { PermissionGroup } from '@/api/v3'
  import { PERMISSION_LABELS, PROTECTED_PERMISSION_SET } from './permissions'

  const props = withDefaults(
    defineProps<{
      groups: PermissionGroup[]
      modelValue: string[]
      labels?: Record<string, string>
      disabled?: boolean
      /** 负责人可勾选受保护权限（保存时二次确认） */
      allowProtected?: boolean
    }>(),
    { labels: () => ({}), disabled: false, allowProtected: false },
  )

  const emit = defineEmits<{
    (e: 'update:modelValue', value: string[]): void
  }>()

  function isProtected(code: string): boolean {
    return PROTECTED_PERMISSION_SET.has(code)
  }

  function labelOf(code: string): string {
    return props.labels[code] ?? PERMISSION_LABELS[code] ?? code
  }

  function checkedCount(group: PermissionGroup): number {
    return group.permissions.filter(p => props.modelValue.includes(p)).length
  }

  function onChange(value: unknown[]): void {
    emit(
      'update:modelValue',
      value.map(item => String(item)),
    )
  }
</script>

<style scoped>
  .permission-matrix {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .perm-group {
    padding: 12px 14px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    background: var(--ds-glass);
  }
  .perm-group-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }
  .perm-group-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .perm-group-count {
    font-size: 12px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }
  .perm-items {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
    gap: 4px 12px;
  }
  .perm-item {
    margin-right: 0;
    height: 28px;
  }
  .perm-label {
    font-size: 13px;
  }
  .perm-tag {
    margin-left: 4px;
    transform: scale(0.9);
  }
  .perm-empty {
    margin: 0;
    font-size: 13px;
    color: var(--ds-text-3);
  }
</style>
