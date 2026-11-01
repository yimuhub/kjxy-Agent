<template>
  <el-card
    class="agent-panel"
    shadow="never"
  >
    <template #header>
      <div class="agent-head">
        <div class="agent-title">
          <strong>Agent 自动执行</strong>
          <el-tag
            size="small"
            :type="overview?.enabled ? 'success' : 'info'"
          >
            {{ overview?.enabled ? '已启用' : '未启用' }}
          </el-tag>
          <el-tag
            v-if="overview?.weather_configured"
            size="small"
            type="success"
            effect="plain"
          >
            天气预报已接入
          </el-tag>
          <el-tag
            v-else
            size="small"
            type="info"
            effect="plain"
          >
            仅设备实况
          </el-tag>
          <span
            v-if="overview?.last_run"
            class="agent-sub"
          >
            上次运行 {{ relative(overview.last_run.finished_at || overview.last_run.created_at) }}
            · {{ RUN_STATUS_LABEL[overview.last_run.status] }}
          </span>
          <span
            v-else
            class="agent-sub"
          >
            还没有运行记录
          </span>
        </div>
        <div class="agent-actions">
          <el-badge
            v-if="overview?.pending_count"
            :value="overview.pending_count"
            type="danger"
          >
            <el-button
              size="small"
              disabled
            >
              待确认
            </el-button>
          </el-badge>
          <el-button
            size="small"
            @click="openRuns"
          >
            运行记录
          </el-button>
          <el-button
            v-if="canManage"
            size="small"
            @click="goSettings"
          >
            设置
          </el-button>
          <el-button
            v-if="canWrite"
            size="small"
            type="primary"
            :loading="running"
            @click="runNow"
          >
            立即运行
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      v-if="overview && !overview.enabled"
      type="info"
      :closable="false"
      show-icon
      class="agent-hint"
      title="未启用自动执行：不会按周期自己跑；点「立即运行」可手动执行一轮，结果同样会落库与推送。"
    />

    <div
      v-if="overview?.last_run"
      class="agent-summary"
    >
      <div class="summary-head">
        <el-tag
          size="small"
          :type="LEVEL_TAG[overview.last_run.level]"
        >
          {{ overview.last_run.level_label }}
        </el-tag>
        <span class="summary-text">
          {{ overview.last_run.summary || '本轮没有需要处理的异常。' }}
        </span>
      </div>
      <div
        v-if="overview.last_run.evidence?.warnings?.length"
        class="summary-warnings"
      >
        证据缺口：{{ overview.last_run.evidence.warnings.join('；') }}
      </div>
      <div class="summary-meta">
        设备 {{ overview.last_run.evidence?.devices ?? 0 }} 台 ·
        台账记录 {{ overview.last_run.evidence?.records_30d ?? 0 }} 条 ·
        发现 {{ overview.last_run.findings.length }} 条 ·
        今日已执行 {{ overview.today_executed }} 个动作
      </div>
    </div>

    <div class="pending-head">
      <strong>待确认动作</strong>
      <span class="agent-sub">按策略为「待确认」的告警/工单/诊断等，确认后才会真正写入。</span>
    </div>

    <el-empty
      v-if="overview && !overview.pending.length"
      description="暂无待确认动作。"
      :image-size="60"
    />
    <div
      v-else
      class="pending-list"
    >
      <div
        v-for="item in overview?.pending || []"
        :key="item.id"
        class="pending-item"
        :class="`level-${item.severity}`"
      >
        <div class="pending-main">
          <div class="pending-line">
            <el-tag
              size="small"
              type="warning"
            >
              {{ item.kind_label }}
            </el-tag>
            <strong>{{ item.title }}</strong>
            <span class="agent-sub">{{ item.rule_label }}</span>
          </div>
          <p class="pending-detail">
            {{ item.detail }}
          </p>
        </div>
        <div
          v-if="canWrite"
          class="pending-actions"
        >
          <el-button
            size="small"
            type="primary"
            :loading="busyId === item.id"
            @click="confirm(item)"
          >
            执行
          </el-button>
          <el-button
            size="small"
            :disabled="busyId === item.id"
            @click="dismiss(item)"
          >
            忽略
          </el-button>
        </div>
      </div>
    </div>

    <el-dialog
      v-model="runsOpen"
      append-to-body
      title="Agent 运行记录"
      width="820px"
    >
      <el-table
        v-col-resize
        v-loading="runsLoading"
        :data="runs"
        size="small"
        row-key="run_id"
        @expand-change="onExpand"
      >
        <el-table-column
          type="expand"
        >
          <template #default="{ row }">
            <div class="run-detail">
              <p class="run-summary">
                {{ row.summary }}
              </p>
              <div
                v-if="row.findings.length"
                class="finding-list"
              >
                <div
                  v-for="f in row.findings"
                  :key="f.dedupe_key"
                  class="finding-item"
                >
                  <el-tag
                    size="small"
                    :type="LEVEL_TAG[f.level]"
                  >
                    {{ f.level_label }}
                  </el-tag>
                  <span class="finding-title">{{ f.title }}</span>
                  <div class="finding-act">
                    建议：{{ f.suggestion }}
                  </div>
                </div>
              </div>
              <div
                v-if="row.actions?.length"
                class="run-actions"
              >
                <div
                  v-for="a in row.actions"
                  :key="a.id"
                  class="run-action"
                >
                  <el-tag size="small">
                    {{ a.kind_label }}
                  </el-tag>
                  <span>{{ a.title }}</span>
                  <el-tag
                    size="small"
                    :type="ACTION_STATUS_TAG[a.status]"
                  >
                    {{ ACTION_STATUS_LABEL[a.status] }}
                  </el-tag>
                  <span
                    v-if="a.error"
                    class="run-error"
                  >{{ a.error }}</span>
                </div>
              </div>
              <p
                v-else
                class="agent-sub"
              >
                本轮没有产出动作（可能被策略关闭或在降噪窗口内）。
              </p>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          label="时间"
          width="150"
        >
          <template #default="{ row }">
            {{ timeText(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column
          label="触发"
          width="80"
        >
          <template #default="{ row }">
            {{ row.trigger === 'schedule' ? '定时' : '手动' }}
          </template>
        </el-table-column>
        <el-table-column
          label="状态"
          width="100"
        >
          <template #default="{ row }">
            <el-tag
              size="small"
              :type="RUN_STATUS_TAG[row.status]"
            >
              {{ RUN_STATUS_LABEL[row.status] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="发现 / 动作"
          width="100"
        >
          <template #default="{ row }">
            {{ row.findings.length }} / {{ row.actions?.length ?? 0 }}
          </template>
        </el-table-column>
        <el-table-column
          prop="summary"
          label="结论"
          show-overflow-tooltip
        />
      </el-table>
      <el-pagination
        class="runs-pager"
        layout="prev, pager, next, total"
        :total="runsTotal"
        :page-size="pageSize"
        :current-page="page"
        @current-change="loadRuns"
      />
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { useRouter } from 'vue-router'
  import {
    farmAgentApi,
    type FarmAgentAction,
    type FarmAgentOverview,
    type FarmAgentRun,
  } from '@/api/modules'
  import { useAuthStore } from '@/stores/auth'

  const emit = defineEmits<{ (e: 'changed'): void }>()
  const router = useRouter()
  const auth = useAuthStore()

  const canWrite = computed(() =>
    auth.hasAnyRole(['yard_owner', 'yard_admin', 'platform_admin', 'expert', 'operator']),
  )
  const canManage = computed(() => auth.isYardAdmin())
  const overview = ref<FarmAgentOverview | null>(null)
  const running = ref(false)
  const busyId = ref('')

  const runsOpen = ref(false)
  const runsLoading = ref(false)
  const runs = ref<FarmAgentRun[]>([])
  const runsTotal = ref(0)
  const page = ref(1)
  const pageSize = 10

  const LEVEL_TAG: Record<string, 'info' | 'warning' | 'danger' | 'success'> = {
    info: 'info',
    watch: 'warning',
    warning: 'warning',
    critical: 'danger',
  }
  const RUN_STATUS_LABEL: Record<string, string> = {
    running: '运行中',
    ok: '正常完成',
    insufficient: '证据不足',
    failed: '运行失败',
  }
  const RUN_STATUS_TAG: Record<string, 'info' | 'success' | 'warning' | 'danger'> = {
    running: 'info',
    ok: 'success',
    insufficient: 'warning',
    failed: 'danger',
  }
  const ACTION_STATUS_LABEL: Record<string, string> = {
    pending: '待确认',
    executed: '已执行',
    dismissed: '已忽略',
    failed: '失败',
  }
  const ACTION_STATUS_TAG: Record<string, 'warning' | 'success' | 'info' | 'danger'> = {
    pending: 'warning',
    executed: 'success',
    dismissed: 'info',
    failed: 'danger',
  }

  function timeText(value: string | null): string {
    if (!value) return '—'
    return new Date(value).toLocaleString('zh-CN', { hour12: false })
  }

  function relative(value: string | null): string {
    if (!value) return '未知'
    const diff = Date.now() - new Date(value).getTime()
    if (diff < 60_000) return '刚刚'
    if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
    if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
    return new Date(value).toLocaleDateString('zh-CN')
  }

  async function load(): Promise<void> {
    try {
      overview.value = await farmAgentApi.overview()
    } catch {
      // 面板属于辅助信息：失败时保持静默，不打断台账主流程
    }
  }

  async function runNow(): Promise<void> {
    running.value = true
    try {
      const result = await farmAgentApi.runNow()
      if (result.status === 'insufficient') {
        ElMessage.warning(result.summary || '证据不足，本轮未产出预警。')
      } else {
        ElMessage.success(`已执行一轮：${result.findings.length} 条发现`)
      }
      await load()
      emit('changed')
    } catch {
      /* 错误 toast 由 client 统一处理 */
    } finally {
      running.value = false
    }
  }

  async function confirm(item: FarmAgentAction): Promise<void> {
    busyId.value = item.id
    try {
      await farmAgentApi.confirm(item.id)
      ElMessage.success('已执行该动作')
      await load()
      emit('changed')
    } catch {
      /* 统一 toast */
    } finally {
      busyId.value = ''
    }
  }

  async function dismiss(item: FarmAgentAction): Promise<void> {
    try {
      await ElMessageBox.confirm(`忽略后本轮不再处理「${item.title}」，确定忽略？`, '忽略动作', {
        type: 'warning',
        confirmButtonText: '忽略',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    busyId.value = item.id
    try {
      await farmAgentApi.dismiss(item.id)
      ElMessage.success('已忽略')
      await load()
    } catch {
      /* 统一 toast */
    } finally {
      busyId.value = ''
    }
  }

  function goSettings(): void {
    router.push({ path: '/settings', query: { tab: 'farm_agent' } })
  }

  async function loadRuns(next = 1): Promise<void> {
    page.value = next
    runsLoading.value = true
    try {
      const result = await farmAgentApi.runs(next, pageSize)
      runs.value = result.items
      runsTotal.value = result.meta.total
    } catch {
      /* 统一 toast */
    } finally {
      runsLoading.value = false
    }
  }

  async function openRuns(): Promise<void> {
    runsOpen.value = true
    await loadRuns(1)
  }

  async function onExpand(row: FarmAgentRun): Promise<void> {
    if (row.actions) return
    try {
      const detail = await farmAgentApi.run(row.run_id)
      const index = runs.value.findIndex(item => item.run_id === row.run_id)
      if (index >= 0) runs.value[index] = detail
    } catch {
      /* 统一 toast */
    }
  }

  onMounted(load)
</script>

<style scoped>
  .agent-panel {
    margin-bottom: 16px;
  }
  .agent-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .agent-title {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .agent-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .agent-sub {
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .agent-hint {
    margin-bottom: 12px;
  }
  .agent-summary {
    border: 1px solid var(--ds-border);
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 12px;
    background: var(--ds-bg-2);
  }
  .summary-head {
    display: flex;
    gap: 8px;
    align-items: flex-start;
  }
  .summary-text {
    white-space: pre-wrap;
    line-height: 1.6;
    font-size: 13px;
  }
  .summary-warnings {
    margin-top: 6px;
    font-size: 12px;
    color: var(--el-color-warning);
  }
  .summary-meta {
    margin-top: 6px;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .pending-head {
    display: flex;
    align-items: baseline;
    gap: 8px;
    margin-bottom: 8px;
  }
  .pending-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .pending-item {
    display: flex;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 12px;
    border-radius: 8px;
    border: 1px solid var(--ds-border);
    border-left: 4px solid var(--ds-border);
  }
  .pending-item.level-warning {
    border-left-color: var(--el-color-warning);
  }
  .pending-item.level-critical {
    border-left-color: var(--el-color-danger);
  }
  .pending-line {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .pending-detail {
    margin: 6px 0 0;
    font-size: 12px;
    color: var(--ds-text-2);
    white-space: pre-wrap;
    line-height: 1.6;
  }
  .pending-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-shrink: 0;
  }
  .run-detail {
    padding: 4px 8px 8px;
  }
  .run-summary {
    white-space: pre-wrap;
    line-height: 1.7;
    margin: 0 0 8px;
  }
  .finding-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .finding-item {
    font-size: 13px;
  }
  .finding-title {
    margin-left: 6px;
    font-weight: 600;
  }
  .finding-act {
    font-size: 12px;
    color: var(--ds-text-2);
    margin-top: 2px;
  }
  .run-actions {
    margin-top: 10px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .run-action {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    flex-wrap: wrap;
  }
  .run-error {
    color: var(--el-color-danger);
  }
  .runs-pager {
    margin-top: 12px;
    justify-content: flex-end;
  }
</style>
