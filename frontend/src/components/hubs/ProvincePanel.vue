<template>
  <div class="panel">
    <div class="hero">
      <div class="hero-main">
        <span class="hero-value">{{ ratePercent }}%</span>
        <span class="hero-label">本月达标率</span>
      </div>
      <p class="hero-note">
        制度锚点：每院每年 {{ insights.anchor_annual }} 篇日志，折算月度
        {{ insights.anchor_monthly }} 篇；{{ insights.summary.compliant }} /
        {{ insights.summary.yards }} 家达标。
      </p>
    </div>

    <HubStatRow :stats="stats" />

    <el-card shadow="never">
      <template #header>按{{ insights.group_label }}看达标进度</template>
      <el-table
        v-col-resize
        :data="insights.groups"
        size="small"
      >
        <el-table-column
          prop="key"
          :label="insights.group_label"
          min-width="180"
        />
        <el-table-column
          prop="yards"
          label="所辖小院"
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
          label="达标率"
          min-width="160"
        >
          <template #default="{ row }">
            <el-progress
              :percentage="groupRate(row)"
              :stroke-width="6"
              :show-text="false"
            />
            <span class="rate-text">{{ groupRate(row) }}%</span>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card shadow="never">
      <template #header>所辖小院明细（跨市分散 · 按培养单位聚合）</template>
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

  const ratePercent = computed(() => Math.round(props.insights.summary.compliance_rate * 100))
  const stats = computed(() => [
    { label: '所辖小院', value: props.insights.summary.yards },
    { label: '培养单位', value: props.insights.summary.groups },
    { label: '本月日志', value: props.insights.summary.journal_count },
    {
      label: '断更小院',
      value: props.insights.summary.stale_count,
      hint: `超过 7 天未更新`,
    },
  ])

  function groupRate(row: Record<string, unknown>): number {
    const yards = Number(row.yards ?? 0)
    const compliant = Number(row.compliant ?? 0)
    return yards ? Math.round((compliant / yards) * 100) : 0
  }
</script>

<style scoped>
  .panel {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .hero {
    display: flex;
    align-items: center;
    gap: 20px;
    padding: 16px 18px;
    border: 1px solid var(--ds-border, #e5e7eb);
    border-radius: var(--ds-radius-md, 10px);
    background: var(--ds-bg-1, #fafafa);
  }
  .hero-main {
    display: flex;
    flex-direction: column;
    min-width: 120px;
  }
  .hero-value {
    font-size: 30px;
    font-weight: 500;
    line-height: 1.1;
    color: var(--ds-text-1, #1f2937);
  }
  .hero-label {
    font-size: 12px;
    color: var(--ds-text-3, #6b7280);
  }
  .hero-note {
    margin: 0;
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--ds-text-2, #4b5563);
  }
  .rate-text {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3, #6b7280);
  }
</style>
