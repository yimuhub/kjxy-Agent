<template>
  <div class="tasks">
    <h2 class="page-title">任务中心</h2>

    <!-- 高风险工具待审批 -->
    <el-card v-if="pendingApprovals.length" class="approval-card">
      <template #header>
        <span>待审批的高风险操作（{{ pendingApprovals.length }}）</span>
      </template>
      <el-table :data="pendingApprovals" stripe>
        <el-table-column prop="tool_code" label="工具" width="200" />
        <el-table-column label="参数" min-width="220">
          <template #default="{ row }">
            <pre class="json-inline">{{ pretty(row.params) }}</pre>
          </template>
        </el-table-column>
        <el-table-column prop="created_at" label="申请时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="180">
          <template #default="{ row }">
            <el-button link type="success" @click="openApproveDialog(row, 'approve')">批准</el-button>
            <el-button link type="danger" @click="openApproveDialog(row, 'reject')">驳回</el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-card>
      <div class="toolbar">
        <el-select v-model="statusFilter" placeholder="全部状态" clearable style="width: 160px" @change="loadTasks">
          <el-option v-for="(meta, key) in TASK_STATUS_META" :key="key" :label="meta.label" :value="key" />
        </el-select>
        <el-button type="primary" @click="loadTasks">刷新</el-button>
      </div>
      <AsyncState :state="listState" empty-text="暂无任务" @retry="loadTasks">
        <el-table :data="tasks" stripe>
          <el-table-column prop="id" label="任务 ID" width="200" show-overflow-tooltip />
          <el-table-column prop="task_type" label="类型" width="160" />
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag :type="taskStatusMeta(row.status).type">
                {{ taskStatusMeta(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="进度" width="160">
            <template #default="{ row }">
              <el-progress v-if="row.progress != null" :percentage="Math.round(row.progress)" />
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" width="180">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="220">
            <template #default="{ row }">
              <el-button link type="primary" @click="openDrawer(row)">详情</el-button>
              <el-button
                v-if="['queued', 'running'].includes(row.status)"
                link
                type="warning"
                @click="doCancel(row)"
              >
                取消
              </el-button>
              <el-button v-if="row.status === 'failed'" link type="success" @click="doRetry(row)">
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
          @change="loadTasks"
        />
      </AsyncState>
    </el-card>

    <!-- 任务详情抽屉 -->
    <el-drawer v-model="drawerVisible" title="任务详情" size="520px">
      <AsyncState :state="detailState" empty-text="任务不存在" @retry="loadDetail">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="任务 ID">{{ detail?.id }}</el-descriptions-item>
          <el-descriptions-item label="类型">{{ detail?.task_type }}</el-descriptions-item>
          <el-descriptions-item label="状态">
            <el-tag v-if="detail" :type="taskStatusMeta(detail.status).type">
              {{ taskStatusMeta(detail.status).label }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="优先级">{{ detail?.priority ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="失败原因">
            {{ detail?.error_message || detail?.error_code || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ formatTime(detail?.created_at) }}</el-descriptions-item>
          <el-descriptions-item label="完成时间">{{ formatTime(detail?.finished_at) }}</el-descriptions-item>
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
        <el-empty v-else description="暂无进度事件" :image-size="60" />
      </AsyncState>
    </el-drawer>
    <!-- 审批确认对话框 -->
    <el-dialog v-model="approvalDialogVisible" :title="approvalAction === 'approve' ? '批准高风险操作' : '驳回高风险操作'" width="480px">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="工具">{{ currentApproval?.tool_code }}</el-descriptions-item>
        <el-descriptions-item label="参数"><pre class="json-inline">{{ pretty(currentApproval?.params) }}</pre></el-descriptions-item>
      </el-descriptions>
      <el-form style="margin-top: 12px">
        <el-form-item :label="approvalAction === 'approve' ? '批准备注' : '驳回理由'">
          <el-input v-model="approvalNote" type="textarea" :rows="3" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approvalDialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="approvalSubmitting" @click="submitApproval">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { approvalApi, taskApi } from '@/api/modules'
import AsyncState from '@/components/AsyncState.vue'
import { classifyError, TASK_STATUS_META, taskStatusMeta, type ViewState } from '@/utils/status'
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

function formatTime(t?: string | null): string {
  if (!t) return '—'
  const d = new Date(t)
  return isNaN(d.getTime()) ? t : d.toLocaleString('zh-CN')
}

function pretty(v: unknown): string {
  if (v === undefined || v === null) return '—'
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

async function loadTasks() {
  listState.value = 'loading'
  try {
    const res = await taskApi.list({ page: page.value, page_size: pageSize.value, status: statusFilter.value || undefined })
    tasks.value = res.items
    total.value = res.meta.total
    listState.value = res.items.length ? 'ready' : 'empty'
  } catch (e) {
    listState.value = classifyError(e)
  }
  loadApprovals()
}

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
  } catch (e) {
    detailState.value = classifyError(e)
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
.tasks {
  padding: 4px;
}
.page-title {
  margin: 0 0 16px;
}
.toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 16px;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
.json {
  background: #f5f7fa;
  border: 1px solid #ebeef5;
  border-radius: 4px;
  padding: 12px;
  font-size: 12px;
  max-height: 240px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
.approval-card {
  margin-bottom: 16px;
}
.json-inline {
  background: #f5f7fa;
  border-radius: 4px;
  padding: 8px;
  font-size: 12px;
  max-height: 120px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}
</style>
