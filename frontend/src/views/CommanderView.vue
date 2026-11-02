<template>
  <div class="commander-page">
    <header class="page-head">
      <div class="head-copy">
        <p class="eyebrow">
          指挥官 · 指挥台
        </p>
        <h1>{{ displayName }}工作台</h1>
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
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage } from 'element-plus'
  import { useAuthStore } from '@/stores/auth'
  import {
    commanderWorkspaceApi,
    type CommanderMemoryItem,
    type CommanderOverview,
  } from '@/api/commanderWorkspace'
  import type { CommanderRun } from '@/api/commander'

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

  onMounted(load)
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
