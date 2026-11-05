<template>
  <el-table
    :data="items"
    size="small"
    :default-sort="{ prop: 'journal_count', order: 'descending' }"
    empty-text="还没有绑定小院"
  >
    <el-table-column
      prop="yard_name"
      label="小院"
      min-width="170"
    />
    <el-table-column
      v-if="showGroup"
      prop="group_key"
      :label="groupLabel"
      min-width="140"
    />
    <el-table-column
      label="关系"
      width="96"
    >
      <template #default="{ row }">
        <el-tag
          size="small"
          effect="plain"
          :type="row.relation_type === 'manage' ? 'primary' : 'info'"
        >
          {{ row.relation_label || '日常管理' }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column
      prop="journal_count"
      label="本月日志"
      width="110"
      sortable
    />    <el-table-column
      prop="active_authors"
      label="活跃作者"
      width="100"
      sortable
    />
    <el-table-column
      label="最近更新"
      width="160"
    >
      <template #default="{ row }">
        {{ row.last_updated_at ? formatTime(row.last_updated_at) : '—' }}
      </template>
    </el-table-column>
    <el-table-column
      label="状态"
      width="90"
    >
      <template #default="{ row }">
        <el-tag
          size="small"
          :type="row.stale ? 'danger' : 'success'"
          effect="plain"
        >
          {{ row.stale ? '断更' : '正常' }}
        </el-tag>
      </template>
    </el-table-column>
  </el-table>
</template>

<script setup lang="ts">
  import type { HubOverviewItem } from '@/api/hubs'
  import { formatTime } from '@/utils/format'

  withDefaults(
    defineProps<{
      items: HubOverviewItem[]
      showGroup?: boolean
      groupLabel?: string
    }>(),
    { showGroup: false, groupLabel: '分组' },
  )
</script>
