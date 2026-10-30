<template>
  <div class="farm">
    <OfflineBar />
    <div class="page-head">
      <div>
        <h2 class="page-title">
          农业台账
        </h2>
        <p class="page-sub">
          地块、种植周期与农事记录沉淀为可追溯的院内台账；日志/周报/季报按记录确定性生成。
        </p>
      </div>
      <div
        v-if="canWrite"
        class="head-actions"
      >
        <el-button
          type="primary"
          @click="openRecord()"
        >
          记一笔
        </el-button>
        <el-button @click="openPlot()">
          新增地块
        </el-button>
      </div>
    </div>

    <QuickActionBar
      v-if="canWrite"
      module="farm"
      title="农业台账"
      placeholder="说一句：新增地块 东一号田 水稻 12 亩，沙壤土；或 东一号田 今天追肥 尿素 5 公斤"
      :examples="QUICK_EXAMPLES"
      :plot-id="filters.plot_id || undefined"
      @created="onQuickCreated"
      @fallback="onQuickFallback"
    />

    <FarmAgentPanel @changed="onAgentChanged" />

    <el-tabs v-model="tab">
      <el-tab-pane
        label="农事记录"
        name="records"
      >
        <div class="filters">
          <el-select
            v-model="filters.plot_id"
            clearable
            placeholder="全部地块"
            class="w180"
            @change="loadRecords(1)"
          >
            <el-option
              v-for="p in plots"
              :key="p.plot_id"
              :label="p.name"
              :value="p.plot_id"
            />
          </el-select>
          <el-select
            v-model="filters.kind"
            clearable
            placeholder="全部类型"
            class="w150"
            @change="loadRecords(1)"
          >
            <el-option
              v-for="k in KIND_OPTIONS"
              :key="k.value"
              :label="k.label"
              :value="k.value"
            />
          </el-select>
          <el-date-picker
            v-model="filters.range"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            class="w260"
            @change="loadRecords(1)"
          />
          <el-button @click="loadRecords(1)">
            刷新
          </el-button>
        </div>

        <el-empty
          v-if="!loading && !records.length"
          description="还没有农事记录。先建地块，再用「记一笔」补录。"
        >
          <el-button
            v-if="canWrite"
            type="primary"
            @click="plots.length ? openRecord() : openPlot()"
          >
            {{ plots.length ? '记一笔' : '先建地块' }}
          </el-button>
        </el-empty>

        <el-table
          v-else
          v-loading="loading"
          :data="records"
          size="small"
          class="record-table"
        >
          <el-table-column
            prop="occurred_on"
            label="日期"
            width="110"
          />
          <el-table-column
            prop="plot_name"
            label="地块"
            width="130"
            show-overflow-tooltip
          />
          <el-table-column
            label="类型"
            width="100"
          >
            <template #default="{ row }">
              <el-tag size="small">
                {{ row.kind_label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            prop="title"
            label="事项"
            min-width="160"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <el-tag
                v-if="row.source === 'agent'"
                size="small"
                type="success"
                effect="dark"
                class="agent-tag"
              >
                Agent 自动
              </el-tag>
              <span v-if="row.source === 'agent' && !row.title">系统观察记录</span>
              <span v-else>{{ row.title }}</span>
            </template>
          </el-table-column>
          <el-table-column
            label="用量"
            width="110"
          >
            <template #default="{ row }">
              {{ row.quantity != null ? `${row.quantity} ${row.unit}` : '—' }}
            </template>
          </el-table-column>
          <el-table-column
            prop="operator_name"
            label="操作人"
            width="110"
          >
            <template #default="{ row }">
              {{ row.operator_name || '—' }}
            </template>
          </el-table-column>
          <el-table-column
            v-if="canWrite"
            label="操作"
            width="130"
            fixed="right"
          >
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                @click="openRecord(row as FarmRecord)"
              >
                编辑
              </el-button>
              <el-button
                link
                type="danger"
                @click="removeRecord(row as FarmRecord)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>

        <el-pagination
          v-if="total > pageSize"
          class="pager"
          layout="total, prev, pager, next"
          :total="total"
          :page-size="pageSize"
          :current-page="page"
          @current-change="loadRecords"
        />
      </el-tab-pane>

      <el-tab-pane
        label="地块与周期"
        name="plots"
      >
        <el-empty
          v-if="!plots.length"
          description="还没有地块。地块是台账的最小单元。"
        />
        <div
          v-else
          class="plot-grid"
        >
          <div
            v-for="p in plots"
            :key="p.plot_id"
            class="plot-card"
          >
            <div class="plot-head">
              <strong>{{ p.name }}</strong>
              <el-tag
                size="small"
                :type="p.status === 'active' ? 'success' : p.status === 'fallow' ? 'warning' : 'info'"
              >
                {{ PLOT_STATUS_LABEL[p.status] }}
              </el-tag>
            </div>
            <div class="plot-meta">
              <span>{{ p.crop || '未填作物' }}</span>
              <span>{{ p.area_mu ? `${p.area_mu} 亩` : '面积未填' }}</span>
            </div>
            <div class="plot-sub">
              {{ p.location || '位置未填' }} · {{ p.irrigation_type || '灌溉方式未填' }}
            </div>
            <div class="plot-actions">
              <el-button
                link
                type="primary"
                @click="openCycles(p)"
              >
                周期（{{ cycleCounts[p.plot_id] ?? '…' }}）
              </el-button>
              <template v-if="canWrite">
                <el-button
                  link
                  @click="openPlot(p)"
                >
                  编辑
                </el-button>
                <el-button
                  link
                  type="danger"
                  @click="removePlot(p)"
                >
                  删除
                </el-button>
              </template>
            </div>
          </div>
        </div>
      </el-tab-pane>

      <el-tab-pane
        label="日志 / 周报 / 季报"
        name="report"
      >
        <div class="report-form">
          <el-select
            v-model="reportForm.kind"
            class="w150"
          >
            <el-option
              label="农事日志"
              value="log"
            />
            <el-option
              label="农事周报"
              value="weekly"
            />
            <el-option
              label="农事季报"
              value="quarterly"
            />
          </el-select>
          <el-date-picker
            v-model="reportForm.range"
            type="daterange"
            value-format="YYYY-MM-DD"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            class="w260"
          />
          <el-select
            v-model="reportForm.plot_id"
            clearable
            placeholder="全部地块"
            class="w180"
          >
            <el-option
              v-for="p in plots"
              :key="p.plot_id"
              :label="p.name"
              :value="p.plot_id"
            />
          </el-select>
          <el-button
            type="primary"
            :loading="reporting"
            @click="generateReport"
          >
            生成报表
          </el-button>
          <el-button
            v-if="report"
            @click="downloadReport"
          >
            下载 Markdown
          </el-button>
          <el-button
            v-if="report && canWrite"
            @click="openShare"
          >
            对外分享
          </el-button>
        </div>

        <el-empty
          v-if="!report"
          description="选择区间后生成报表；内容只来自本院已录入的农事记录。"
        />
        <div
          v-else
          class="report-card"
        >
          <div class="report-meta">
            共 {{ report.records }} 条记录 · 涉及 {{ report.plots }} 个地块
          </div>
          <SafeMarkdown :content="report.content_md" />
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 对外分享（只读快照） -->
    <el-dialog
      v-model="shareDialog"
      append-to-body
      title="对外分享报表"
      width="600px"
    >
      <p class="share-tip">
        分享内容为创建时的只读快照：已隐去操作人姓名，不含院内其他数据；可随时撤销，过期自动失效。
      </p>
      <div class="share-create">
        <el-select
          v-model="shareDays"
          class="w150"
        >
          <el-option
            label="1 天"
            :value="1"
          />
          <el-option
            label="7 天"
            :value="7"
          />
          <el-option
            label="30 天"
            :value="30"
          />
        </el-select>
        <el-button
          type="primary"
          :loading="sharing"
          :disabled="!report"
          @click="createShare"
        >
          生成分享链接
        </el-button>
      </div>
      <div
        v-if="createdShare"
        class="share-created"
      >
        <el-input
          :model-value="shareUrl(createdShare.path)"
          readonly
        >
          <template #append>
            <el-button @click="copyShareUrl(createdShare.path)">
              复制
            </el-button>
          </template>
        </el-input>
        <span class="muted">有效期至 {{ formatTime(createdShare.expires_at) }}，原始链接只展示这一次。</span>
      </div>

      <div class="share-list">
        <div class="share-list-head">
          <span>已创建的分享</span>
          <el-button
            link
            size="small"
            @click="loadShares"
          >
            刷新
          </el-button>
        </div>
        <el-empty
          v-if="!sharesLoading && !shares.length"
          description="还没有分享链接"
          :image-size="60"
        />
        <el-table
          v-else
          v-loading="sharesLoading"
          :data="shares"
          size="small"
        >
          <el-table-column
            prop="title"
            label="标题"
            min-width="180"
            show-overflow-tooltip
          />
          <el-table-column
            label="访问"
            width="70"
          >
            <template #default="{ row }">
              {{ row.view_count }}
            </template>
          </el-table-column>
          <el-table-column
            label="有效期至"
            width="150"
          >
            <template #default="{ row }">
              {{ formatTime(row.expires_at) }}
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="80"
          >
            <template #default="{ row }">
              <el-button
                link
                type="danger"
                size="small"
                @click="revokeShare(row.share_id)"
              >
                撤销
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </el-dialog>

    <!-- 记录编辑 -->
    <el-dialog
      v-model="recordDialog"
      append-to-body
      :title="recordForm.record_id ? '编辑农事记录' : '记一笔农事'"
      width="620px"
    >
      <el-form
        label-width="88px"
      >
        <el-form-item label="地块">
          <el-select
            v-model="recordForm.plot_id"
            placeholder="选择地块"
            class="full"
          >
            <el-option
              v-for="p in plots"
              :key="p.plot_id"
              :label="p.name"
              :value="p.plot_id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="类型">
          <el-select
            v-model="recordForm.kind"
            class="full"
          >
            <el-option
              v-for="k in KIND_OPTIONS"
              :key="k.value"
              :label="k.label"
              :value="k.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="日期">
          <el-date-picker
            v-model="recordForm.occurred_on"
            type="date"
            value-format="YYYY-MM-DD"
            class="full"
          />
        </el-form-item>
        <el-form-item label="事项">
          <el-input
            v-model="recordForm.title"
            maxlength="120"
            placeholder="如：追施分蘖肥"
          />
        </el-form-item>
        <el-form-item label="详情">
          <el-input
            v-model="recordForm.content"
            type="textarea"
            :rows="4"
            maxlength="5000"
            placeholder="天气、做法、观察结果等（可留空）"
          />
        </el-form-item>
        <el-form-item label="用量">
          <div class="qty-row">
            <el-input-number
              v-model="recordForm.quantity"
              :min="0.01"
              :precision="2"
              controls-position="right"
            />
            <el-input
              v-model="recordForm.unit"
              placeholder="单位（公斤/亩/立方米）"
              class="w180"
            />
          </div>
        </el-form-item>
        <el-form-item label="操作人">
          <el-input
            v-model="recordForm.operator_name"
            maxlength="60"
            placeholder="留空则记为当前账号"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="recordDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="saveRecord"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 地块编辑 -->
    <el-dialog
      v-model="plotDialog"
      append-to-body
      :title="plotForm.plot_id ? '编辑地块' : '新增地块'"
      width="560px"
    >
      <el-form label-width="88px">
        <el-form-item label="名称">
          <el-input
            v-model="plotForm.name"
            maxlength="80"
            placeholder="如：东一号田"
          />
        </el-form-item>
        <el-form-item label="作物">
          <el-input
            v-model="plotForm.crop"
            maxlength="60"
            placeholder="如：水稻"
          />
        </el-form-item>
        <el-form-item label="面积（亩）">
          <el-input-number
            v-model="plotForm.area_mu"
            :min="0.01"
            :precision="2"
            controls-position="right"
          />
        </el-form-item>
        <el-form-item label="位置">
          <el-input
            v-model="plotForm.location"
            maxlength="200"
          />
        </el-form-item>
        <el-form-item label="土壤">
          <el-input
            v-model="plotForm.soil_type"
            maxlength="40"
            placeholder="如：黏壤土"
          />
        </el-form-item>
        <el-form-item label="灌溉">
          <el-input
            v-model="plotForm.irrigation_type"
            maxlength="40"
            placeholder="如：滴灌"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select
            v-model="plotForm.status"
            class="full"
          >
            <el-option
              label="在种"
              value="active"
            />
            <el-option
              label="休耕"
              value="fallow"
            />
            <el-option
              label="归档"
              value="archived"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="plotForm.notes"
            type="textarea"
            :rows="2"
            maxlength="2000"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="plotDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="savePlot"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 周期抽屉 -->
    <el-dialog
      v-model="cycleDrawer"
      append-to-body
      :title="`种植周期 · ${cyclePlot?.name ?? ''}`"
      width="460px"
      align-center
    >
      <el-empty
        v-if="!cycles.length"
        description="该地块还没有周期记录。"
      />
      <div
        v-for="c in cycles"
        :key="c.cycle_id"
        class="cycle-row"
      >
        <div>
          <strong>{{ c.crop }}{{ c.variety ? ` · ${c.variety}` : '' }}</strong>
          <span class="cycle-status">{{ CYCLE_STATUS_LABEL[c.status] }}</span>
        </div>
        <div class="cycle-meta">
          {{ c.planted_on || '未填播种日' }} → {{ c.expected_harvest_on || '未填收获日' }}
        </div>
        <el-button
          v-if="canWrite"
          link
          type="danger"
          @click="removeCycle(c)"
        >
          删除
        </el-button>
      </div>

      <el-divider v-if="canWrite">
        新增周期
      </el-divider>
      <el-form
        v-if="canWrite"
        label-width="80px"
      >
        <el-form-item label="作物">
          <el-input
            v-model="cycleForm.crop"
            maxlength="60"
          />
        </el-form-item>
        <el-form-item label="品种">
          <el-input
            v-model="cycleForm.variety"
            maxlength="60"
          />
        </el-form-item>
        <el-form-item label="播种日">
          <el-date-picker
            v-model="cycleForm.planted_on"
            type="date"
            value-format="YYYY-MM-DD"
            class="full"
          />
        </el-form-item>
        <el-form-item label="预计收获">
          <el-date-picker
            v-model="cycleForm.expected_harvest_on"
            type="date"
            value-format="YYYY-MM-DD"
            class="full"
          />
        </el-form-item>
        <el-form-item label="状态">
          <el-select
            v-model="cycleForm.status"
            class="full"
          >
            <el-option
              label="生长中"
              value="growing"
            />
            <el-option
              label="已收获"
              value="harvested"
            />
            <el-option
              label="失败"
              value="failed"
            />
          </el-select>
        </el-form-item>
        <el-button
          type="primary"
          :loading="saving"
          @click="saveCycle"
        >
          保存周期
        </el-button>
      </el-form>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import {
    farmApi,
    shareApi,
    type CreatedShareLink,
    type FarmCycle,
    type FarmPlot,
    type FarmRecord,
    type FarmRecordKind,
    type FarmReport,
    type ShareLink,
  } from '@/api/modules'
  import { useAuthStore } from '@/stores/auth'
  import SafeMarkdown from '@/components/teams/SafeMarkdown.vue'
  import OfflineBar from '@/components/offline/OfflineBar.vue'
  import QuickActionBar from '@/components/farm/QuickActionBar.vue'
  import FarmAgentPanel from '@/components/farm/FarmAgentPanel.vue'
  import { installOfflineQueue, writeWithQueue } from '@/offline/queue'
  import { formatDateTime as formatTime } from '@/utils/format'

  const KIND_OPTIONS = [
    { value: 'sowing', label: '播种/移栽' },
    { value: 'fertilizing', label: '施肥' },
    { value: 'irrigation', label: '灌溉' },
    { value: 'pesticide', label: '用药' },
    { value: 'weeding', label: '除草' },
    { value: 'scouting', label: '巡田' },
    { value: 'harvest', label: '采收' },
    { value: 'other', label: '其他' },
  ]

  const PLOT_STATUS_LABEL: Record<string, string> = {
    active: '在种',
    fallow: '休耕',
    archived: '归档',
  }
  const CYCLE_STATUS_LABEL: Record<string, string> = {
    growing: '生长中',
    harvested: '已收获',
    failed: '失败',
  }

  const auth = useAuthStore()
  const canWrite = computed(() =>
    auth.hasAnyRole(['yard_owner', 'yard_admin', 'platform_admin', 'expert', 'operator']),
  )

  const tab = ref('records')
  const loading = ref(false)
  const saving = ref(false)
  const reporting = ref(false)

  const plots = ref<FarmPlot[]>([])
  const cycleCounts = reactive<Record<string, number>>({})
  const records = ref<FarmRecord[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = 20
  const filters = reactive<{ plot_id?: string; kind?: string; range?: [string, string] | null }>({})

  const recordDialog = ref(false)
  const recordForm = reactive<{
    record_id: string
    plot_id: string
    cycle_id: string
    kind: FarmRecordKind
    occurred_on: string
    title: string
    content: string
    quantity: number | undefined
    unit: string
    operator_name: string
  }>({
    record_id: '',
    plot_id: '',
    cycle_id: '',
    kind: 'fertilizing',
    occurred_on: '',
    title: '',
    content: '',
    quantity: undefined,
    unit: '',
    operator_name: '',
  })

  const plotDialog = ref(false)
  const plotForm = reactive<{
    plot_id: string
    name: string
    crop: string
    area_mu: number | undefined
    location: string
    soil_type: string
    irrigation_type: string
    status: 'active' | 'fallow' | 'archived'
    notes: string
  }>({
    plot_id: '',
    name: '',
    crop: '',
    area_mu: undefined,
    location: '',
    soil_type: '',
    irrigation_type: '',
    status: 'active',
    notes: '',
  })

  const cycleDrawer = ref(false)
  const cyclePlot = ref<FarmPlot | null>(null)
  const cycles = ref<FarmCycle[]>([])
  const cycleForm = reactive<{
    crop: string
    variety: string
    planted_on: string
    expected_harvest_on: string
    status: 'growing' | 'harvested' | 'failed'
  }>({
    crop: '',
    variety: '',
    planted_on: '',
    expected_harvest_on: '',
    status: 'growing',
  })

  const reportForm = reactive<{
    kind: 'log' | 'weekly' | 'quarterly'
    range?: [string, string] | null
    plot_id?: string
  }>({ kind: 'log' })
  const report = ref<FarmReport | null>(null)

  function today(): string {
    return new Date().toISOString().slice(0, 10)
  }

  async function loadPlots() {
    try {
      plots.value = await farmApi.plots()
      await Promise.all(
        plots.value.map(async p => {
          try {
            const list = await farmApi.cycles(p.plot_id)
            cycleCounts[p.plot_id] = list.length
          } catch {
            cycleCounts[p.plot_id] = 0
          }
        }),
      )
    } catch {
      plots.value = []
    }
  }

  async function loadRecords(nextPage = page.value) {
    page.value = nextPage
    loading.value = true
    try {
      const res = await farmApi.records({
        plot_id: filters.plot_id,
        kind: filters.kind,
        start: filters.range?.[0],
        end: filters.range?.[1],
        page: page.value,
        page_size: pageSize,
      })
      records.value = res.items
      total.value = res.meta.total
    } catch {
      records.value = []
      total.value = 0
    } finally {
      loading.value = false
    }
  }

  const recordRevision = ref(0)
  const plotRevision = ref(0)

  const QUICK_EXAMPLES = [
    '新增地块 东一号田 水稻 12 亩，沙壤土',
    '东一号田 今天追肥 尿素 5 公斤',
  ]

  async function onQuickCreated(result: { action: string; created: Record<string, unknown> }) {
    if (result.action === 'plot.create') {
      await loadPlots()
      tab.value = 'plots'
      return
    }
    await Promise.all([loadRecords(1), loadPlots()])
    tab.value = 'records'
  }

  function onQuickFallback(data: { action: string; payload: Record<string, unknown> }) {
    if (data.action === 'plot.create') {
      openPlot(data.payload as unknown as FarmPlot)
      return
    }
    openRecord(data.payload as unknown as FarmRecord)
  }

  /** Agent 自动执行可能新增地块/观察记录：刷新两个列表，让结果立即可见 */
  async function onAgentChanged() {
    await Promise.all([loadRecords(1), loadPlots()])
  }

  function openRecord(row?: FarmRecord) {
    recordRevision.value = row?.revision ?? 0
    Object.assign(recordForm, {
      record_id: row?.record_id ?? '',
      plot_id: row?.plot_id ?? filters.plot_id ?? plots.value[0]?.plot_id ?? '',
      cycle_id: row?.cycle_id ?? '',
      kind: row?.kind ?? 'fertilizing',
      occurred_on: row?.occurred_on ?? today(),
      title: row?.title ?? '',
      content: row?.content ?? '',
      quantity: row?.quantity ?? undefined,
      unit: row?.unit ?? '',
      operator_name: row?.operator_name ?? '',
    })
    recordDialog.value = true
  }

  async function saveRecord() {
    if (!recordForm.plot_id) return ElMessage.warning('请选择地块')
    saving.value = true
    try {
      const payload = {
        plot_id: recordForm.plot_id,
        cycle_id: recordForm.cycle_id || null,
        kind: recordForm.kind,
        occurred_on: recordForm.occurred_on,
        title: recordForm.title,
        content: recordForm.content,
        quantity: recordForm.quantity ?? null,
        unit: recordForm.unit,
        operator_name: recordForm.operator_name,
      }
      if (recordForm.record_id) {
        const res = await writeWithQueue({
          label: `更新农事记录「${payload.title || payload.kind}」`,
          method: 'PATCH',
          url: `/farm/records/${recordForm.record_id}`,
          body: { ...payload, expected_revision: recordRevision.value },
        })
        ElMessage.success(res.queued ? '已保存到本机，联网后自动同步' : '记录已更新')
      } else {
        const res = await writeWithQueue({
          label: `农事记录「${payload.title || payload.kind}」`,
          method: 'POST',
          url: '/farm/records',
          body: payload,
          needsClientOpId: true,
        })
        ElMessage.success(res.queued ? '已保存到本机，联网后自动同步' : '记录已保存')
      }
      recordDialog.value = false
      await loadRecords()
    } finally {
      saving.value = false
    }
  }

  async function removeRecord(row: FarmRecord) {
    await ElMessageBox.confirm(`删除 ${row.occurred_on} 的「${row.title || row.kind_label}」？`, '删除记录', {
      type: 'warning',
    })
    const res = await writeWithQueue({
      label: `删除农事记录「${row.title || row.kind_label}」`,
      method: 'DELETE',
      url: `/farm/records/${row.record_id}?expected_revision=${row.revision}`,
    })
    ElMessage.success(res.queued ? '已在本机标记删除，联网后自动同步' : '已删除')
    await loadRecords()
  }

  function openPlot(row?: FarmPlot) {
    plotRevision.value = row?.revision ?? 0
    Object.assign(plotForm, {
      plot_id: row?.plot_id ?? '',
      name: row?.name ?? '',
      crop: row?.crop ?? '',
      area_mu: row?.area_mu ?? undefined,
      location: row?.location ?? '',
      soil_type: row?.soil_type ?? '',
      irrigation_type: row?.irrigation_type ?? '',
      status: row?.status ?? 'active',
      notes: row?.notes ?? '',
    })
    plotDialog.value = true
  }

  async function savePlot() {
    if (!plotForm.name.trim()) return ElMessage.warning('请填写地块名称')
    saving.value = true
    try {
      const payload = {
        name: plotForm.name.trim(),
        crop: plotForm.crop,
        area_mu: plotForm.area_mu ?? null,
        location: plotForm.location,
        soil_type: plotForm.soil_type,
        irrigation_type: plotForm.irrigation_type,
        status: plotForm.status,
        notes: plotForm.notes,
      }
      if (plotForm.plot_id) {
        const res = await writeWithQueue({
          label: `更新地块「${payload.name}」`,
          method: 'PATCH',
          url: `/farm/plots/${plotForm.plot_id}`,
          body: { ...payload, expected_revision: plotRevision.value },
        })
        ElMessage.success(res.queued ? '已保存到本机，联网后自动同步' : '地块已更新')
      } else {
        const res = await writeWithQueue({
          label: `地块「${payload.name}」`,
          method: 'POST',
          url: '/farm/plots',
          body: payload,
          needsClientOpId: true,
        })
        ElMessage.success(res.queued ? '已保存到本机，联网后自动同步' : '地块已创建')
      }
      plotDialog.value = false
      await loadPlots()
    } finally {
      saving.value = false
    }
  }

  async function removePlot(row: FarmPlot) {
    await ElMessageBox.confirm(
      `删除地块「${row.name}」？已有农事记录的地块不能删除，可改为归档。`,
      '删除地块',
      { type: 'warning' },
    )
    const res = await writeWithQueue({
      label: `删除地块「${row.name}」`,
      method: 'DELETE',
      url: `/farm/plots/${row.plot_id}?expected_revision=${row.revision}`,
    })
    ElMessage.success(res.queued ? '已在本机标记删除，联网后自动同步' : '已删除')
    await loadPlots()
    await loadRecords(1)
  }

  async function openCycles(plot: FarmPlot) {
    cyclePlot.value = plot
    cycleDrawer.value = true
    Object.assign(cycleForm, {
      crop: plot.crop,
      variety: '',
      planted_on: '',
      expected_harvest_on: '',
      status: 'growing',
    })
    cycles.value = await farmApi.cycles(plot.plot_id)
  }

  async function saveCycle() {
    if (!cyclePlot.value || !cycleForm.crop.trim()) return ElMessage.warning('请填写作物')
    saving.value = true
    try {
      const res = await writeWithQueue({
        label: `种植周期「${cycleForm.crop.trim()}」`,
        method: 'POST',
        url: `/farm/plots/${cyclePlot.value.plot_id}/cycles`,
        body: {
          crop: cycleForm.crop.trim(),
          variety: cycleForm.variety,
          planted_on: cycleForm.planted_on || null,
          expected_harvest_on: cycleForm.expected_harvest_on || null,
          status: cycleForm.status,
        },
        needsClientOpId: true,
      })
      ElMessage.success(res.queued ? '已保存到本机，联网后自动同步' : '周期已创建')
      cycles.value = await farmApi.cycles(cyclePlot.value.plot_id)
      cycleCounts[cyclePlot.value.plot_id] = cycles.value.length
    } finally {
      saving.value = false
    }
  }

  async function removeCycle(row: FarmCycle) {
    await ElMessageBox.confirm(
      `删除 ${row.crop} 的周期记录？已有农事记录关联的周期不能删除。`,
      '删除周期',
      { type: 'warning' },
    )
    const res = await writeWithQueue({
      label: `删除种植周期「${row.crop}」`,
      method: 'DELETE',
      url: `/farm/cycles/${row.cycle_id}?expected_revision=${row.revision}`,
    })
    if (res.queued) ElMessage.success('已在本机标记删除，联网后自动同步')
    if (cyclePlot.value) {
      cycles.value = await farmApi.cycles(cyclePlot.value.plot_id)
      cycleCounts[cyclePlot.value.plot_id] = cycles.value.length
    }
  }

  async function generateReport() {
    if (!reportForm.range?.length) return ElMessage.warning('请选择报表区间')
    reporting.value = true
    try {
      report.value = await farmApi.report({
        kind: reportForm.kind,
        start: reportForm.range[0],
        end: reportForm.range[1],
        plot_id: reportForm.plot_id,
      })
    } finally {
      reporting.value = false
    }
  }

  function downloadReport() {
    if (!report.value) return
    const blob = new Blob([report.value.content_md], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${report.value.title}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  /** ---- 对外分享（v3.12）：只读快照，可撤销 ---- */
  const shareDialog = ref(false)
  const shareDays = ref(7)
  const sharing = ref(false)
  const sharesLoading = ref(false)
  const shares = ref<ShareLink[]>([])
  const createdShare = ref<CreatedShareLink | null>(null)

  function shareUrl(path: string): string {
    return `${window.location.origin}${path}`
  }

  async function loadShares() {
    sharesLoading.value = true
    try {
      shares.value = await shareApi.list()
    } catch {
      shares.value = []
    } finally {
      sharesLoading.value = false
    }
  }

  async function openShare() {
    createdShare.value = null
    shareDialog.value = true
    await loadShares()
  }

  async function createShare() {
    if (!report.value || !reportForm.range?.length) {
      ElMessage.warning('请先生成报表')
      return
    }
    sharing.value = true
    try {
      createdShare.value = await shareApi.create({
        resource_type: 'farm_report',
        report_kind: reportForm.kind,
        start: reportForm.range[0],
        end: reportForm.range[1],
        plot_id: reportForm.plot_id,
        expires_days: shareDays.value,
      })
      ElMessage.success('分享链接已生成')
      await loadShares()
    } finally {
      sharing.value = false
    }
  }

  async function copyShareUrl(path: string) {
    try {
      await navigator.clipboard.writeText(shareUrl(path))
      ElMessage.success('链接已复制')
    } catch {
      ElMessage.warning('复制失败，请手动选中复制')
    }
  }

  async function revokeShare(shareId: string) {
    try {
      await ElMessageBox.confirm('撤销后旧链接立即失效，确定撤销？', '撤销分享', {
        type: 'warning',
      })
    } catch {
      return
    }
    await shareApi.revoke(shareId)
    ElMessage.success('已撤销')
    await loadShares()
  }

  onMounted(async () => {
    installOfflineQueue()
    await loadPlots()
    await loadRecords(1)
  })
</script>

<style scoped>
  .farm {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .agent-tag {
    margin-right: 6px;
  }
  .page-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
  }
  .page-title {
    margin: 0;
    font-size: 20px;
  }
  .page-sub {
    margin: 6px 0 0;
    color: var(--ds-text-3);
    font-size: 13px;
  }
  .head-actions {
    display: flex;
    gap: 8px;
    flex-shrink: 0;
  }
  .filters,
  .report-form {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    margin-bottom: 12px;
  }
  .w150 {
    width: 150px;
  }
  .w180 {
    width: 180px;
  }
  .w260 {
    width: 260px;
  }
  .full {
    width: 100%;
  }
  .qty-row {
    display: flex;
    gap: 8px;
  }
  .pager {
    margin-top: 12px;
    justify-content: flex-end;
  }
  .plot-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
  }
  .plot-card {
    padding: 14px;
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-glass);
  }
  .plot-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .plot-meta {
    display: flex;
    gap: 12px;
    margin-top: 8px;
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .plot-sub {
    margin-top: 6px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .plot-actions {
    display: flex;
    gap: 6px;
    margin-top: 10px;
  }
  .report-card {
    padding: 16px;
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-glass);
  }
  .report-meta {
    margin-bottom: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .share-tip {
    margin: 0 0 12px;
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.6;
  }
  .share-create {
    display: flex;
    gap: 8px;
    align-items: center;
    margin-bottom: 12px;
  }
  .share-created {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 16px;
  }
  .share-created .muted {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .share-list-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 8px;
    font-size: 13px;
    font-weight: 600;
  }
  .cycle-row {
    display: grid;
    gap: 4px;
    padding: 10px 0;
    border-bottom: 1px dashed var(--ds-border);
  }
  .cycle-status {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .cycle-meta {
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>
