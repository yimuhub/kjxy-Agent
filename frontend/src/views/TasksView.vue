<template>
  <div class="tasks">
    <div class="page-head">
      <h2 class="page-title">
        任务中心
      </h2>
      <div class="page-actions">
        <el-select
          v-model="statusFilter"
          placeholder="全部状态"
          clearable
          style="width: 160px"
          @change="() => loadTasks()"
        >
          <el-option
            v-for="(meta, key) in TASK_STATUS_META"
            :key="key"
            :label="meta.label"
            :value="key"
          />
        </el-select>
        <el-button
          type="primary"
          @click="() => loadTasks()"
        >
          刷新
        </el-button>
      </div>
    </div>

    <!-- 高风险工具待审批 -->
    <el-card
      v-if="pendingApprovals.length"
      class="approval-card"
    >
      <template #header>
        <span>待审批的高风险操作（{{ pendingApprovals.length }}）</span>
      </template>
      <el-table
        :data="pendingApprovals"
        stripe
      >
        <el-table-column
          prop="tool_code"
          label="工具"
          width="200"
        />
        <el-table-column
          label="参数"
          min-width="220"
        >
          <template #default="{ row }">
            <pre class="json-inline">{{ pretty(row.params) }}</pre>
          </template>
        </el-table-column>
        <el-table-column
          prop="created_at"
          label="申请时间"
          width="180"
        >
          <template #default="{ row }">
            {{ formatTime(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="180"
        >
          <template #default="{ row }">
            <el-button
              link
              type="success"
              @click="openApproveDialog(row, 'approve')"
            >
              批准
            </el-button>
            <el-button
              link
              type="danger"
              @click="openApproveDialog(row, 'reject')"
            >
              驳回
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card>
      <AsyncState
        :state="listState"
        empty-text="暂无任务"
        @retry="loadTasks"
      >
        <el-table
          :data="tasks"
          stripe
        >
          <el-table-column
            label="任务名称"
            min-width="280"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <span class="task-title">{{ row.title || row.task_type }}</span>
            </template>
          </el-table-column>
          <el-table-column
            label="类型"
            width="150"
          >
            <template #default="{ row }">
              {{ row.task_type_label || row.task_type }}
            </template>
          </el-table-column>
          <el-table-column
            prop="id"
            label="任务 ID"
            width="180"
            show-overflow-tooltip
          />
          <el-table-column
            label="状态"
            width="110"
          >
            <template #default="{ row }">
              <el-tag :type="taskStatusMeta(row.status).type">
                {{ taskStatusMeta(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="进度"
            width="160"
          >
            <template #default="{ row }">
              <el-progress
                v-if="row.progress != null"
                :percentage="Math.round(row.progress)"
              />
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column
            prop="created_at"
            label="创建时间"
            width="180"
          >
            <template #default="{ row }">
              {{ formatTime(row.created_at) }}
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="220"
          >
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                @click="openDrawer(row as Task)"
              >
                详情
              </el-button>
              <el-button
                v-if="['queued', 'running'].includes(row.status)"
                link
                type="warning"
                @click="doCancel(row as Task)"
              >
                取消
              </el-button>
              <el-button
                v-if="row.status === 'failed'"
                link
                type="success"
                @click="doRetry(row as Task)"
              >
                重试
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          layout="prev, pager, next, total"
          class="pager"
          @change="() => loadTasks()"
        />
      </AsyncState>
    </el-card>

    <!-- 任务详情抽屉 -->
    <el-dialog
      v-model="drawerVisible"
      append-to-body
      title="任务详情"
      width="520px"
      align-center
      @close="closeDetailStream"
    >
      <AsyncState
        :state="detailState"
        empty-text="任务不存在"
        @retry="loadDetail"
      >
        <el-descriptions
          :column="1"
          border
        >
          <el-descriptions-item label="任务名称">
            {{ detail?.title || detail?.task_type }}
          </el-descriptions-item>
          <el-descriptions-item label="类型">
            {{ detail?.task_type_label || detail?.task_type }}
          </el-descriptions-item>
          <el-descriptions-item label="任务 ID">
            {{ detail?.id }}
          </el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag
              v-if="detail"
              :type="taskStatusMeta(detail.status).type"
            >
              {{ taskStatusMeta(detail.status).label }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="优先级">
            {{ detail?.priority ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="失败原因">
            {{ detail?.error_message || detail?.error_code || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">
            {{
              formatTime(detail?.created_at)
            }}
          </el-descriptions-item>
          <el-descriptions-item label="开始时间">
            {{
              formatTime(detail?.started_at)
            }}
          </el-descriptions-item>
          <el-descriptions-item label="完成时间">
            {{
              formatTime(detail?.finished_at)
            }}
          </el-descriptions-item>
          <el-descriptions-item label="耗时">
            {{
              formatDuration(detail?.started_at, detail?.finished_at)
            }}
          </el-descriptions-item>
        </el-descriptions>

        <h4>输入</h4>
        <pre class="json">{{ pretty(detail?.input) }}</pre>
        <h4>输出</h4>
        <pre class="json">{{ pretty(detail?.output) }}</pre>

        <h4>进度事件</h4>
        <el-timeline v-if="events.length">
          <el-timeline-item
            v-for="e in events"
            :key="e.id ?? e.seq"
            :timestamp="formatTime(e.created_at)"
          >
            {{ e.message || e.event_type }}
          </el-timeline-item>
        </el-timeline>
        <el-empty
          v-else
          description="暂无进度事件"
          :image-size="60"
        />
      </AsyncState>
    </el-dialog>
    <!-- 审批确认对话框 -->
    <el-dialog
      v-model="approvalDialogVisible"
      append-to-body
      :title="approvalAction === 'approve' ? '批准高风险操作' : '驳回高风险操作'"
      width="480px"
    >
      <el-descriptions
        :column="1"
        border
      >
        <el-descriptions-item label="工具">
          {{ currentApproval?.tool_code }}
        </el-descriptions-item>
        <el-descriptions-item label="参数">
          <pre class="json-inline">{{ pretty(currentApproval?.params) }}</pre>
        </el-descriptions-item>
      </el-descriptions>
      <el-form style="margin-top: 12px">
        <el-form-item :label="approvalAction === 'approve' ? '批准备注' : '驳回理由'">
          <el-input
            v-model="approvalNote"
            type="textarea"
            :rows="3"
            placeholder="可选"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approvalDialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="approvalSubmitting"
          @click="submitApproval"
        >
          确定
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, onUnmounted, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { approvalApi, taskApi } from '@/api/modules'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, TASK_STATUS_META, taskStatusMeta, type ViewState } from '@/utils/status'
  import { formatDuration, formatTime } from '@/utils/format'
  import { openSseStream, type SseHandle } from '@/utils/sse'
  import type { Task, TaskEvent, ToolApproval } from '@/types/api'

  const tasks = ref<Task[]>([])
  const page = ref(1)
  const pageSize = ref(20)
  const total = ref(0)
  const statusFilter = ref('')
  const listState = ref<ViewState>('loading')

  const pendingApprovals = ref<ToolApproval[]>([])
  const approvalDialogVisible = ref(false)
  const approvalAction = ref<'approve' | 'reject'>('approve')
  const currentApproval = ref<ToolApproval | null>(null)
  const approvalNote = ref('')
  const approvalSubmitting = ref(false)

  const drawerVisible = ref(false)
  const detail = ref<Task | null>(null)
  const events = ref<TaskEvent[]>([])
  const detailState = ref<ViewState>('loading')

  function pretty(v: unknown): string {
    if (v === undefined || v === null) return '—'
    try {
      return JSON.stringify(v, null, 2)
    } catch {
      return String(v)
    }
  }

  /** silent：轮询刷新时不重置为 loading，避免列表闪烁 */
  async function loadTasks(silent = false) {
    if (!silent) listState.value = 'loading'
    try {
      const res = await taskApi.list({
        page: page.value,
        page_size: pageSize.value,
        status: statusFilter.value || undefined,
      })
      tasks.value = res.items
      total.value = res.meta.total
      if (!silent) listState.value = res.items.length ? 'ready' : 'empty'
      else if (!res.items.length) listState.value = 'empty'
    } catch (e) {
      if (!silent) listState.value = classifyError(e)
    }
    loadApprovals()
    ensurePolling()
  }

  // ---------- 活跃任务轮询：存在 queued/running 时 5s 刷新一次 ----------
  const POLL_INTERVAL_MS = 5000
  let pollTimer: number | undefined

  function hasActiveTasks(): boolean {
    return tasks.value.some(t => t.status === 'queued' || t.status === 'running')
  }

  function ensurePolling() {
    const active = hasActiveTasks()
    if (active && pollTimer === undefined) {
      pollTimer = window.setInterval(() => loadTasks(true), POLL_INTERVAL_MS)
    } else if (!active && pollTimer !== undefined) {
      window.clearInterval(pollTimer)
      pollTimer = undefined
    }
  }

  onUnmounted(() => {
    if (pollTimer !== undefined) {
      window.clearInterval(pollTimer)
      pollTimer = undefined
    }
    closeDetailStream()
  })

  async function loadApprovals() {
    try {
      const res = await approvalApi.list({ status: 'pending', page_size: 50 })
      pendingApprovals.value = res.items
    } catch {
      /* 无权限查看时静默忽略（普通成员） */
      pendingApprovals.value = []
    }
  }

  function openApproveDialog(row: ToolApproval, action: 'approve' | 'reject') {
    currentApproval.value = row
    approvalAction.value = action
    approvalNote.value = ''
    approvalDialogVisible.value = true
  }

  async function submitApproval() {
    if (!currentApproval.value?.id) return
    approvalSubmitting.value = true
    try {
      if (approvalAction.value === 'approve') {
        await approvalApi.approve(currentApproval.value.id, approvalNote.value || undefined)
        ElMessage.success('已批准并执行')
      } else {
        await approvalApi.reject(currentApproval.value.id, approvalNote.value || undefined)
        ElMessage.success('已驳回')
      }
      approvalDialogVisible.value = false
      loadTasks()
      if (drawerVisible.value) loadDetail()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      approvalSubmitting.value = false
    }
  }

  function openDrawer(task: Task) {
    detail.value = task
    drawerVisible.value = true
    loadDetail()
  }

  async function loadDetail() {
    if (!detail.value) return
    const id = detail.value.id
    detailState.value = 'loading'
    try {
      const [t, evts] = await Promise.all([taskApi.get(id), taskApi.events(id)])
      detail.value = t
      events.value = evts
      detailState.value = 'ready'
      // 详情打开且任务未进终态 → 订阅 SSE 事件流（借鉴 agency-orchestrator：
      // 执行过程实时上屏；关闭抽屉只是停止"看"，服务端任务照常 running）
      if (drawerVisible.value && detail.value?.id === id && !TERMINAL_STATUSES.has(t.status)) {
        openDetailStream(id)
      }
    } catch (e) {
      detailState.value = classifyError(e)
    }
  }

  // ---------- 详情 SSE 订阅：断连自动重连≤3 次，仍失败降级回详情轮询 ----------
  const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled'])
  const DETAIL_POLL_MS = 5000
  let detailStream: SseHandle | null = null
  let detailPollTimer: number | undefined

  function closeDetailStream() {
    detailStream?.close()
    detailStream = null
    stopDetailPolling()
  }

  function stopDetailPolling() {
    if (detailPollTimer !== undefined) {
      window.clearInterval(detailPollTimer)
      detailPollTimer = undefined
    }
  }

  /** SSE 不可用时的降级：抽屉保持打开时每 5s 静默刷新一次详情 */
  function startDetailPolling() {
    if (detailPollTimer !== undefined) return
    detailPollTimer = window.setInterval(async () => {
      if (!drawerVisible.value || !detail.value) return stopDetailPolling()
      try {
        const [t, evts] = await Promise.all([taskApi.get(detail.value.id), taskApi.events(detail.value.id)])
        detail.value = t
        events.value = evts
        if (TERMINAL_STATUSES.has(t.status)) stopDetailPolling()
      } catch {
        /* 静默：下一轮再试 */
      }
    }, DETAIL_POLL_MS)
  }

  function openDetailStream(id: string) {
    closeDetailStream()
    const base = import.meta.env.VITE_API_BASE ?? '/api/v1'
    detailStream = openSseStream({
      url: `${base}/tasks/${id}/events/stream`,
      onFrame: frame => {
        if (frame.event === 'task-event') {
          try {
            const e = JSON.parse(frame.data) as TaskEvent
            if (!events.value.some(x => x.id === e.id)) events.value.push(e)
          } catch {
            /* 忽略坏帧 */
          }
        } else if (frame.event === 'task-status') {
          try {
            const s = JSON.parse(frame.data) as { status: Task['status'] }
            if (detail.value?.id === id) detail.value = { ...detail.value, status: s.status }
          } catch {
            /* 忽略坏帧 */
          }
        } else if (frame.event === 'stream-end') {
          // 服务端已收尾：主动关闭避免 EOF 触发重连，然后静默刷新一次拿终态与输出
          detailStream?.close()
          detailStream = null
          void loadDetailQuiet(id)
        }
      },
      onFallback: startDetailPolling,
    })
  }

  async function loadDetailQuiet(id: string) {
    if (!detail.value || detail.value.id !== id) return
    try {
      const [t, evts] = await Promise.all([taskApi.get(id), taskApi.events(id)])
      detail.value = t
      events.value = evts
    } catch {
      /* 静默 */
    }
  }

  async function doCancel(task: Task) {
    try {
      await ElMessageBox.confirm(`确定取消任务 ${task.id} 吗？`, '取消任务', { type: 'warning' })
    } catch {
      return
    }
    try {
      await taskApi.cancel(task.id)
      ElMessage.success('已取消')
      loadTasks()
      if (drawerVisible.value) loadDetail()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  async function doRetry(task: Task) {
    try {
      await taskApi.retry(task.id)
      ElMessage.success('已重新入队')
      loadTasks()
      if (drawerVisible.value) loadDetail()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  onMounted(loadTasks)
</script>

<style scoped>
  .task-title {
    font-weight: 600;
  }
  .tasks {
    padding: 4px;
  }
  .page-head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 16px;
  }
  .page-head .page-title {
    margin-bottom: 0;
  }
  .pager {
    margin-top: 16px;
    justify-content: flex-end;
  }
  .json {
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    padding: 12px;
    font-size: 12px;
    max-height: 240px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-all;
  }
  /* 详情弹窗内进度事件随 SSE 实时追加：限高滚动，避免长列表顶高整个弹窗 */
  .el-timeline {
    max-height: 240px;
    overflow-y: auto;
  }
  .approval-card {
    margin-bottom: 16px;
  }
  .json-inline {
    background: var(--ds-glass);
    border-radius: var(--ds-radius-control);
    padding: 8px;
    font-size: 12px;
    max-height: 120px;
    overflow: auto;
    white-space: pre-wrap;
    word-break: break-all;
    margin: 0;
  }
</style>
