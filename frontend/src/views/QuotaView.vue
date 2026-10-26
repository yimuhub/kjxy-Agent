<template>
  <div class="quota">
    <h2 class="page-title">
      用量额度与 Token 统计
    </h2>
    <el-alert
      v-for="w in warnings"
      :key="w.kind"
      type="warning"
      :title="w.message"
      show-icon
      class="warn"
    />

    <!-- 全场景 Token 统计看板（全员可见） -->
    <div
      v-if="overview"
      class="overview-grid"
    >
      <div class="ov-card">
        <span class="ov-label">今日消耗 Token</span>
        <strong class="ov-val token">⚡ {{ overview.today_tokens.toLocaleString() }}</strong>
        <span class="ov-sub">今日调用 {{ overview.today_calls }} 次</span>
      </div>
      <div class="ov-card">
        <span class="ov-label">近 30 天累计 Token</span>
        <strong class="ov-val">{{ overview.total_tokens.toLocaleString() }}</strong>
        <span class="ov-sub">累计调用 {{ overview.total_calls }} 次</span>
      </div>
      <div class="ov-card">
        <span class="ov-label">近 30 天输入 Token</span>
        <strong class="ov-val">{{ overview.total_input_tokens.toLocaleString() }}</strong>
        <span class="ov-sub">Prompt / 知识检索上下文</span>
      </div>
      <div class="ov-card">
        <span class="ov-label">近 30 天输出 Token</span>
        <strong class="ov-val">{{ overview.total_output_tokens.toLocaleString() }}</strong>
        <span class="ov-sub">模型生成内容</span>
      </div>
    </div>

    <el-row
      v-if="overview"
      :gutter="16"
      class="section"
    >
      <el-col
        :xs="24"
        :md="12"
      >
        <el-card shadow="never">
          <template #header>
            按场景 Token 消耗（近 30 天）
          </template>
          <el-table
            :data="overview.by_purpose"
            size="small"
            empty-text="暂无分场景消耗记录"
          >
            <el-table-column
              label="使用场景"
              min-width="110"
            >
              <template #default="{ row }">
                {{ purposeLabel(row.purpose) }}
              </template>
            </el-table-column>
            <el-table-column
              prop="calls"
              label="调用次数"
              width="85"
            />
            <el-table-column
              prop="input_tokens"
              label="输入"
              width="90"
            />
            <el-table-column
              prop="output_tokens"
              label="输出"
              width="90"
            />
            <el-table-column
              label="总 Tokens"
              width="110"
            >
              <template #default="{ row }">
                <span class="token-num">⚡ {{ row.total_tokens.toLocaleString() }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col
        :xs="24"
        :md="12"
      >
        <el-card shadow="never">
          <template #header>
            按智能体 Token 消耗排行（近 30 天）
          </template>
          <el-table
            :data="overview.by_agent"
            size="small"
            empty-text="暂无智能体消耗记录"
          >
            <el-table-column
              prop="agent_ref"
              label="智能体 / 模块"
              min-width="130"
              show-overflow-tooltip
            />
            <el-table-column
              prop="calls"
              label="调用次数"
              width="85"
            />
            <el-table-column
              prop="input_tokens"
              label="输入"
              width="90"
            />
            <el-table-column
              prop="output_tokens"
              label="输出"
              width="90"
            />
            <el-table-column
              label="总 Tokens"
              width="110"
            >
              <template #default="{ row }">
                <span class="token-num">⚡ {{ row.total_tokens.toLocaleString() }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="section">
      <template #header>
        模型调用观测（近 7 天）
      </template>
      <el-table :data="usageRows" size="small" empty-text="暂无调用记录">
        <el-table-column prop="day" label="日期" width="110" />
        <el-table-column prop="model" label="模型" min-width="140" show-overflow-tooltip />
        <el-table-column prop="calls" label="次数" width="70" />
        <el-table-column prop="tokens" label="总 tokens" width="100" />
        <el-table-column label="平均延迟" width="100">
          <template #default="{ row }">{{ row.avg_latency_ms }} ms</template>
        </el-table-column>
        <el-table-column prop="errors" label="错误" width="70">
          <template #default="{ row }">
            <span :class="{ err: row.errors > 0 }">{{ row.errors }}</span>
          </template>
        </el-table-column>
      </el-table>
      <div class="trace-title">最近调用流水</div>
      <el-table :data="traceRows" size="small" empty-text="暂无">
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="用途" width="110">
          <template #default="{ row }">{{ purposeLabel(row.purpose) }}</template>
        </el-table-column>
        <el-table-column prop="agent_ref" label="来源" min-width="120" show-overflow-tooltip />
        <el-table-column prop="model" label="模型" min-width="110" show-overflow-tooltip />
        <el-table-column label="输入 / 输出 / 总计" width="160">
          <template #default="{ row }">
            {{ row.input_tokens ?? 0 }} / {{ row.output_tokens ?? 0 }} /
            <b>{{ (row.input_tokens ?? 0) + (row.output_tokens ?? 0) }}</b>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tooltip v-if="row.prompt_digest" :content="row.prompt_digest" placement="top">
              <el-tag :type="row.status === 'ok' ? 'success' : 'danger'" size="small">
                {{ row.status === 'ok' ? '成功' : '失败' }}
              </el-tag>
            </el-tooltip>
            <el-tag v-else :type="row.status === 'ok' ? 'success' : 'danger'" size="small">
              {{ row.status === 'ok' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card class="section">
      <template #header>
        用量额度（{{ unitsName }}）
      </template>
      <AsyncState
        :state="quotaState"
        empty-text="暂无配额信息"
        @retry="loadQuota"
      >
        <el-table
          :data="rows"
          stripe
        >
          <el-table-column
            prop="label"
            label="额度类型"
            width="180"
          />
          <el-table-column
            prop="used"
            label="已用"
            width="120"
          />
          <el-table-column
            prop="limit"
            label="上限"
            width="120"
          />
          <el-table-column
            prop="remaining"
            label="剩余"
            width="120"
          />
          <el-table-column label="使用率">
            <template #default="{ row }">
              <el-progress
                :percentage="row.usage_percent"
                :status="progressStatus(row.usage_percent)"
                :stroke-width="12"
              />
            </template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="140"
          >
            <template #default="{ row }">
              <el-tag
                v-if="row.usage_percent >= 90"
                type="danger"
                size="small"
              >
                即将用尽
              </el-tag><el-tag
                v-else
                type="success"
                size="small"
              >
                充足
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </AsyncState>
    </el-card>
  </div>
</template>
<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import {
    yardApi,
    llmTraceApi,
    type LlmTraceItem,
    type LlmUsageOverview,
    type LlmUsageRow,
  } from '@/api/modules'
  import { formatTime } from '@/utils/format'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'
  import { useAuthStore } from '@/stores/auth'

  interface QuotaItem {
    limit: number
    used: number
    remaining: number
    usage_percent: number
  }
  interface QuotaWarning {
    kind: string
    level: string
    message: string
  }
  interface QuotaRow extends QuotaItem {
    kind: string
    label: string
  }
  const KIND_LABELS: Record<string, string> = {
    llm_calls: '模型调用',
    knowledge_docs: '知识库文档',
    tasks: '任务',
  }
  const PURPOSE_LABELS: Record<string, string> = {
    chat: '智能对话',
    qa: '知识问答',
    agent: '智能体执行',
    team: '班组接力',
    supervisor: '总控路由',
    factory: '智能体工厂',
    embedding: '知识向量化',
    evolution: '技能自生长',
  }
  function purposeLabel(p: string): string {
    return PURPOSE_LABELS[p] ?? p
  }

  const auth = useAuthStore()
  const quotas = reactive<Record<string, QuotaItem>>({})
  const warnings = ref<QuotaWarning[]>([])
  const quotaState = ref<ViewState>('loading')
  const overview = ref<LlmUsageOverview | null>(null)

  const unitsName = computed(() => auth.yard?.name ?? '当前小院')
  const rows = computed<QuotaRow[]>(() =>
    Object.entries(quotas).map(([kind, item]) => ({
      kind,
      label: KIND_LABELS[kind] ?? kind,
      ...item,
    })),
  )
  function progressStatus(percent: number): 'success' | 'warning' | 'exception' | undefined {
    if (percent >= 90) return 'exception'
    if (percent >= 70) return 'warning'
    return undefined
  }
  async function loadQuota() {
    quotaState.value = 'loading'
    try {
      if (!auth.yard?.name) {
        try {
          await auth.fetchYard()
        } catch {
          /* ignore */
        }
      }
      const data = await yardApi.getQuota()
      Object.keys(quotas).forEach(k => delete quotas[k])
      Object.assign(quotas, data?.quotas ?? {})
      warnings.value = data?.warnings ?? []
      quotaState.value = rows.value.length ? 'ready' : 'empty'
    } catch (e) {
      quotaState.value = classifyError(e)
    }
  }

  const usageRows = ref<LlmUsageRow[]>([])
  const traceRows = ref<LlmTraceItem[]>([])
  async function loadTraces() {
    try {
      overview.value = await llmTraceApi.overview(30)
    } catch {
      overview.value = null
    }
    try {
      const [s, t] = await Promise.all([
        llmTraceApi.summary(7),
        llmTraceApi.traces({ days: 7, limit: 20 }),
      ])
      usageRows.value = s
      traceRows.value = t
    } catch {
      usageRows.value = []
      traceRows.value = []
    }
  }

  onMounted(() => {
    void loadQuota()
    void loadTraces()
  })
</script>
<style scoped>
  .quota {
    padding: 4px;
    max-width: 1080px;
  }
  .overview-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
    margin-bottom: 16px;
  }
  .ov-card {
    padding: 14px 16px;
    border-radius: 12px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .ov-label {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .ov-val {
    font-size: 22px;
    font-weight: 700;
    color: var(--ds-text-1);
    font-variant-numeric: tabular-nums;
  }
  .ov-val.token {
    color: #34d399;
  }
  .ov-sub {
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
  .token-num {
    color: #34d399;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .trace-title {
    margin: 12px 0 6px;
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.04em;
    color: var(--ds-text-2);
  }
  .err {
    color: var(--ds-danger);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .warn {
    margin-bottom: 12px;
  }
  .section {
    margin-bottom: 16px;
  }
</style>
