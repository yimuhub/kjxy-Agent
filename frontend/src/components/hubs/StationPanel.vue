<template>
  <div class="panel">
    <HubStatRow :stats="stats" />

    <el-card shadow="never">
      <template #header>小院类型分布（一院一策）</template>
      <div class="type-cloud">
        <div
          v-for="group in insights.groups"
          :key="group.key"
          class="type-chip"
        >
          <span class="type-name">{{ group.key }}</span>
          <span class="type-count">{{ group.yards }} 家</span>
          <span class="type-meta">本月 {{ group.journal_count }} 篇</span>
        </div>
        <el-empty
          v-if="!insights.groups.length"
          description="还没有绑定小院"
          :image-size="60"
        />
      </div>
    </el-card>

    <el-card shadow="never">
      <template #header>小院群横向对比</template>
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
    { label: '小院群', value: props.insights.summary.yards },
    { label: '类型数', value: props.insights.summary.groups },
    { label: '本月日志', value: props.insights.summary.journal_count },
    {
      label: '达标小院',
      value: `${props.insights.summary.compliant} / ${props.insights.summary.yards}`,
      hint: `月均 ${props.insights.anchor_monthly} 篇为达标`,
    },
  ])
</script>

<style scoped>
  .panel {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .type-cloud {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
  .type-chip {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 120px;
    padding: 10px 12px;
    border: 1px solid var(--ds-border, #e5e7eb);
    border-radius: var(--ds-radius-md, 10px);
    background: var(--ds-surface, #fafafa);
  }
  .type-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--ds-text-1, #1f2937);
  }
  .type-count {
    font-size: 18px;
    color: var(--ds-text-1, #1f2937);
  }
  .type-meta {
    font-size: 11.5px;
    color: var(--ds-text-3, #9ca3af);
  }
</style>
