<template>
  <div class="grant-matrix">
    <el-table
      v-if="items.length"
      :data="items"
      stripe
    >
      <el-table-column
        label="管理员"
        min-width="190"
      >
        <template #default="{ row }">
          <div class="grant-user">
            <span class="grant-name">{{ row.display_name || row.username }}</span>
            <span class="grant-username">@{{ row.username }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column
        v-for="item in GRANT_ITEMS"
        :key="item.key"
        :label="item.label"
        width="150"
      >
        <template #default="{ row }">
          <el-tooltip
            :content="item.hint"
            placement="top"
          >
            <el-switch
              :model-value="isOn(row as GrantRow, item.key)"
              :disabled="!enabled"
              :loading="savingId === row.user_id"
              @change="val => toggle(row as GrantRow, item.key, Boolean(val))"
            />
          </el-tooltip>
        </template>
      </el-table-column>
      <el-table-column
        label="操作"
        width="160"
      >
        <template #default="{ row }">
          <el-button
            size="small"
            type="primary"
            :disabled="!enabled || !isDirty(row as GrantRow)"
            :loading="savingId === row.user_id"
            @click="save(row as GrantRow)"
          >
            保存
          </el-button>
          <el-button
            v-if="isDirty(row as GrantRow)"
            link
            size="small"
            :disabled="savingId === row.user_id"
            @click="reset(row as GrantRow)"
          >
            撤销
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-empty
      v-else
      description="暂无小院管理员。邀请或设置为管理员后，可在此逐项授权"
    />
    <p class="grant-note">
      授权后管理员查看会话正文仍需填写原因并留审计；未授权时敏感数据一律脱敏。
    </p>
  </div>
</template>

<script setup lang="ts">
  import { reactive, watch } from 'vue'
  import type { GrantRow } from '@/api/v3'
  import { GRANT_ITEMS } from './permissions'

  const props = withDefaults(
    defineProps<{
      items: GrantRow[]
      enabled?: boolean
      savingId?: string
    }>(),
    { enabled: true, savingId: '' },
  )

  const emit = defineEmits<{
    (e: 'save', userId: string, grants: string[]): void
  }>()

  /** 每行的草稿授权集合：改动后在行内点「保存」才提交 */
  const draft = reactive<Record<string, string[]>>({})

  function syncDraft(): void {
    const alive = new Set(props.items.map(item => item.user_id))
    Object.keys(draft).forEach(key => {
      if (!alive.has(key)) delete draft[key]
    })
    props.items.forEach(item => {
      draft[item.user_id] = [...(item.grants ?? [])]
    })
  }

  watch(() => props.items, syncDraft, { immediate: true, deep: true })

  function currentGrants(row: GrantRow): string[] {
    if (!draft[row.user_id]) draft[row.user_id] = [...(row.grants ?? [])]
    return draft[row.user_id]
  }

  function isOn(row: GrantRow, key: string): boolean {
    return currentGrants(row).includes(key)
  }

  function toggle(row: GrantRow, key: string, value: boolean): void {
    const list = currentGrants(row)
    const index = list.indexOf(key)
    if (value && index === -1) list.push(key)
    if (!value && index !== -1) list.splice(index, 1)
  }

  function normalized(list: string[]): string {
    return [...list].sort().join(',')
  }

  function isDirty(row: GrantRow): boolean {
    return normalized(currentGrants(row)) !== normalized(row.grants ?? [])
  }

  function reset(row: GrantRow): void {
    draft[row.user_id] = [...(row.grants ?? [])]
  }

  function save(row: GrantRow): void {
    // 保留非三项开关的历史授权，避免保存时被意外丢弃
    const ours = new Set(GRANT_ITEMS.map(item => item.key))
    const rest = (row.grants ?? []).filter(key => !ours.has(key))
    emit('save', row.user_id, [...rest, ...currentGrants(row)].filter((v, i, arr) => arr.indexOf(v) === i))
  }
</script>

<style scoped>
  .grant-user {
    display: flex;
    flex-direction: column;
    line-height: 1.4;
  }
  .grant-name {
    color: var(--ds-text-1);
    font-weight: 500;
  }
  .grant-username {
    color: var(--ds-text-3);
    font-size: 12px;
  }
  .grant-note {
    margin: 10px 0 0;
    color: var(--ds-text-2);
    font-size: 12px;
    line-height: 1.6;
  }
</style>
