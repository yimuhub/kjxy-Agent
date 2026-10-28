<template>
  <div class="workorders">
    <div class="page-head">
      <div>
        <h2 class="page-title">
          告警与工单
        </h2>
        <p class="page-sub">
          告警转工单、指派到人、处置回执全程留痕；critical 告警自动建单，逾期未完成由系统自动升级。
        </p>
      </div>
      <div class="head-actions">
        <el-button
          v-if="canWrite"
          @click="openAlertDialog"
        >
          上报告警
        </el-button>
        <el-button
          v-if="canWrite"
          type="primary"
          @click="openCreate"
        >
          新建工单
        </el-button>
      </div>
    </div>

    <el-tabs v-model="tab">
      <el-tab-pane
        label="工单"
        name="orders"
      >
        <div class="filters">
          <el-select
            v-model="statusFilter"
            clearable
            placeholder="全部状态"
            class="w150"
            @change="loadOrders(1)"
          >
            <el-option
              v-for="s in STATUS_OPTIONS"
              :key="s.value"
              :label="s.label"
              :value="s.value"
            />
          </el-select>
          <el-select
            v-model="priorityFilter"
            clearable
            placeholder="全部优先级"
            class="w150"
            @change="loadOrders(1)"
          >
            <el-option
              v-for="p in PRIORITY_OPTIONS"
              :key="p.value"
              :label="p.label"
              :value="p.value"
            />
          </el-select>
          <el-button @click="loadOrders(1)">
            刷新
          </el-button>
        </div>

        <el-empty
          v-if="!loading && !orders.length"
          description="暂无工单。可以从告警转单，或直接为地块创建一张处置工单。"
        />

        <el-table
          v-else
          v-loading="loading"
          :data="orders"
          size="small"
        >
          <el-table-column
            label="创建时间"
            width="140"
          >
            <template #default="{ row }">
              {{ formatTime(row.created_at) }}
            </template>
          </el-table-column>
          <el-table-column
            prop="title"
            label="标题"
            min-width="200"
            show-overflow-tooltip
          />
          <el-table-column
            label="优先级"
            width="100"
          >
            <template #default="{ row }">
              <el-tag
                :type="priorityTag(row.priority)"
                size="small"
                effect="light"
              >
                {{ priorityLabel(row.priority) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="110"
          >
            <template #default="{ row }">
              <el-tag
                :type="statusTag(row.status)"
                size="small"
                effect="plain"
              >
                {{ statusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="负责人"
            width="120"
          >
            <template #default="{ row }">
              {{ row.assignee_name || '未指派' }}
            </template>
          </el-table-column>
          <el-table-column
            label="截止"
            width="110"
          >
            <template #default="{ row }">
              <span :class="{ overdue: isOverdue(row as WorkOrder) }">{{ row.due_on || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            label="来源"
            width="90"
          >
            <template #default="{ row }">
              {{ sourceLabel(row.source) }}
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="110"
            fixed="right"
          >
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                size="small"
                @click="openDetail(row.work_order_id)"
              >
                查看
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <div
          v-if="total > pageSize"
          class="pager"
        >
          <el-pagination
            :current-page="page"
            :page-size="pageSize"
            :total="total"
            layout="prev, pager, next, total"
            @current-change="loadOrders"
          />
        </div>
      </el-tab-pane>

      <el-tab-pane
        label="告警"
        name="alerts"
      >
        <div class="filters">
          <el-select
            v-model="alertSeverityFilter"
            clearable
            placeholder="全部级别"
            class="w150"
            @change="loadAlerts(1)"
          >
            <el-option
              v-for="s in SEVERITY_OPTIONS"
              :key="s.value"
              :label="s.label"
              :value="s.value"
            />
          </el-select>
          <el-button @click="loadAlerts(1)">
            刷新
          </el-button>
        </div>

        <el-empty
          v-if="!alertLoading && !alerts.length"
          description="暂无告警。设备异常、巡田发现都可以在这里上报并转成工单。"
        />

        <el-table
          v-else
          v-loading="alertLoading"
          :data="alerts"
          size="small"
        >
          <el-table-column
            label="时间"
            width="140"
          >
            <template #default="{ row }">
              {{ formatTime(row.created_at) }}
            </template>
          </el-table-column>
          <el-table-column
            prop="title"
            label="告警"
            min-width="200"
            show-overflow-tooltip
          />
          <el-table-column
            label="级别"
            width="100"
          >
            <template #default="{ row }">
              <el-tag
                :type="severityTag(row.severity)"
                size="small"
                effect="light"
              >
                {{ severityLabel(row.severity) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="110"
          >
            <template #default="{ row }">
              {{
                row.work_order_id ? '已转工单' : row.status === 'dismissed' ? '已忽略' : '待处理'
              }}
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="150"
            fixed="right"
          >
            <template #default="{ row }">
              <el-button
                v-if="row.work_order_id"
                link
                type="primary"
                size="small"
                @click="openDetail(row.work_order_id)"
              >
                查看工单
              </el-button>
              <el-button
                v-else-if="canWrite"
                link
                type="primary"
                size="small"
                :loading="convertingId === row.alert_id"
                @click="convertAlert(row.alert_id)"
              >
                转工单
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <div
          v-if="alertTotal > pageSize"
          class="pager"
        >
          <el-pagination
            :current-page="alertPage"
            :page-size="pageSize"
            :total="alertTotal"
            layout="prev, pager, next, total"
            @current-change="loadAlerts"
          />
        </div>
      </el-tab-pane>
    </el-tabs>

    <el-dialog
      v-model="createDialog"
      title="新建工单"
      width="520px"
    >
      <el-form
        label-width="84px"
        label-position="left"
      >
        <el-form-item
          label="标题"
          required
        >
          <el-input
            v-model="createForm.title"
            maxlength="160"
            placeholder="例如：3 号大棚滴灌主管漏水"
          />
        </el-form-item>
        <el-form-item label="说明">
          <el-input
            v-model="createForm.description"
            type="textarea"
            :rows="3"
            maxlength="4000"
            placeholder="现场情况、已采取的措施、需要的支持"
          />
        </el-form-item>
        <el-form-item label="优先级">
          <el-select
            v-model="createForm.priority"
            class="w-full"
          >
            <el-option
              v-for="p in PRIORITY_OPTIONS"
              :key="p.value"
              :label="p.label"
              :value="p.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="地块">
          <el-select
            v-model="createForm.plot_id"
            clearable
            class="w-full"
            placeholder="不关联地块"
          >
            <el-option
              v-for="p in plots"
              :key="p.plot_id"
              :label="p.name"
              :value="p.plot_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="指派给">
          <el-select
            v-model="createForm.assignee_id"
            clearable
            filterable
            class="w-full"
            placeholder="暂不指派"
          >
            <el-option
              v-for="m in members"
              :key="m.user_id"
              :label="m.display_name"
              :value="m.user_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="截止日">
          <el-date-picker
            v-model="createForm.due_on"
            type="date"
            value-format="YYYY-MM-DD"
            class="w-full"
            placeholder="可不填"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="creating"
          @click="submitCreate"
        >
          创建
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="alertDialog"
      title="上报告警"
      width="480px"
    >
      <el-form
        label-width="84px"
        label-position="left"
      >
        <el-form-item
          label="告警内容"
          required
        >
          <el-input
            v-model="alertForm.title"
            maxlength="160"
            placeholder="例如：2 号棚夜间温度低于 8℃"
          />
        </el-form-item>
        <el-form-item label="详情">
          <el-input
            v-model="alertForm.detail"
            type="textarea"
            :rows="3"
            maxlength="4000"
          />
        </el-form-item>
        <el-form-item label="级别">
          <el-select
            v-model="alertForm.severity"
            class="w-full"
          >
            <el-option
              v-for="s in SEVERITY_OPTIONS"
              :key="s.value"
              :label="s.label"
              :value="s.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="地块">
          <el-select
            v-model="alertForm.plot_id"
            clearable
            class="w-full"
            placeholder="不关联地块"
          >
            <el-option
              v-for="p in plots"
              :key="p.plot_id"
              :label="p.name"
              :value="p.plot_id"
            />
          </el-select>
        </el-form-item>
      </el-form>
      <p class="hint">
        级别为「严重」时系统会自动创建紧急工单，不允许只提示不处置。
      </p>
      <template #footer>
        <el-button @click="alertDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="alertSaving"
          @click="submitAlert"
        >
          上报
        </el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="detailDrawer"
      :title="detail ? detail.title : '工单详情'"
      size="520px"
    >
      <div
        v-if="detail"
        v-loading="detailLoading"
        class="detail"
      >
        <div class="detail-meta">
          <el-tag
            :type="statusTag(detail.status)"
            size="small"
            effect="plain"
          >
            {{ statusLabel(detail.status) }}
          </el-tag>
          <el-tag
            :type="priorityTag(detail.priority)"
            size="small"
            effect="light"
          >
            {{ priorityLabel(detail.priority) }}
          </el-tag>
          <span
            v-if="detail.escalation_level > 0"
            class="escalated"
          >已升级 {{ detail.escalation_level }} 次</span>
          <span class="muted">来源：{{ sourceLabel(detail.source) }}</span>
          <span class="muted">创建：{{ formatTime(detail.created_at) }}</span>
          <span class="muted">截止：{{ detail.due_on || '—' }}</span>
        </div>

        <p
          v-if="detail.description"
          class="detail-desc"
        >
          {{ detail.description }}
        </p>

        <div
          v-if="canWrite && !isTerminal(detail.status)"
          class="actions"
        >
          <el-select
            v-model="assigneeDraft"
            clearable
            filterable
            placeholder="指派给"
            class="w180"
          >
            <el-option
              v-for="m in members"
              :key="m.user_id"
              :label="m.display_name"
              :value="m.user_id"
            />
          </el-select>
          <el-button
            size="small"
            :loading="acting"
            @click="applyAssignee"
          >
            指派
          </el-button>
          <el-select
            v-model="statusDraft"
            placeholder="变更状态"
            class="w150"
          >
            <el-option
              v-for="s in transitionOptions(detail.status)"
              :key="s.value"
              :label="s.label"
              :value="s.value"
            />
          </el-select>
          <el-button
            size="small"
            :loading="acting"
            @click="applyStatus"
          >
            更新状态
          </el-button>
          <el-button
            size="small"
            type="warning"
            plain
            :loading="acting"
            @click="runEscalate"
          >
            升级
          </el-button>
        </div>

        <div
          v-if="canWrite && !isTerminal(detail.status)"
          class="receipt"
        >
          <el-input
            v-model="receiptForm.resolution"
            type="textarea"
            :rows="2"
            maxlength="4000"
            placeholder="处置结论：做了什么、结果如何、后续注意"
          />
          <div class="receipt-actions">
            <el-checkbox v-model="receiptForm.close">
              直接关闭（不勾选则标记完成待验收）
            </el-checkbox>
            <el-button
              type="primary"
              size="small"
              :loading="acting"
              @click="submitReceipt"
            >
              提交回执
            </el-button>
          </div>
        </div>

        <div
          v-if="detail.resolution"
          class="resolution"
        >
          <div class="section-title">
            最终回执
          </div>
          <p>{{ detail.resolution }}</p>
          <span class="muted">关闭时间：{{ formatTime(detail.closed_at) }}</span>
        </div>

        <div class="section-title">
          处理记录
        </div>
        <el-timeline>
          <el-timeline-item
            v-for="event in detail.events"
            :key="event.event_id"
            :timestamp="formatTime(event.created_at)"
            placement="top"
          >
            <div class="event">
              <span class="event-kind">{{ eventLabel(event.kind) }}</span>
              <span>{{ event.note }}</span>
            </div>
          </el-timeline-item>
        </el-timeline>

        <div
          v-if="canWrite && !isTerminal(detail.status)"
          class="comment"
        >
          <el-input
            v-model="commentDraft"
            placeholder="补充一条处理记录"
            maxlength="4000"
            @keyup.enter="submitComment"
          />
          <el-button
            size="small"
            :loading="acting"
            @click="submitComment"
          >
            添加
          </el-button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref, watch } from 'vue'
  import { ElMessage } from 'element-plus'
  import {
    farmApi,
    workOrderApi,
    type FarmAlert,
    type FarmPlot,
    type WorkOrder,
    type WorkOrderPriority,
    type WorkOrderStatus,
    type AssignableMember,
    type AlertSeverity,
  } from '@/api/modules'
  import { useAuthStore } from '@/stores/auth'

  const STATUS_OPTIONS: { value: WorkOrderStatus; label: string }[] = [
    { value: 'open', label: '待处理' },
    { value: 'assigned', label: '已指派' },
    { value: 'in_progress', label: '处理中' },
    { value: 'done', label: '待验收' },
    { value: 'closed', label: '已关闭' },
    { value: 'cancelled', label: '已取消' },
  ]
  const PRIORITY_OPTIONS: { value: WorkOrderPriority; label: string }[] = [
    { value: 'low', label: '低' },
    { value: 'normal', label: '普通' },
    { value: 'high', label: '高' },
    { value: 'urgent', label: '紧急' },
  ]
  const SEVERITY_OPTIONS: { value: AlertSeverity; label: string }[] = [
    { value: 'low', label: '低' },
    { value: 'normal', label: '普通' },
    { value: 'high', label: '高' },
    { value: 'critical', label: '严重' },
  ]
  const TRANSITIONS: Record<WorkOrderStatus, WorkOrderStatus[]> = {
    open: ['assigned', 'in_progress', 'cancelled'],
    assigned: ['open', 'in_progress', 'done', 'cancelled'],
    in_progress: ['done', 'cancelled'],
    done: ['in_progress', 'closed'],
    closed: [],
    cancelled: [],
  }
  const TERMINAL: WorkOrderStatus[] = ['closed', 'cancelled']

  const statusLabel = (value: string) => STATUS_OPTIONS.find(s => s.value === value)?.label || value
  const priorityLabel = (value: string) =>
    PRIORITY_OPTIONS.find(p => p.value === value)?.label || value
  const severityLabel = (value: string) =>
    SEVERITY_OPTIONS.find(s => s.value === value)?.label || value
  const statusTag = (value: string): 'success' | 'warning' | 'info' | 'danger' => {
    if (value === 'closed') return 'success'
    if (value === 'cancelled') return 'info'
    if (value === 'done') return 'warning'
    if (value === 'in_progress') return 'warning'
    if (value === 'assigned') return 'info'
    return 'danger'
  }
  const priorityTag = (value: string): 'success' | 'warning' | 'info' | 'danger' => {
    if (value === 'urgent') return 'danger'
    if (value === 'high') return 'warning'
    if (value === 'low') return 'info'
    return 'success'
  }
  const severityTag = (value: string): 'success' | 'warning' | 'info' | 'danger' => {
    if (value === 'critical') return 'danger'
    if (value === 'high') return 'warning'
    if (value === 'low') return 'info'
    return 'success'
  }
  const sourceLabel = (value: string) =>
    ({ manual: '手工', alert: '告警', device: '设备', diagnosis: '诊断' })[value] || value
  const eventLabel = (value: string) =>
    ({
      created: '创建',
      converted: '转单',
      assigned: '指派',
      status: '状态',
      comment: '记录',
      receipt: '回执',
      escalated: '升级',
    })[value] || value

  const auth = useAuthStore()
  const canWrite = computed(() =>
    auth.hasAnyRole(['yard_owner', 'yard_admin', 'platform_admin', 'expert', 'operator']),
  )

  const tab = ref('orders')
  const pageSize = 20

  const loading = ref(false)
  const orders = ref<WorkOrder[]>([])
  const page = ref(1)
  const total = ref(0)
  const statusFilter = ref('')
  const priorityFilter = ref('')

  const alertLoading = ref(false)
  const alerts = ref<FarmAlert[]>([])
  const alertPage = ref(1)
  const alertTotal = ref(0)
  const alertSeverityFilter = ref('')
  const convertingId = ref('')

  const plots = ref<FarmPlot[]>([])
  const members = ref<AssignableMember[]>([])

  const createDialog = ref(false)
  const creating = ref(false)
  const createForm = reactive({
    title: '',
    description: '',
    priority: 'normal' as WorkOrderPriority,
    plot_id: '',
    assignee_id: '',
    due_on: '',
  })

  const alertDialog = ref(false)
  const alertSaving = ref(false)
  const alertForm = reactive({
    title: '',
    detail: '',
    severity: 'normal' as AlertSeverity,
    plot_id: '',
  })

  const detailDrawer = ref(false)
  const detailLoading = ref(false)
  const detail = ref<WorkOrder | null>(null)
  const acting = ref(false)
  const assigneeDraft = ref('')
  const statusDraft = ref<WorkOrderStatus | ''>('')
  const commentDraft = ref('')
  const receiptForm = reactive({ resolution: '', close: true })

  function formatTime(value?: string | null): string {
    if (!value) return '—'
    return value.replace('T', ' ').slice(0, 16)
  }

  function isTerminal(status: string): boolean {
    return TERMINAL.includes(status as WorkOrderStatus)
  }

  function isOverdue(row: WorkOrder): boolean {
    if (!row.due_on || isTerminal(row.status)) return false
    return row.due_on < new Date().toISOString().slice(0, 10)
  }

  function transitionOptions(status: string) {
    const allowed = TRANSITIONS[status as WorkOrderStatus] || []
    return STATUS_OPTIONS.filter(s => allowed.includes(s.value))
  }

  async function loadOrders(nextPage = page.value) {
    page.value = nextPage
    loading.value = true
    try {
      const res = await workOrderApi.list({
        status: statusFilter.value || undefined,
        priority: priorityFilter.value || undefined,
        page: page.value,
        page_size: pageSize,
      })
      orders.value = res.items
      total.value = res.meta.total
    } catch {
      orders.value = []
      total.value = 0
    } finally {
      loading.value = false
    }
  }

  async function loadAlerts(nextPage = alertPage.value) {
    alertPage.value = nextPage
    alertLoading.value = true
    try {
      const res = await workOrderApi.alerts({
        severity: alertSeverityFilter.value || undefined,
        page: alertPage.value,
        page_size: pageSize,
      })
      alerts.value = res.items
      alertTotal.value = res.meta.total
    } catch {
      alerts.value = []
      alertTotal.value = 0
    } finally {
      alertLoading.value = false
    }
  }

  async function loadRefs() {
    try {
      plots.value = await farmApi.plots()
    } catch {
      plots.value = []
    }
    try {
      members.value = await workOrderApi.assignable()
    } catch {
      members.value = []
    }
  }

  function openCreate() {
    Object.assign(createForm, {
      title: '',
      description: '',
      priority: 'normal',
      plot_id: '',
      assignee_id: '',
      due_on: '',
    })
    createDialog.value = true
  }

  async function submitCreate() {
    if (!createForm.title.trim()) {
      ElMessage.warning('请填写工单标题')
      return
    }
    creating.value = true
    try {
      await workOrderApi.create({
        title: createForm.title.trim(),
        description: createForm.description.trim() || undefined,
        priority: createForm.priority,
        plot_id: createForm.plot_id || undefined,
        assignee_id: createForm.assignee_id || undefined,
        due_on: createForm.due_on || undefined,
      })
      ElMessage.success('工单已创建')
      createDialog.value = false
      tab.value = 'orders'
      await loadOrders(1)
    } finally {
      creating.value = false
    }
  }

  function openAlertDialog() {
    Object.assign(alertForm, { title: '', detail: '', severity: 'normal', plot_id: '' })
    alertDialog.value = true
  }

  async function submitAlert() {
    if (!alertForm.title.trim()) {
      ElMessage.warning('请填写告警内容')
      return
    }
    alertSaving.value = true
    try {
      await workOrderApi.createAlert({
        title: alertForm.title.trim(),
        detail: alertForm.detail.trim() || undefined,
        severity: alertForm.severity,
        plot_id: alertForm.plot_id || undefined,
      })
      ElMessage.success(
        alertForm.severity === 'critical' ? '已上报并自动创建紧急工单' : '告警已上报',
      )
      alertDialog.value = false
      await Promise.all([loadAlerts(1), loadOrders(1)])
    } finally {
      alertSaving.value = false
    }
  }

  async function convertAlert(alertId: string) {
    convertingId.value = alertId
    try {
      const order = await workOrderApi.convertAlert(alertId)
      ElMessage.success('已转为工单')
      await Promise.all([loadAlerts(), loadOrders(1)])
      await openDetail(order.work_order_id)
    } finally {
      convertingId.value = ''
    }
  }

  async function openDetail(orderId: string) {
    detailDrawer.value = true
    detailLoading.value = true
    try {
      const data = await workOrderApi.detail(orderId)
      applyDetail(data)
    } catch {
      detail.value = null
    } finally {
      detailLoading.value = false
    }
  }

  function applyDetail(data: WorkOrder) {
    detail.value = data
    assigneeDraft.value = data.assignee_id || ''
    statusDraft.value = ''
    commentDraft.value = ''
    receiptForm.resolution = ''
    receiptForm.close = true
  }

  async function applyAssignee() {
    if (!detail.value) return
    acting.value = true
    try {
      const data = await workOrderApi.update(detail.value.work_order_id, {
        assignee_id: assigneeDraft.value || undefined,
      })
      applyDetail(data)
      await loadOrders()
    } finally {
      acting.value = false
    }
  }

  async function applyStatus() {
    if (!detail.value || !statusDraft.value) {
      ElMessage.warning('请选择目标状态')
      return
    }
    acting.value = true
    const target = statusDraft.value
    try {
      const data = await workOrderApi.update(detail.value.work_order_id, { status: target })
      applyDetail(data)
      await loadOrders()
    } finally {
      acting.value = false
    }
  }

  async function runEscalate() {
    if (!detail.value) return
    acting.value = true
    try {
      const data = await workOrderApi.escalate(detail.value.work_order_id)
      applyDetail(data)
      ElMessage.success(`已升级为${priorityLabel(data.priority)}`)
      await loadOrders()
    } finally {
      acting.value = false
    }
  }

  async function submitComment() {
    if (!detail.value || !commentDraft.value.trim()) return
    acting.value = true
    try {
      const data = await workOrderApi.comment(detail.value.work_order_id, commentDraft.value.trim())
      applyDetail(data)
    } finally {
      acting.value = false
    }
  }

  async function submitReceipt() {
    if (!detail.value) return
    if (!receiptForm.resolution.trim()) {
      ElMessage.warning('请填写处置结论')
      return
    }
    acting.value = true
    try {
      const data = await workOrderApi.receipt(detail.value.work_order_id, {
        resolution: receiptForm.resolution.trim(),
        close: receiptForm.close,
      })
      applyDetail(data)
      ElMessage.success(receiptForm.close ? '已关闭并留存回执' : '已标记完成，待验收')
      await loadOrders()
    } finally {
      acting.value = false
    }
  }

  watch(tab, value => {
    if (value === 'alerts' && !alerts.value.length) loadAlerts(1)
  })

  onMounted(async () => {
    await loadRefs()
    await loadOrders(1)
  })
</script>

<style scoped>
  .workorders {
    padding: 4px 2px 24px;
  }
  .page-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    margin-bottom: 12px;
  }
  .page-title {
    margin: 0 0 4px;
    font-size: 20px;
    font-weight: 600;
  }
  .page-sub {
    margin: 0;
    color: var(--el-text-color-secondary);
    font-size: 13px;
  }
  .head-actions {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 12px;
  }
  .w150 {
    width: 150px;
  }
  .w180 {
    width: 180px;
  }
  .w-full {
    width: 100%;
  }
  .pager {
    display: flex;
    justify-content: flex-end;
    margin-top: 12px;
  }
  .overdue {
    color: var(--el-color-danger);
    font-weight: 600;
  }
  .hint {
    margin: 0;
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }
  .detail-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    margin-bottom: 12px;
  }
  .muted {
    color: var(--el-text-color-secondary);
  }
  .escalated {
    color: var(--el-color-warning);
    font-weight: 600;
  }
  .detail-desc {
    margin: 0 0 16px;
    white-space: pre-wrap;
    line-height: 1.6;
  }
  .actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    padding: 12px;
    border-radius: 8px;
    background: var(--el-fill-color-light);
    margin-bottom: 12px;
  }
  .receipt {
    margin-bottom: 16px;
  }
  .receipt-actions {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 8px;
    gap: 8px;
  }
  .resolution {
    padding: 12px;
    border-left: 3px solid var(--el-color-success);
    background: var(--el-fill-color-lighter);
    border-radius: 4px;
    margin-bottom: 16px;
  }
  .resolution p {
    margin: 4px 0 8px;
    white-space: pre-wrap;
  }
  .section-title {
    font-size: 13px;
    font-weight: 600;
    margin: 12px 0 8px;
  }
  .event {
    display: flex;
    gap: 8px;
    font-size: 13px;
  }
  .event-kind {
    color: var(--el-color-primary);
    flex-shrink: 0;
  }
  .comment {
    display: flex;
    gap: 8px;
    margin-top: 12px;
  }
</style>
