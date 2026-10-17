<template>
  <div class="agents">
    <h2 class="page-title">
      专业助手
    </h2>

    <!-- 列表区四态：加载 / 空 / 失败重试 / 无权限 -->
    <AsyncState
      :state="listState"
      empty-text="暂无可用的专业助手"
      @retry="loadAgents"
    >
      <el-row
        :gutter="16"
        class="cards"
      >
        <el-col
          v-for="a in agents"
          :key="a.code"
          :span="6"
          :xs="24"
          :sm="12"
        >
          <el-card
            class="agent-card"
            :class="{ active: selected === a.code }"
            shadow="hover"
            @click="select(a.code)"
          >
            <div class="agent-name">
              {{ a.name }}
            </div>
            <div class="agent-desc">
              {{ a.description }}
            </div>
            <el-tag
              size="small"
              :type="riskType(a.risk_level)"
              class="mt"
            >
              {{ riskLabel(a.risk_level) }}
            </el-tag>
            <div
              class="skills"
              @click.stop
            >
              <span class="skills-title">初始技能</span>
              <template v-if="a.skills?.length">
                <el-tag
                  v-for="s in a.skills"
                  :key="s.code"
                  size="small"
                  type="info"
                  class="skill-tag"
                  @click="openSkill(s)"
                >
                  {{ s.name }}
                </el-tag>
              </template>
              <span
                v-else
                class="no-skills"
              >暂无预置技能</span>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </AsyncState>

    <el-card
      v-if="selected"
      class="form-card"
    >
      <template #header>
        <span>{{ currentAgent?.name }} · 输入</span>
      </template>

      <!-- 作物诊断 -->
      <el-form
        v-if="selected === 'crop_diagnosis'"
        :model="diagForm"
        label-width="110px"
      >
        <el-form-item
          label="作物"
          required
        >
          <el-input
            v-model="diagForm.crop"
            placeholder="如：水稻"
          />
        </el-form-item>
        <el-form-item label="品种">
          <el-input
            v-model="diagForm.variety"
            placeholder="如：南粳46（可选）"
          />
        </el-form-item>
        <el-form-item label="生育期">
          <el-input
            v-model="diagForm.growth_stage"
            placeholder="如：分蘖期"
          />
        </el-form-item>
        <el-form-item label="区域">
          <el-input
            v-model="diagForm.region"
            placeholder="如：江苏南京"
          />
        </el-form-item>
        <el-form-item
          label="症状描述"
          required
        >
          <el-input
            v-model="diagForm.symptoms"
            type="textarea"
            :rows="4"
            maxlength="2000"
            show-word-limit
            placeholder="描述观察到的症状，如：叶片发黄、叶鞘有褐色云纹状病斑"
          />
        </el-form-item>
        <el-form-item label="环境">
          <el-input
            v-model="diagForm.environment"
            placeholder="如：连续阴雨、湿度大（可选）"
          />
        </el-form-item>
      </el-form>

      <!-- 农事规划 -->
      <el-form
        v-if="selected === 'farm_planner'"
        :model="planForm"
        label-width="110px"
      >
        <el-form-item
          label="地块"
          required
        >
          <el-input
            v-model="planForm.plot_id"
            placeholder="如：东大田3号"
          />
        </el-form-item>
        <el-form-item
          label="作物"
          required
        >
          <el-input
            v-model="planForm.crop"
            placeholder="如：小麦"
          />
        </el-form-item>
        <el-form-item
          label="生育期"
          required
        >
          <el-input
            v-model="planForm.growth_stage"
            placeholder="如：播种期"
          />
        </el-form-item>
        <el-form-item
          label="计划周期"
          required
        >
          <el-date-picker
            v-model="planRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
          />
        </el-form-item>
        <el-form-item label="天气信息">
          <el-input
            v-model="planForm.weather_info"
            placeholder="如：未来一周多云转晴（可选）"
          />
        </el-form-item>
        <el-form-item label="资源约束">
          <el-input
            v-model="planForm.resource_constraints"
            placeholder="如：人工2人、拖拉机1台（可选，逗号分隔）"
          />
        </el-form-item>
      </el-form>

      <!-- 科研辅助 -->
      <el-form
        v-if="selected === 'research_assistant'"
        :model="raForm"
        label-width="110px"
      >
        <el-form-item
          label="CSV 数据"
          required
        >
          <el-input
            v-model="raForm.csv_text"
            type="textarea"
            :rows="8"
            placeholder="粘贴 CSV 文本，第一行为表头"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            size="small"
            @click="fillSampleCsv"
          >
            填入示例数据
          </el-button>
        </el-form-item>
      </el-form>

      <!-- 数据汇总 -->
      <el-form
        v-if="selected === 'data_analyst'"
        :model="daForm"
        label-width="110px"
      >
        <el-form-item label="数据源">
          <el-checkbox-group v-model="daForm.sources">
            <el-checkbox label="tasks">
              任务
            </el-checkbox>
            <el-checkbox label="knowledge">
              知识库
            </el-checkbox>
            <el-checkbox label="llm_calls">
              模型调用
            </el-checkbox>
          </el-checkbox-group>
        </el-form-item>
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="daRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
          />
          <span class="hint">不选则默认近 30 天</span>
        </el-form-item>
      </el-form>

      <el-form-item>
        <el-button
          type="primary"
          :loading="running"
          :disabled="!canRun"
          @click="run"
        >
          运行
        </el-button>
        <el-button @click="reset">
          重置
        </el-button>
      </el-form-item>
    </el-card>

    <el-card
      v-if="result"
      class="result-card"
    >
      <template #header>
        <div class="result-header">
          <span>运行结果</span>
          <el-tag
            :type="result.status === 'succeeded' ? 'success' : 'warning'"
            size="small"
          >
            {{ statusLabel(result.status) }}
          </el-tag>
        </div>
      </template>

      <el-alert
        v-for="(w, i) in warnings"
        :key="i"
        :title="w"
        type="warning"
        show-icon
        class="mb"
      />

      <div
        v-if="selected === 'crop_diagnosis' && diagResult"
        class="diag-result"
      >
        <h4>候选问题</h4>
        <el-table
          :data="diagResult.candidates"
          stripe
          size="small"
        >
          <el-table-column
            prop="name"
            label="候选问题"
          />
          <el-table-column
            prop="confidence"
            label="置信度"
            width="90"
          >
            <template #default="{ row }">
              {{ pct(row.confidence) }}
            </template>
          </el-table-column>
          <el-table-column
            prop="notes"
            label="说明"
          />
        </el-table>
        <h4 v-if="diagResult.needs_info?.length">
          需补充的信息
        </h4>
        <ul v-if="diagResult.needs_info?.length">
          <li
            v-for="(q, i) in diagResult.needs_info"
            :key="i"
          >
            {{ q }}
          </li>
        </ul>
        <h4 v-if="diagResult.next_steps?.length">
          建议的下一步
        </h4>
        <ul v-if="diagResult.next_steps?.length">
          <li
            v-for="(s, i) in diagResult.next_steps"
            :key="i"
          >
            {{ s }}
          </li>
        </ul>
        <el-alert
          v-if="diagResult.needs_expert_review"
          title="建议专家复核"
          type="error"
          show-icon
          class="mb"
        />
        <p class="disclaimer">
          {{ diagResult.disclaimer }}
        </p>
      </div>

      <div
        v-if="selected === 'farm_planner' && planResult"
        class="plan-result"
      >
        <p>计划版本：v{{ planResult.version }}</p>
        <h4>农事建议（{{ planResult.suggestions?.length }} 项）</h4>
        <el-table
          :data="planResult.suggestions"
          stripe
          size="small"
        >
          <el-table-column
            prop="task_name"
            label="任务"
          />
          <el-table-column
            label="时间窗"
            width="220"
          >
            <template #default="{ row }">
              {{ row.time_window?.start }} ~ {{ row.time_window?.end }}
            </template>
          </el-table-column>
          <el-table-column
            prop="note"
            label="说明"
          />
        </el-table>
        <el-alert
          title="本期仅生成建议，不自动派单"
          type="info"
          show-icon
          class="mb mt"
        />
      </div>

      <div
        v-if="selected === 'research_assistant' && raResult"
        class="ra-result"
      >
        <h4>导入报告</h4>
        <p>
          {{ raResult.import_report?.rows }} 行 × {{ raResult.import_report?.columns?.length }} 列
        </p>
        <h4>描述统计</h4>
        <el-table
          :data="raStatRows"
          stripe
          size="small"
        >
          <el-table-column
            prop="col"
            label="列"
          />
          <el-table-column
            prop="count"
            label="计数"
          />
          <el-table-column
            prop="mean"
            label="均值"
          />
          <el-table-column
            prop="std"
            label="标准差"
          />
          <el-table-column
            prop="min"
            label="最小"
          />
          <el-table-column
            prop="max"
            label="最大"
          />
        </el-table>
        <h4 v-if="raResult.assumptions?.length">
          统计假设
        </h4>
        <ul v-if="raResult.assumptions?.length">
          <li
            v-for="(a, i) in raResult.assumptions"
            :key="i"
          >
            {{ a }}
          </li>
        </ul>
      </div>

      <div
        v-if="selected === 'data_analyst' && daResult"
        class="da-result"
      >
        <p class="meta">
          生成时间：{{ daResult.generated_at }} · 版本：{{ daResult.version }}
        </p>
        <h4>关键指标</h4>
        <el-table
          :data="daResult.metrics"
          stripe
          size="small"
        >
          <el-table-column
            prop="name"
            label="指标"
          />
          <el-table-column
            prop="value"
            label="值"
            width="120"
          />
          <el-table-column
            prop="unit"
            label="单位"
            width="80"
          />
          <el-table-column
            label="数据来源"
            min-width="200"
          >
            <template #default="{ row }">
              <el-tooltip
                :content="traceText(row.trace)"
                placement="top"
              >
                <span class="trace">{{ row.trace?.source }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
        </el-table>
      </div>

      <el-collapse class="mt">
        <el-collapse-item title="完整原始输出（JSON）">
          <pre class="json">{{ pretty(result) }}</pre>
        </el-collapse-item>
      </el-collapse>
    </el-card>
    <el-dialog
      v-model="skillDialogVisible"
      :title="skillDetail?.name || '技能详情'"
      width="420px"
    >
      <el-descriptions
        :column="1"
        border
        size="small"
      >
        <el-descriptions-item label="技能代码">
          {{ skillDetail?.code }}
        </el-descriptions-item>
        <el-descriptions-item label="说明">
          {{ skillDetail?.description }}
        </el-descriptions-item>
        <el-descriptions-item label="风险等级">
          <el-tag
            :type="skillRiskType(skillDetail?.risk_level)"
            size="small"
          >
            {{ skillRiskLabel(skillDetail?.risk_level) }}
          </el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { agentApi } from '@/api/modules'
  import { extractErrorMessage } from '@/api/client'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, friendlyErrorMessage, type ViewState } from '@/utils/status'
  import type { AgentDefinition, AgentRunResult, AgentSkill } from '@/types/api'

  const agents = ref<AgentDefinition[]>([])
  const listState = ref<ViewState>('loading')
  const selected = ref('')
  const running = ref(false)
  const result = ref<AgentRunResult | null>(null)
  const skillDialogVisible = ref(false)
  const skillDetail = ref<AgentSkill | null>(null)

  const diagForm = ref({
    crop: '',
    variety: '',
    growth_stage: '',
    region: '',
    symptoms: '',
    environment: '',
  })
  const planForm = ref({
    plot_id: '',
    crop: '',
    growth_stage: '',
    weather_info: '',
    resource_constraints: '',
  })
  const planRange = ref<[string, string] | null>(null)
  const raForm = ref({ csv_text: '' })
  const daForm = ref({ sources: ['tasks', 'knowledge', 'llm_calls'] as string[] })
  const daRange = ref<[string, string] | null>(null)

  const currentAgent = computed(() => agents.value.find(a => a.code === selected.value))
  const warnings = computed(() => result.value?.warnings ?? [])

  const diagResult = computed(() => result.value?.output as Record<string, any> | undefined)
  const planResult = computed(() => result.value?.output as Record<string, any> | undefined)
  const raResult = computed(() => result.value?.output as Record<string, any> | undefined)
  const daResult = computed(() => result.value?.output as Record<string, any> | undefined)

  const raStatRows = computed(() => {
    const stats = raResult.value?.descriptive_stats as Record<string, any> | undefined
    if (!stats) return []
    return Object.entries(stats).map(([col, s]: [string, any]) => ({
      col,
      count: s.count,
      mean: round2(s.mean),
      std: round2(s.std),
      min: s.min,
      max: s.max,
    }))
  })

  function round2(x: unknown): unknown {
    return typeof x === 'number' ? Math.round(x * 100) / 100 : x
  }
  function pct(x: unknown): string {
    return typeof x === 'number' ? `${Math.round(x * 100)}%` : '-'
  }
  function pretty(o: unknown): string {
    return JSON.stringify(o, null, 2)
  }
  function traceText(t: any): string {
    if (!t) return ''
    return `来源: ${t.source}; 条件: ${JSON.stringify(t.filters)}; 时间: ${JSON.stringify(t.time_range)}`
  }
  function riskType(r?: string): string {
    return r === 'medium' ? 'warning' : 'success'
  }
  function riskLabel(r?: string): string {
    return r === 'medium' ? '中等风险' : '低风险'
  }
  function statusLabel(s: string): string {
    return s === 'succeeded' ? '成功' : s === 'awaiting_review' ? '待审核' : '失败'
  }

  function openSkill(s: AgentSkill) {
    skillDetail.value = s
    skillDialogVisible.value = true
  }
  function skillRiskType(r?: string): string {
    if (r === 'external_side_effect') return 'danger'
    if (r === 'write_reversible' || r === 'draft') return 'warning'
    return 'success'
  }
  function skillRiskLabel(r?: string): string {
    if (r === 'external_side_effect') return '外部副作用（高风险）'
    if (r === 'write_reversible') return '可逆写入'
    if (r === 'draft') return '草稿'
    return '只读'
  }

  const canRun = computed(() => {
    if (selected.value === 'crop_diagnosis')
      return !!(diagForm.value.crop.trim() && diagForm.value.symptoms.trim())
    if (selected.value === 'farm_planner')
      return !!(
        planForm.value.plot_id.trim() &&
        planForm.value.crop.trim() &&
        planForm.value.growth_stage.trim() &&
        planRange.value
      )
    if (selected.value === 'research_assistant') return !!raForm.value.csv_text.trim()
    if (selected.value === 'data_analyst') return daForm.value.sources.length > 0
    return false
  })

  function select(code: string) {
    selected.value = code
    result.value = null
  }

  function reset() {
    result.value = null
  }

  function fillSampleCsv() {
    raForm.value.csv_text = [
      'plot,yield_kg,rainfall_mm',
      'A1,5200,820',
      'A2,5450,860',
      'A3,4980,790',
      'B1,5600,900',
      'B2,5310,845',
    ].join('\n')
  }

  function buildInput(): Record<string, unknown> {
    if (selected.value === 'crop_diagnosis') {
      return {
        crop: diagForm.value.crop.trim(),
        variety: diagForm.value.variety.trim() || undefined,
        growth_stage: diagForm.value.growth_stage.trim() || undefined,
        region: diagForm.value.region.trim() || undefined,
        symptoms: diagForm.value.symptoms.trim(),
        environment: diagForm.value.environment.trim() || undefined,
      }
    }
    if (selected.value === 'farm_planner') {
      const rc = planForm.value.resource_constraints
        .split(/[,，]/)
        .map(s => s.trim())
        .filter(Boolean)
      return {
        plot_id: planForm.value.plot_id.trim(),
        crop: planForm.value.crop.trim(),
        growth_stage: planForm.value.growth_stage.trim(),
        plan_period: { start: planRange.value?.[0], end: planRange.value?.[1] },
        weather: planForm.value.weather_info.trim()
          ? { description: planForm.value.weather_info.trim() }
          : undefined,
        resource_constraints: rc,
      }
    }
    if (selected.value === 'research_assistant') {
      return { csv_text: raForm.value.csv_text }
    }
    if (selected.value === 'data_analyst') {
      const input: Record<string, unknown> = { sources: daForm.value.sources }
      if (daRange.value) {
        input.time_range = { start: daRange.value[0], end: daRange.value[1] }
      }
      return input
    }
    return {}
  }

  async function run() {
    if (!selected.value || running.value) return
    running.value = true
    result.value = null
    try {
      result.value = await agentApi.run(selected.value, buildInput())
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message))
    } finally {
      running.value = false
    }
  }

  async function loadAgents() {
    listState.value = 'loading'
    try {
      const all = await agentApi.list()
      // 只展示 4 个专业助手（过滤掉 orchestrator / agriculture_qa）
      const pros = ['crop_diagnosis', 'farm_planner', 'research_assistant', 'data_analyst']
      agents.value = all.filter(a => pros.includes(a.code))
      if (agents.value.length) selected.value = agents.value[0].code
      listState.value = agents.value.length ? 'ready' : 'empty'
    } catch (e) {
      agents.value = []
      listState.value = classifyError(e)
      if (listState.value === 'error') {
        ElMessage.error('加载专业助手列表失败')
      }
    }
  }

  onMounted(loadAgents)
</script>

<style scoped>
  .page-title {
    margin: 0 0 16px;
    font-size: 20px;
  }
  .cards {
    margin-bottom: 16px;
  }
  .agent-card {
    cursor: pointer;
    min-height: 150px;
  }
  .agent-card.active {
    border-color: var(--el-color-primary);
    box-shadow: 0 0 0 1px var(--el-color-primary);
  }
  .agent-name {
    font-weight: 600;
    margin-bottom: 8px;
  }
  .agent-desc {
    color: var(--el-text-color-secondary);
    font-size: 13px;
    min-height: 60px;
  }
  .skills {
    margin-top: 10px;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    align-items: center;
  }
  .skills-title {
    font-size: 12px;
    color: var(--el-text-color-secondary);
    width: 100%;
  }
  .skill-tag {
    cursor: pointer;
  }
  .no-skills {
    font-size: 12px;
    color: var(--el-text-color-placeholder);
  }
  .mt {
    margin-top: 8px;
  }
  .mb {
    margin-bottom: 12px;
  }
  .form-card,
  .result-card {
    margin-bottom: 16px;
  }
  .result-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .hint {
    color: var(--el-text-color-secondary);
    font-size: 12px;
    margin-left: 8px;
  }
  .json {
    background: var(--el-fill-color-light);
    padding: 12px;
    border-radius: 4px;
    font-size: 12px;
    overflow: auto;
    max-height: 400px;
  }
  .disclaimer {
    color: var(--el-text-color-secondary);
    font-size: 13px;
  }
  .meta {
    color: var(--el-text-color-secondary);
    font-size: 13px;
  }
  .trace {
    color: var(--el-color-primary);
    cursor: help;
    text-decoration: underline dotted;
  }
  h4 {
    margin: 16px 0 8px;
  }
  ul {
    margin: 0 0 8px 20px;
    padding: 0;
  }
</style>
