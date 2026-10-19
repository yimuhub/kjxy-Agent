<template>
  <div class="devices">
    <h2 class="page-title">设备中心</h2>
    <p class="sub">接入气象站 / 墒情 / 虫情 / 棚室等智慧农业设施：设备推送（Webhook）或平台定时拉取（自定义连接器）。平台只读取数据与建议，不会自动控制设备。</p>

    <AsyncState :state="listState" empty-text="还没有设备，点击右上角「新建设备」接入第一台" @retry="loadAll">
      <div class="bar">
        <span class="note">共 {{ devices.length }} 台</span>
        <el-button v-if="canManage" type="primary" size="small" @click="openCreate">新建设备</el-button>
      </div>
      <el-row :gutter="16">
        <el-col v-for="d in devices" :key="d.id" :xs="24" :sm="12" :md="8">
          <el-card class="dev-card" shadow="hover">
            <div class="dev-head">
              <span class="dev-name">{{ d.name }}</span>
              <el-tag size="small" effect="plain">{{ typeName(d.device_type) }}</el-tag>
              <el-tag size="small" :type="d.source_type === 'webhook' ? 'success' : 'warning'" effect="plain">
                {{ d.source_type === 'webhook' ? '设备推送' : '平台拉取' }}
              </el-tag>
            </div>
            <div class="dev-meta">
              {{ d.location_note || '未标注位置' }}
              <span v-if="!d.enabled" class="off">（已停用）</span>
              <span v-if="d.consecutive_failures > 0" class="fail">连续失败 {{ d.consecutive_failures }} 次</span>
            </div>
            <el-table v-if="d.latest && d.latest.length" :data="d.latest.slice(0, 4)" size="small" class="mt8">
              <el-table-column prop="metric_name" label="指标" />
              <el-table-column label="最新值" width="110">
                <template #default="{ row }">{{ row.value ?? row.text ?? '—' }} {{ row.unit || '' }}</template>
              </el-table-column>
              <el-table-column label="时间" width="150">
                <template #default="{ row }">{{ formatTime(row.collected_at) }}</template>
              </el-table-column>
            </el-table>
            <div v-else class="note mt8">暂无读数</div>
            <div class="dev-ops mt8">
              <el-button size="small" @click="openData(d)">数据</el-button>
              <template v-if="canManage">
                <el-switch
                  :model-value="d.enabled"
                  size="small"
                  class="ml"
                  @change="(v: boolean) => toggleDevice(d, v)"
                />
                <el-button v-if="d.source_type === 'webhook'" size="small" class="ml" @click="rotate(d)">换令牌</el-button>
                <el-button size="small" type="danger" class="ml" @click="removeDevice(d)">删除</el-button>
              </template>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </AsyncState>

    <!-- 新建设备 -->
    <el-dialog v-model="createVisible" title="新建设备" width="640px">
      <el-form label-width="100px">
        <el-form-item label="名称"><el-input v-model="form.name" placeholder="一号田气象站" /></el-form-item>
        <el-form-item label="类型">
          <el-select v-model="form.device_type" style="width: 200px">
            <el-option v-for="t in caps?.device_types || []" :key="t.value" :label="t.name" :value="t.value" />
          </el-select>
        </el-form-item>
        <el-form-item label="接入方式">
          <el-radio-group v-model="form.source_type">
            <el-radio v-for="s in caps?.source_types || []" :key="s.value" :value="s.value">{{ s.name }}</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="位置备注"><el-input v-model="form.location_note" /></el-form-item>
        <el-form-item label="经纬度">
          <el-input-number v-model="form.longitude" :precision="6" :controls="false" placeholder="经度" class="coord" />
          <el-input-number v-model="form.latitude" :precision="6" :controls="false" placeholder="纬度" class="coord ml" />
        </el-form-item>
        <template v-if="form.source_type === 'connector'">
          <el-form-item label="接口地址"><el-input v-model="conn.url" placeholder="https://厂商云/api/data?station=xxx" /></el-form-item>
          <el-form-item label="方法/周期">
            <el-select v-model="conn.method" style="width: 100px"><el-option label="GET" value="GET" /><el-option label="POST" value="POST" /></el-select>
            <el-input-number v-model="conn.interval_seconds" :min="60" :max="86400" class="ml" /> <span class="note">秒（≥60）</span>
            <el-input-number v-model="conn.timeout_seconds" :min="1" :max="30" class="ml" /> <span class="note">秒超时</span>
          </el-form-item>
          <el-form-item label="鉴权头">
            <div class="full">
              <div v-for="(h, i) in headerRows" :key="i" class="row">
                <el-input v-model="h.k" placeholder="头名 如 Authorization" class="half" />
                <el-input v-model="h.v" placeholder="值（保存后仅显掩码）" class="half ml" show-password />
                <el-button link type="danger" class="ml" @click="headerRows.splice(i, 1)">删</el-button>
              </div>
              <el-button link type="primary" @click="headerRows.push({ k: '', v: '' })">+ 添加头</el-button>
            </div>
          </el-form-item>
          <el-form-item label="数据路径"><el-input v-model="conn.json_path" placeholder="如 $.data.list[*]，留空取顶层" /></el-form-item>
          <el-form-item label="字段映射">
            <div class="full">
              <div v-for="(m, i) in mapRows" :key="i" class="row">
                <el-input v-model="m.k" placeholder="厂商字段 如 ws.temp" class="third" />
                <el-select v-model="m.metric" placeholder="映射指标" class="third ml">
                  <el-option v-for="mt in allMetrics" :key="mt.code" :label="`${mt.name}(${mt.code})`" :value="mt.code" />
                </el-select>
                <el-input v-model="m.unit" placeholder="单位覆盖(可选)" class="third ml" />
                <el-button link type="danger" class="ml" @click="mapRows.splice(i, 1)">删</el-button>
              </div>
              <el-button link type="primary" @click="mapRows.push({ k: '', metric: 'other', unit: '' })">+ 添加映射</el-button>
            </div>
          </el-form-item>
        </template>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitCreate">保存</el-button>
      </template>
    </el-dialog>

    <!-- 一次性令牌展示 -->
    <el-dialog v-model="tokenVisible" title="设备接入令牌（仅显示这一次）" width="560px">
      <el-alert type="warning" :closable="false" class="mb"
        title="请立即复制保存！关闭后系统不再显示明文，丢失只能重新生成（旧令牌立即失效）。" />
      <div class="token">{{ tokenValue }}</div>
      <div class="hint mt8">
        设备侧上报示例：POST /api/v1/devices/&lt;设备ID&gt;/ingest，请求头 X-Device-Token: &lt;令牌&gt;，
        请求体 {"data": {"temperature": 26.4, "soil_moisture_20": 23.5}}
      </div>
      <template #footer>
        <el-button type="primary" @click="copyToken">复制令牌</el-button>
        <el-button @click="tokenVisible = false">我已保存，关闭</el-button>
      </template>
    </el-dialog>

    <!-- 数据抽屉 -->
    <el-drawer v-model="dataVisible" :title="`数据：${dataDevice?.name || ''}`" size="52%">
      <el-select v-model="dataMetric" class="mb" @change="loadReadings">
        <el-option v-for="mt in dataMetricOptions" :key="mt.code" :label="`${mt.name}(${mt.code})`" :value="mt.code" />
      </el-select>
      <template v-if="numericReadings.length">
        <div class="big-num">
          {{ numericReadings[numericReadings.length - 1].value }}
          <span class="unit">{{ numericReadings[numericReadings.length - 1].unit || '' }}</span>
          <span class="note ml">最新值 · 近 7 天 {{ numericReadings.length }} 点</span>
        </div>
        <div class="chart mt8">
          <div v-for="r in barSlice" :key="r.id" class="bar-row">
            <span class="bar-time">{{ formatTime(r.collected_at).slice(5, 16) }}</span>
            <div class="bar-track">
              <div class="bar-fill" :style="{ width: barWidth(r.value as number) }" />
            </div>
            <span class="bar-val">{{ r.value }}</span>
          </div>
        </div>
      </template>
      <el-empty v-else :description="`近 7 天无「${dataMetric}」数值读数`">
        <div class="note">可先用模拟设备造数：backend/ 下运行 python scripts/sim_devices.py --create</div>
      </el-empty>
      <el-table v-if="recent50.length" :data="recent50" size="small" max-height="260" class="mt">
        <el-table-column label="时间" width="160"><template #default="{ row }">{{ formatTime(row.collected_at) }}</template></el-table-column>
        <el-table-column label="值"><template #default="{ row }">{{ row.value ?? row.text }} {{ row.unit || '' }}</template></el-table-column>
      </el-table>
    </el-drawer>
    <!-- 告警规则与告警记录 -->
    <div class="bar mt">
      <h3 class="sec-title">告警规则</h3>
      <el-button v-if="canManage" size="small" type="primary" @click="openRule()">新建规则</el-button>
    </div>
    <el-table :data="rules" size="small" empty-text="暂无规则">
      <el-table-column label="设备" min-width="120">
        <template #default="{ row }">{{ row.device_id ? deviceName(row.device_id) : '租户全部设备' }}</template>
      </el-table-column>
      <el-table-column label="指标" width="130"><template #default="{ row }">{{ metricName(row.metric) }}</template></el-table-column>
      <el-table-column label="条件" width="120">
        <template #default="{ row }">{{ row.op === 'gt' ? '大于' : '小于' }} {{ row.threshold }}</template>
      </el-table-column>
      <el-table-column prop="notify_note" label="备注" min-width="140" show-overflow-tooltip />
      <el-table-column label="启用" width="70">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" size="small" :disabled="!canManage"
            @change="(v: boolean) => deviceApi.patchRule(row.id, { enabled: v }).then(loadRules)" />
        </template>
      </el-table-column>
      <el-table-column v-if="canManage" label="操作" width="120">
        <template #default="{ row }">
          <el-button size="small" @click="openRule(row)">编辑</el-button>
          <el-button size="small" type="danger" @click="removeRule(row.id)">删</el-button>
        </template>
      </el-table-column>
    </el-table>

    <h3 class="sec-title mt">最近告警</h3>
    <el-table :data="alerts" size="small" empty-text="暂无告警" :row-class-name="() => ''">
      <el-table-column label="时间" width="160"><template #default="{ row }">{{ formatTime(row.triggered_at) }}</template></el-table-column>
      <el-table-column label="设备" min-width="120"><template #default="{ row }">{{ row.device_name || '—' }}</template></el-table-column>
      <el-table-column label="指标" width="120"><template #default="{ row }">{{ row.metric_name || row.metric }}</template></el-table-column>
      <el-table-column label="触发值" width="110"><template #default="{ row }">{{ row.value }}</template></el-table-column>
      <el-table-column prop="notified_text" label="说明" min-width="200" show-overflow-tooltip />
    </el-table>

    <!-- 规则对话框 -->
    <el-dialog v-model="ruleVisible" :title="ruleEditing?.id ? '编辑告警规则' : '新建告警规则'" width="480px">
      <el-form label-width="90px">
        <el-form-item label="设备">
          <el-select v-model="ruleForm.device_id" clearable placeholder="留空=本租户该指标全部设备" style="width: 100%">
            <el-option v-for="d in devices" :key="d.id" :label="d.name" :value="d.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="指标">
          <el-select v-model="ruleForm.metric" style="width: 100%">
            <el-option v-for="mt in caps?.metrics || []" :key="mt.code" :label="`${mt.name}(${mt.unit})`" :value="mt.code" />
          </el-select>
        </el-form-item>
        <el-form-item label="条件">
          <el-select v-model="ruleForm.op" style="width: 100px">
            <el-option label="大于" value="gt" /><el-option label="小于" value="lt" />
          </el-select>
          <el-input-number v-model="ruleForm.threshold" :controls="false" class="ml" placeholder="阈值" />
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="ruleForm.notify_note" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="ruleVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="submitRule">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  deviceApi,
  type Device,
  type DeviceAlert,
  type DeviceAlertRule,
  type DeviceCapabilities,
  type DeviceMetric,
  type DeviceReading,
} from '@/api/modules'
import { extractErrorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { formatTime } from '@/utils/format'
import AsyncState from '@/components/AsyncState.vue'
import type { ViewState } from '@/utils/status'

const auth = useAuthStore()
const canManage = computed(() => auth.isSpaceAdmin())

const caps = ref<DeviceCapabilities | null>(null)
const devices = ref<Device[]>([])
const rules = ref<DeviceAlertRule[]>([])
const alerts = ref<DeviceAlert[]>([])
const listState = ref<ViewState>('loading')
const saving = ref(false)

const allMetrics = computed<DeviceMetric[]>(() => [
  ...(caps.value?.metrics || []),
  { code: 'other', name: '其他(保留原始)', unit: '' },
])
function typeName(v: string) {
  return caps.value?.device_types.find(t => t.value === v)?.name || v
}
function metricName(v: string) {
  return caps.value?.metrics.find(m => m.code === v)?.name || v
}
function deviceName(id: string) {
  return devices.value.find(d => d.id === id)?.name || id
}

async function loadAll() {
  listState.value = 'loading'
  try {
    const [c, ds] = await Promise.all([caps.value ? null : deviceApi.capabilities(), deviceApi.list()])
    if (c) caps.value = c
    devices.value = ds
    // 列表接口无 latest：逐台补详情（设备数量小，可接受）
    devices.value = await Promise.all(ds.map(d => deviceApi.detail(d.id).catch(() => d)))
    listState.value = ds.length ? 'ready' : 'empty'
    await Promise.all([loadRules(), loadAlerts()])
  } catch {
    listState.value = 'error'
  }
}
async function loadRules() {
  try {
    rules.value = await deviceApi.rules()
  } catch {
    rules.value = []
  }
}
async function loadAlerts() {
  try {
    alerts.value = await deviceApi.alerts(50)
  } catch {
    alerts.value = []
  }
}

/* ---------- 新建 ---------- */
const createVisible = ref(false)
const form = ref({ name: '', device_type: 'weather', source_type: 'webhook' as 'webhook' | 'connector', location_note: '', longitude: null as number | null, latitude: null as number | null })
const conn = ref({ url: '', method: 'GET' as 'GET' | 'POST', interval_seconds: 300, timeout_seconds: 10, json_path: '' })
const headerRows = ref<{ k: string; v: string }[]>([])
const mapRows = ref<{ k: string; metric: string; unit: string }[]>([])

function openCreate() {
  form.value = { name: '', device_type: 'weather', source_type: 'webhook', location_note: '', longitude: null, latitude: null }
  conn.value = { url: '', method: 'GET', interval_seconds: 300, timeout_seconds: 10, json_path: '' }
  headerRows.value = []
  mapRows.value = [{ k: '', metric: 'other', unit: '' }]
  createVisible.value = true
}

async function submitCreate() {
  const f = form.value
  if (!f.name.trim()) return ElMessage.warning('请填写设备名称')
  const payload: Record<string, unknown> = {
    name: f.name.trim(),
    device_type: f.device_type,
    source_type: f.source_type,
    location_note: f.location_note || undefined,
    longitude: f.longitude,
    latitude: f.latitude,
  }
  if (f.source_type === 'connector') {
    const c = conn.value
    if (!c.url.trim()) return ElMessage.warning('请填写接口地址')
    const fieldMap: Record<string, { metric: string; unit?: string }> = {}
    for (const m of mapRows.value) {
      if (m.k.trim()) fieldMap[m.k.trim()] = { metric: m.metric, ...(m.unit.trim() ? { unit: m.unit.trim() } : {}) }
    }
    if (!Object.keys(fieldMap).length) return ElMessage.warning('至少配置一条字段映射')
    const headers: Record<string, string> = {}
    for (const h of headerRows.value) if (h.k.trim()) headers[h.k.trim()] = h.v
    payload.connector = {
      url: c.url.trim(),
      method: c.method,
      headers: Object.keys(headers).length ? headers : null,
      interval_seconds: c.interval_seconds,
      json_path: c.json_path.trim() || '$',
      field_map: fieldMap,
      timeout_seconds: c.timeout_seconds,
    }
  }
  saving.value = true
  try {
    const created = await deviceApi.create(payload as never)
    createVisible.value = false
    if (created.device_token) {
      tokenValue.value = created.device_token
      tokenVisible.value = true
    } else {
      ElMessage.success('连接器设备已创建，平台将按周期自动拉取')
    }
    await loadAll()
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '创建失败')
  } finally {
    saving.value = false
  }
}

/* ---------- 令牌 ---------- */
const tokenVisible = ref(false)
const tokenValue = ref('')
async function copyToken() {
  try {
    await navigator.clipboard.writeText(tokenValue.value)
    ElMessage.success('已复制到剪贴板')
  } catch {
    ElMessage.warning('浏览器不允许自动复制，请手动选中复制')
  }
}
async function rotate(d: Device) {
  await ElMessageBox.confirm(`重新生成「${d.name}」的接入令牌？旧令牌将立即失效，设备侧需要同步更新。`, '确认', { type: 'warning' })
  try {
    const r = await deviceApi.rotateToken(d.id)
    tokenValue.value = r.device_token
    tokenVisible.value = true
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '操作失败')
  }
}
/* ---------- 启停/删除 ---------- */
async function toggleDevice(d: Device, v: boolean) {
  try {
    await deviceApi.patch(d.id, { enabled: v })
    ElMessage.success(v ? `已启用「${d.name}」` : `已停用「${d.name}」`)
    await loadAll()
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '操作失败')
  }
}
async function removeDevice(d: Device) {
  await ElMessageBox.confirm(
    `删除「${d.name}」将连同其全部读数与告警记录一并删除，不可恢复。确认删除？`,
    '危险操作',
    { type: 'error', confirmButtonText: '确认删除' },
  )
  try {
    await deviceApi.remove(d.id)
    ElMessage.success('已删除')
    await loadAll()
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '删除失败')
  }
}

/* ---------- 数据抽屉 ---------- */
const dataVisible = ref(false)
const dataDevice = ref<Device | null>(null)
const dataMetric = ref('temp')
const readings = ref<DeviceReading[]>([])
const dataMetricOptions = computed(() => {
  const seen = new Map<string, DeviceMetric>()
  for (const l of dataDevice.value?.latest || []) {
    if (!seen.has(l.metric)) seen.set(l.metric, { code: l.metric, name: l.metric_name, unit: l.unit || '' })
  }
  for (const m of caps.value?.metrics || []) if (!seen.has(m.code)) seen.set(m.code, m)
  return [...seen.values()]
})
const weekAgo = () => new Date(Date.now() - 7 * 86400_000).toISOString()
const numericReadings = computed(() => readings.value.filter(r => typeof r.value === 'number'))
const barSlice = computed(() => numericReadings.value.slice(-40))
const recent50 = computed(() => [...readings.value].reverse().slice(0, 50))
const barMax = computed(() => Math.max(1e-9, ...numericReadings.value.map(r => Math.abs(r.value ?? 0))))
function barWidth(v: number) {
  return `${Math.max(2, (Math.abs(v) / barMax.value) * 100)}%`
}
function openData(d: Device) {
  dataDevice.value = d
  const first = (d.latest?.length && d.latest[0].metric) || caps.value?.metrics[0]?.code || 'temp'
  dataMetric.value = first
  readings.value = []
  dataVisible.value = true
  void loadReadings()
}
async function loadReadings() {
  if (!dataDevice.value) return
  try {
    readings.value = await deviceApi.readings(dataDevice.value.id, {
      metric: dataMetric.value,
      start: weekAgo(),
      limit: 1000,
    })
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '读数加载失败')
  }
}

/* ---------- 规则 ---------- */
const ruleVisible = ref(false)
const ruleEditing = ref<DeviceAlertRule | null>(null)
const ruleForm = ref({ device_id: null as string | null, metric: 'temp', op: 'gt' as 'gt' | 'lt', threshold: 0, notify_note: '' })
function openRule(row?: DeviceAlertRule) {
  ruleEditing.value = row ?? null
  ruleForm.value = row
    ? { device_id: row.device_id, metric: row.metric, op: row.op, threshold: row.threshold, notify_note: row.notify_note || '' }
    : { device_id: null, metric: caps.value?.metrics[0]?.code || 'temp', op: 'gt', threshold: 35, notify_note: '' }
  ruleVisible.value = true
}
async function submitRule() {
  saving.value = true
  try {
    const payload = {
      device_id: ruleForm.value.device_id,
      metric: ruleForm.value.metric,
      op: ruleForm.value.op,
      threshold: ruleForm.value.threshold,
      notify_note: ruleForm.value.notify_note || null,
    }
    if (ruleEditing.value) await deviceApi.patchRule(ruleEditing.value.id, payload)
    else await deviceApi.createRule(payload)
    ruleVisible.value = false
    ElMessage.success('规则已保存')
    await loadRules()
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '保存失败')
  } finally {
    saving.value = false
  }
}
async function removeRule(id: string) {
  try {
    await deviceApi.removeRule(id)
    await loadRules()
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '删除失败')
  }
}

onMounted(loadAll)
</script>
<style scoped>
.page-title { margin: 0 0 6px; font-size: 20px; }
.sub { margin: 0 0 16px; font-size: 13px; color: var(--el-text-color-secondary); }
.bar { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.sec-title { margin: 0; font-size: 16px; }
.dev-card { margin-bottom: 16px; }
.dev-head { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.dev-name { font-weight: 600; }
.dev-meta { margin-top: 4px; font-size: 12px; color: var(--el-text-color-secondary); }
.off { color: var(--el-text-color-disabled); }
.fail { margin-left: 8px; color: var(--el-color-danger); }
.dev-ops { display: flex; align-items: center; flex-wrap: wrap; }
.mt8 { margin-top: 8px; }
.mt { margin-top: 16px; }
.ml { margin-left: 8px; }
.mb { margin-bottom: 12px; }
.note { font-size: 12px; color: var(--el-text-color-secondary); }
.token {
  padding: 12px;
  font-family: monospace;
  font-size: 15px;
  word-break: break-all;
  border-radius: 6px;
  background: var(--el-fill-color-light);
}
.hint { font-size: 12px; color: var(--el-text-color-secondary); line-height: 1.7; }
.coord { width: 150px; }
.full { width: 100%; }
.row { display: flex; align-items: center; margin-bottom: 6px; }
.half { width: 42%; }
.third { width: 30%; }
.big-num { font-size: 34px; font-weight: 700; }
.big-num .unit { font-size: 16px; font-weight: 400; }
.chart { max-height: 320px; overflow-y: auto; }
.bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.bar-time { width: 92px; font-size: 11px; color: var(--el-text-color-secondary); }
.bar-track { flex: 1; height: 12px; border-radius: 6px; background: var(--el-fill-color-lighter); }
.bar-fill { height: 100%; border-radius: 6px; background: var(--el-color-success-light-3); min-width: 2px; }
.bar-val { width: 64px; font-size: 12px; text-align: right; }
</style>
