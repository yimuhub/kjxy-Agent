<template>
  <div class="usage-trend">
    <div class="trend-head">
      <div class="legend">
        <span class="legend-item"><i class="dot dot-token" />Token</span>
        <span class="legend-item"><i class="dot dot-message" />消息</span>
      </div>
      <span class="trend-range">{{ rangeText }}</span>
    </div>

    <div
      v-if="rows.length"
      class="chart"
    >
      <div
        v-for="row in rows"
        :key="row.date"
        class="chart-col"
      >
        <div class="bars">
          <div
            class="bar bar-token"
            :style="{ height: barHeight(row.tokens, maxTokens) }"
            :title="`${row.label} Token：${formatNumber(row.tokens)}（输入 ${formatNumber(row.input)} / 输出 ${formatNumber(row.output)}）`"
          />
          <div
            class="bar bar-message"
            :style="{ height: barHeight(row.messages, maxMessages) }"
            :title="`${row.label} 消息：${formatNumber(row.messages)} 条`"
          />
        </div>
        <div class="chart-date">
          {{ row.label }}
        </div>
        <div class="chart-value">
          {{ formatNumber(row.tokens) }}
        </div>
        <div class="chart-sub">
          {{ formatNumber(row.messages) }} 条
        </div>
      </div>
    </div>
    <el-empty
      v-else
      description="近 7 天暂无用量数据"
    />

    <div
      v-if="rows.length"
      class="trend-foot"
    >
      <span>合计 Token：{{ formatNumber(totalTokens) }}</span>
      <span>合计消息：{{ formatNumber(totalMessages) }} 条</span>
      <span>单日峰值 Token：{{ formatNumber(maxTokens) }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { computed } from 'vue'
  import type { DailyUsageRow } from '@/api/v3'

  const props = withDefaults(defineProps<{ items: DailyUsageRow[] }>(), { items: () => [] })

  function num(value: unknown): number {
    const parsed = Number(value ?? 0)
    return Number.isFinite(parsed) ? parsed : 0
  }

  function formatNumber(value: number): string {
    return new Intl.NumberFormat('zh-CN').format(Math.round(value))
  }

  interface TrendRow {
    date: string
    label: string
    tokens: number
    input: number
    output: number
    messages: number
  }

  const rows = computed<TrendRow[]>(() =>
    props.items.map(item => {
      const date = String(item.date ?? '')
      return {
        date,
        label: date.length >= 10 ? date.slice(5, 10) : date || '—',
        tokens: num(item.total_tokens),
        input: num(item.input_tokens),
        output: num(item.output_tokens),
        messages: num(item.messages),
      }
    }),
  )

  const maxTokens = computed(() => Math.max(1, ...rows.value.map(row => row.tokens)))
  const maxMessages = computed(() => Math.max(1, ...rows.value.map(row => row.messages)))
  const totalTokens = computed(() => rows.value.reduce((sum, row) => sum + row.tokens, 0))
  const totalMessages = computed(() => rows.value.reduce((sum, row) => sum + row.messages, 0))
  const rangeText = computed(() => {
    if (!rows.value.length) return ''
    return `${rows.value[0].date} 至 ${rows.value[rows.value.length - 1].date}`
  })

  function barHeight(value: number, max: number): string {
    if (value <= 0) return '0%'
    return `${Math.max(3, (value / max) * 100)}%`
  }
</script>

<style scoped>
  .usage-trend {
    width: 100%;
  }
  .trend-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }
  .legend {
    display: flex;
    align-items: center;
    gap: 16px;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .legend-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 2px;
    display: inline-block;
  }
  .dot-token {
    background: var(--ds-grad-brand);
  }
  .dot-message {
    background: var(--ds-accent-2);
    opacity: 0.7;
  }
  .trend-range {
    font-size: 12px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }
  .chart {
    display: flex;
    align-items: flex-end;
    gap: 10px;
    height: 210px;
    padding: 8px 4px 0;
    border-bottom: 1px solid var(--ds-border);
  }
  .chart-col {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    height: 100%;
  }
  .bars {
    flex: 1;
    width: 100%;
    display: flex;
    align-items: flex-end;
    justify-content: center;
    gap: 4px;
    min-height: 0;
  }
  .bar {
    width: 28%;
    max-width: 22px;
    border-radius: 5px 5px 0 0;
    transition: height 0.25s ease;
    cursor: default;
  }
  .bar-token {
    background: linear-gradient(180deg, #22d3ee 0%, #6366f1 100%);
    box-shadow: 0 0 12px rgba(99, 102, 241, 0.35);
  }
  .bar-message {
    background: rgba(34, 211, 238, 0.45);
  }
  .chart-date {
    margin-top: 6px;
    font-size: 11px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }
  .chart-value {
    font-size: 11px;
    color: var(--ds-text-2);
    font-variant-numeric: tabular-nums;
  }
  .chart-sub {
    font-size: 10px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }
  .trend-foot {
    display: flex;
    flex-wrap: wrap;
    gap: 18px;
    margin-top: 10px;
    font-size: 12px;
    color: var(--ds-text-2);
    font-variant-numeric: tabular-nums;
  }
</style>
