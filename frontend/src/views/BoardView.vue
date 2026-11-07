<template>
  <div
    class="board"
    data-board-page
  >
    <header class="board-head">
      <div>
        <h2>{{ yardName }} · 运行大屏</h2>
        <span class="clock">{{ clock }}</span>
      </div>
      <span class="hint">投在电视/大屏上，给来访与晨会用；数据 {{ days }} 天窗口</span>
    </header>

    <div class="tiles">
      <div class="tile">
        <span class="k">估算节省人时</span>
        <strong class="v accent">{{ roi?.saved_hours_estimate.toLocaleString() ?? '—' }}<small> 小时</small></strong>
      </div>
      <div class="tile">
        <span class="k">工单闭环率</span>
        <strong class="v">{{ roi?.work_orders.close_rate ?? '—' }}<small v-if="roi?.work_orders.close_rate !== null && roi?.work_orders.close_rate !== undefined">%</small></strong>
        <span class="s">逾期 {{ roi?.work_orders.overdue ?? '—' }} 张</span>
      </div>
      <div class="tile">
        <span class="k">诊断已复核</span>
        <strong class="v">{{ roi?.diagnoses.reviewed ?? '—' }}<small> / {{ roi?.diagnoses.total ?? '—' }}</small></strong>
      </div>
      <div class="tile">
        <span class="k">指挥官成功率</span>
        <strong class="v">{{ roi?.commander_runs.success_rate ?? '—' }}<small v-if="roi?.commander_runs.success_rate !== null && roi?.commander_runs.success_rate !== undefined">%</small></strong>
      </div>
      <div class="tile">
        <span class="k">模型投入</span>
        <strong class="v">¥{{ roi?.cost.yuan.toLocaleString() ?? '—' }}</strong>
        <span class="s">{{ roi?.cost.calls.toLocaleString() ?? '—' }} 次调用</span>
      </div>
      <div class="tile">
        <span class="k">知识资产</span>
        <strong class="v">{{ ((roi?.assets.farm_records ?? 0) + (roi?.assets.knowledge_docs ?? 0)).toLocaleString() }}</strong>
        <span class="s">农事记录 + 文档</span>
      </div>
    </div>

    <div class="charts">
      <div class="chart-card wide">
        <h4>近 {{ days }} 天 Token 消耗</h4>
        <UsageTrendChart
          v-if="rows.length"
          :points="rows"
          :height="260"
        />
        <p
          v-else
          class="empty"
        >
          暂无用量数据
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
  /**
   * 数据大屏（v3.71，轻量版）：复用 ROI 聚合 + 每日用量趋势，
   * 一页铺满大屏——审查报告「数据大屏/汇报出口弱」的最小闭环。
   * 自动 60s 刷新，适配暗色。
   */
  import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
  import { ownerApi, type RoiDashboard } from '@/api/v3'
  import { useAuthStore } from '@/stores/auth'
  import UsageTrendChart from '@/components/usage/UsageTrendChart.vue'

  const auth = useAuthStore()
  const days = ref(14)
  const roi = ref<RoiDashboard | null>(null)
  const rows = ref<{ day: string; total_tokens: number; calls: number }[]>([])
  const clock = ref('')
  let timer: number | null = null
  let clockTimer: number | null = null

  const yardName = computed(() => auth.user?.display_name ?? '科技小院')

  async function refresh(): Promise<void> {
    try {
      const [roiData, usage] = await Promise.all([ownerApi.roi(days.value), ownerApi.dailyUsage(days.value)])
      roi.value = roiData
      rows.value = usage.items.map(item => ({
        day: item.date,
        total_tokens: item.total_tokens,
        calls: item.sessions,
      }))
    } catch {
      /* 大屏只读，失败保留上一帧 */
    }
  }

  function tickClock(): void {
    clock.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  }

  onMounted(() => {
    void refresh()
    timer = window.setInterval(() => void refresh(), 60_000)
    tickClock()
    clockTimer = window.setInterval(tickClock, 1000)
  })

  onBeforeUnmount(() => {
    if (timer !== null) window.clearInterval(timer)
    if (clockTimer !== null) window.clearInterval(clockTimer)
  })
</script>

<style scoped>
  .board {
    min-height: 100%;
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .board-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }

  .board-head h2 {
    margin: 0;
    font-size: 22px;
  }

  .clock {
    margin-left: 12px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }

  .hint {
    color: var(--ds-text-3);
    font-size: 12.5px;
  }

  .tiles {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
    gap: 12px;
  }

  .tile {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 18px 20px;
    border: 1px solid var(--ds-border);
    border-radius: 14px;
    background: var(--ds-bg-1);
  }

  .tile .k {
    color: var(--ds-text-3);
    font-size: 12.5px;
  }

  .tile .v {
    font-size: 30px;
    font-weight: 800;
    line-height: 1.15;
  }

  .tile .v.accent {
    color: var(--ds-accent);
  }

  .tile .v small {
    font-size: 13px;
    font-weight: 400;
    color: var(--ds-text-3);
  }

  .tile .s {
    color: var(--ds-text-3);
    font-size: 12px;
  }

  .chart-card {
    border: 1px solid var(--ds-border);
    border-radius: 14px;
    background: var(--ds-bg-1);
    padding: 14px 16px;
  }

  .chart-card h4 {
    margin: 0 0 8px;
    font-size: 13.5px;
  }

  .empty {
    color: var(--ds-text-3);
    font-size: 12.5px;
    text-align: center;
    padding: 40px 0;
  }
</style>
