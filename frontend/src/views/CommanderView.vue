<template>
  <div class="commander-page">
    <header class="page-head">
      <div class="head-copy">
        <p class="eyebrow">
          指挥官 · 指挥台
        </p>
        <h1>{{ displayName }}工作台</h1>
        <div class="head-tags">
          <el-tag
            size="small"
            effect="plain"
          >
            {{ personaLabel }}
          </el-tag>
          <el-tag
            v-if="overview?.commander.persona_locked"
            size="small"
            type="info"
          >
            人格已锁定
          </el-tag>
          <el-tag
            v-if="overview?.commander.evolution_lock"
            size="small"
            type="warning"
          >
            进化锁开启
          </el-tag>
          <el-tag
            v-if="overview && !overview.commander.evaluator_enabled"
            size="small"
            type="info"
          >
            自动测评关闭
          </el-tag>
        </div>
        <p class="sub">
          预算、运行质量、记忆与成本集中在这里；深度指令仍进入对话页下达。
        </p>
      </div>
      <div class="head-actions">
        <el-button @click="openChat">
          进入对话
        </el-button>
        <el-button
          type="primary"
          :loading="loading"
          @click="load"
        >
          刷新
        </el-button>
      </div>
    </header>

    <div
      v-if="error"
      class="state-box"
    >
      <p>{{ error }}</p>
      <el-button
        type="primary"
        plain
        @click="load"
      >
        重试
      </el-button>
    </div>

    <el-skeleton
      v-else-if="loading && !overview"
      :rows="8"
      animated
    />

    <template v-else-if="overview">
      <section class="stat-grid">
        <article class="stat-card">
          <span class="stat-label">本轮预算</span>
          <strong class="stat-value">{{ compact(overview.commander.token_budget) }}</strong>
          <el-progress
            :percentage="budgetPercent"
            :status="budgetPercent >= 100 ? 'exception' : budgetPercent >= 80 ? 'warning' : ''"
            :stroke-width="8"
          />
          <span class="stat-note">
            最近运行已用 {{ compact(lastRunTokens) }} tokens
          </span>
        </article>

        <article class="stat-card">
          <span class="stat-label">任务成功率</span>
          <strong class="stat-value">{{ percent(overview.stats.subtasks.success_rate) }}</strong>
          <span class="stat-note">
            {{ overview.stats.runs.total }} 次运行 · 子任务 {{ overview.stats.subtasks.ok }}/{{ overview.stats.subtasks.total }} 成功
          </span>
        </article>

        <article class="stat-card">
          <span class="stat-label">近 {{ overview.cost.window_days }} 天成本</span>
          <strong class="stat-value">{{ compact(overview.cost.total.tokens) }}</strong>
          <span class="stat-note">
            {{ overview.cost.total.calls }} 次模型调用 · 估算 ¥{{ money(overview.cost.total.cost) }}
          </span>
        </article>

        <article class="stat-card">
          <span class="stat-label">记忆资产</span>
          <strong class="stat-value">{{ overview.memory.counts.total || 0 }}</strong>
          <span class="stat-note">
            运行复盘 {{ overview.memory.counts.episode || 0 }} · 日复盘 {{ overview.memory.counts.summary || 0 }}
          </span>
        </article>
      </section>

      <section class="main-grid">
        <div class="column">
          <el-card
            shadow="never"
            class="panel"
          >
            <template #header>
              <div class="panel-head">
                <div>
                  <h2>最新日复盘</h2>
                  <p>只保留业务决策、失败与教训，不堆放原始日志。</p>
                </div>
                <el-button
                  v-if="canManage"
                  size="small"
                  :loading="summarizing"
                  @click="generateSummary"
                >
                  生成今日复盘
                </el-button>
              </div>
            </template>
            <div
              v-if="overview.memory.latest_summary"
              class="summary-body"
            >
              <div class="summary-title">
                {{ overview.memory.latest_summary.title }}
                <span class="muted">{{ formatTime(overview.memory.latest_summary.created_at) }}</span>
              </div>
              <p class="pre-line">
                {{ overview.memory.latest_summary.content }}
              </p>
            </div>
            <el-empty
              v-else
              description="还没有日复盘；跑完一次指挥官任务后会按天生成"
              :image-size="72"
            />
          </el-card>

          <el-card
            shadow="never"
            class="panel"
          >
            <template #header>
              <div class="panel-head">
                <div>
                  <h2>最近运行</h2>
                  <p>点一行可回到对应会话继续处理。</p>
                </div>
              </div>
            </template>
            <el-table
              v-if="overview.runs.length"
              v-col-resize
              :data="overview.runs"
              size="small"
              class="run-table"
              @row-click="openRun"
            >
              <el-table-column
                prop="goal"
                label="目标"
                min-width="220"
                show-overflow-tooltip
              />
              <el-table-column
                label="状态"
                width="100"
              >
                <template #default="{ row }">
                  <el-tag
                    size="small"
                    :type="statusTone(row.status)"
                  >
                    {{ statusLabel(row.status) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column
                label="Token"
                width="96"
              >
                <template #default="{ row }">
                  {{ compact(row.tokens_total) }}
                </template>
              </el-table-column>
              <el-table-column
                label="子任务"
                width="90"
              >
                <template #default="{ row }">
                  {{ subtaskSummary(row as CommanderRun) }}
                </template>
              </el-table-column>
              <el-table-column
                label="时间"
                width="150"
              >
                <template #default="{ row }">
                  {{ formatTime(row.created_at) }}
                </template>
              </el-table-column>
            </el-table>
            <el-empty
              v-else
              description="还没有指挥官运行"
              :image-size="72"
            />
          </el-card>
        </div>

        <div class="column">
          <el-card
            shadow="never"
            class="panel governance-panel"
          >
            <template #header>
              <div class="panel-head">
                <div>
                  <h2>监督测评</h2>
                  <p>独立考官按周打分；报告只作建议，是否改人格由锁与开关决定。</p>
                </div>
                <div
                  v-if="canManage"
                  class="panel-actions"
                >
                  <el-button
                    size="small"
                    :loading="runningEval"
                    @click="runEvaluation"
                  >
                    立即测评
                  </el-button>
                  <el-button
                    size="small"
                    :loading="snapshotBusy"
                    @click="createSnapshot"
                  >
                    创建快照
                  </el-button>
                </div>
              </div>
            </template>

            <template v-if="latestEvaluation">
              <div class="eval-head">
                <div class="eval-title">
                  <el-tag
                    size="small"
                    :type="latestEvaluation.persona_applied ? 'success' : 'info'"
                  >
                    {{ latestEvaluation.persona_label }}
                  </el-tag>
                  <strong>{{ latestEvaluation.week_key }}</strong>
                  <span class="muted">
                    {{ latestEvaluation.source === 'llm' ? '模型测评' : '规则测评' }}
                    · {{ formatTime(latestEvaluation.created_at) }}
                  </span>
                </div>
                <div class="eval-total">
                  <strong>{{ latestEvaluation.total }}</strong>
                  <span>综合分</span>
                </div>
              </div>

              <div class="score-chips">
                <div
                  v-for="(label, key) in SCORE_LABELS"
                  :key="key"
                  class="score-chip"
                >
                  <span>{{ label }}</span>
                  <strong>{{ latestEvaluation.scores[key] ?? '—' }}</strong>
                </div>
              </div>

              <p class="eval-summary">
                {{ latestEvaluation.summary || '报告已生成，点击查看完整内容。' }}
              </p>
              <div
                v-if="latestEvaluation.persona_reason"
                class="eval-reason"
              >
                人格处理：{{ reasonLabel(latestEvaluation.persona_reason) }}
              </div>
              <el-button
                link
                type="primary"
                @click="openReport(latestEvaluation)"
              >
                查看完整报告
              </el-button>
            </template>
            <el-empty
              v-else
              description="还没有测评报告；点「立即测评」先出一份"
              :image-size="64"
            />

            <div class="snapshot-block">
              <div class="snapshot-head">
                <span>版本快照</span>
                <span class="muted">回滚前会自动备份当前状态</span>
              </div>
              <div
                v-if="snapshots.length"
                class="snapshot-list"
              >
                <div
                  v-for="item in snapshots"
                  :key="item.id"
                  class="snapshot-row"
                >
                  <div class="snapshot-info">
                    <strong>{{ item.label || '指挥官快照' }}</strong>
                    <span class="muted">
                      {{ formatTime(item.created_at) }} · {{ snapshotSourceLabel(item.source) }}
                    </span>
                  </div>
                  <el-button
                    v-if="canManage"
                    link
                    type="warning"
                    :loading="restoringId === item.id"
                    @click="restoreSnapshot(item)"
                  >
                    回滚
                  </el-button>
                </div>
              </div>
              <p
                v-else
                class="search-hint"
              >
                还没有快照；需要保留当前配置时先创建一份。
              </p>
            </div>
          </el-card>

          <el-card
            shadow="never"
            class="panel"
          >
            <template #header>
              <div class="panel-head">
                <div>
                  <h2>记忆检索</h2>
                  <p>按问题找同类任务的历史复盘与偏好，不会跨小院。</p>
                </div>
              </div>
            </template>
            <div class="search-row">
              <el-input
                v-model="query"
                placeholder="例如：给小院做宣传 PPT"
                clearable
                @keyup.enter="search"
              />
              <el-button
                type="primary"
                :loading="searching"
                @click="search"
              >
                检索
              </el-button>
            </div>
            <div
              v-if="searching"
              class="search-hint"
            >
              正在检索…
            </div>
            <div
              v-else-if="results.length"
              class="memory-list"
            >
              <article
                v-for="item in results"
                :key="item.id"
                class="memory-item"
              >
                <div class="memory-head">
                  <el-tag
                    size="small"
                    effect="plain"
                  >
                    {{ kindLabel(item.kind) }}
                  </el-tag>
                  <strong>{{ item.title }}</strong>
                  <span class="score">{{ Math.round((item.score || 0) * 100) }}%</span>
                </div>
                <p class="pre-line">
                  {{ item.content }}
                </p>
              </article>
            </div>
            <p
              v-else-if="searched"
              class="search-hint"
            >
              没有匹配到记忆；先跑一次任务，系统会自动沉淀复盘。
            </p>
            <p
              v-else
              class="search-hint"
            >
              输入一个问题，看看指挥官记得什么。
            </p>
          </el-card>

          <el-card
            v-if="canManage"
            shadow="never"
            class="panel assets-panel"
          >
            <template #header>
              <div class="panel-head">
                <div>
                  <h2>经验资产</h2>
                  <p>成功流程复用、失败场景避坑、Worker 技能入库，全部按小院隔离。</p>
                </div>
                <div class="panel-actions">
                  <el-button
                    size="small"
                    :loading="assetBusy === 'scan'"
                    @click="scanAssets"
                  >
                    扫描沉淀
                  </el-button>
                </div>
              </div>
            </template>

            <div class="asset-counts">
              <div class="asset-count">
                <strong>{{ assetCounts.workflows }}</strong>
                <span>标准工作流</span>
              </div>
              <div class="asset-count">
                <strong>{{ assetCounts.failures }}</strong>
                <span>失败黑名单</span>
              </div>
              <div class="asset-count">
                <strong>{{ assetCounts.skills }}</strong>
                <span>生效技能</span>
              </div>
            </div>

            <el-tabs
              v-model="assetTab"
              class="asset-tabs"
            >
              <el-tab-pane
                label="工作流"
                name="workflows"
              >
                <div
                  v-if="workflows.length"
                  class="asset-list"
                >
                  <div
                    v-for="item in workflows"
                    :key="item.id"
                    class="asset-row"
                  >
                    <div class="asset-main">
                      <div class="asset-title">
                        <strong>{{ item.name }}</strong>
                        <el-tag
                          size="small"
                          effect="plain"
                        >
                          {{ item.code }} · v{{ item.version }}
                        </el-tag>
                      </div>
                      <span class="muted">
                        {{ item.steps.length }} 步 · 成功 {{ item.success_count }} 次
                        · {{ compact(item.cost_baseline_tokens) }} tokens
                      </span>
                      <p class="asset-scene">
                        {{ item.scene }}
                      </p>
                    </div>
                    <el-button
                      link
                      :type="item.status === 'active' ? 'warning' : 'primary'"
                      :loading="assetBusy === item.id"
                      @click="toggleWorkflow(item)"
                    >
                      {{ item.status === 'active' ? '下线' : '启用' }}
                    </el-button>
                  </div>
                </div>
                <p
                  v-else
                  class="search-hint"
                >
                  还没有工作流；成功跑一轮同类任务后会自动沉淀。
                </p>
              </el-tab-pane>

              <el-tab-pane
                :label="`失败黑名单 ${assetCounts.failures}`"
                name="failures"
              >
                <div
                  v-if="failures.length"
                  class="asset-list"
                >
                  <div
                    v-for="item in failures"
                    :key="item.id"
                    class="asset-row"
                  >
                    <div class="asset-main">
                      <div class="asset-title">
                        <el-tag
                          size="small"
                          :type="item.status === 'active' ? 'danger' : 'info'"
                          effect="plain"
                        >
                          {{ item.error_code }}
                        </el-tag>
                        <strong>{{ failureStatusLabel(item.status) }}</strong>
                        <span class="muted">累计 {{ item.occurrences }} 次</span>
                      </div>
                      <p class="asset-scene">
                        {{ item.scene }}
                      </p>
                      <p class="asset-fix">
                        规避：{{ item.avoidance }}
                      </p>
                    </div>
                    <el-button
                      link
                      :type="item.status === 'resolved' ? 'primary' : 'success'"
                      :loading="assetBusy === item.id"
                      @click="toggleFailure(item)"
                    >
                      {{ item.status === 'resolved' ? '重新启用' : '标记已解决' }}
                    </el-button>
                  </div>
                </div>
                <p
                  v-else
                  class="search-hint"
                >
                  暂无达到阈值的失败场景；同类错误累计到阈值后会自动入列。
                </p>
              </el-tab-pane>

              <el-tab-pane
                :label="`技能片段 ${assetCounts.skills}`"
                name="skills"
              >
                <div
                  v-if="skills.length"
                  class="asset-list"
                >
                  <div
                    v-for="item in skills"
                    :key="item.id"
                    class="asset-row"
                  >
                    <div class="asset-main">
                      <div class="asset-title">
                        <strong>{{ item.name }}</strong>
                        <el-tag
                          size="small"
                          :type="item.status === 'active' ? 'success' : 'info'"
                          effect="plain"
                        >
                          {{ skillStatusLabel(item.status) }}
                        </el-tag>
                      </div>
                      <span class="muted">
                        {{ item.source_agent_code }} · {{ item.sample_count }} 个样本
                        · 成功率 {{ Math.round((item.success_rate || 0) * 100) }}%
                      </span>
                    </div>
                    <el-button
                      link
                      :type="item.status === 'active' ? 'warning' : 'primary'"
                      :loading="assetBusy === item.id"
                      @click="toggleSkill(item)"
                    >
                      {{ item.status === 'active' ? '归档' : '转正' }}
                    </el-button>
                  </div>
                </div>
                <p
                  v-else
                  class="search-hint"
                >
                  暂无技能片段；同一 Worker 同类任务稳定成功 2 次后自动转正。
                </p>
              </el-tab-pane>
            </el-tabs>
          </el-card>

          <el-card
            v-if="canManage"
            shadow="never"
            class="panel evolution-panel"
          >
            <template #header>
              <div class="panel-head">
                <div>
                  <h2>进化报告</h2>
                  <p>日复盘看细节、周报告调策略、月度迭代留版本，每次都能回滚。</p>
                </div>
                <div class="panel-actions">
                  <el-button
                    size="small"
                    :loading="evolutionBusy === 'daily'"
                    @click="openDailyReport"
                  >
                    日复盘
                  </el-button>
                  <el-button
                    size="small"
                    :loading="evolutionBusy === 'weekly'"
                    @click="openWeeklyReport"
                  >
                    周优化
                  </el-button>
                  <el-button
                    size="small"
                    type="primary"
                    :loading="evolutionBusy === 'iteration'"
                    @click="createIteration"
                  >
                    生成月度迭代
                  </el-button>
                </div>
              </div>
            </template>

            <div
              v-if="iterations.length"
              class="iteration-list"
            >
              <div
                v-for="item in iterations"
                :key="item.id"
                class="iteration-row"
              >
                <div class="iteration-main">
                  <div class="iteration-title">
                    <el-tag
                      size="small"
                      effect="plain"
                    >
                      {{ item.version_label }}
                    </el-tag>
                    <strong>{{ item.month_key }}</strong>
                    <span class="muted">{{ personaText(item.persona) }}</span>
                  </div>
                  <p class="iteration-summary">
                    {{ item.summary }}
                  </p>
                </div>
                <div class="iteration-actions">
                  <el-button
                    link
                    type="primary"
                    @click="openIteration(item)"
                  >
                    查看报告
                  </el-button>
                  <el-button
                    link
                    type="warning"
                    :disabled="!item.snapshot_id"
                    :loading="restoringId === item.snapshot_id"
                    @click="restoreIteration(item)"
                  >
                    回滚
                  </el-button>
                </div>
              </div>
            </div>
            <p
              v-else
              class="search-hint"
            >
              还没有月度版本；点「生成月度迭代」为上一个完整月建一版，并自动保存迭代前快照。
            </p>
          </el-card>

          <el-card
            shadow="never"
            class="panel"
          >
            <template #header>
              <div class="panel-head">
                <div>
                  <h2>成本结构</h2>
                  <p>按用途拆分，优先盯住规划与文档生成。</p>
                </div>
              </div>
            </template>
            <div
              v-if="overview.cost.by_purpose.length"
              class="cost-list"
            >
              <div
                v-for="item in overview.cost.by_purpose.slice(0, 6)"
                :key="item.purpose"
                class="cost-row"
              >
                <div class="cost-meta">
                  <span>{{ purposeLabel(item.purpose) }}</span>
                  <span class="muted">{{ compact(item.tokens) }} tokens · {{ item.calls }} 次</span>
                </div>
                <el-progress
                  :percentage="costPercent(item.tokens)"
                  :show-text="false"
                  :stroke-width="6"
                />
              </div>
            </div>
            <el-empty
              v-else
              description="暂无模型调用记录"
              :image-size="72"
            />
          </el-card>
        </div>
      </section>
    </template>

    <el-dialog
      v-model="reportVisible"
      :title="reportTitle"
      width="760px"
      append-to-body
    >
      <pre class="report-body">{{ reportText }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { useAuthStore } from '@/stores/auth'
  import {
    commanderWorkspaceApi,
    type CommanderAssets,
    type CommanderEvaluation,
    type CommanderFailureCase,
    type CommanderIteration,
    type CommanderMemoryItem,
    type CommanderOverview,
    type CommanderSkillSnippet,
    type CommanderSnapshot,
    type CommanderWorkflow,
  } from '@/api/commanderWorkspace'
  import {
    COMMANDER_PERSONA_LABELS,
    type CommanderPersona,
    type CommanderRun,
  } from '@/api/commander'

  const router = useRouter()
  const auth = useAuthStore()

  const overview = ref<CommanderOverview | null>(null)
  const loading = ref(false)
  const error = ref('')
  const summarizing = ref(false)
  const searching = ref(false)
  const searched = ref(false)
  const query = ref('')
  const results = ref<CommanderMemoryItem[]>([])
  const runningEval = ref(false)
  const snapshotBusy = ref(false)
  const restoringId = ref('')
  const reportVisible = ref(false)
  const reportTitle = ref('测评报告')
  const reportText = ref('')
  const assets = ref<CommanderAssets | null>(null)
  const assetTab = ref('workflows')
  const assetBusy = ref('')
  const iterations = ref<CommanderIteration[]>([])
  const evolutionBusy = ref('')

  const SCORE_LABELS: Record<string, string> = {
    persona: '人格一致性',
    scheduling: '决策调度',
    cost: '成本管控',
    recovery: '故障处理',
    learning: '跨 Agent 学习',
  }

  const latestEvaluation = computed<CommanderEvaluation | null>(
    () => overview.value?.governance?.latest_evaluation || null,
  )
  const snapshots = computed<CommanderSnapshot[]>(
    () => overview.value?.governance?.snapshots || [],
  )
  const workflows = computed<CommanderWorkflow[]>(() => assets.value?.workflows || [])
  const failures = computed<CommanderFailureCase[]>(() => assets.value?.failures || [])
  const skills = computed<CommanderSkillSnippet[]>(() => assets.value?.skills || [])
  const assetCounts = computed(() => {
    const counts = assets.value?.counts || overview.value?.governance?.assets
    return {
      workflows: Number(counts?.workflows || 0),
      failures: Number(counts?.failures || 0),
      skills: Number(counts?.skills || 0),
    }
  })

  const personaLabel = computed(() => {
    const persona = overview.value?.commander.persona || 'balanced'
    return COMMANDER_PERSONA_LABELS[persona] || '平衡型'
  })

  function reasonLabel(reason?: string | null): string {
    const labels: Record<string, string> = {
      applied: '已按建议调整人格',
      same: '与当前人格一致，无需调整',
      persona_locked: '人格已锁定，仅存档建议',
      evolution_locked: '进化锁开启，暂停自动调整',
      auto_persona_off: '自动切换人格已关闭',
      insufficient_samples: '样本不足，暂不切换',
    }
    return labels[String(reason || '')] || '仅生成报告'
  }

  function snapshotSourceLabel(source?: string): string {
    const labels: Record<string, string> = {
      manual: '手动创建',
      persona: '人格调整前',
      restore: '回滚前备份',
      monthly: '月度迭代前',
    }
    return labels[String(source || '')] || '自动备份'
  }

  function personaText(persona?: string | null): string {
    const key = String(persona || 'balanced') as CommanderPersona
    return COMMANDER_PERSONA_LABELS[key] || '平衡型'
  }

  function failureStatusLabel(status?: string): string {
    const labels: Record<string, string> = {
      watching: '观察中',
      active: '已验证的坑',
      resolved: '已解决',
    }
    return labels[String(status || '')] || '观察中'
  }

  function skillStatusLabel(status?: string): string {
    const labels: Record<string, string> = {
      candidate: '候选',
      active: '已转正',
      archived: '已归档',
    }
    return labels[String(status || '')] || '候选'
  }

  const canManage = computed(() =>
    auth.roles.some(role => ['yard_owner', 'yard_admin', 'platform_admin'].includes(role)),
  )
  const displayName = computed(() => overview.value?.commander.display_name || '指挥官')
  const lastRunTokens = computed(() => Number(overview.value?.runs?.[0]?.tokens_total || 0))
  const budgetPercent = computed(() => {
    const budget = Number(overview.value?.commander.token_budget || 0)
    if (!budget) return 0
    return Math.min(100, Math.round((lastRunTokens.value / budget) * 100))
  })

  const RUN_STATUS_LABELS: Record<string, string> = {
    queued: '排队中',
    running: '执行中',
    completed: '已完成',
    partial: '部分完成',
    failed: '失败',
    cancelled: '已取消',
    preview: '预览',
  }

  function statusLabel(status?: string): string {
    return RUN_STATUS_LABELS[String(status || '')] || String(status || '未知')
  }

  function statusTone(status?: string): 'success' | 'warning' | 'danger' | 'info' {
    if (status === 'completed') return 'success'
    if (status === 'partial' || status === 'running' || status === 'queued') return 'warning'
    if (status === 'failed') return 'danger'
    return 'info'
  }

  function compact(value?: number | null): string {
    const n = Number(value || 0)
    if (!Number.isFinite(n) || n <= 0) return '0'
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
    if (n >= 10_000) return `${(n / 1_000).toFixed(1)}k`
    return n.toLocaleString()
  }

  function percent(value?: number | null): string {
    const n = Number(value || 0)
    return `${Math.round(Math.max(0, Math.min(1, n)) * 100)}%`
  }

  function money(value?: number | null): string {
    return Number(value || 0).toFixed(2)
  }

  function costPercent(tokens: number): number {
    const max = Math.max(...(overview.value?.cost.by_purpose || []).map(item => item.tokens), 1)
    return Math.round((Number(tokens || 0) / max) * 100)
  }

  function purposeLabel(value?: string): string {
    const labels: Record<string, string> = {
      commander_plan: '计划规划',
      commander_merge: '汇总',
      declarative: '助手执行',
      embedding: '向量检索',
      eval: '评测',
      tool: '工具',
      platform: '平台',
    }
    return labels[String(value || '')] || String(value || '其他')
  }

  function kindLabel(value?: string): string {
    const labels: Record<string, string> = {
      episode: '运行复盘',
      summary: '日复盘',
      preference: '用户偏好',
      fact: '业务事实',
    }
    return labels[String(value || '')] || '记忆'
  }

  function formatTime(value?: string | null): string {
    if (!value) return '—'
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return '—'
    return date.toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  function subtaskSummary(run: CommanderRun): string {
    const list = run.subtasks || []
    if (!list.length) return '—'
    const ok = list.filter(item => item.status === 'ok').length
    return `${ok}/${list.length}`
  }

  async function load(): Promise<void> {
    loading.value = true
    error.value = ''
    try {
      overview.value = await commanderWorkspaceApi.overview(30)
    } catch {
      error.value = '指挥台数据加载失败，请稍后重试。'
    } finally {
      loading.value = false
    }
  }

  async function search(): Promise<void> {
    const text = query.value.trim()
    if (!text) {
      results.value = []
      searched.value = false
      return
    }
    searching.value = true
    try {
      const data = await commanderWorkspaceApi.searchMemories({ query: text, top_k: 5 })
      results.value = data.items || []
      searched.value = true
    } catch {
      ElMessage.error('记忆检索失败，请稍后重试')
    } finally {
      searching.value = false
    }
  }

  async function generateSummary(): Promise<void> {
    summarizing.value = true
    try {
      const day = new Date().toISOString().slice(0, 10)
      const data = await commanderWorkspaceApi.summarize(day)
      if (data.created) {
        ElMessage.success('日复盘已生成')
        await load()
      } else {
        ElMessage.info(data.reason || '当天没有可复盘的运行')
      }
    } catch {
      ElMessage.error('生成日复盘失败')
    } finally {
      summarizing.value = false
    }
  }

  function openRun(run: CommanderRun): void {
    if (run.session_id) {
      void router.push({ path: '/chat', query: { session_id: run.session_id } })
    }
  }

  function openChat(): void {
    void router.push('/chat')
  }

  function openReport(item: CommanderEvaluation): void {
    reportTitle.value = `测评报告 · ${item.week_key} · ${personaText(item.persona)}`
    reportText.value = item.report_md || item.summary || '报告内容为空'
    reportVisible.value = true
  }

  async function runEvaluation(): Promise<void> {
    runningEval.value = true
    try {
      const data = await commanderWorkspaceApi.runEvaluation({ use_llm: true, force: true })
      ElMessage.success(data.created ? '测评报告已生成' : '测评报告已刷新')
      await load()
      openReport(data.evaluation)
    } catch {
      ElMessage.error('测评失败，请稍后重试')
    } finally {
      runningEval.value = false
    }
  }

  async function createSnapshot(): Promise<void> {
    snapshotBusy.value = true
    try {
      const label = `手动快照 · ${new Date().toLocaleString('zh-CN')}`
      await commanderWorkspaceApi.createSnapshot({ label, reason: '手动创建' })
      ElMessage.success('快照已创建')
      await load()
    } catch {
      ElMessage.error('创建快照失败')
    } finally {
      snapshotBusy.value = false
    }
  }

  async function restoreSnapshot(item: Pick<CommanderSnapshot, 'id' | 'label'>): Promise<void> {
    try {
      await ElMessageBox.confirm(
        `将把指挥官设置回滚到「${item.label || '该快照'}」，当前状态会先自动备份。`,
        '回滚快照',
        { type: 'warning', confirmButtonText: '确认回滚', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    restoringId.value = item.id
    try {
      await commanderWorkspaceApi.restoreSnapshot(item.id)
      ElMessage.success('已回滚到该快照')
      await load()
    } catch {
      ElMessage.error('回滚失败，请稍后重试')
    } finally {
      restoringId.value = ''
    }
  }

  async function openDailyReport(): Promise<void> {
    evolutionBusy.value = 'daily'
    try {
      const data = await commanderWorkspaceApi.dailyReport()
      reportTitle.value = `日复盘 · ${data.day}`
      reportText.value = data.markdown
      reportVisible.value = true
    } catch {
      ElMessage.error('日复盘加载失败，请稍后重试')
    } finally {
      evolutionBusy.value = ''
    }
  }

  async function openWeeklyReport(): Promise<void> {
    evolutionBusy.value = 'weekly'
    try {
      const data = await commanderWorkspaceApi.weeklyReport()
      reportTitle.value = `周优化报告 · ${data.week_key}`
      reportText.value = data.markdown
      reportVisible.value = true
    } catch {
      ElMessage.error('周优化报告加载失败，请稍后重试')
    } finally {
      evolutionBusy.value = ''
    }
  }

  function openIteration(item: CommanderIteration): void {
    reportTitle.value = `月度迭代 · ${item.version_label}`
    reportText.value = item.report_md || item.summary || '报告内容为空'
    reportVisible.value = true
  }

  async function createIteration(): Promise<void> {
    try {
      await ElMessageBox.confirm(
        '将以上一个完整月的数据生成月度迭代版本，并自动保存迭代前快照；同月重复生成不会覆盖已有版本。',
        '生成月度迭代',
        { type: 'info', confirmButtonText: '开始生成', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    evolutionBusy.value = 'iteration'
    try {
      const data = await commanderWorkspaceApi.runIteration()
      ElMessage.success(data.created ? '月度迭代已生成' : '该月份已有迭代版本')
      await loadIterations()
      openIteration(data.iteration)
    } catch {
      ElMessage.error('生成月度迭代失败，请稍后重试')
    } finally {
      evolutionBusy.value = ''
    }
  }

  async function restoreIteration(item: CommanderIteration): Promise<void> {
    if (!item.snapshot_id) return
    await restoreSnapshot({ id: item.snapshot_id, label: item.version_label })
  }

  async function loadIterations(): Promise<void> {
    if (!canManage.value) return
    try {
      const data = await commanderWorkspaceApi.listIterations()
      iterations.value = data.items
    } catch {
      // 进化报告失败不影响主面板；保留空态即可
      iterations.value = []
    }
  }

  async function loadAssets(): Promise<void> {
    if (!canManage.value) return
    try {
      assets.value = await commanderWorkspaceApi.assets()
    } catch {
      ElMessage.error('经验资产加载失败，请稍后重试')
    }
  }

  async function scanAssets(): Promise<void> {
    assetBusy.value = 'scan'
    try {
      const data = await commanderWorkspaceApi.scanAssets()
      assets.value = data
      ElMessage.success(
        `已扫描 ${data.runs_scanned} 次运行，新增 ${data.workflows_created} 个工作流`,
      )
      await load()
    } catch {
      ElMessage.error('扫描沉淀失败，请稍后重试')
    } finally {
      assetBusy.value = ''
    }
  }

  async function toggleWorkflow(item: CommanderWorkflow): Promise<void> {
    assetBusy.value = item.id
    try {
      const status = item.status === 'active' ? 'archived' : 'active'
      await commanderWorkspaceApi.updateWorkflow(item.id, { status })
      ElMessage.success(status === 'active' ? '工作流已启用' : '工作流已下线')
      await loadAssets()
      await load()
    } catch {
      ElMessage.error('工作流更新失败')
    } finally {
      assetBusy.value = ''
    }
  }

  async function toggleFailure(item: CommanderFailureCase): Promise<void> {
    assetBusy.value = item.id
    try {
      const status = item.status === 'resolved' ? 'active' : 'resolved'
      await commanderWorkspaceApi.updateFailure(item.id, status)
      ElMessage.success(status === 'resolved' ? '已标记为已解决' : '已重新启用')
      await loadAssets()
      await load()
    } catch {
      ElMessage.error('失败案例更新失败')
    } finally {
      assetBusy.value = ''
    }
  }

  async function toggleSkill(item: CommanderSkillSnippet): Promise<void> {
    assetBusy.value = item.id
    try {
      const status = item.status === 'active' ? 'archived' : 'active'
      await commanderWorkspaceApi.updateSkill(item.id, status)
      ElMessage.success(status === 'active' ? '技能已转正' : '技能已归档')
      await loadAssets()
      await load()
    } catch {
      ElMessage.error('技能片段更新失败')
    } finally {
      assetBusy.value = ''
    }
  }

  onMounted(() => {
    void load()
    void loadAssets()
    void loadIterations()
  })
</script>

<style scoped>
  .commander-page {
    display: flex;
    flex-direction: column;
    gap: 18px;
    padding: 4px 2px 28px;
  }

  .page-head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }

  .eyebrow {
    margin: 0 0 4px;
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--ds-text-3);
  }

  h1 {
    margin: 0;
    font-size: 24px;
    font-weight: 650;
    color: var(--ds-text-1);
  }

  .sub {
    margin: 6px 0 0;
    font-size: 13px;
    color: var(--ds-text-3);
  }

  .head-tags {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 8px;
  }

  .head-actions {
    display: flex;
    gap: 8px;
  }

  .state-box {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
    padding: 24px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
    color: var(--ds-text-2);
    background: var(--ds-bg-1);
  }

  .stat-grid {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
  }

  .stat-card {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 16px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
    background: var(--ds-bg-1);
  }

  .stat-label {
    font-size: 12px;
    color: var(--ds-text-3);
  }

  .stat-value {
    font-size: 26px;
    line-height: 1.15;
    color: var(--ds-text-1);
  }

  .stat-note {
    font-size: 11.5px;
    color: var(--ds-text-3);
  }

  .main-grid {
    display: grid;
    grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr);
    gap: 14px;
    align-items: start;
  }

  .column {
    display: flex;
    flex-direction: column;
    gap: 14px;
    min-width: 0;
  }

  .panel {
    border-radius: var(--ds-radius-card);
  }

  .panel :deep(.el-card__header) {
    padding: 14px 16px;
  }

  .panel :deep(.el-card__body) {
    padding: 14px 16px;
  }

  .panel-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
  }

  .panel-head h2 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .panel-head p {
    margin: 3px 0 0;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }

  .summary-title {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .summary-body p {
    margin: 8px 0 0;
    font-size: 12.5px;
    line-height: 1.75;
    color: var(--ds-text-2);
  }

  .pre-line {
    white-space: pre-line;
  }

  .muted {
    font-size: 11.5px;
    font-weight: 400;
    color: var(--ds-text-3);
  }

  .panel-actions {
    display: flex;
    gap: 6px;
    flex-shrink: 0;
  }

  .eval-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
  }

  .eval-title {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    min-width: 0;
  }

  .eval-title strong {
    font-size: 13px;
    color: var(--ds-text-1);
  }

  .eval-total {
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    line-height: 1.1;
    flex-shrink: 0;
  }

  .eval-total strong {
    font-size: 22px;
    font-weight: 650;
    color: var(--ds-text-1);
  }

  .eval-total span {
    font-size: 11px;
    color: var(--ds-text-3);
  }

  .score-chips {
    display: grid;
    grid-template-columns: repeat(5, minmax(0, 1fr));
    gap: 6px;
    margin-top: 12px;
  }

  .score-chip {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 8px 6px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-chip, 8px);
    background: var(--ds-bg-1);
    text-align: center;
  }

  .score-chip span {
    font-size: 10.5px;
    color: var(--ds-text-3);
    white-space: nowrap;
  }

  .score-chip strong {
    font-size: 15px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .eval-summary {
    margin: 12px 0 0;
    font-size: 12.5px;
    line-height: 1.7;
    color: var(--ds-text-2);
  }

  .eval-reason {
    margin-top: 6px;
    font-size: 12px;
    color: var(--ds-text-3);
  }

  .snapshot-block {
    margin-top: 16px;
    padding-top: 14px;
    border-top: 1px dashed var(--ds-border);
  }

  .snapshot-head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 10px;
    font-size: 12.5px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .snapshot-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-top: 10px;
    max-height: 220px;
    overflow-y: auto;
  }

  .snapshot-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
  }

  .snapshot-info {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .snapshot-info strong {
    font-size: 12.5px;
    font-weight: 500;
    color: var(--ds-text-1);
  }

  .report-body {
    margin: 0;
    max-height: 62vh;
    overflow: auto;
    padding: 14px;
    border-radius: 10px;
    background: var(--ds-bg-1);
    font-family: var(--ds-font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
    font-size: 12.5px;
    line-height: 1.7;
    color: var(--ds-text-2);
    white-space: pre-wrap;
    word-break: break-word;
  }

  .asset-counts {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 8px;
  }

  .asset-count {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 10px 6px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-chip, 8px);
    background: var(--ds-bg-1);
    text-align: center;
  }

  .asset-count strong {
    font-size: 19px;
    font-weight: 650;
    color: var(--ds-text-1);
  }

  .asset-count span {
    font-size: 11px;
    color: var(--ds-text-3);
  }

  .asset-tabs {
    margin-top: 10px;
  }

  .asset-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-height: 320px;
    overflow-y: auto;
    padding-right: 2px;
  }

  .asset-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 10px;
    padding-bottom: 9px;
    border-bottom: 1px dashed var(--ds-border);
  }

  .asset-row:last-child {
    padding-bottom: 0;
    border-bottom: 0;
  }

  .asset-main {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
  }

  .asset-title {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    min-width: 0;
  }

  .asset-title strong {
    font-size: 12.5px;
    font-weight: 600;
    color: var(--ds-text-1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 240px;
  }

  .asset-scene {
    margin: 0;
    font-size: 11.5px;
    line-height: 1.6;
    color: var(--ds-text-3);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .asset-fix {
    margin: 0;
    font-size: 11.5px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }

  .iteration-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .iteration-row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 14px;
    padding: 10px 12px;
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-field);
  }
  .iteration-main {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .iteration-title {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .iteration-summary {
    margin: 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }
  .iteration-actions {
    display: flex;
    gap: 4px;
    flex-shrink: 0;
  }

  @media (max-width: 620px) {
    .score-chips {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  .run-table {
    width: 100%;
    cursor: pointer;
  }

  .search-row {
    display: flex;
    gap: 8px;
  }

  .search-hint {
    margin: 12px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }

  .memory-list {
    display: flex;
    flex-direction: column;
    gap: 10px;
    margin-top: 12px;
    max-height: 360px;
    overflow: auto;
  }

  .memory-item {
    padding: 10px 12px;
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-bg-2);
  }

  .memory-head {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12.5px;
    color: var(--ds-text-1);
  }

  .memory-head strong {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .memory-head .score {
    margin-left: auto;
    font-size: 11px;
    color: var(--ds-text-3);
  }

  .memory-item p {
    margin: 6px 0 0;
    font-size: 12px;
    line-height: 1.65;
    color: var(--ds-text-2);
    display: -webkit-box;
    -webkit-line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }

  .cost-list {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .cost-meta {
    display: flex;
    justify-content: space-between;
    gap: 10px;
    margin-bottom: 5px;
    font-size: 12px;
    color: var(--ds-text-2);
  }

  @media (max-width: 1200px) {
    .stat-grid {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 900px) {
    .main-grid {
      grid-template-columns: minmax(0, 1fr);
    }
  }

  @media (max-width: 620px) {
    .stat-grid {
      grid-template-columns: minmax(0, 1fr);
    }

    .page-head {
      align-items: flex-start;
    }
  }
</style>
