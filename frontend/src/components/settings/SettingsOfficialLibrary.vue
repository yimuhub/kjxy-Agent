<template>
  <el-card class="section source-card">
    <template #header>
      <div class="card-header">
        <span>官方库源（{{ sources.length }}）</span>
        <div class="header-actions">
          <el-button
            size="small"
            :loading="loading"
            aria-label="刷新官方库源列表"
            @click="loadSources"
          >
            刷新
          </el-button>
          <el-button
            size="small"
            type="primary"
            aria-label="新增官方库源"
            @click="openCreate"
          >
            新增源
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      class="source-note"
      type="info"
      :closable="false"
      show-icon
      title="官方库由平台签名发布，客户端只接受验签通过的包；离线时继续使用本地已安装版本。"
    />

    <AsyncState
      :state="state"
      empty-text="还没有配置官方库源"
      empty-action-text="新增源"
      :error-text="errorText"
      @retry="loadSources"
      @action="openCreate"
    >
      <div class="table-scroll">
        <el-table
          :data="sources"
          row-key="id"
          stripe
        >
          <el-table-column
            label="名称"
            min-width="180"
          >
            <template #default="{ row }">
              <div class="source-name">
                {{ row.name || '未命名源' }}
              </div>
              <div
                v-if="row.last_error"
                class="source-error"
                :title="row.last_error"
              >
                {{ row.last_error }}
              </div>
            </template>
          </el-table-column>
          <el-table-column
            label="类型"
            width="90"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                effect="plain"
                :type="row.kind === 'official' ? 'success' : 'info'"
              >
                {{ kindLabel(row.kind) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="地址"
            min-width="200"
          >
            <template #default="{ row }">
              <span
                class="mono ellipsis"
                :title="row.base_url || ''"
              >{{ row.base_url || '—' }}</span>
            </template>
          </el-table-column>
          <el-table-column
            label="通道"
            width="90"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                effect="plain"
                :type="row.channel === 'beta' ? 'warning' : 'info'"
              >
                {{ channelLabel(row.channel) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="110"
          >
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="statusMeta(row as LibrarySource).type"
              >
                {{ statusMeta(row as LibrarySource).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="上次同步"
            width="170"
          >
            <template #default="{ row }">
              {{ formatTime(row.last_sync_at) }}
            </template>
          </el-table-column>
          <el-table-column
            label="公钥指纹"
            width="170"
          >
            <template #default="{ row }">
              <el-tooltip
                v-if="row.key_id"
                :content="row.key_id"
                placement="top"
              >
                <span class="mono">{{ shortKey(row.key_id) }}</span>
              </el-tooltip>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column
            label="启用"
            width="80"
          >
            <template #default="{ row }">
              <el-switch
                size="small"
                :model-value="row.enabled !== false"
                :loading="togglingId === row.id"
                :aria-label="`${row.enabled === false ? '启用' : '停用'} ${row.name || '库源'}`"
                @change="toggleEnabled(row as LibrarySource, $event)"
              />
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="240"
            fixed="right"
          >
            <template #default="{ row }">
              <el-button
                link
                type="primary"
                :loading="syncingId === row.id"
                :aria-label="`立即同步 ${row.name || '库源'}`"
                @click="syncSource(row as LibrarySource)"
              >
                立即同步
              </el-button>
              <el-button
                link
                :aria-label="`编辑 ${row.name || '库源'}`"
                @click="openEdit(row as LibrarySource)"
              >
                编辑
              </el-button>
              <el-button
                link
                type="danger"
                :aria-label="`删除 ${row.name || '库源'}`"
                @click="removeSource(row as LibrarySource)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </AsyncState>

    <div class="source-footnote">
      官方源仅平台管理员可创建；小院管理员可维护私有源。客户端只接受验签通过的包，验签失败会拒绝安装。
    </div>
  </el-card>

  <el-dialog
    v-model="dialogOpen"
    append-to-body
    destroy-on-close
    align-center
    :title="editing ? '编辑库源' : '新增库源'"
    width="min(560px, 94vw)"
  >
    <el-form
      :model="form"
      label-width="110px"
    >
      <el-form-item
        label="名称"
        required
      >
        <el-input
          v-model="form.name"
          maxlength="120"
          placeholder="如：平台官方库"
        />
      </el-form-item>
      <el-form-item label="类型">
        <el-select
          v-model="form.kind"
          :disabled="!canManageOfficial"
          class="full-col"
          aria-label="库源类型"
        >
          <el-option
            label="官方（平台签名）"
            value="official"
            :disabled="!canManageOfficial"
          />
          <el-option
            label="私有（小院自建）"
            value="private"
          />
        </el-select>
        <div
          v-if="!canManageOfficial"
          class="note"
        >
          仅平台管理员可新增官方源，当前仅能配置私有源。
        </div>
      </el-form-item>
      <el-form-item
        label="Base URL"
        required
      >
        <el-input
          v-model="form.base_url"
          placeholder="https://library.example.com"
        />
      </el-form-item>
      <el-form-item label="发布通道">
        <el-radio-group
          v-model="form.channel"
          aria-label="发布通道"
        >
          <el-radio-button value="stable">
            stable
          </el-radio-button>
          <el-radio-button value="beta">
            beta
          </el-radio-button>
        </el-radio-group>
        <div class="note">
          beta 通道可提前试用新条目，稳定后再切回 stable。
        </div>
      </el-form-item>
      <el-form-item label="公钥">
        <el-input
          v-model="form.public_key_b64"
          type="textarea"
          :rows="3"
          class="mono"
          placeholder="Base64 编码的 Ed25519 公钥"
          aria-label="库源公钥（Base64）"
        />
        <div class="note">
          {{
            editing
              ? '留空表示不更换公钥；填写则替换原公钥。'
              : '官方源需填写平台下发的 Base64 公钥，用于校验包签名。'
          }}
        </div>
      </el-form-item>
      <el-form-item label="启用">
        <el-switch
          v-model="form.enabled"
          aria-label="启用该库源"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button
        aria-label="取消编辑库源"
        @click="dialogOpen = false"
      >
        取消
      </el-button>
      <el-button
        type="primary"
        :loading="saving"
        aria-label="保存库源"
        @click="save"
      >
        保存
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  officialLibraryApi,
  type LibraryChannel,
  type LibrarySource,
  type LibrarySourceInput,
  type LibrarySourceKind,
  type LibrarySourcePatch,
} from '@/api/library'
import { extractErrorMessage } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import { useAuthStore } from '@/stores/auth'
import { classifyError, friendlyErrorMessage, type ViewState } from '@/utils/status'

type TagType = 'success' | 'warning' | 'danger' | 'info'

interface SourceForm {
  name: string
  kind: LibrarySourceKind
  base_url: string
  /** el-radio-group 的 v-model 类型较宽，提交时再收敛为 stable/beta */
  channel: string
  public_key_b64: string
  enabled: boolean
}

const SOURCE_STATUS_META: Record<string, { label: string; type: TagType }> = {
  ok: { label: '正常', type: 'success' },
  healthy: { label: '正常', type: 'success' },
  ready: { label: '正常', type: 'success' },
  synced: { label: '正常', type: 'success' },
  idle: { label: '正常', type: 'success' },
  pending: { label: '待同步', type: 'info' },
  syncing: { label: '同步中', type: 'warning' },
  running: { label: '同步中', type: 'warning' },
  error: { label: '同步失败', type: 'danger' },
  failed: { label: '同步失败', type: 'danger' },
  invalid: { label: '验签失败', type: 'danger' },
  offline: { label: '离线', type: 'warning' },
  disabled: { label: '已停用', type: 'info' },
}

const auth = useAuthStore()

const sources = ref<LibrarySource[]>([])
const state = ref<ViewState>('loading')
const errorText = ref('')
const loading = ref(false)
const saving = ref(false)
const syncingId = ref('')
const togglingId = ref('')
const dialogOpen = ref(false)
const editing = ref(false)
const editingId = ref('')
const editingKind = ref<LibrarySourceKind>('private')

const form = reactive<SourceForm>({
  name: '',
  kind: 'private',
  base_url: '',
  channel: 'stable',
  public_key_b64: '',
  enabled: true,
})

/** 角色未加载（为空）时不强制收敛，交给后端 403 兜底 */
const canManageOfficial = computed(
  () => !auth.roles.length || auth.roles.includes('platform_admin'),
)

function kindLabel(kind?: string): string {
  if (kind === 'official') return '官方'
  if (kind === 'private') return '私有'
  return kind || '未知'
}

function channelLabel(channel?: string): string {
  if (channel === 'beta') return 'beta'
  if (channel === 'stable') return 'stable'
  return channel || 'stable'
}

function statusMeta(row: LibrarySource): { label: string; type: TagType } {
  if (row.enabled === false) return { label: '已停用', type: 'info' }
  const raw = (row.status || '').toLowerCase()
  if (!raw) return { label: '未同步', type: 'info' }
  return SOURCE_STATUS_META[raw] ?? { label: row.status || '未知', type: 'info' }
}

function formatTime(value?: string | null): string {
  if (!value) return '从未同步'
  const time = new Date(value)
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString()
}

function shortKey(keyId: string): string {
  return keyId.length > 18 ? `${keyId.slice(0, 18)}…` : keyId
}

function isNetworkCode(code: string): boolean {
  return code === 'NETWORK_ERROR' || code === 'TIMEOUT'
}

function normalizeChannel(value: string): LibraryChannel {
  return value === 'beta' ? 'beta' : 'stable'
}

function reportFailure(err: unknown, action: string) {
  const { code, message } = extractErrorMessage(err)
  if (isNetworkCode(code)) {
    ElMessage.warning(`网络不可用，「${action}」未生效`)
    return
  }
  ElMessage.error(`${action}失败：${friendlyErrorMessage(code, message)}`)
}

async function loadSources() {
  loading.value = true
  if (!sources.value.length) state.value = 'loading'
  try {
    sources.value = await officialLibraryApi.sources()
    errorText.value = ''
    state.value = sources.value.length ? 'ready' : 'empty'
  } catch (err) {
    if (sources.value.length) {
      // 已有列表时保留原状态，错误提示由请求层统一处理
      state.value = 'ready'
    } else {
      const { code, message } = extractErrorMessage(err)
      errorText.value = friendlyErrorMessage(code, message)
      state.value = classifyError(err)
    }
  } finally {
    loading.value = false
  }
}

function resetForm() {
  Object.assign(form, {
    name: '',
    kind: canManageOfficial.value ? 'official' : 'private',
    base_url: '',
    channel: 'stable',
    public_key_b64: '',
    enabled: true,
  })
}

function openCreate() {
  editing.value = false
  editingId.value = ''
  editingKind.value = 'private'
  resetForm()
  dialogOpen.value = true
}

function openEdit(source: LibrarySource) {
  editing.value = true
  editingId.value = source.id
  editingKind.value = source.kind === 'official' ? 'official' : 'private'
  Object.assign(form, {
    name: source.name || '',
    kind: editingKind.value,
    base_url: source.base_url || '',
    channel: source.channel === 'beta' ? 'beta' : 'stable',
    public_key_b64: '',
    enabled: source.enabled !== false,
  })
  dialogOpen.value = true
}

async function save() {
  if (saving.value) return
  const name = form.name.trim()
  const baseUrl = form.base_url.trim()
  if (!name || !baseUrl) {
    ElMessage.warning('请填写名称与 Base URL')
    return
  }
  if (!/^https?:\/\//i.test(baseUrl)) {
    ElMessage.warning('Base URL 需以 http:// 或 https:// 开头')
    return
  }
  if (form.kind === 'official' && !/^https:\/\//i.test(baseUrl)) {
    ElMessage.warning('官方源需使用 HTTPS，避免公钥与包体被篡改')
    return
  }
  const needsPublicKey =
    form.kind === 'official' && (!editing.value || editingKind.value !== 'official')
  if (needsPublicKey && !form.public_key_b64.trim()) {
    ElMessage.warning('官方源需填写 Base64 公钥，用于校验包签名')
    return
  }

  const publicKey = form.public_key_b64.trim()
  const base = {
    name,
    base_url: baseUrl,
    channel: normalizeChannel(form.channel),
    enabled: form.enabled,
    ...(publicKey ? { public_key_b64: publicKey } : {}),
  }

  saving.value = true
  try {
    if (editing.value) {
      // 非平台管理员编辑官方源时不回传 kind，避免误改源类型
      const payload: LibrarySourcePatch = {
        ...base,
        ...(canManageOfficial.value || editingKind.value !== 'official'
          ? { kind: form.kind }
          : {}),
      }
      await officialLibraryApi.updateSource(editingId.value, payload)
      ElMessage.success(`已更新库源「${name}」`)
    } else {
      const payload: LibrarySourceInput = { ...base, kind: form.kind }
      await officialLibraryApi.createSource(payload)
      ElMessage.success(`已新增库源「${name}」`)
    }
    dialogOpen.value = false
    await loadSources()
  } catch (err) {
    reportFailure(err, '保存库源')
  } finally {
    saving.value = false
  }
}

async function syncSource(source: LibrarySource) {
  if (syncingId.value) return
  syncingId.value = source.id
  try {
    const result = await officialLibraryApi.syncSource(source.id)
    const parts = [
      `新增 ${result.added ?? 0}`,
      `更新 ${result.updated ?? 0}`,
      `移除 ${result.removed ?? 0}`,
      `跳过 ${result.skipped ?? 0}`,
    ]
    const suffix = result.message ? `；${result.message}` : ''
    ElMessage.success(`「${source.name || '库源'}」同步完成：${parts.join('，')}${suffix}`)
    await loadSources()
  } catch (err) {
    reportFailure(err, `同步「${source.name || '库源'}」`)
  } finally {
    syncingId.value = ''
  }
}

async function toggleEnabled(source: LibrarySource, value: string | number | boolean) {
  if (togglingId.value) return
  const enabled = value === true
  togglingId.value = source.id
  try {
    await officialLibraryApi.updateSource(source.id, { enabled })
    ElMessage.success(enabled ? '已启用该源' : '已停用该源')
    await loadSources()
  } catch (err) {
    reportFailure(err, enabled ? '启用库源' : '停用库源')
  } finally {
    togglingId.value = ''
  }
}

async function removeSource(source: LibrarySource) {
  try {
    await ElMessageBox.confirm(
      `确定删除库源「${source.name || '未命名源'}」？删除后该源下的条目不再可安装，已安装版本不受影响。`,
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await officialLibraryApi.removeSource(source.id)
    ElMessage.success('已删除库源')
    await loadSources()
  } catch (err) {
    reportFailure(err, '删除库源')
  }
}

onMounted(() => {
  void loadSources()
})
</script>

<style scoped>
.source-card :deep(.el-card__body) {
  padding-top: 12px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.header-actions {
  display: flex;
  gap: 8px;
}
.source-note {
  margin-bottom: 12px;
}
.table-scroll {
  width: 100%;
  overflow-x: auto;
}
.source-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--ds-text-1);
}
.source-error {
  margin-top: 2px;
  max-width: 240px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  color: var(--ds-danger);
}
.mono {
  font-family: ui-monospace, monospace;
  font-size: 12px;
  color: var(--ds-text-2);
}
.ellipsis {
  display: inline-block;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  vertical-align: bottom;
}
.full-col {
  width: 100%;
}
.note {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--ds-text-3);
}
.source-footnote {
  margin-top: 12px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--ds-text-3);
}
</style>
