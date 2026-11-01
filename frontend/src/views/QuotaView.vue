<template>
  <div class="quota">
    <div class="page-head">
      <div class="head-text">
        <h2 class="page-title">
          用量额度与 Token 统计
        </h2>
        <p class="page-sub">
          全场景计量口径为 UTC；趋势与排行窗口可切换，超出固定高度的内容在表格内滚动浏览。
        </p>
      </div>
      <div class="head-actions">
        <el-radio-group
          v-model="range"
          size="small"
          :disabled="overviewLoading"
        >
          <el-radio-button
            v-for="opt in RANGE_OPTIONS"
            :key="opt.value"
            :value="opt.value"
          >
            {{ opt.label }}
          </el-radio-button>
        </el-radio-group>
        <el-button
          size="small"
          :disabled="!overview"
          @click="exportStats"
        >
          导出统计 CSV
        </el-button>
        <el-button
          size="small"
          :disabled="!traceRows.length"
          @click="exportTraces"
        >
          导出调用流水 CSV
        </el-button>
      </div>
    </div>

    <el-tabs
      v-model="tab"
      class="quota-tabs"
    >
      <el-tab-pane
        label="概览"
        name="overview"
      >
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
        <span class="ov-sub">今日调用 {{ overview.today_calls.toLocaleString() }} 次</span>
      </div>
      <div class="ov-card">
        <span class="ov-label">近 {{ range }} 天累计 Token</span>
        <strong class="ov-val">{{ overview.total_tokens.toLocaleString() }}</strong>
        <span class="ov-sub">累计调用 {{ overview.total_calls.toLocaleString() }} 次</span>
      </div>
      <div class="ov-card">
        <span class="ov-label">近 {{ range }} 天输入 Token</span>
        <strong class="ov-val">{{ overview.total_input_tokens.toLocaleString() }}</strong>
        <span class="ov-sub">Prompt / 知识检索上下文</span>
      </div>
      <div class="ov-card">
        <span class="ov-label">近 {{ range }} 天输出 Token</span>
        <strong class="ov-val">{{ overview.total_output_tokens.toLocaleString() }}</strong>
        <span class="ov-sub">模型生成内容</span>
      </div>
    </div>

    <el-card
      v-loading="overviewLoading"
      class="section trend-card"
      shadow="never"
    >
      <template #header>
        <div class="card-head">
          <span>Token 消耗趋势（近 {{ range }} 天）</span>
          <span class="card-hint">柱：Token 总量 · 折线：调用次数</span>
        </div>
      </template>
      <UsageTrendChart
        v-if="overview && overview.by_day.length"
        :points="overview.by_day"
        :height="300"
      />
      <div
        v-else
        class="chart-empty"
      >
        暂无趋势数据
      </div>
    </el-card>

    <el-row
      v-if="overview"
      :gutter="16"
      class="section row-fixed"
    >
      <el-col
        :xs="24"
        :md="12"
      >
        <el-card
          class="fixed-card"
          shadow="never"
        >
          <template #header>
            <div class="card-head">
              <span>按场景 Token 消耗</span>
              <span class="card-hint">近 {{ range }} 天</span>
            </div>
          </template>
          <el-table
            v-col-resize
            :data="overview.by_purpose"
            :height="320"
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
            <el-table-column
              label="占比"
              min-width="120"
            >
              <template #default="{ row }">
                <el-progress
                  :percentage="share(row.total_tokens)"
                  :stroke-width="8"
                  :show-text="false"
                  color="#34d399"
                />
                <span class="share-text">{{ share(row.total_tokens) }}%</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>

      <el-col
        :xs="24"
        :md="12"
      >
        <el-card
          class="fixed-card"
          shadow="never"
        >
          <template #header>
            <div class="card-head">
              <span>按智能体 Token 消耗排行</span>
              <span class="card-hint">近 {{ range }} 天</span>
            </div>
          </template>
          <el-table
            v-col-resize
            :data="overview.by_agent"
            :height="320"
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
            <el-table-column
              label="占比"
              min-width="120"
            >
              <template #default="{ row }">
                <el-progress
                  :percentage="share(row.total_tokens)"
                  :stroke-width="8"
                  :show-text="false"
                  color="#60a5fa"
                />
                <span class="share-text">{{ share(row.total_tokens) }}%</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-row
      v-if="overview"
      :gutter="16"
      class="section row-fixed"
    >
      <el-col
        :xs="24"
        :md="12"
      >
        <el-card
          class="fixed-card"
          shadow="never"
        >
          <template #header>
            <div class="card-head">
              <span>按模型 Token 消耗</span>
              <span class="card-hint">近 {{ range }} 天</span>
            </div>
          </template>
          <el-table
            v-col-resize
            :data="overview.by_model"
            :height="320"
            size="small"
            empty-text="暂无模型消耗记录"
          >
            <el-table-column
              prop="model"
              label="模型"
              min-width="140"
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

      <el-col
        :xs="24"
        :md="12"
      >
        <el-card
          class="fixed-card"
          shadow="never"
        >
          <template #header>
            <div class="card-head">
              <span>模型调用观测</span>
              <span class="card-hint">按日 × 模型</span>
            </div>
          </template>
          <el-table
            v-col-resize
            :data="usageRows"
            :height="320"
            size="small"
            empty-text="暂无调用记录"
          >
            <el-table-column
              prop="day"
              label="日期"
              width="110"
            />
            <el-table-column
              prop="model"
              label="模型"
              min-width="130"
              show-overflow-tooltip
            />
            <el-table-column
              prop="calls"
              label="次数"
              width="70"
            />
            <el-table-column
              prop="tokens"
              label="总 tokens"
              width="100"
            />
            <el-table-column
              label="平均延迟"
              width="100"
            >
              <template #default="{ row }">
                {{ row.avg_latency_ms }} ms
              </template>
            </el-table-column>
            <el-table-column
              prop="errors"
              label="错误"
              width="70"
            >
              <template #default="{ row }">
                <span :class="{ err: row.errors > 0 }">{{ row.errors }}</span>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-card
      class="section"
      shadow="never"
    >
      <template #header>
        <div class="card-head">
          <span>最近调用流水</span>
          <span class="card-hint">近 {{ range }} 天 · 最多 100 条</span>
        </div>
      </template>
      <el-table
        v-col-resize
        :data="traceRows"
        :height="360"
        size="small"
        empty-text="暂无调用流水"
      >
        <el-table-column
          label="时间"
          width="150"
        >
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column
          label="用途"
          width="110"
        >
          <template #default="{ row }">
            {{ purposeLabel(row.purpose) }}
          </template>
        </el-table-column>
        <el-table-column
          prop="agent_ref"
          label="来源"
          min-width="120"
          show-overflow-tooltip
        />
        <el-table-column
          prop="model"
          label="模型"
          min-width="110"
          show-overflow-tooltip
        />
        <el-table-column
          label="输入 / 输出 / 总计"
          width="160"
        >
          <template #default="{ row }">
            {{ row.input_tokens ?? 0 }} / {{ row.output_tokens ?? 0 }} /
            <b>{{ (row.input_tokens ?? 0) + (row.output_tokens ?? 0) }}</b>
          </template>
        </el-table-column>
        <el-table-column
          label="状态"
          width="80"
        >
          <template #default="{ row }">
            <el-tooltip
              v-if="row.prompt_digest"
              :content="row.prompt_digest"
              placement="top"
            >
              <el-tag
                :type="row.status === 'ok' ? 'success' : 'danger'"
                size="small"
              >
                {{ row.status === 'ok' ? '成功' : '失败' }}
              </el-tag>
            </el-tooltip>
            <el-tag
              v-else
              :type="row.status === 'ok' ? 'success' : 'danger'"
              size="small"
            >
              {{ row.status === 'ok' ? '成功' : '失败' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card
      class="section"
      shadow="never"
    >
      <template #header>
        <div class="card-head">
          <span>用量额度（{{ unitsName }}）</span>
          <div
            v-if="canManageQuota"
            class="quota-actions"
          >
            <span class="card-hint">上限为 0 表示停用该额度</span>
            <el-button
              size="small"
              @click="openQuotaDialog"
            >
              调整额度
            </el-button>
            <el-button
              size="small"
              :loading="quotaResetting"
              @click="resetQuota"
            >
              清零已用
            </el-button>
          </div>
        </div>
      </template>
      <AsyncState
        :state="quotaState"
        empty-text="暂无配额信息"
        @retry="loadQuota"
      >
        <el-table
          v-col-resize
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

    <el-dialog
      v-model="quotaDialogVisible"
      title="调整用量额度"
      width="480px"
      append-to-body
    >
      <p class="dialog-hint">
        上限为 0 表示停用该额度；调整只影响之后的新请求，不会回溯已发生的调用。
      </p>
      <el-form label-width="110px">
        <el-form-item
          v-for="item in quotaForm"
          :key="item.kind"
          :label="item.label"
        >
          <el-input-number
            v-model="item.limit"
            :min="0"
            :max="100000000"
            :step="100"
          />
          <span class="form-hint">已用 {{ item.used }}</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="quotaDialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="quotaSaving"
          @click="saveQuota"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
      </el-tab-pane>
      <el-tab-pane
        label="额度策略"
        name="policies"
        lazy
      >
        <QuotaPolicies :can-manage="canManageQuota" />
      </el-tab-pane>
      <el-tab-pane
        label="额度申请"
        name="requests"
        lazy
      >
        <QuotaRequests :can-approve="canApproveQuota" />
      </el-tab-pane>
    </el-tabs>
  </div>
</template>
<script setup lang="ts">
  import { computed, onMounted, reactive, ref, watch } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import {
    yardApi,
    llmTraceApi,
    type LlmTraceItem,
    type LlmUsageOverview,
    type LlmUsageRow,
    type QuotaLimitPatch,
  } from '@/api/modules'
  import { formatTime } from '@/utils/format'
  import { downloadCsvSections, todayStamp, type CsvSection } from '@/utils/csv'
  import UsageTrendChart from '@/components/usage/UsageTrendChart.vue'
  import QuotaPolicies from '@/components/quota/QuotaPolicies.vue'
  import QuotaRequests from '@/components/quota/QuotaRequests.vue'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'
  import { useAuthStore } from '@/stores/auth'

  interface QuotaItem {
    label?: string
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
  // 与后端 llm/capabilities.py 的能力清单保持同名，避免页面上出现英文用途码
  const PURPOSE_LABELS: Record<string, string> = {
    declarative: '专业助手对话',
    chat_plan: '对话内方案生成',
    chat_plan_continue: '方案续写',
    chat_plan_repair: '方案补章',
    chat_document: '对话内文档生成',
    chat_capability: '能力卡片判定',
    group_gate: '群聊应答自判',
    supervisor: '班组总控',
    team_synthesis: '班组检索融合',
    quick_action: '一句话业务解析',
    factory: 'Agent 工厂蓝图',
    agent_generation: '一句话生成助手',
    skill_generation: '一句话生成技能',
    plugin_generation: '一句话生成插件',
    plugin: '插件工作流推理',
    skill_suggest: '技能改进建议',
    skill_upgrade: '技能自动升级',
    tool_generation: '一句话生成工具',
    tool_run: '自定义工具执行',
    self_improve: '知识自生长',
    embedding: '知识向量化',
    device_analysis: '设备数据解读',
    image_generation: '图像生成',
    video_generation: '视频生成',
    platform: '平台通用',
    chat: '智能对话',
    qa: '知识问答',
    agent: '智能体执行',
    team: '班组接力',
    evolution: '技能自生长',
  }
  function purposeLabel(p: string): string {
    return PURPOSE_LABELS[p] ?? p
  }

  const RANGE_OPTIONS = [
    { label: '近 7 天', value: 7 },
    { label: '近 30 天', value: 30 },
    { label: '近 90 天', value: 90 },
  ]

  const auth = useAuthStore()
  const quotas = reactive<Record<string, QuotaItem>>({})
  const warnings = ref<QuotaWarning[]>([])
  const quotaState = ref<ViewState>('loading')
  const overview = ref<LlmUsageOverview | null>(null)
  const overviewLoading = ref(false)
  const range = ref(30)

  const unitsName = computed(() => auth.yard?.name ?? '当前小院')
  const canManageQuota = computed(() => auth.hasPermission('quota:manage'))
  const canApproveQuota = computed(() => auth.hasPermission('quota:approve'))
  const tab = ref('overview')
  const rows = computed<QuotaRow[]>(() =>
    Object.entries(quotas).map(([kind, item]) => ({
      kind,
      label: item.label ?? KIND_LABELS[kind] ?? kind,
      ...item,
    })),
  )

  // 管理员调整上限：三个额度一起改，避免只改一项时其余被误清空
  const quotaDialogVisible = ref(false)
  const quotaSaving = ref(false)
  const quotaResetting = ref(false)
  const quotaForm = ref<{ kind: string; label: string; limit: number; used: number }[]>([])

  function openQuotaDialog() {
    quotaForm.value = rows.value.map(r => ({
      kind: r.kind,
      label: r.label,
      limit: r.limit,
      used: r.used,
    }))
    quotaDialogVisible.value = true
  }

  async function saveQuota() {
    const yardId = auth.yard?.yard_id
    if (!yardId) {
      ElMessage.error('未取到当前小院，无法调整额度')
      return
    }
    const payload: QuotaLimitPatch = {}
    for (const item of quotaForm.value) {
      if (item.limit < 0 || !Number.isFinite(item.limit)) {
        ElMessage.error(`${item.label}的上限必须是非负整数`)
        return
      }
      if (item.limit < item.used) {
        ElMessage.warning(`${item.label}的上限低于已用量，保存后该额度会立即处于超限状态`)
      }
      const limit = Math.floor(item.limit)
      if (item.kind === 'llm_calls') payload.llm_calls_limit = limit
      else if (item.kind === 'knowledge_docs') payload.knowledge_docs_limit = limit
      else if (item.kind === 'tasks') payload.tasks_limit = limit
    }
    quotaSaving.value = true
    try {
      await yardApi.updateQuota(yardId, payload)
      ElMessage.success('额度已更新')
      quotaDialogVisible.value = false
      await loadQuota()
    } catch (e) {
      ElMessage.error('调整额度失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      quotaSaving.value = false
    }
  }

  async function resetQuota() {
    const yardId = auth.yard?.yard_id
    if (!yardId) {
      ElMessage.error('未取到当前小院，无法清零')
      return
    }
    try {
      await ElMessageBox.confirm(
        '清零只重置「已用」计数（模型调用 / 知识库文档 / 任务），上限保持不变。确定继续？',
        '清零已用量',
        { type: 'warning', confirmButtonText: '清零', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    quotaResetting.value = true
    try {
      await yardApi.resetQuota(yardId)
      ElMessage.success('已用量已清零')
      await loadQuota()
    } catch (e) {
      ElMessage.error('清零失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      quotaResetting.value = false
    }
  }
  function progressStatus(percent: number): 'success' | 'warning' | 'exception' | undefined {
    if (percent >= 90) return 'exception'
    if (percent >= 70) return 'warning'
    return undefined
  }

  function share(tokens: number): number {
    const total = overview.value?.total_tokens ?? 0
    if (!total) return 0
    return Math.min(100, Math.round((tokens / total) * 100))
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

  async function loadOverview() {
    overviewLoading.value = true
    try {
      overview.value = await llmTraceApi.overview(range.value)
    } catch {
      overview.value = null
    } finally {
      overviewLoading.value = false
    }
  }

  async function loadTraces() {
    try {
      const [s, t] = await Promise.all([
        llmTraceApi.summary(range.value),
        llmTraceApi.traces({ days: range.value, limit: 100 }),
      ])
      usageRows.value = s
      traceRows.value = t
    } catch {
      usageRows.value = []
      traceRows.value = []
    }
  }

  function exportStats() {
    const o = overview.value
    if (!o) {
      ElMessage.info('统计数据尚未加载完成')
      return
    }
    const sections: CsvSection[] = [
      {
        title: `用量统计汇总（近 ${range.value} 天，导出 ${todayStamp()}）`,
        rows: [
          ['指标', '数值'],
          ['今日消耗 Token', o.today_tokens],
          ['今日调用次数', o.today_calls],
          [`近 ${range.value} 天累计 Token`, o.total_tokens],
          [`近 ${range.value} 天调用次数`, o.total_calls],
          [`近 ${range.value} 天输入 Token`, o.total_input_tokens],
          [`近 ${range.value} 天输出 Token`, o.total_output_tokens],
        ],
      },
      {
        title: '按日趋势',
        rows: [
          ['日期', '调用次数', '输入 Token', '输出 Token', '总 Token'],
          ...o.by_day.map(d => [d.day, d.calls, d.input_tokens, d.output_tokens, d.total_tokens]),
        ],
      },
      {
        title: '按场景',
        rows: [
          ['使用场景', '调用次数', '输入 Token', '输出 Token', '总 Token'],
          ...o.by_purpose.map(p => [
            purposeLabel(p.purpose),
            p.calls,
            p.input_tokens,
            p.output_tokens,
            p.total_tokens,
          ]),
        ],
      },
      {
        title: '按智能体',
        rows: [
          ['智能体 / 模块', '调用次数', '输入 Token', '输出 Token', '总 Token'],
          ...o.by_agent.map(a => [
            a.agent_ref,
            a.calls,
            a.input_tokens,
            a.output_tokens,
            a.total_tokens,
          ]),
        ],
      },
      {
        title: '按模型',
        rows: [
          ['模型', '调用次数', '输入 Token', '输出 Token', '总 Token'],
          ...o.by_model.map(m => [
            m.model,
            m.calls,
            m.input_tokens,
            m.output_tokens,
            m.total_tokens,
          ]),
        ],
      },
      {
        title: '按日 × 模型',
        rows: [
          ['日期', '模型', '调用次数', '总 tokens', '平均延迟(ms)', '错误数'],
          ...usageRows.value.map(u => [
            u.day,
            u.model,
            u.calls,
            u.tokens,
            u.avg_latency_ms,
            u.errors,
          ]),
        ],
      },
    ]
    if (rows.value.length) {
      sections.push({
        title: '用量额度',
        rows: [
          ['额度类型', '已用', '上限', '剩余', '使用率%'],
          ...rows.value.map(r => [r.label, r.used, r.limit, r.remaining, r.usage_percent]),
        ],
      })
    }
    downloadCsvSections(`token-usage-${todayStamp()}.csv`, sections)
    ElMessage.success('统计 CSV 已导出')
  }

  function exportTraces() {
    if (!traceRows.value.length) {
      ElMessage.info('暂无可导出的调用流水')
      return
    }
    const sections: CsvSection[] = [
      {
        title: `最近调用流水（近 ${range.value} 天，共 ${traceRows.value.length} 条）`,
        rows: [
          ['时间', '用途', '来源', '模型', '输入 Token', '输出 Token', '总 Token', '状态'],
          ...traceRows.value.map(t => [
            t.created_at,
            purposeLabel(t.purpose),
            t.agent_ref ?? '',
            t.model,
            t.input_tokens ?? 0,
            t.output_tokens ?? 0,
            (t.input_tokens ?? 0) + (t.output_tokens ?? 0),
            t.status === 'ok' ? '成功' : '失败',
          ]),
        ],
      },
    ]
    downloadCsvSections(`llm-traces-${todayStamp()}.csv`, sections)
    ElMessage.success('调用流水 CSV 已导出')
  }

  watch(range, () => {
    void loadOverview()
    void loadTraces()
  })

  onMounted(() => {
    void loadQuota()
    void loadOverview()
    void loadTraces()
  })
</script>
<style scoped>
  .quota {
    padding: 4px;
    max-width: 1180px;
  }
  .page-head {
    display: flex;
    flex-wrap: wrap;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;
  }
  .head-text {
    min-width: 240px;
  }
  .page-title {
    margin: 0;
    font-size: 20px;
    font-weight: 700;
    color: var(--ds-text-1);
  }
  .page-sub {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .head-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
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
  .card-head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: 6px;
  }
  .card-hint {
    font-size: 11.5px;
    font-weight: 400;
    color: var(--ds-text-3);
  }
  .chart-empty {
    height: 300px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 13px;
    color: var(--ds-text-3);
  }
  .share-text {
    margin-left: 6px;
    font-size: 11.5px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }
  .err {
    color: var(--ds-danger);
    font-weight: 600;
    font-variant-numeric: tabular-nums;
  }
  .warn {
    margin-bottom: 12px;
  }
  .quota-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .dialog-hint {
    margin: 0 0 12px;
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-3);
  }
  .form-hint {
    margin-left: 10px;
    font-size: 12px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }
  .section {
    margin-bottom: 16px;
  }
  .row-fixed {
    row-gap: 16px;
  }
  .fixed-card :deep(.el-card__header) {
    padding: 12px 16px;
  }
  .fixed-card :deep(.el-table__inner-wrapper) {
    /* 固定高度表格内部滚动，两侧卡片视觉高度一致 */
    font-variant-numeric: tabular-nums;
  }
  @media (max-width: 768px) {
    .head-actions {
      width: 100%;
    }
  }
</style>
