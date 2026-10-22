<template>
  <div class="quota">
    <h2 class="page-title">
      用量额度
    </h2>
    <el-alert
      v-for="w in warnings"
      :key="w.kind"
      type="warning"
      :title="w.message"
      show-icon
      class="warn"
    />
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
      <div class="trace-title">最近调用</div>
      <el-table :data="traceRows" size="small" empty-text="暂无">
        <el-table-column label="时间" width="150">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="purpose" label="用途" width="100" />
        <el-table-column prop="agent_ref" label="来源" min-width="120" show-overflow-tooltip />
        <el-table-column prop="model" label="模型" min-width="110" show-overflow-tooltip />
        <el-table-column label="tokens" width="100">
          <template #default="{ row }">{{ (row.input_tokens ?? 0) + (row.output_tokens ?? 0) }}</template>
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
  import { tenantApi, llmTraceApi, type LlmTraceItem, type LlmUsageRow } from '@/api/modules'
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
  const auth = useAuthStore()
  const quotas = reactive<Record<string, QuotaItem>>({})
  const warnings = ref<QuotaWarning[]>([])
  const quotaState = ref<ViewState>('loading')
  /** 顶栏/接口给出的使用空间（单位）名称，替代原来展示的租户 ID */
  const unitsName = computed(() => auth.tenant?.name ?? '当前使用空间')
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
      // 顶栏还没有空间名称时补拉一次（后端 403 时静默忽略）
      if (!auth.tenant?.name) {
        try {
          await auth.fetchTenant()
        } catch {
          /* 保持占位名称 */
        }
      }
      const data = await tenantApi.getQuota()
      Object.keys(quotas).forEach(k => delete quotas[k])
      Object.assign(quotas, data?.quotas ?? {})
      warnings.value = data?.warnings ?? []
      quotaState.value = rows.value.length ? 'ready' : 'empty'
    } catch (e) {
      quotaState.value = classifyError(e)
    }
  }
  /* ---------- 模型调用观测 ---------- */
  const usageRows = ref<LlmUsageRow[]>([])
  const traceRows = ref<LlmTraceItem[]>([])
  async function loadTraces() {
    try {
      const [s, t] = await Promise.all([
        llmTraceApi.summary(7),
        llmTraceApi.traces({ days: 7, limit: 20 }),
      ])
      usageRows.value = s
      traceRows.value = t
    } catch {
      // 观测是辅助信息：失败静默空表，不打扰额度主功能
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
  /* 设计系统 v2：颜色一律走 --ds-* token */
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
  .quota {
    padding: 4px;
    max-width: 960px;
  }
  .warn {
    margin-bottom: 12px;
  }
  .section {
    margin-bottom: 16px;
  }
</style>
