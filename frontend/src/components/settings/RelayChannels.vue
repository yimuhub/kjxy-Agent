<template>
  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>官方供应商（{{ channels.length }}）</span>
        <div class="page-actions">
          <el-button
            size="small"
            @click="load()"
          >
            刷新
          </el-button>
          <el-button
            size="small"
            type="primary"
            @click="openCreate"
          >
            新建渠道
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="hint"
      title="这是我们自己的大模型 API 官方供应商入口。填地址与访问 Key 后拉取模型目录；以后换模型、换价格只需在服务端更新，客户端点同步即可。"
    />

    <AsyncState
      :state="state"
      empty-text="还没有官方供应商渠道。新建一个后即可同步服务端的模型目录。"
      @retry="load()"
    >
      <template #empty-action>
        <el-button
          type="primary"
          plain
          @click="openCreate"
        >
          新建渠道
        </el-button>
      </template>

      <div class="table-scroll">
        <el-table
          v-col-resize
          :data="channels"
          row-key="id"
          stripe
          empty-text="暂无官方供应商渠道"
        >
          <el-table-column
            label="名称"
            min-width="150"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ (row as RelayChannel).name || '未命名渠道' }}
            </template>
          </el-table-column>
          <el-table-column
            label="类型"
            width="100"
          >
            <template #default="{ row }">
              <el-tag
                :type="kindMeta((row as RelayChannel).kind).type"
                effect="plain"
                size="small"
              >
                {{ kindMeta((row as RelayChannel).kind).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            label="地址"
            min-width="240"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              <code class="endpoint">{{ (row as RelayChannel).base_url || '—' }}</code>
            </template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="110"
          >
            <template #default="{ row }">
              <el-tooltip
                :content="(row as RelayChannel).last_error || '状态由最近一次同步结果决定'"
                placement="top"
              >
                <el-tag
                  :type="statusMeta(row as RelayChannel).type"
                  size="small"
                >
                  {{ statusMeta(row as RelayChannel).label }}
                </el-tag>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column
            label="上次同步"
            width="170"
          >
            <template #default="{ row }">
              {{ formatDateTime((row as RelayChannel).last_sync_at) }}
            </template>
          </el-table-column>
          <el-table-column
            label="模型数"
            width="90"
          >
            <template #default="{ row }">
              {{ modelCount(row as RelayChannel) }}
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="220"
            fixed="right"
          >
            <template #default="{ row }">
              <el-button
                size="small"
                type="primary"
                text
                :loading="syncingId === (row as RelayChannel).id"
                :disabled="!(row as RelayChannel).id"
                @click="syncChannel(row as RelayChannel)"
              >
                同步目录
              </el-button>
              <el-button
                size="small"
                text
                :disabled="!(row as RelayChannel).id"
                @click="openEdit(row as RelayChannel)"
              >
                编辑
              </el-button>
              <el-button
                size="small"
                text
                type="danger"
                :disabled="!(row as RelayChannel).id"
                @click="removeChannel(row as RelayChannel)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
    </AsyncState>

    <div class="secret-note">
      <el-icon><Lock /></el-icon>
      <span>访问 Key 加密存储、只展示打码形式；编辑时留空表示不修改。同步只会更新模型目录，不会改动已有绑定。</span>
    </div>

    <!-- 新建 / 编辑渠道 -->
    <el-dialog
      v-model="dialogVisible"
      append-to-body
      :title="editingId ? '编辑官方供应商渠道' : '新建官方供应商渠道'"
      width="640px"
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
            placeholder="如 官方供应商"
            maxlength="60"
          />
        </el-form-item>
        <el-form-item
          label="类型"
          required
        >
          <el-radio-group v-model="form.kind">
            <el-radio value="official">
              官方
            </el-radio>
            <el-radio value="private">
              私有
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item
          label="Base URL"
          required
        >
          <el-input
            v-model="form.base_url"
            placeholder="https://relay.example.com/v1"
          />
          <span class="note">官方供应商根地址，目录路径会拼接在其后</span>
        </el-form-item>
        <el-form-item
          :label="editingId ? '访问 Key（轮换）' : '访问 Key'"
          :required="!editingId"
        >
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="editingId ? '留空则不修改' : '官方供应商访问 Key，加密存储'"
          />
        </el-form-item>
        <el-form-item label="公钥（可选）">
          <el-input
            v-model="form.public_key"
            type="textarea"
            :rows="2"
            placeholder="如需校验官方供应商签名，可填写公钥"
          />
        </el-form-item>
        <el-form-item label="目录路径">
          <el-input
            v-model="form.catalog_path"
            placeholder="/models"
            maxlength="120"
          />
          <span class="note">默认 /models；同步时读取该路径下的模型目录</span>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="saveChannel"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 同步结果 -->
    <el-dialog
      v-model="syncVisible"
      append-to-body
      title="同步目录结果"
      width="620px"
    >
      <template v-if="syncResult">
        <el-descriptions
          :column="3"
          border
        >
          <el-descriptions-item label="新增">
            {{ syncResult.created ?? 0 }}
          </el-descriptions-item>
          <el-descriptions-item label="更新">
            {{ syncResult.updated ?? 0 }}
          </el-descriptions-item>
          <el-descriptions-item label="移除">
            {{ syncResult.removed ?? 0 }}
          </el-descriptions-item>
        </el-descriptions>
        <div
          v-if="syncResult.message"
          class="note sync-message"
        >
          {{ syncResult.message }}
          <template v-if="syncResult.source">
            （来源：{{ syncResult.source }}）
          </template>
        </div>
        <el-table
          v-col-resize
          v-if="syncResult.items?.length"
          :data="syncResult.items"
          max-height="260"
          stripe
          class="sync-table"
        >
          <el-table-column
            prop="model_key"
            label="模型 Key"
            min-width="220"
            show-overflow-tooltip
          />
          <el-table-column
            label="动作"
            width="100"
          >
            <template #default="{ row }">
              <el-tag
                :type="syncActionMeta((row as RelaySyncItem).action).type"
                effect="plain"
                size="small"
              >
                {{ syncActionMeta((row as RelaySyncItem).action).label }}
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
      </template>
      <template #footer>
        <el-button
          type="primary"
          @click="syncVisible = false"
        >
          关闭
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
  /** 官方供应商：渠道 CRUD + 目录同步。 */
  import { onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { Lock } from '@element-plus/icons-vue'
  import { extractErrorMessage } from '@/api/client'
  import { relayChannelApi } from '@/api/modelChannels'
  import type {
    RelayChannel,
    RelayChannelKind,
    RelaySyncItem,
    RelaySyncResult,
    TagType,
  } from '@/api/modelChannels'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'
  import { formatDateTime } from '@/utils/format'

  const emit = defineEmits<{ changed: [] }>()

  const channels = ref<RelayChannel[]>([])
  /** 渠道 id → 模型数；后端未在渠道行返回 model_count 时用模型接口兜底 */
  const counts = ref<Record<string, number>>({})
  const state = ref<ViewState>('loading')
  const dialogVisible = ref(false)
  const editingId = ref<string | null>(null)
  const saving = ref(false)
  const syncingId = ref('')
  const syncVisible = ref(false)
  const syncResult = ref<RelaySyncResult | null>(null)

  const form = reactive({
    name: '',
    kind: 'official' as RelayChannelKind,
    base_url: '',
    api_key: '',
    public_key: '',
    catalog_path: '',
  })

  function kindMeta(kind?: string | null): { label: string; type: TagType } {
    return kind === 'private'
      ? { label: '私有', type: 'info' }
      : { label: '官方', type: 'primary' }
  }

  function statusMeta(channel: RelayChannel): { label: string; type: TagType } {
    const raw = channel.status
    if (typeof raw === 'number') {
      if (raw === 1) return { label: '正常', type: 'success' }
      return { label: '停用', type: 'info' }
    }
    const key = String(raw ?? '').toLowerCase()
    if (['1', 'active', 'enabled', 'ok', 'synced', 'ready'].includes(key)) {
      return { label: '正常', type: 'success' }
    }
    if (['0', '2', 'disabled', 'inactive'].includes(key)) {
      return { label: '停用', type: 'info' }
    }
    if (['error', 'failed', 'failure'].includes(key)) {
      return { label: '异常', type: 'danger' }
    }
    if (channel.last_error) return { label: '异常', type: 'danger' }
    if (channel.last_sync_at) return { label: '已同步', type: 'success' }
    return { label: raw ? String(raw) : '待同步', type: 'info' }
  }

  function syncActionMeta(action?: string): {
    label: string
    type: 'success' | 'warning' | 'info' | 'danger'
  } {
    switch (action) {
      case 'create':
      case 'created':
      case 'add':
        return { label: '新增', type: 'success' }
      case 'update':
      case 'updated':
        return { label: '更新', type: 'warning' }
      case 'remove':
      case 'removed':
      case 'delete':
        return { label: '移除', type: 'info' }
      case 'skip':
        return { label: '跳过', type: 'info' }
      case 'error':
        return { label: '错误', type: 'danger' }
      default:
        return { label: action || '—', type: 'info' }
    }
  }

  async function load(showSkeleton = true): Promise<void> {
    if (showSkeleton) state.value = 'loading'
    try {
      channels.value = await relayChannelApi.list()
      state.value = channels.value.length ? 'ready' : 'empty'
      await loadCounts()
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  /** 渠道行没带 model_count 时，用目录接口数一下，避免表格只有「—」 */
  async function loadCounts(): Promise<void> {
    const next: Record<string, number> = {}
    await Promise.all(
      channels.value.map(async channel => {
        if (!channel.id) return
        if (channel.model_count !== undefined && channel.model_count !== null) {
          next[channel.id] = channel.model_count
          return
        }
        try {
          next[channel.id] = (await relayChannelApi.models(channel.id, true)).length
        } catch {
          /* 目录接口不可用时保持占位 */
        }
      }),
    )
    counts.value = next
  }

  function modelCount(channel: RelayChannel): number | string {
    if (channel.id && counts.value[channel.id] !== undefined) return counts.value[channel.id]
    if (channel.model_count !== undefined && channel.model_count !== null) return channel.model_count
    return '—'
  }

  function resetForm(): void {
    Object.assign(form, {
      name: '',
      kind: 'official' as RelayChannelKind,
      base_url: '',
      api_key: '',
      public_key: '',
      catalog_path: '',
    })
  }

  function openCreate(): void {
    editingId.value = null
    resetForm()
    dialogVisible.value = true
  }

  function openEdit(channel: RelayChannel): void {
    editingId.value = channel.id ?? null
    Object.assign(form, {
      name: channel.name ?? '',
      kind: (channel.kind === 'private' ? 'private' : 'official') as RelayChannelKind,
      base_url: channel.base_url ?? '',
      api_key: '',
      public_key: channel.public_key ?? '',
      catalog_path: channel.catalog_path ?? '',
    })
    dialogVisible.value = true
  }

  async function saveChannel(): Promise<void> {
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
    if (!editingId.value && !form.api_key.trim()) {
      ElMessage.warning('请填写访问 Key')
      return
    }
    const payload = {
      name,
      kind: form.kind,
      base_url: baseUrl,
      api_key: form.api_key.trim() || undefined,
      public_key: form.public_key.trim() || undefined,
      catalog_path: form.catalog_path.trim() || '/models',
    }
    saving.value = true
    try {
      if (editingId.value) {
        await relayChannelApi.update(editingId.value, payload)
        ElMessage.success('渠道已更新')
      } else {
        await relayChannelApi.create(payload)
        ElMessage.success('渠道已创建')
      }
      dialogVisible.value = false
      await load(false)
      emit('changed')
    } catch (e) {
      ElMessage.error(`保存失败：${extractErrorMessage(e).message}`)
    } finally {
      saving.value = false
    }
  }

  async function syncChannel(channel: RelayChannel): Promise<void> {
    const id = channel.id
    if (!id || syncingId.value) return
    syncingId.value = id
    try {
      const res = await relayChannelApi.sync(id)
      syncResult.value = res
      syncVisible.value = true
      ElMessage.success(
        `同步完成：新增 ${res.created ?? 0}，更新 ${res.updated ?? 0}，移除 ${res.removed ?? 0}`,
      )
      await load(false)
      emit('changed')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      syncingId.value = ''
    }
  }

  async function removeChannel(channel: RelayChannel): Promise<void> {
    const id = channel.id
    if (!id) return
    try {
      await ElMessageBox.confirm(
        `确定删除官方供应商渠道「${channel.name || id}」？已同步到本地的模型不会被自动删除。`,
        '删除确认',
        { type: 'warning' },
      )
    } catch {
      return
    }
    try {
      await relayChannelApi.remove(id)
      ElMessage.success('已删除')
      await load(false)
      emit('changed')
    } catch (e) {
      ElMessage.error(`删除失败：${extractErrorMessage(e).message}`)
    }
  }

  onMounted(() => {
    void load()
  })
</script>

<style scoped>
  .section {
    margin-bottom: 16px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .hint {
    margin-bottom: 12px;
  }
  .table-scroll {
    overflow-x: auto;
  }
  .endpoint {
    font-family: ui-monospace, monospace;
    font-size: 12px;
    color: var(--ds-text-2);
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: 6px;
    padding: 1px 6px;
  }
  .note {
    margin-top: 4px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .sync-message {
    margin-top: 10px;
  }
  .sync-table {
    margin-top: 10px;
  }
  .secret-note {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 16px;
    color: var(--ds-text-2);
    font-size: 13px;
  }
</style>
