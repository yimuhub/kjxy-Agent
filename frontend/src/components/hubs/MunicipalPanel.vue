<template>
  <div class="panel">
    <HubStatRow :stats="stats" />

    <el-card shadow="never">
      <template #header>县域覆盖与进度</template>
      <el-table
        :data="insights.groups"
        size="small"
      >
        <el-table-column
          prop="key"
          :label="insights.group_label"
          min-width="160"
        />
        <el-table-column
          prop="yards"
          label="覆盖小院"
          width="100"
        />
        <el-table-column
          prop="journal_count"
          label="本月日志"
          width="100"
        />
        <el-table-column
          label="达标院数"
          width="110"
        >
          <template #default="{ row }">
            {{ row.compliant }} / {{ row.yards }}
          </template>
        </el-table-column>
        <el-table-column
          label="状态"
          width="90"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="row.journal_count > 0 ? 'success' : 'warning'"
              effect="plain"
            >
              {{ row.journal_count > 0 ? '在推进' : '未启动' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card
      v-if="staleYards.length"
      shadow="never"
    >
      <template #header>断更跟进清单（{{ staleYards.length }} 家）</template>
      <div class="stale-list">
        <el-tag
          v-for="yard in staleYards"
          :key="yard.yard_id"
          type="danger"
          effect="plain"
        >
          {{ yard.yard_name }} · {{ yard.group_key || '未标注县域' }}
        </el-tag>
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>所辖小院明细（全域推广 · 按县域分组）</template>
      <HubYardTable
        :items="items"
        show-group
        :group-label="insights.group_label"
      />
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import { computed } from 'vue'
  import type { HubInsights, HubOverviewItem } from '@/api/hubs'
  import HubStatRow from './HubStatRow.vue'
  import HubYardTable from './HubYardTable.vue'

  const props = defineProps<{ insights: HubInsights; items: HubOverviewItem[] }>()

  const stats = computed(() => [
    { label: '覆盖县域', value: props.insights.summary.groups },
    { label: '所辖小院', value: props.insights.summary.yards },
    { label: '本月日志', value: props.insights.summary.journal_count },
    {
      label: '断更小院',
      value: props.insights.summary.stale_count,
      hint: '需县域跟进',
    },
  ])
  const staleYards = computed(() => props.items.filter(i => i.stale))
</script>

<style scoped>
  .panel {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .stale-list {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
</style>
