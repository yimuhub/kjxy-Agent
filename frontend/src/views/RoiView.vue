<template>
  <div
    class="roi-page"
    data-roi-page
  >
    <header class="page-head">
      <div>
        <h2>投入产出</h2>
        <p class="sub">
          平台替小院干了多少活、省了多少时间、花了多少钱——一页看全。
          「节省人时」为估算口径（系数见页脚），给院长汇报时请如实说明。
        </p>
      </div>
      <div class="controls">
        <el-radio-group
          v-model="days"
          size="small"
        >
          <el-radio-button :value="7">近 7 天</el-radio-button>
          <el-radio-button :value="30">近 30 天</el-radio-button>
          <el-radio-button :value="90">近 90 天</el-radio-button>
        </el-radio-group>
        <el-button
          size="small"
          @click="refresh"
        >
          刷新
        </el-button>
      </div>
    </header>

    <AsyncState :state="view">
      <template v-if="data">
        <!-- 核心三数 -->
        <div class="hero-grid">
          <div class="hero-card accent">
            <span class="k">估算节省人时</span>
            <strong class="v">{{ data.saved_hours_estimate.toLocaleString() }} <small>小时</small></strong>
            <span class="s">≈ {{ (data.saved_hours_estimate / 8).toFixed(1) }} 个人日</span>
          </div>
          <div class="hero-card">
            <span class="k">模型投入（近 {{ data.window_days }} 天）</span>
            <strong class="v">¥{{ data.cost.yuan.toLocaleString() }}</strong>
            <span class="s">{{ data.cost.calls.toLocaleString() }} 次调用 · {{ formatTokens(data.cost.tokens_in + data.cost.tokens_out) }} tokens</span>
          </div>
          <div class="hero-card">
            <span class="k">指挥官成功率</span>
            <strong class="v">{{ data.commander_runs.success_rate ?? '—' }}<small v-if="data.commander_runs.success_rate !== null">%</small></strong>
            <span class="s">{{ data.commander_runs.completed }}/{{ data.commander_runs.total }} 轮完成</span>
          </div>
        </div>

        <!-- 效率矩阵 -->
        <div class="grid">
          <div class="card">
            <h4>工单闭环</h4>
            <div class="rows">
              <div class="row">
                <span>累计闭环率</span>
                <strong>{{ data.work_orders.close_rate ?? '—' }}<small>%</small></strong>
              </div>
              <div class="row">
                <span>平均闭环时长</span>
                <strong>{{ data.work_orders.avg_close_hours ?? '—' }}<small> 小时</small></strong>
              </div>
              <div class="row">
                <span>进行中 / 已逾期</span>
                <strong :class="{ danger: data.work_orders.overdue > 0 }">
                  {{ data.work_orders.active }} / {{ data.work_orders.overdue }}
                </strong>
              </div>
              <div class="row">
                <span>累计闭环张数</span>
                <strong>{{ data.work_orders.closed }}</strong>
              </div>
            </div>
          </div>
          <div class="card">
            <h4>诊断时效</h4>
            <div class="rows">
              <div class="row">
                <span>诊断总数</span>
                <strong>{{ data.diagnoses.total }}</strong>
              </div>
              <div class="row">
                <span>已专家复核</span>
                <strong>{{ data.diagnoses.reviewed }}</strong>
              </div>
              <div class="row">
                <span>平均复核时长</span>
                <strong>{{ data.diagnoses.avg_review_hours ?? '—' }}<small> 小时</small></strong>
              </div>
            </div>
          </div>
          <div class="card">
            <h4>知识资产沉淀</h4>
            <div class="rows">
              <div class="row">
                <span>农事记录</span>
                <strong>{{ data.assets.farm_records.toLocaleString() }}</strong>
              </div>
              <div class="row">
                <span>知识文档</span>
                <strong>{{ data.assets.knowledge_docs.toLocaleString() }}</strong>
              </div>
              <div class="row">
                <span>指挥官消耗 Token</span>
                <strong>{{ formatTokens(data.commander_runs.tokens) }}</strong>
              </div>
            </div>
          </div>
        </div>

        <footer class="assume">
          人时折算口径：闭环工单 ×{{ data.saved_hours_assumptions.closed_order_hours }} 小时 +
          复核诊断 ×{{ data.saved_hours_assumptions.reviewed_diagnosis_hours }} 小时 +
          完成 run ×{{ data.saved_hours_assumptions.completed_run_hours }} 小时。
          系数为运营估算值，可在服务端常量层调整。
        </footer>
      </template>
    </AsyncState>
  </div>
</template>

<script setup lang="ts">
  /**
   * 投入产出看板（v3.71）：审查报告 P0 项。
   * 数据全部来自 /yards/current/owner/roi（insights:read 权限），
   * 目标是给院长/负责人一页讲清「平台带来的回报」。
   */
  import { onMounted, ref, watch } from 'vue'
  import { ownerApi, type RoiDashboard } from '@/api/v3'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  const days = ref(30)
  const data = ref<RoiDashboard | null>(null)
  const view = ref<ViewState>('loading')

  function formatTokens(value: number): string {
    if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
    if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`
    return String(value)
  }

  async function refresh(): Promise<void> {
    view.value = 'loading'
    try {
      data.value = await ownerApi.roi(days.value)
      view.value = 'ready'
    } catch (err) {
      view.value = classifyError(err)
    }
  }

  watch(days, refresh)
  onMounted(refresh)
</script>

<style scoped>
  .roi-page {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  .page-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }

  .page-head h2 {
    margin: 0 0 4px;
    font-size: 20px;
  }

  .sub {
    margin: 0;
    color: var(--ds-text-3);
    font-size: 13px;
    max-width: 560px;
  }

  .controls {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .hero-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    gap: 12px;
  }

  .hero-card {
    display: flex;
    flex-direction: column;
    gap: 4px;
    padding: 16px 18px;
    border: 1px solid var(--ds-border);
    border-radius: 14px;
    background: var(--ds-bg-1);
  }

  .hero-card.accent {
    border-color: color-mix(in srgb, var(--ds-accent) 45%, transparent);
    background: color-mix(in srgb, var(--ds-accent) 8%, var(--ds-bg-1));
  }

  .hero-card .k {
    color: var(--ds-text-3);
    font-size: 12.5px;
  }

  .hero-card .v {
    font-size: 28px;
    font-weight: 800;
    line-height: 1.2;
  }

  .hero-card .v small {
    font-size: 13px;
    font-weight: 400;
    color: var(--ds-text-3);
  }

  .hero-card .s {
    color: var(--ds-text-3);
    font-size: 12px;
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
    gap: 12px;
  }

  .card {
    padding: 14px 16px;
    border: 1px solid var(--ds-border);
    border-radius: 12px;
    background: var(--ds-bg-1);
  }

  .card h4 {
    margin: 0 0 10px;
    font-size: 13.5px;
    color: var(--ds-text-1);
  }

  .rows {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    font-size: 13px;
  }

  .row span {
    color: var(--ds-text-3);
  }

  .row strong {
    font-size: 15px;
  }

  .row strong.danger {
    color: var(--ds-danger);
  }

  .row strong small {
    font-size: 12px;
    font-weight: 400;
    color: var(--ds-text-3);
  }

  .assume {
    color: var(--ds-text-3);
    font-size: 12px;
    border-top: 1px dashed var(--ds-border);
    padding-top: 10px;
  }
</style>
