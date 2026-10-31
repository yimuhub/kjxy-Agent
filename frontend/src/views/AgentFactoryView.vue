<template>
  <div class="factory">
    <h2 class="page-title">
      Agent 工厂
    </h2>
    <p class="sub">
      描述你的行业与场景，平台先解析画像、按需检索公开资料，再规划 3-6 个互补角色组成工作流；逐张确认后一键装配（生成结果全部先进草稿，发布由你点击——符合审核红线）。
    </p>

    <!-- 生成区 -->
    <el-card class="gen-card" shadow="never">
      <el-form label-position="top">
        <el-form-item label="行业描述（必填）">
          <el-input
            v-model="brief"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="例：苏北棉花种植与农机服务合作社，成员 200 户，需要有种植技术指导、农机调度、政策解读和对外宣传支持"
          />
        </el-form-item>
        <el-form-item label="侧重方向（可选）">
          <el-input
            v-model="focus"
            maxlength="200"
            placeholder="例：侧重农机调度与用工统筹"
          />
        </el-form-item>
        <el-button
          type="primary"
          :loading="generating"
          :disabled="!brief.trim() || generating"
          @click="generate"
        >
          {{ generating ? '工厂生产中…' : '开始生成' }}
        </el-button>
      </el-form>

      <!-- 进度区 -->
      <template v-if="currentRun">
        <el-divider />
        <el-steps
          :active="stepActive"
          finish-status="success"
          align-center
        >
          <el-step title="规划蓝图" />
          <el-step title="蓝图就绪" />
          <el-step title="生成草稿" />
          <el-step title="完成" />
        </el-steps>
        <el-alert
          v-if="currentRun.status === 'failed'"
          class="mt"
          type="error"
          :closable="false"
          title="本次生成失败"
          :description="currentRun.error || '未知原因'"
        />
      </template>
    </el-card>

    <!-- 蓝图卡片墙 -->
    <template v-if="blueprint">
      <div class="bp-header">
        <h3 class="bp-title">
          能力蓝图（{{ blueprint.agents.length }} 个角色 · {{ generatedByLabel(blueprint.generated_by) }}）
        </h3>
        <div class="bp-meta">
          <el-tooltip
            v-if="blueprint.research?.used"
            :content="researchTooltip"
            placement="top"
          >
            <el-tag size="small" type="success" effect="plain">
              联网情报 {{ blueprint.research.sources?.length || 0 }} 条
            </el-tag>
          </el-tooltip>
          <el-tag v-else size="small" type="info" effect="plain" class="research-off">
            {{ blueprint.research?.reason || '未联网' }}
          </el-tag>
          <span class="note">已选 {{ selectedCodes.length }} / {{ blueprint.agents.length }}</span>
        </div>
      </div>
      <div v-if="workflowStages.length" class="wf-band">
        <span class="wf-label">工作流</span>
        <template v-for="(s, si) in workflowStages" :key="s.key">
          <span class="wf-stage">
            <span class="wf-stage-name">{{ s.label }}</span>
            <span class="wf-roles">{{ stageRoleNames(s.roles).join('、') }}</span>
          </span>
          <span v-if="si < workflowStages.length - 1" class="wf-arrow">→</span>
        </template>
      </div>
      <el-checkbox-group v-model="selectedCodes">
        <el-row :gutter="16">
          <el-col
            v-for="a in blueprint.agents"
            :key="a.agent_code"
            :xs="24"
            :sm="12"
          >
            <el-card class="role-card" shadow="hover">
              <el-checkbox :value="a.agent_code" class="role-check">
                <span class="role-name">{{ a.emoji || '🤖' }} {{ a.name }}</span>
              </el-checkbox>
              <div class="role-desc">
                {{ a.description || '—' }}
              </div>
              <div class="role-tags">
                <el-tag
                  v-if="a.stage"
                  size="small"
                  :type="stageType(a.stage)"
                  effect="dark"
                >
                  {{ stageLabel(a.stage) }}
                </el-tag>
                <el-tag
                  size="small"
                  :type="a.risk_level === 'low' ? 'success' : 'warning'"
                >
                  {{ a.risk_level === 'low' ? '低风险·可自发布' : '高风险·需平台审核' }}
                </el-tag>
                <el-tag
                  v-for="code in a.suggested_skills || []"
                  :key="code"
                  size="small"
                  type="info"
                  effect="plain"
                >
                  {{ skillLabel(code) }}
                </el-tag>
                <el-tag
                  v-if="a.knowledge_coverage > 0"
                  size="small"
                  type="success"
                  effect="plain"
                >
                  知识库可支撑 {{ a.knowledge_coverage }} 条
                </el-tag>
                <el-tag
                  v-else
                  size="small"
                  type="warning"
                  effect="plain"
                >
                  知识缺口——建议先上传该方向资料
                </el-tag>
              </div>
              <el-collapse class="mt">
                <el-collapse-item title="它会回答这类问题" :name="a.agent_code">
                  <li
                    v-for="(q, i) in a.suggested_questions.slice(0, 3)"
                    :key="i"
                    class="q"
                  >
                    {{ q }}
                  </li>
                </el-collapse-item>
              </el-collapse>
              <el-button
                v-if="a.needs_knowledge"
                link
                type="primary"
                class="mt"
                @click="router.push('/knowledge')"
              >
                去知识库上传资料
              </el-button>
            </el-card>
          </el-col>
        </el-row>
      </el-checkbox-group>
      <div class="apply-bar">
        <div class="as-team-row">
          <el-checkbox v-model="asTeam">
            同时装配为协作班组
          </el-checkbox>
          <el-input
            v-if="asTeam"
            v-model="asTeamName"
            size="small"
            maxlength="100"
            placeholder="班组成员名（默认＝行业名＋班组）"
            style="width: 240px"
          />
          <span class="note">成员按工作流阶段接力，审核阶段角色自动担任审核员</span>
        </div>
        <el-button
          type="primary"
          :disabled="!selectedCodes.length || applying"
          :loading="applying"
          @click="apply(true)"
        >
          采纳所选并发布
        </el-button>
        <el-button
          :disabled="!selectedCodes.length || applying"
          @click="apply(false)"
        >
          仅保存草稿
        </el-button>
        <span class="note">发布遵循分级审核：低风险直接上线，高风险进入平台审核队列</span>
      </div>
    </template>

    <!-- 历史运行 -->
    <h3 class="hist-title">
      历史运行
    </h3>
    <AsyncState :state="listState" empty-text="还没有生成记录，从上方开始第一次" @retry="loadRuns">
      <el-table
        :data="runs"
        size="small"
        class="pointer"
        @row-click="openRun"
      >
        <el-table-column prop="industry_brief" label="行业描述" min-width="220" show-overflow-tooltip />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag size="small" :type="statusType(row.status)">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="90">
          <template #default="{ row }">
            {{ generatedByLabel(row.generated_by) }}
          </template>
        </el-table-column>
        <el-table-column label="角色" width="70">
          <template #default="{ row }">
            {{ row.role_count ?? '—' }}
          </template>
        </el-table-column>
        <el-table-column label="情报" width="90">
          <template #default="{ row }">
            <el-tag
              v-if="row.research_used"
              size="small"
              type="success"
              effect="plain"
            >
              {{ row.research_sources }} 条
            </el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column label="知识缺口" width="90">
          <template #default="{ row }">
            <el-tag
              v-if="(row.needs_knowledge_count ?? 0) > 0"
              size="small"
              type="warning"
            >
              {{ row.needs_knowledge_count }}
            </el-tag>
            <span v-else>—</span>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="时间" width="170">
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
      </el-table>
    </AsyncState>

    <!-- 采纳结果 -->
    <el-dialog
      v-model="resultVisible"
      append-to-body
      title="采纳结果"
      width="620px"
    >
      <el-descriptions v-if="applyResult" :column="4" border size="small" class="mb">
        <el-descriptions-item label="发布">
          {{ applyResult.published }}
        </el-descriptions-item>
        <el-descriptions-item label="待平台审核">
          {{ applyResult.pending_review }}
        </el-descriptions-item>
        <el-descriptions-item label="草稿">
          {{ applyResult.draft }}
        </el-descriptions-item>
        <el-descriptions-item label="失败">
          {{ applyResult.failed }}
        </el-descriptions-item>
      </el-descriptions>
      <el-alert
        v-if="applyResult?.team"
        class="mb"
        type="success"
        show-icon
        :closable="false"
        :title="`已同步创建协作班组「${applyResult.team.name}」，可在「协作班组」页运行`"
      />
      <el-alert
        v-else-if="applyResult?.team_error"
        class="mb"
        type="warning"
        show-icon
        :closable="false"
        :title="`助手装配完成，但自动组队失败（${applyResult.team_error}），可前往「协作班组」手动创建`"
      />
      <el-table
        v-if="applyResult"
        :data="applyResult.results"
        size="small"
        max-height="280"
      >
        <el-table-column prop="agent_code" label="编码" min-width="180" />
        <el-table-column label="结果" width="160">
          <template #default="{ row }">
            <el-tag
              v-if="row.ok"
              size="small"
              type="success"
            >
              {{ statusLabel(row.status) }}
            </el-tag>
            <el-tag
              v-else
              size="small"
              type="danger"
            >
              {{ row.error || '失败' }}
            </el-tag>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="resultVisible = false">
          关闭
        </el-button>
        <el-button
          type="primary"
          @click="goAgents"
        >
          去专业助手查看
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  factoryApi,
  type FactoryBlueprint,
  type FactoryApplyResult,
  type FactoryRunSummary,
} from '@/api/modules'
import { extractErrorMessage } from '@/api/client'
import { openSseStream, type SseHandle } from '@/utils/sse'
import { formatTime } from '@/utils/format'
import AsyncState from '@/components/AsyncState.vue'
import type { ViewState } from '@/utils/status'

const router = useRouter()
const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

const brief = ref('')
const focus = ref('')
const generating = ref(false)
const applying = ref(false)

const runs = ref<FactoryRunSummary[]>([])
const listState = ref<ViewState>('loading')
const currentRun = ref<FactoryRunSummary | null>(null)
const blueprint = ref<FactoryBlueprint | null>(null)
const selectedCodes = ref<string[]>([])
const applyResult = ref<FactoryApplyResult | null>(null)
const resultVisible = ref(false)
// W2 装配即组队：勾选后 apply 请求体带 as_team:{name}
const asTeam = ref(false)
const asTeamName = ref('')

let stream: SseHandle | null = null
let pollTimer: number | undefined

/* ---------- 状态展示 ---------- */
const stepActive = computed(() => {
  const s = currentRun.value?.status
  if (s === 'completed') return 4
  if (s === 'failed') return 0
  return { claimed: 0, planning: 1, generating: 3 }[s ?? 'claimed'] ?? 0
})

function statusType(s: string | null): 'success' | 'warning' | 'danger' | 'info' | 'primary' {
  if (s === 'completed') return 'success'
  if (s === 'failed') return 'danger'
  if (s === 'claimed' || s === 'planning' || s === 'generating') return 'primary'
  return 'info'
}
function statusLabel(s: string | null): string {
  return (
    {
      claimed: '排队中',
      planning: '规划中',
      generating: '生成中',
      completed: '已完成',
      failed: '失败',
      published: '已发布',
      draft: '草稿',
      pending_platform_review: '待平台审核',
    }[s ?? ''] || (s ?? '—')
  )
}
function generatedByLabel(g: string | null): string {
  if (g === 'llm') return 'LLM 生成'
  if (g === 'heuristic') return '规则兜底'
  return '—'
}

/* ---------- 阶段与情报展示 ---------- */
type StageTagType = 'primary' | 'success' | 'warning' | 'info' | 'danger'
const STAGE_META: Record<string, { label: string; type: StageTagType }> = {
  analysis: { label: '分析取证', type: 'primary' },
  plan: { label: '方案设计', type: 'success' },
  execution: { label: '执行落地', type: 'warning' },
  delivery: { label: '交付汇总', type: 'info' },
  review: { label: '审核把关', type: 'danger' },
}
function stageLabel(stage?: string): string {
  return stage ? STAGE_META[stage]?.label ?? stage : ''
}
function stageType(stage?: string): StageTagType {
  return stage ? STAGE_META[stage]?.type ?? 'info' : 'info'
}

const OFFICE_SKILL_LABELS: Record<string, string> = {
  office_word_report: 'Word 排版',
  office_excel_table: 'Excel 表格',
  office_ppt_deck: 'PPT 演示',
  office_weekly_report: '周报月报',
  office_data_clean: '数据整理',
  office_doc_review: '文档审校',
  // PPT 出片链路（叙事 → 版式 → 图表 → 质检 → 模板）
  office_ppt_storyline: '叙事线',
  office_ppt_design: '版式设计',
  office_ppt_chart: '图表设计',
  office_ppt_review: '演示质检',
  office_ppt_template: '模板出片',
  // 党政机关公文
  gov_notice: '通知',
  gov_request: '请示',
  gov_report: '报告',
  gov_letter: '函',
  gov_minutes: '纪要',
  gov_work_plan: '工作方案',
  gov_summary: '工作总结',
  gov_reply: '批复答复',
  gov_speech: '讲话稿',
  gov_briefing: '信息简报',
  // 科研项目申报与验收
  grant_project_application: '项目申报',
  grant_tech_program: '科技项目',
  grant_subsidy: '补贴申请',
  grant_honor: '荣誉资质',
  grant_midterm: '中期检查',
  grant_acceptance: '结题验收',
  grant_budget: '预算说明',
  // 招投标
  tender_document: '招标文件',
  bid_document: '投标文件',
  bid_response_matrix: '评分响应',
  bid_technical: '技术方案',
  bid_commercial: '商务响应',
  bid_risk_review: '废标审查',
  bid_clarification: '澄清函',
  // 长文与会议材料
  formal_longform: '正式长文',
  exec_brief: '一页纸简报',
  policy_interpretation: '政策解读',
  meeting_pack: '会议材料包',
}

/** 未知 code 原样回显，避免新技能上线前标签空白 */
function skillLabel(code: string): string {
  return OFFICE_SKILL_LABELS[code] ?? code
}

const workflowStages = computed(() => blueprint.value?.workflow?.stages ?? [])

function stageRoleNames(codes: string[]): string[] {
  const names = new Map(
    (blueprint.value?.agents ?? []).map(a => [a.agent_code, a.name.replace(/（.*）$/, '')]),
  )
  return codes.map(code => names.get(code) ?? code)
}

const researchTooltip = computed(() => {
  const research = blueprint.value?.research
  if (!research?.used) return ''
  const head = `来源：${research.provider || '免费搜索源'}；查询：${(research.queries ?? []).join(' / ')}`
  const items = (research.sources ?? [])
    .slice(0, 5)
    .map(s => s.title || s.url)
    .join('\n')
  return items ? `${head}\n${items}` : head
})

/* ---------- 列表与详情 ---------- */
async function loadRuns() {
  listState.value = 'loading'
  try {
    runs.value = await factoryApi.runs()
    listState.value = runs.value.length ? 'ready' : 'empty'
  } catch {
    listState.value = 'error'
  }
}

async function openRun(row: FactoryRunSummary) {
  try {
    const d = await factoryApi.run(row.id)
    currentRun.value = d
    blueprint.value = d.blueprint_json
    selectedCodes.value = (d.blueprint_json?.agents ?? [])
      .filter(a => a.risk_level === 'low')
      .map(a => a.agent_code)
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '加载运行详情失败')
  }
}

/* ---------- 生成 + 进度 ---------- */
async function generate() {
  generating.value = true
  blueprint.value = null
  try {
    const g = await factoryApi.generate({
      industry_brief: brief.value.trim(),
      focus: focus.value.trim() || undefined,
    })
    currentRun.value = {
      id: g.run_id,
      status: 'claimed',
      industry_brief: brief.value.trim(),
      generated_by: null,
      error: null,
      created_by: null,
      approved_by: null,
      created_at: new Date().toISOString(),
      updated_at: null,
      role_count: null,
      needs_knowledge_count: null,
    }
    watchProgress(g.task_id, g.run_id)
  } catch (e) {
    generating.value = false
    const err = extractErrorMessage(e)
    ElMessage.error(err.code === 'QUOTA_EXCEEDED' ? '本月任务配额已用完，请下月再试或联系平台' : err.message || '生成失败')
  }
}

function stopWatch() {
  stream?.close()
  stream = null
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = undefined
  }
}

function watchProgress(taskId: string, runId: string) {
  stopWatch()
  const onDone = async (ok: boolean) => {
    generating.value = false
    stopWatch()
    await loadRuns()
    const d = await factoryApi.run(runId)
    currentRun.value = d
    blueprint.value = d.blueprint_json
    selectedCodes.value = (d.blueprint_json?.agents ?? [])
      .filter(a => a.risk_level === 'low')
      .map(a => a.agent_code)
    if (ok) ElMessage.success('蓝图已就绪，勾选需要的角色后一键装配')
    else ElMessage.error('工厂生产失败，详见进度区')
  }
  stream = openSseStream({
    url: `${API_BASE}/tasks/${taskId}/events/stream`,
    onFrame: frame => {
      if (frame.event !== 'task-event') return
      let payload: { milestone?: string; status?: string } = {}
      try {
        payload = JSON.parse(frame.data)
      } catch {
        return
      }
      if (payload.milestone) {
        const m = payload.milestone
        if (m === 'planning') currentRun.value = currentRun.value && { ...currentRun.value, status: 'planning' }
        if (m === 'generating') currentRun.value = currentRun.value && { ...currentRun.value, status: 'generating' }
        if (m === 'completed') void onDone(true)
        if (m === 'failed') void onDone(false)
      }
    },
    onFallback: () => startPolling(runId),
  })
}

/** SSE 重连失败降级：5s 轮询 run 状态 */
function startPolling(runId: string) {
  if (pollTimer) return
  pollTimer = window.setInterval(async () => {
    try {
      const d = await factoryApi.run(runId)
      currentRun.value = d
      if (d.status === 'completed' || d.status === 'failed') {
        await onPollDone(d)
      }
    } catch {
      /* 网络抖动等下一轮 */
    }
  }, 5000)
}

async function onPollDone(d: FactoryRunSummary & { blueprint_json?: FactoryBlueprint | null }) {
  generating.value = false
  stopWatch()
  await loadRuns()
  currentRun.value = d
  blueprint.value = d.blueprint_json ?? null
  selectedCodes.value = (blueprint.value?.agents ?? [])
    .filter(a => a.risk_level === 'low')
    .map(a => a.agent_code)
}

/* ---------- 采纳 ---------- */
function defaultTeamName(): string {
  const brief = (blueprint.value?.industry_brief ?? '新行业').trim().slice(0, 40)
  return `${brief}班组`
}

async function apply(publish: boolean) {
  const run = currentRun.value
  if (!run || !selectedCodes.value.length) return
  applying.value = true
  const asTeamSpec = asTeam.value ? { name: asTeamName.value.trim() || defaultTeamName() } : undefined
  try {
    applyResult.value = await factoryApi.apply(run.id, selectedCodes.value, publish, asTeamSpec)
    resultVisible.value = true
    if (applyResult.value.team) {
      ElMessage.success(`已同步创建协作班组「${applyResult.value.team.name}」`)
    } else if (applyResult.value.team_error) {
      ElMessage.warning(`自动组队失败（${applyResult.value.team_error}），助手装配不受影响`)
    }
    await loadRuns()
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '采纳失败')
  } finally {
    applying.value = false
  }
}

function goAgents() {
  resultVisible.value = false
  router.push('/agents')
}

onMounted(() => {
  void loadRuns()
})
onUnmounted(stopWatch)
</script>

<style scoped>
.page-title {
  margin: 0 0 6px;
}
.sub {
  margin: 0 0 16px;
  font-size: 13px;
  color: var(--ds-text-2);
}
.gen-card {
  margin-bottom: 20px;
}
.bp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin: 8px 0 12px;
  flex-wrap: wrap;
}
.bp-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}
.research-off {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wf-band {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  padding: 10px 12px;
  margin-bottom: 14px;
  border-radius: 8px;
  background: var(--ds-bg-2);
  font-size: 12px;
}
.wf-label {
  font-weight: 600;
  color: var(--ds-text-1);
}
.wf-stage {
  display: inline-flex;
  align-items: baseline;
  gap: 6px;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--ds-bg-1);
  border: 1px solid var(--ds-border);
}
.wf-stage-name {
  font-weight: 600;
  color: var(--ds-text-1);
}
.wf-roles {
  color: var(--ds-text-2);
}
.wf-arrow {
  color: var(--ds-text-3);
}
.bp-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--ds-text-1);
}
.role-card {
  margin-bottom: 16px;
}
.role-name {
  font-weight: 600;
  color: var(--ds-text-1);
}
.role-desc {
  margin: 6px 0 8px;
  font-size: 13px;
  color: var(--ds-text-2);
}
.role-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.q {
  font-size: 12px;
  color: var(--ds-text-3);
}
.apply-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin: 4px 0 24px;
  flex-wrap: wrap;
}
.as-team-row {
  flex: 1 1 100%;
  display: flex;
  align-items: center;
  gap: 10px;
}
.hist-title {
  margin: 8px 0 10px;
  font-size: 16px;
  font-weight: 600;
  color: var(--ds-text-1);
}
.pointer :deep(tbody tr) {
  cursor: pointer;
}
.mt {
  margin-top: 12px;
}
.mb {
  margin-bottom: 12px;
}
.note {
  font-size: 12px;
  color: var(--ds-text-2);
}
</style>
