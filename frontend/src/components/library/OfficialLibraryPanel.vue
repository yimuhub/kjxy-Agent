<template>
  <section class="official-panel">
    <div class="status-bar glass-panel">
      <div class="status-item">
        <span class="status-label">源状态</span>
        <el-tag
          size="small"
          effect="plain"
          :type="sourceStatusMeta.type"
        >
          {{ sourceStatusMeta.label }}
        </el-tag>
      </div>
      <div class="status-item">
        <span class="status-label">上次同步</span>
        <span class="status-value">{{ lastSyncText }}</span>
      </div>
      <div class="status-item">
        <span class="status-label">可更新</span>
        <span class="status-value">{{ updateCount }} 个</span>
      </div>
      <div class="status-actions">
        <el-button
          size="small"
          aria-label="查看已授权的插件权限"
          @click="openConsents"
        >
          授权管理
        </el-button>
        <el-button
          size="small"
          :loading="checking"
          aria-label="检查官方库更新"
          @click="checkUpdates"
        >
          检查更新
        </el-button>
        <el-button
          size="small"
          aria-label="刷新官方库"
          @click="init"
        >
          刷新
        </el-button>
      </div>
    </div>

    <section
      v-if="runtime"
      class="runtime-strip glass-panel"
      aria-label="本机能力桥状态"
    >
      <div class="runtime-main">
        <span class="runtime-title">本机能力</span>
        <el-tag
          size="small"
          effect="plain"
          :type="runtime.bridge.available ? 'success' : 'info'"
        >
          {{ runtime.bridge.available ? '桥已就绪' : '桥不可用' }}
        </el-tag>
        <span class="runtime-meta">{{ platformLabel(runtime.platform) }}</span>
        <span class="runtime-meta">
          {{ runtime.plugins.length }} 个插件 · 已授权
          {{ authorizedRuntime }} 个 · {{ runtime.bridge.actions.length }} 种动作
        </span>
      </div>
      <span class="runtime-tip">本机动作需插件授权 + 工具审批，所有执行留审计</span>
    </section>

    <section
      v-if="updates.length"
      class="update-center glass-panel"
      aria-label="更新中心"
    >
      <header class="update-head">
        <div>
          <h3>更新中心</h3>
          <p>
            {{ updates.length }} 个更新待处理
            <span v-if="updatesAuto">· {{ updatesAuto }} 个可自动更新</span>
          </p>
        </div>
        <el-button
          size="small"
          :loading="updatesLoading"
          @click="loadUpdates"
        >
          刷新列表
        </el-button>
      </header>
      <ul class="update-list">
        <li
          v-for="update in updates"
          :key="update.id"
          class="update-row"
        >
          <div class="update-main">
            <strong>{{ update.name || update.item_key }}</strong>
            <span class="update-version">
              {{ update.installed_version }} → {{ update.latest_version }}
            </span>
            <el-tag
              size="small"
              effect="plain"
              :type="riskTagType(update.risk_level)"
            >
              {{ riskLabel(update.risk_level) }}
            </el-tag>
            <el-tag
              v-if="update.pinned"
              size="small"
              type="info"
              effect="plain"
            >
              已固定
            </el-tag>
          </div>
          <el-button
            size="small"
            type="primary"
            :disabled="update.pinned"
            :loading="updatingId === update.id"
            @click="updateFromCenter(update)"
          >
            更新
          </el-button>
        </li>
      </ul>
    </section>

    <el-alert
      v-if="offline"
      class="offline-alert"
      type="info"
      :closable="false"
      show-icon
      :title="offlineTip"
    >
      <el-button
        v-if="networkDown"
        size="small"
        aria-label="重新连接官方库"
        @click="init"
      >
        重新连接
      </el-button>
    </el-alert>

    <div
      v-if="itemsHint"
      class="inline-hint"
      role="status"
      aria-live="polite"
    >
      {{ itemsHint }}
    </div>

    <AsyncState
      v-if="!configured"
      :state="sourcesState === 'ready' ? 'empty' : sourcesState"
      :empty-text="networkDown ? '当前离线，无法读取官方库配置' : '还没有配置官方库源'"
      :error-text="statusError"
      @retry="init"
    >
      <template #empty-action>
        <div
          v-if="!networkDown"
          class="empty-guide"
        >
          <p class="empty-text">
            官方库由平台签名发布，配置源后即可浏览、安装、更新与回滚官方技能/插件；离线时继续使用本地已安装版本。
          </p>
          <el-button
            v-if="canManageSettings"
            type="primary"
            aria-label="前往系统设置配置官方库源"
            @click="goSettings"
          >
            去系统设置配置
          </el-button>
          <p
            v-else
            class="empty-text"
          >
            请联系小院管理员在「系统设置 → 技能与插件 → 官方库」中配置源。
          </p>
        </div>
      </template>
    </AsyncState>

    <AsyncState
      v-else
      :state="itemsState"
      :empty-text="
        offline
          ? '当前离线，暂不展示官方库条目；已安装版本可继续使用'
          : '官方库暂无条目，先在系统设置中同步源'
      "
      :empty-action-text="canManageSettings && !offline ? '去系统设置' : ''"
      :error-text="itemsError"
      @retry="loadItems"
      @action="goSettings"
    >
      <div class="card-grid">
        <article
          v-for="row in rows"
          :key="row.key"
          class="official-card glass-panel"
          :aria-label="`${row.name}，${typeLabel(row.item.item_type)}，版本 ${row.item.version || '未知'}`"
        >
          <header class="card-head">
            <div class="card-title">
              <h3>{{ row.name }}</h3>
              <span class="code">{{ row.code }}</span>
            </div>
            <div class="badges">
              <el-tag
                size="small"
                type="info"
                effect="plain"
              >
                {{ typeLabel(row.item.item_type) }}
              </el-tag>
              <el-tag
                size="small"
                effect="plain"
                :type="riskTagType(row.item.risk_level)"
              >
                {{ riskLabel(row.item.risk_level) }}
              </el-tag>
              <el-tag
                v-if="row.retired"
                size="small"
                type="info"
                effect="plain"
              >
                已下架
              </el-tag>
              <el-tag
                v-else-if="row.hasInstall"
                size="small"
                effect="plain"
                :type="row.updateAvailable ? 'warning' : 'success'"
              >
                {{ row.updateAvailable ? '可更新' : '已安装' }}
              </el-tag>
            </div>
          </header>

          <p class="card-desc">
            {{ row.item.description || '暂无描述' }}
          </p>

          <dl class="card-meta">
            <div>
              <dt>版本</dt>
              <dd>{{ row.item.version || '—' }}</dd>
            </div>
            <div>
              <dt>许可证</dt>
              <dd>{{ row.item.license || '未声明' }}</dd>
            </div>
            <div>
              <dt>签名</dt>
              <dd :title="row.item.sha256 || ''">
                {{ row.signature }}
              </dd>
            </div>
            <div>
              <dt>来源</dt>
              <dd :title="row.sourceTitle">
                {{ row.sourceName }}
              </dd>
            </div>
          </dl>

          <p
            v-if="row.item.changelog"
            class="changelog"
            :title="row.item.changelog"
          >
            {{ row.item.changelog }}
          </p>

          <p
            v-if="row.hasInstall"
            class="install-line"
          >
            已安装 {{ row.installedVersion }}
            <span v-if="row.previousVersion">· 可回滚至 {{ row.previousVersion }}</span>
            <span v-if="row.updateAvailable && row.latest">· 最新 {{ row.latest }}</span>
          </p>

          <footer class="card-actions">
            <el-button
              v-if="!row.hasInstall"
              type="primary"
              size="small"
              :loading="busy === `${row.key}:install`"
              :disabled="offline || row.retired"
              :title="offline ? '离线状态下暂不可安装' : ''"
              :aria-label="`安装 ${row.name}`"
              @click="installItem(row)"
            >
              安装
            </el-button>
            <el-button
              v-else-if="row.updateAvailable"
              type="primary"
              size="small"
              :loading="busy === `${row.key}:update`"
              :disabled="offline"
              :title="offline ? '离线状态下暂不可更新' : ''"
              :aria-label="`更新 ${row.name}`"
              @click="updateItem(row)"
            >
              更新
            </el-button>
            <el-button
              v-if="row.hasInstall && row.previousVersion"
              size="small"
              :loading="busy === `${row.key}:rollback`"
              :aria-label="`回滚 ${row.name}`"
              @click="rollbackItem(row)"
            >
              回滚
            </el-button>
            <div
              v-if="row.hasInstall"
              class="toggles"
            >
              <span class="toggle-label">固定</span>
              <el-switch
                size="small"
                :model-value="row.pinned"
                :loading="busy === `${row.key}:pin`"
                :aria-label="`固定 ${row.name} 的版本`"
                @change="onPinChange(row, $event)"
              />
              <span class="toggle-label">自动更新</span>
              <el-switch
                size="small"
                :model-value="row.autoUpdate"
                :disabled="row.pinned"
                :loading="busy === `${row.key}:auto`"
                :aria-label="`${row.name} 自动更新`"
                @change="onAutoUpdateChange(row, $event)"
              />
            </div>
          </footer>
        </article>
      </div>
    </AsyncState>

    <ConsentDialog
      v-model="consentOpen"
      :requirement="consentRequirement"
      :loading="consentSaving"
      @confirm="confirmConsent"
    />

    <el-dialog
      v-model="consentsOpen"
      append-to-body
      width="680px"
      title="已授权的插件权限"
    >
      <p class="consent-manager-tip">
        授权按「权限集合」记录：插件升级但权限不变时继续有效；权限变化需重新确认。撤销后该插件需重新授权才能安装或更新。
      </p>
      <div
        v-if="!consents.length"
        class="consent-empty"
      >
        暂无授权记录
      </div>
      <ul
        v-else
        class="consent-list"
      >
        <li
          v-for="row in consents"
          :key="row.id"
          class="consent-row"
        >
          <div class="consent-main">
            <strong>{{ row.plugin_id }}</strong>
            <el-tag
              size="small"
              effect="plain"
              :type="row.active ? riskTagType(row.risk_level) : 'info'"
            >
              {{ row.active ? riskLabel(row.risk_level) : '已撤销' }}
            </el-tag>
            <span class="consent-scope">{{ permissionText(row) }}</span>
          </div>
          <el-button
            v-if="row.active"
            size="small"
            :loading="revokingId === row.id"
            @click="revokeConsent(row)"
          >
            撤销授权
          </el-button>
        </li>
      </ul>
    </el-dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  officialLibraryApi,
  type ConsentRequirement,
  type LibraryInstall,
  type LibraryItem,
  type LibrarySource,
  type LibrarySourceStatus,
  type LibraryStatus,
  type LibraryUpdate,
  type PluginConsent,
} from '@/api/library'
import { extractErrorMessage } from '@/api/client'
import { localRuntimeApi, type RuntimeStatus } from '@/api/localRuntime'
import AsyncState from '@/components/AsyncState.vue'
import ConsentDialog from '@/components/library/ConsentDialog.vue'
import {
  riskLabel,
  riskTagType,
  typeLabel,
} from '@/components/library/libraryMeta'
import { classifyError, friendlyErrorMessage, type ViewState } from '@/utils/status'
import { useAuthStore } from '@/stores/auth'

interface OfficialRow {
  key: string
  name: string
  code: string
  item: LibraryItem
  install: LibraryInstall | null
  hasInstall: boolean
  installedVersion: string
  previousVersion: string
  pinned: boolean
  autoUpdate: boolean
  latest: string
  updateAvailable: boolean
  retired: boolean
  signature: string
  sourceName: string
  sourceTitle: string
}

interface StatusCache {
  last_sync_at: string | null
  update_count: number
  sources: LibrarySourceStatus[]
}

const CACHE_KEY = 'ky_library_status_v1'
/** 源状态里视为「异常」的取值（后端字段仍在并行实现，做宽松兼容） */
const SOURCE_ERROR_STATUS = ['error', 'failed', 'failure', 'invalid', 'offline']
const RETIRED_ITEM_STATUS = ['deprecated', 'disabled', 'removed', 'archived']

const router = useRouter()
const auth = useAuthStore()

const canManageSettings = computed(() => auth.hasPermission('settings:manage'))

const status = ref<LibraryStatus | null>(null)
const sources = ref<LibrarySource[]>([])
const items = ref<LibraryItem[]>([])
const installs = ref<LibraryInstall[]>([])

const updates = ref<LibraryUpdate[]>([])
const updatesLoading = ref(false)
const updatingId = ref('')
const updatesAuto = computed(() => updates.value.filter(row => row.can_auto_update).length)

const consentOpen = ref(false)
const consentRequirement = ref<ConsentRequirement | null>(null)
const consentSaving = ref(false)
const pendingAction = ref<{ kind: 'install' | 'update'; row: OfficialRow } | null>(null)

const consentsOpen = ref(false)
const consents = ref<PluginConsent[]>([])
const revokingId = ref('')

const runtime = ref<RuntimeStatus | null>(null)
const authorizedRuntime = computed(
  () => runtime.value?.plugins.filter(row => row.consent_granted).length ?? 0,
)

function platformLabel(platform: string): string {
  const labels: Record<string, string> = {
    macos: 'macOS',
    windows: 'Windows',
    linux: 'Linux',
  }
  return labels[platform] || platform
}

const sourcesState = ref<ViewState>('loading')
const itemsState = ref<ViewState>('loading')
const statusError = ref('')
const itemsError = ref('')
const itemsHint = ref('')
const offline = ref(false)
const networkDown = ref(false)
const checking = ref(false)
const busy = ref('')

const configured = computed(
  () => sources.value.length > 0 || (status.value?.sources?.length ?? 0) > 0,
)

const statusSources = computed<LibrarySourceStatus[]>(() =>
  sources.value.length
    ? sources.value.map(s => ({
        id: s.id,
        name: s.name,
        channel: s.channel,
        status: s.enabled === false ? 'disabled' : (s.status ?? ''),
        last_sync_at: s.last_sync_at,
      }))
    : (status.value?.sources ?? []),
)

const sourceStatusMeta = computed<{
  label: string
  type: 'success' | 'warning' | 'danger' | 'info'
}>(() => {
  if (sourcesState.value === 'loading' && !configured.value) return { label: '检测中', type: 'info' }
  if (offline.value) return { label: '离线', type: 'warning' }
  if (!configured.value) return { label: '未配置', type: 'info' }
  const enabled = statusSources.value.filter(s => s.status !== 'disabled')
  if (!enabled.length) return { label: '已停用', type: 'info' }
  const broken = enabled.some(
    s => s.status && SOURCE_ERROR_STATUS.includes(s.status.toLowerCase()),
  )
  return broken ? { label: '部分异常', type: 'danger' } : { label: '正常', type: 'success' }
})

const lastSyncText = computed(() => {
  const stamps = [
    status.value?.last_sync_at,
    ...statusSources.value.map(s => s.last_sync_at),
  ].filter((v): v is string => typeof v === 'string' && v.length > 0)
  if (!stamps.length) return '暂无记录'
  const latest = stamps.reduce((acc, cur) => (toTime(cur) > toTime(acc) ? cur : acc))
  return formatTime(latest)
})

const offlineTip = computed(
  () =>
    `当前离线，仅显示上次同步时间（${lastSyncText.value}）；已安装条目继续可用，联网后可检查更新。`,
)

const installMap = computed(() => {
  const map = new Map<string, LibraryInstall>()
  for (const install of installs.value) {
    if (install.item_key) map.set(install.item_key, install)
  }
  return map
})

const updateCount = computed(() => {
  // /library/status 的 update_count 由服务端汇总，优先采用；缺失时按台账推算
  const declared = status.value?.update_count
  if (typeof declared === 'number') return declared
  return installs.value.filter(install => {
    if (typeof install.update_available === 'boolean') return install.update_available
    const latest = install.latest_version?.trim()
    return !!latest && !!install.installed_version && latest !== install.installed_version
  }).length
})

const sourceLookup = computed(() => {
  const map = new Map<string, LibrarySource>()
  for (const source of sources.value) map.set(source.id, source)
  // 无「系统设置」权限时只拿到 /library/status 的精简源，用它补齐名称
  for (const source of status.value?.sources ?? []) {
    if (map.has(source.id)) continue
    map.set(source.id, {
      id: source.id,
      name: source.name,
      channel: source.channel,
      status: source.status,
      last_sync_at: source.last_sync_at,
    })
  }
  return map
})

const rows = computed<OfficialRow[]>(() =>
  items.value.map(item => {
    const key = item.id || item.item_key || item.code || ''
    const install = (item.item_key ? installMap.value.get(item.item_key) : null) ?? null
    const latest = resolveLatest(item, install)
    const source = item.source_id ? sourceLookup.value.get(item.source_id) : undefined
    const updateAvailable = resolveUpdate(item, install)
    return {
      key,
      name: item.name || item.code || item.item_key || '未命名条目',
      code: item.code || item.item_key || '—',
      item,
      install,
      hasInstall: !!install,
      installedVersion: install?.installed_version || '—',
      previousVersion: install?.previous_version || '',
      pinned: install?.pinned === true,
      autoUpdate: install?.auto_update === true,
      latest,
      updateAvailable,
      retired:
        typeof item.status === 'string' && RETIRED_ITEM_STATUS.includes(item.status),
      signature: item.sha256 ? `sha256:${item.sha256.slice(0, 12)}…` : '未提供',
      sourceName: source?.name || (item.source_id ? '官方源' : '平台官方'),
      sourceTitle: [source?.name, source?.key_id ? `公钥 ${source.key_id}` : '']
        .filter(Boolean)
        .join(' · '),
    }
  }),
)

function toTime(value?: string | null): number {
  if (!value) return 0
  const time = new Date(value).getTime()
  return Number.isNaN(time) ? 0 : time
}

function formatTime(value?: string | null): string {
  if (!value) return '暂无记录'
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString()
}

function isNetworkCode(code: string): boolean {
  return code === 'NETWORK_ERROR' || code === 'TIMEOUT'
}

function resolveLatest(item: LibraryItem, install: LibraryInstall | null): string {
  return install?.latest_version || item.version || ''
}

function resolveUpdate(item: LibraryItem, install: LibraryInstall | null): boolean {
  if (!install) return false
  if (typeof install.update_available === 'boolean') return install.update_available
  const latest = resolveLatest(item, install)
  return !!latest && !!install.installed_version && latest !== install.installed_version
}

function readCache(): StatusCache | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<StatusCache>
    return {
      last_sync_at: typeof parsed.last_sync_at === 'string' ? parsed.last_sync_at : null,
      update_count: typeof parsed.update_count === 'number' ? parsed.update_count : 0,
      sources: Array.isArray(parsed.sources) ? parsed.sources : [],
    }
  } catch {
    return null
  }
}

function writeCache() {
  try {
    const payload: StatusCache = {
      last_sync_at: status.value?.last_sync_at ?? null,
      update_count: Number(status.value?.update_count ?? 0),
      sources: status.value?.sources ?? [],
    }
    localStorage.setItem(CACHE_KEY, JSON.stringify(payload))
  } catch {
    // 隐私模式/配额满时忽略缓存写入
  }
}

function reportFailure(err: unknown, action: string) {
  const { code, message } = extractErrorMessage(err)
  if (isNetworkCode(code)) {
    ElMessage.warning(`网络不可用，「${action}」未生效`)
    return
  }
  ElMessage.error(`${action}失败：${friendlyErrorMessage(code, message)}`)
}

async function refreshStatus() {
  sourcesState.value = 'loading'
  // 源配置详情仅管理员可见；普通使用者从 /library/status 取名称即可，避免 403 打扰
  const sourcesRequest = canManageSettings.value
    ? officialLibraryApi.sources()
    : Promise.resolve<LibrarySource[]>([])
  const [statusRes, sourcesRes] = await Promise.allSettled([
    officialLibraryApi.status(),
    sourcesRequest,
  ])

  let failure: unknown = null
  if (statusRes.status === 'fulfilled') {
    status.value = statusRes.value ?? null
  } else {
    failure = statusRes.reason
  }
  if (sourcesRes.status === 'fulfilled') {
    sources.value = sourcesRes.value ?? []
  } else if (!failure) {
    failure = sourcesRes.reason
  }

  if (statusRes.status === 'fulfilled') {
    networkDown.value = false
    offline.value = status.value?.offline === true
    statusError.value = ''
    sourcesState.value = configured.value ? 'ready' : 'empty'
    writeCache()
    return
  }

  const { code, message } = extractErrorMessage(failure)
  if (isNetworkCode(code)) {
    // 纯断网：回落到上次成功缓存，不弹错误
    const cached = readCache()
    if (cached) {
      status.value = {
        sources: cached.sources,
        last_sync_at: cached.last_sync_at,
        update_count: cached.update_count,
        offline: true,
      }
    }
    networkDown.value = true
    offline.value = true
    statusError.value = ''
    sourcesState.value = configured.value ? 'ready' : 'empty'
    return
  }

  networkDown.value = false
  statusError.value = friendlyErrorMessage(code, message)
  sourcesState.value = sources.value.length ? 'ready' : classifyError(failure)
}

/** 操作成功后只刷新总览，不把整块状态条切回 loading */
async function refreshSummary() {
  try {
    const next = await officialLibraryApi.status()
    if (next) {
      status.value = next
      offline.value = next.offline === true
      writeCache()
    }
  } catch {
    // 保留原有状态
  }
}

async function loadInstalls() {
  try {
    installs.value = await officialLibraryApi.installs()
  } catch {
    // 已安装台账读取失败时保留原状态，不打断条目浏览
  }
}

async function loadItems() {
  if (!configured.value) {
    items.value = []
    itemsState.value = 'empty'
    return
  }
  itemsState.value = 'loading'
  itemsHint.value = ''
  try {
    items.value = await officialLibraryApi.items()
    itemsError.value = ''
    itemsState.value = items.value.length ? 'ready' : 'empty'
  } catch (err) {
    const { code, message } = extractErrorMessage(err)
    if (isNetworkCode(code) || offline.value) {
      itemsState.value = items.value.length ? 'ready' : 'empty'
      itemsHint.value = '当前离线，条目列表可能不是最新；已安装版本可继续使用'
      return
    }
    itemsError.value = friendlyErrorMessage(code, message)
    itemsState.value = items.value.length ? 'ready' : classifyError(err)
  }
}

async function init() {
  await refreshStatus()
  await Promise.all([loadInstalls(), loadItems(), loadUpdates(), loadRuntime()])
}

async function loadRuntime() {
  try {
    runtime.value = await localRuntimeApi.status()
  } catch {
    // 无 runtime:read 权限或后端未启用时静默隐藏
    runtime.value = null
  }
}

async function loadUpdates() {
  updatesLoading.value = true
  try {
    const result = await officialLibraryApi.updates()
    updates.value = result.items
  } catch {
    // 更新中心失败不打断主列表
  } finally {
    updatesLoading.value = false
  }
}

async function loadConsents() {
  try {
    const result = await officialLibraryApi.consents()
    consents.value = result.items
  } catch (err) {
    reportFailure(err, '加载授权记录')
  }
}

function permissionText(row: PluginConsent): string {
  const details = row.permission_details || []
  if (details.length) return details.map(item => item.label).join(' · ')
  return row.permissions.join(' · ') || '无声明权限'
}

async function openConsents() {
  consentsOpen.value = true
  await loadConsents()
}

async function revokeConsent(row: PluginConsent) {
  if (revokingId.value) return
  try {
    await ElMessageBox.confirm(
      `撤销「${row.plugin_id}」的权限授权？撤销后该插件需要重新授权才能安装或更新。`,
      '撤销授权',
      { type: 'warning', confirmButtonText: '确认撤销', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  revokingId.value = row.id
  try {
    await officialLibraryApi.revokeConsent(row.id)
    ElMessage.success('已撤销授权')
    await loadConsents()
  } catch (err) {
    reportFailure(err, '撤销授权')
  } finally {
    revokingId.value = ''
  }
}

/** 安装/更新前确认权限：需要授权时打开弹窗，确认后继续原动作。 */
async function ensureConsent(row: OfficialRow, kind: 'install' | 'update'): Promise<boolean> {
  try {
    const requirement = await officialLibraryApi.itemConsent(row.item.id)
    if (!requirement.required || requirement.granted) return true
    consentRequirement.value = requirement
    pendingAction.value = { kind, row }
    consentOpen.value = true
    return false
  } catch (err) {
    reportFailure(err, '检查插件权限')
    return false
  }
}

async function confirmConsent() {
  const pending = pendingAction.value
  if (!pending) return
  consentSaving.value = true
  try {
    await officialLibraryApi.grantConsent(pending.row.item.id)
    ElMessage.success('已记录授权')
    consentOpen.value = false
    consentRequirement.value = null
    pendingAction.value = null
    if (pending.kind === 'install') await doInstall(pending.row)
    else await doUpdate(pending.row)
  } catch (err) {
    reportFailure(err, '记录授权')
  } finally {
    consentSaving.value = false
  }
}

async function checkUpdates() {
  if (checking.value) return
  checking.value = true
  try {
    await refreshStatus()
    await Promise.all([loadInstalls(), loadUpdates()])
    if (!configured.value) {
      ElMessage.info('还没有配置官方库源，请先到系统设置 → 技能与插件 → 官方库配置')
      return
    }
    if (offline.value) {
      ElMessage.info(`当前离线，上次同步：${lastSyncText.value}`)
      return
    }
    if (updateCount.value > 0) {
      ElMessage.success(`发现 ${updateCount.value} 个可更新条目`)
    } else {
      ElMessage.success('官方库已是最新')
    }
  } finally {
    checking.value = false
  }
}

function goSettings() {
  void router.push({ name: 'Settings', query: { tab: 'skills' } })
}

async function installItem(row: OfficialRow) {
  if (busy.value) return
  if (!(await ensureConsent(row, 'install'))) return
  await doInstall(row)
}

async function doInstall(row: OfficialRow) {
  busy.value = `${row.key}:install`
  try {
    await officialLibraryApi.installItem(row.item.id, row.item.version)
    ElMessage.success(`「${row.name}」已安装`)
    await Promise.all([loadInstalls(), refreshSummary(), loadUpdates()])
  } catch (err) {
    reportFailure(err, `安装「${row.name}」`)
  } finally {
    busy.value = ''
  }
}

async function updateItem(row: OfficialRow) {
  if (!row.install || busy.value) return
  const target = row.latest || '最新版本'
  try {
    await ElMessageBox.confirm(
      `将「${row.name}」从 ${row.install.installed_version || '当前版本'} 更新到 ${target}？更新后仍可回滚。`,
      '更新确认',
      { type: 'warning', confirmButtonText: '确认更新', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  if (!(await ensureConsent(row, 'update'))) return
  await doUpdate(row)
}

async function doUpdate(row: OfficialRow) {
  if (!row.install) return
  busy.value = `${row.key}:update`
  try {
    const result = await officialLibraryApi.updateInstall(row.install.id)
    ElMessage.success(
      `「${row.name}」已更新到 ${result.installed_version || row.latest || '最新版本'}`,
    )
    await Promise.all([loadInstalls(), refreshSummary(), loadUpdates()])
  } catch (err) {
    reportFailure(err, `更新「${row.name}」`)
  } finally {
    busy.value = ''
  }
}

async function updateFromCenter(update: LibraryUpdate) {
  if (updatingId.value) return
  const row = rows.value.find(item => item.install?.id === update.id)
  if (!row) {
    ElMessage.warning('该条目尚未加载完成，请稍后重试')
    return
  }
  updatingId.value = update.id
  try {
    await updateItem(row)
  } finally {
    updatingId.value = ''
  }
}

async function rollbackItem(row: OfficialRow) {
  if (!row.install || busy.value) return
  const target = row.install.previous_version || '上一版本'
  try {
    await ElMessageBox.confirm(
      `将「${row.name}」回滚到 ${target}？回滚后当前版本会被替换。`,
      '回滚确认',
      { type: 'warning', confirmButtonText: '确认回滚', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  busy.value = `${row.key}:rollback`
  try {
    const result = await officialLibraryApi.rollbackInstall(row.install.id)
    ElMessage.success(`「${row.name}」已回滚到 ${result.installed_version || target}`)
    await Promise.all([loadInstalls(), refreshSummary(), loadUpdates()])
  } catch (err) {
    reportFailure(err, `回滚「${row.name}」`)
  } finally {
    busy.value = ''
  }
}

async function onPinChange(row: OfficialRow, value: string | number | boolean) {
  if (!row.install || busy.value) return
  const pinned = value === true
  busy.value = `${row.key}:pin`
  try {
    await officialLibraryApi.pinInstall(row.install.id, pinned)
    ElMessage.success(pinned ? '已固定版本，后续不再自动更新' : '已取消固定版本')
    await loadInstalls()
  } catch (err) {
    reportFailure(err, pinned ? '固定版本' : '取消固定')
  } finally {
    busy.value = ''
  }
}

async function onAutoUpdateChange(row: OfficialRow, value: string | number | boolean) {
  if (!row.install || busy.value) return
  const autoUpdate = value === true
  busy.value = `${row.key}:auto`
  try {
    await officialLibraryApi.setAutoUpdate(row.install.id, autoUpdate)
    ElMessage.success(autoUpdate ? '已开启自动更新' : '已关闭自动更新')
    await loadInstalls()
  } catch (err) {
    reportFailure(err, autoUpdate ? '开启自动更新' : '关闭自动更新')
  } finally {
    busy.value = ''
  }
}

onMounted(() => {
  void init()
})
</script>

<style scoped>
.official-panel {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.status-bar {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px 20px;
  padding: 10px 14px;
}
.status-item {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}
.status-label {
  font-size: 12px;
  color: var(--ds-text-3);
}
.status-value {
  font-size: 13px;
  color: var(--ds-text-1);
}
.status-actions {
  display: flex;
  gap: 8px;
  margin-left: auto;
}
.offline-alert {
  margin: 0;
}
.inline-hint {
  padding: 10px 12px;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-control);
  background: var(--ds-glass);
  color: var(--ds-text-2);
  font-size: 13px;
  line-height: 1.6;
}
.empty-guide {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  max-width: 560px;
}
.empty-text {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--ds-text-2);
}
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}
.official-card {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  min-width: 0;
}
.card-head {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.card-title {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}
.card-title h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  color: var(--ds-text-1);
  word-break: break-word;
}
.code {
  flex: none;
  font-family: ui-monospace, monospace;
  font-size: 11px;
  color: var(--ds-text-3);
}
.badges {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.card-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--ds-text-2);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-meta {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 12px;
  margin: 0;
  padding-top: 10px;
  border-top: 1px solid var(--ds-border);
}
.card-meta div {
  min-width: 0;
}
.card-meta dt {
  margin-bottom: 2px;
  font-size: 11px;
  color: var(--ds-text-3);
}
.card-meta dd {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--ds-text-2);
}
.changelog {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ds-text-3);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}
.install-line {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ds-text-2);
}
.card-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: auto;
  padding-top: 4px;
}
.toggles {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
}
.toggle-label {
  font-size: 12px;
  color: var(--ds-text-3);
}
.runtime-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 8px 14px;
  padding: 10px 14px;
  border-color: rgba(56, 189, 248, 0.22);
  background: linear-gradient(150deg, rgba(56, 189, 248, 0.1), rgba(99, 102, 241, 0.04));
}
.runtime-main {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 12px;
}
.runtime-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--ds-text-1);
}
.runtime-meta {
  font-size: 12px;
  color: var(--ds-text-3);
  font-variant-numeric: tabular-nums;
}
.runtime-tip {
  font-size: 12px;
  color: var(--ds-text-3);
}
.update-center {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  border-color: rgba(99, 102, 241, 0.28);
  background: linear-gradient(160deg, rgba(99, 102, 241, 0.12), rgba(56, 189, 248, 0.05));
}
.update-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}
.update-head h3 {
  margin: 0;
  font-size: 15px;
  color: var(--ds-text-1);
}
.update-head p {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--ds-text-3);
}
.update-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}
.update-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 8px 10px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.04);
}
.update-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}
.update-main strong {
  font-size: 13.5px;
  color: var(--ds-text-1);
}
.update-version {
  font-size: 12px;
  color: var(--ds-text-3);
  font-variant-numeric: tabular-nums;
}
.consent-manager-tip {
  margin: 0 0 10px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--ds-text-3);
}
.consent-empty {
  padding: 24px 0;
  text-align: center;
  color: var(--ds-text-3);
  font-size: 13px;
}
.consent-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
  max-height: 420px;
  overflow-y: auto;
}
.consent-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--ds-border);
  border-radius: 10px;
}
.consent-main {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  min-width: 0;
}
.consent-main strong {
  font-size: 13.5px;
  color: var(--ds-text-1);
}
.consent-scope {
  flex-basis: 100%;
  font-size: 12px;
  color: var(--ds-text-3);
}
@media (max-width: 720px) {
  .status-actions {
    margin-left: 0;
    width: 100%;
  }
  .card-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .toggles {
    margin-left: 0;
    width: 100%;
  }
}
</style>
