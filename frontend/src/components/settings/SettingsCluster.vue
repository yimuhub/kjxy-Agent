<template>
  <el-card
    shadow="never"
    class="panel cluster-panel"
  >
    <template #header>
      <div class="panel-head">
        <div>
          <h2>集群节点</h2>
          <p>登记其他部署实例，统一体检版本与数据库迁移；只读探测公开 meta，不保存任何令牌。</p>
        </div>
        <div class="panel-actions">
          <el-button
            size="small"
            :disabled="!nodes.length || busy === 'all'"
            :loading="busy === 'all'"
            @click="probeAll"
          >
            全部探测
          </el-button>
          <el-button
            size="small"
            type="primary"
            @click="openCreate"
          >
            新增节点
          </el-button>
        </div>
      </div>
    </template>

    <div class="summary-chips">
      <span class="chip">共 {{ nodes.length }} 个</span>
      <span class="chip online">在线 {{ summary.online }}</span>
      <span class="chip degraded">版本不一致 {{ summary.degraded }}</span>
      <span class="chip offline">离线 {{ summary.offline }}</span>
      <span class="chip never">未探测 {{ summary.never }}</span>
    </div>

    <AsyncState
      :state="state"
      empty-text="还没有登记节点；先新增一个其他部署实例。"
      @retry="load"
    >
      <el-table
        v-col-resize
        :data="nodes"
        size="small"
        class="node-table"
      >
        <el-table-column
          prop="name"
          label="节点"
          min-width="140"
        >
          <template #default="{ row }">
            <div class="node-name">
              <strong>{{ row.name }}</strong>
              <small v-if="row.note">{{ row.note }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          prop="base_url"
          label="地址"
          min-width="220"
          show-overflow-tooltip
        />
        <el-table-column
          label="状态"
          width="150"
        >
          <template #default="{ row }">
            <el-tooltip
              :disabled="!row.last_error"
              :content="row.last_error"
              placement="top"
            >
              <el-tag
                size="small"
                effect="plain"
                :type="statusTone(row.status)"
              >
                {{ row.status_label }}{{ row.stale ? '（已过期）' : '' }}
              </el-tag>
            </el-tooltip>
          </template>
        </el-table-column>
        <el-table-column
          label="版本 / 迁移"
          min-width="170"
        >
          <template #default="{ row }">
            <div class="node-version">
              <span>{{ row.version || '—' }}</span>
              <small>{{ row.migration_head || '—' }}</small>
            </div>
          </template>
        </el-table-column>
        <el-table-column
          label="延迟"
          width="90"
        >
          <template #default="{ row }">
            {{ row.last_checked_at ? `${row.latency_ms} ms` : '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="最近检测"
          width="160"
        >
          <template #default="{ row }">
            {{ formatTime(row.last_checked_at) }}
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          width="170"
          fixed="right"
        >
          <template #default="{ row }">
            <el-button
              link
              type="primary"
              :loading="busy === (row as ClusterNode).id"
              @click="probe(row as ClusterNode)"
            >
              探测
            </el-button>
            <el-button
              link
              @click="openEdit(row as ClusterNode)"
            >
              编辑
            </el-button>
            <el-button
              link
              type="danger"
              @click="remove(row as ClusterNode)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </AsyncState>

    <p class="foot-note">
      节点地址变更会清空旧探测结论；探测失败只记录短错误，不保存响应正文。
    </p>

    <el-dialog
      v-model="dialogVisible"
      :title="editingId ? '编辑集群节点' : '新增集群节点'"
      width="560px"
      append-to-body
    >
      <el-form
        label-width="90px"
        @submit.prevent
      >
        <el-form-item label="节点名称">
          <el-input
            v-model="form.name"
            maxlength="60"
            show-word-limit
            placeholder="例如：华东主节点"
          />
        </el-form-item>
        <el-form-item label="根地址">
          <el-input
            v-model="form.base_url"
            maxlength="300"
            placeholder="https://example.com（不要带 /api 路径）"
          />
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="form.note"
            maxlength="200"
            placeholder="可选：机房、用途或负责人"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="save"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </el-card>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import {
    clusterApi,
    type ClusterNode,
    type ClusterNodeInput,
    type ClusterNodeSummary,
  } from '@/api/modules'
  import AsyncState from '@/components/AsyncState.vue'
  import { useAuthStore } from '@/stores/auth'
  import { classifyError, type ViewState } from '@/utils/status'

  const auth = useAuthStore()
  const nodes = ref<ClusterNode[]>([])
  const state = ref<ViewState>('loading')
  const busy = ref('')
  const dialogVisible = ref(false)
  const editingId = ref('')
  const saving = ref(false)
  const form = reactive<ClusterNodeInput>({ name: '', base_url: '', note: '' })

  const summary = computed<ClusterNodeSummary>(() => ({
    total: nodes.value.length,
    online: nodes.value.filter(item => item.status === 'online').length,
    degraded: nodes.value.filter(item => item.status === 'degraded').length,
    offline: nodes.value.filter(item => item.status === 'offline').length,
    never: nodes.value.filter(item => item.status === 'never').length,
  }))

  function statusTone(status: string): 'success' | 'warning' | 'danger' | 'info' {
    if (status === 'online') return 'success'
    if (status === 'degraded') return 'warning'
    if (status === 'offline') return 'danger'
    return 'info'
  }

  function formatTime(value?: string | null): string {
    if (!value) return '—'
    const date = new Date(value)
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('zh-CN')
  }

  async function load(): Promise<void> {
    if (!auth.roles.includes('platform_admin')) return
    state.value = 'loading'
    try {
      const data = await clusterApi.list()
      nodes.value = data.items
      state.value = nodes.value.length ? 'ready' : 'empty'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  function replaceNode(next: ClusterNode): void {
    const index = nodes.value.findIndex(item => item.id === next.id)
    if (index >= 0) nodes.value.splice(index, 1, next)
    else nodes.value.unshift(next)
  }

  function openCreate(): void {
    editingId.value = ''
    form.name = ''
    form.base_url = ''
    form.note = ''
    dialogVisible.value = true
  }

  function openEdit(row: ClusterNode): void {
    editingId.value = row.id
    form.name = row.name
    form.base_url = row.base_url
    form.note = row.note
    dialogVisible.value = true
  }

  async function save(): Promise<void> {
    if (!form.name.trim() || !form.base_url.trim()) {
      ElMessage.warning('节点名称与根地址都要填写')
      return
    }
    saving.value = true
    try {
      const payload = {
        name: form.name.trim(),
        base_url: form.base_url.trim(),
        note: (form.note || '').trim(),
      }
      if (editingId.value) {
        await clusterApi.update(editingId.value, payload)
        ElMessage.success('节点已更新')
      } else {
        await clusterApi.create(payload)
        ElMessage.success('节点已新增')
      }
      dialogVisible.value = false
      await load()
    } catch {
      ElMessage.error('保存失败，请检查名称/地址是否重复')
    } finally {
      saving.value = false
    }
  }

  async function probe(row: ClusterNode): Promise<void> {
    busy.value = row.id
    try {
      const data = await clusterApi.probe(row.id)
      replaceNode(data.node)
      if (data.node.status === 'online') ElMessage.success('节点在线，版本与迁移一致')
      else if (data.node.status === 'degraded') ElMessage.warning('节点可达，但版本或迁移不一致')
      else ElMessage.error('节点不可达，已记录原因')
    } catch {
      ElMessage.error('探测失败，请稍后重试')
    } finally {
      busy.value = ''
    }
  }

  async function probeAll(): Promise<void> {
    busy.value = 'all'
    try {
      const data = await clusterApi.probeAll()
      nodes.value = data.items
      ElMessage.success(
        `已探测 ${data.summary.total} 个节点：在线 ${data.summary.online}，不一致 ${data.summary.degraded}，离线 ${data.summary.offline}`,
      )
    } catch {
      ElMessage.error('批量探测失败，请稍后重试')
    } finally {
      busy.value = ''
    }
  }

  async function remove(row: ClusterNode): Promise<void> {
    try {
      await ElMessageBox.confirm(`删除节点「${row.name}」？只移除登记，不影响远端实例。`, '删除节点', {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    try {
      await clusterApi.remove(row.id)
      ElMessage.success('节点已删除')
      await load()
    } catch {
      ElMessage.error('删除失败，请稍后重试')
    }
  }

  onMounted(() => {
    void load()
  })
</script>

<style scoped>
  .cluster-panel {
    border: 1px solid var(--ds-border);
    border-radius: 12px;
    background: var(--ds-surface);
  }

  .panel-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }

  .panel-head h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .panel-head p {
    margin: 4px 0 0;
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--ds-text-3);
  }

  .summary-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 12px;
  }

  .chip {
    padding: 3px 10px;
    border: 1px solid var(--ds-border);
    border-radius: 999px;
    background: var(--ds-field);
    font-size: 11.5px;
    color: var(--ds-text-2);
  }

  .chip.online {
    border-color: rgba(76, 175, 120, 0.4);
    color: #4caf78;
  }

  .chip.degraded {
    border-color: rgba(230, 162, 60, 0.4);
    color: #e0a030;
  }

  .chip.offline {
    border-color: rgba(230, 90, 90, 0.4);
    color: #e05a5a;
  }

  .chip.never {
    color: var(--ds-text-3);
  }

  .node-name,
  .node-version {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }

  .node-name small,
  .node-version small {
    font-size: 11px;
    color: var(--ds-text-3);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .foot-note {
    margin: 12px 0 0;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
</style>
