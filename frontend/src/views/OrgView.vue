<template>
  <div class="org-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">
          组织架构
        </h2>
        <p class="sub">
          把合作社 / 公司的部门、岗位与本小院 Agent 对齐；后续可从知识库资料自动推导。
        </p>
      </div>
      <div class="page-actions">
        <el-switch
          v-model="includeArchived"
          active-text="含已归档"
          aria-label="显示已归档组织"
          @change="() => reload()"
        />
        <el-button
          :loading="loading"
          @click="reload"
        >
          刷新
        </el-button>
        <el-button
          v-if="canManage"
          type="primary"
          @click="openUnitDialog()"
        >
          新建组织
        </el-button>
      </div>
    </div>

    <div class="stats">
      <div class="stat-card">
        <span class="stat-label">组织单元</span>
        <strong class="stat-value">{{ tree?.summary.units ?? 0 }}</strong>
      </div>
      <div class="stat-card">
        <span class="stat-label">部门 / 小组</span>
        <strong class="stat-value">{{ tree?.summary.departments ?? 0 }}</strong>
      </div>
      <div class="stat-card">
        <span class="stat-label">岗位</span>
        <strong class="stat-value">{{ tree?.summary.positions ?? 0 }}</strong>
      </div>
      <div class="stat-card">
        <span class="stat-label">已绑定 Agent</span>
        <strong class="stat-value">{{ tree?.summary.agents_bound ?? 0 }}</strong>
      </div>
    </div>

    <AsyncState
      :state="state"
      empty-text="还没有组织架构，先新建一个组织主体"
      empty-action-text="新建组织"
      @action="openUnitDialog()"
      @retry="reload"
    >
      <div class="org-layout">
        <el-card
          class="tree-card"
          shadow="never"
        >
          <template #header>
            <div class="card-head">
              <span>组织结构</span>
              <span class="note">{{ tree?.items.length ?? 0 }} 个顶层</span>
            </div>
          </template>
          <el-tree
            v-if="tree?.items.length"
            :data="tree.items"
            node-key="id"
            :props="{ children: 'children', label: 'name' }"
            default-expand-all
            highlight-current
            :current-node-key="selectedId"
            :expand-on-click-node="false"
            @node-click="selectUnit"
          >
            <template #default="{ data }">
              <div class="tree-node">
                <span class="tree-name">{{ data.name }}</span>
                <el-tag
                  size="small"
                  effect="plain"
                >
                  {{ data.kind_label }}
                </el-tag>
                <span
                  v-if="data.positions.length"
                  class="tree-count"
                >{{ data.positions.length }} 岗</span>
                <el-tag
                  v-if="data.status === 'archived'"
                  size="small"
                  type="info"
                >
                  已归档
                </el-tag>
              </div>
            </template>
          </el-tree>
          <el-empty
            v-else
            description="暂无组织单元"
            :image-size="88"
          >
            <el-button
              v-if="canManage"
              type="primary"
              plain
              @click="openUnitDialog()"
            >
              新建组织
            </el-button>
          </el-empty>
        </el-card>

        <el-card
          class="detail-card"
          shadow="never"
        >
          <template v-if="selected">
            <div class="detail-head">
              <div>
                <div class="detail-title">
                  {{ selected.name }}
                  <el-tag
                    size="small"
                    effect="plain"
                  >
                    {{ selected.kind_label }}
                  </el-tag>
                  <el-tag
                    v-if="selected.status === 'archived'"
                    size="small"
                    type="info"
                  >
                    已归档
                  </el-tag>
                </div>
                <div class="detail-meta">
                  <span class="mono">{{ selected.code }}</span>
                  <span>排序 {{ selected.sort_order }}</span>
                  <span>来源 {{ sourceLabel(selected.source) }}</span>
                </div>
              </div>
              <div
                v-if="canManage"
                class="detail-actions"
              >
                <el-button
                  size="small"
                  @click="openUnitDialog(selected)"
                >
                  编辑
                </el-button>
                <el-button
                  v-if="selected.status === 'active'"
                  size="small"
                  @click="setUnitStatus(selected, 'archived')"
                >
                  归档
                </el-button>
                <el-button
                  v-else
                  size="small"
                  @click="setUnitStatus(selected, 'active')"
                >
                  恢复
                </el-button>
                <el-button
                  size="small"
                  type="danger"
                  @click="removeUnit(selected)"
                >
                  删除
                </el-button>
              </div>
            </div>

            <p class="detail-desc">
              {{ selected.description || '暂无说明。' }}
            </p>

            <div class="positions-head">
              <div>
                <span class="section-title">岗位</span>
                <span class="note">把岗位职责与 Agent 绑定，后续编排可直接引用。</span>
              </div>
              <el-button
                v-if="canManage"
                size="small"
                type="primary"
                plain
                @click="openPositionDialog()"
              >
                新增岗位
              </el-button>
            </div>

            <el-table
              v-col-resize
              :data="selected.positions"
              border
              size="small"
              empty-text="该组织下还没有岗位"
            >
              <el-table-column
                prop="title"
                label="岗位"
                min-width="160"
                show-overflow-tooltip
              />
              <el-table-column
                prop="responsibilities"
                label="职责"
                min-width="260"
                show-overflow-tooltip
              >
                <template #default="{ row }">
                  {{ row.responsibilities || '—' }}
                </template>
              </el-table-column>
              <el-table-column
                prop="headcount"
                label="编制"
                width="90"
              />
              <el-table-column
                label="绑定 Agent"
                min-width="150"
                show-overflow-tooltip
              >
                <template #default="{ row }">
                  <span v-if="row.agent">{{ row.agent.emoji || '🤖' }} {{ row.agent.name }}</span>
                  <span
                    v-else
                    class="muted"
                  >未绑定</span>
                </template>
              </el-table-column>
              <el-table-column
                label="状态"
                width="100"
              >
                <template #default="{ row }">
                  <el-tag
                    size="small"
                    :type="row.status === 'active' ? 'success' : 'info'"
                    effect="plain"
                  >
                    {{ row.status === 'active' ? '在用' : '停用' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column
                v-if="canManage"
                label="操作"
                width="160"
                fixed="right"
              >
                <template #default="{ row }">
                  <el-button
                    link
                    type="primary"
                    @click="openPositionDialog(row as OrgPosition)"
                  >
                    编辑
                  </el-button>
                  <el-button
                    link
                    type="danger"
                    @click="removePosition(row as OrgPosition)"
                  >
                    删除
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </template>
          <el-empty
            v-else
            description="从左侧选择一个组织单元"
            :image-size="96"
          />
        </el-card>
      </div>
    </AsyncState>

    <el-dialog
      v-model="unitDialogVisible"
      append-to-body
      align-center
      draggable
      :title="unitForm.id ? '编辑组织单元' : '新建组织单元'"
      width="min(620px, 94vw)"
    >
      <el-form
        label-width="88px"
        @submit.prevent
      >
        <el-form-item
          label="名称"
          required
        >
          <el-input
            v-model="unitForm.name"
            maxlength="80"
            show-word-limit
            placeholder="如：技术部 / 种植小组"
          />
        </el-form-item>
        <el-form-item label="类型">
          <el-select
            v-model="unitForm.kind"
            class="full"
          >
            <el-option
              v-for="item in UNIT_KINDS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="上级组织">
          <el-select
            v-model="unitForm.parent_id"
            class="full"
            clearable
            placeholder="不选则作为顶层"
          >
            <el-option
              v-for="unit in parentOptions"
              :key="unit.id"
              :label="unit.path"
              :value="unit.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="编码">
          <el-input
            v-model="unitForm.code"
            maxlength="60"
            placeholder="留空自动生成，如 tech-dept"
          />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number
            v-model="unitForm.sort_order"
            :min="0"
            :max="9999"
            controls-position="right"
          />
        </el-form-item>
        <el-form-item label="说明">
          <el-input
            v-model="unitForm.description"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="这个单元负责什么"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="unitDialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="!unitForm.name.trim()"
          @click="saveUnit"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="positionDialogVisible"
      append-to-body
      align-center
      draggable
      :title="positionForm.id ? '编辑岗位' : '新增岗位'"
      width="min(640px, 94vw)"
    >
      <el-form
        label-width="88px"
        @submit.prevent
      >
        <el-form-item
          label="所属组织"
          required
        >
          <el-select
            v-model="positionForm.org_unit_id"
            class="full"
            :disabled="Boolean(positionForm.id)"
          >
            <el-option
              v-for="unit in tree?.flat ?? []"
              :key="unit.id"
              :label="unit.path"
              :value="unit.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          label="岗位名称"
          required
        >
          <el-input
            v-model="positionForm.title"
            maxlength="80"
            placeholder="如：种植技术负责人"
          />
        </el-form-item>
        <el-form-item label="编制人数">
          <el-input-number
            v-model="positionForm.headcount"
            :min="1"
            :max="999"
            controls-position="right"
          />
        </el-form-item>
        <el-form-item label="绑定 Agent">
          <el-select
            v-model="positionForm.agent_id"
            class="full"
            clearable
            filterable
            placeholder="可选：选择本小院 Agent"
          >
            <el-option
              v-for="agent in agents"
              :key="agent.id"
              :label="`${agent.emoji || '🤖'} ${agent.name}`"
              :value="agent.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number
            v-model="positionForm.sort_order"
            :min="0"
            :max="9999"
            controls-position="right"
          />
        </el-form-item>
        <el-form-item label="岗位职责">
          <el-input
            v-model="positionForm.responsibilities"
            type="textarea"
            :rows="4"
            maxlength="4000"
            show-word-limit
            placeholder="这个岗位承担哪些工作、向谁汇报"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="positionDialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          :disabled="!positionForm.title.trim() || !positionForm.org_unit_id"
          @click="savePosition"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { extractErrorMessage } from '@/api/client'
  import { declarativeApi, type DeclarativeAgent } from '@/api/modules'
  import {
    orgApi,
    type OrgPosition,
    type OrgTree,
    type OrgUnit,
    type OrgUnitKind,
    type OrgUnitStatus,
  } from '@/api/org'
  import AsyncState from '@/components/AsyncState.vue'
  import { useAuthStore } from '@/stores/auth'
  import { classifyError, type ViewState } from '@/utils/status'

  const UNIT_KINDS: { value: OrgUnitKind; label: string }[] = [
    { value: 'company', label: '组织主体' },
    { value: 'coop', label: '合作社' },
    { value: 'department', label: '部门' },
    { value: 'team', label: '小组' },
  ]

  const auth = useAuthStore()
  const canManage = computed(() => auth.hasPermission('org:manage'))
  const state = ref<ViewState>('loading')
  const loading = ref(false)
  const saving = ref(false)
  const includeArchived = ref(false)
  const tree = ref<OrgTree | null>(null)
  const selectedId = ref('')
  const selected = ref<OrgUnit | null>(null)
  const agents = ref<DeclarativeAgent[]>([])

  const unitDialogVisible = ref(false)
  const positionDialogVisible = ref(false)
  const unitForm = reactive({
    id: '',
    name: '',
    kind: 'department' as OrgUnitKind,
    parent_id: null as string | null,
    code: '',
    description: '',
    sort_order: 0,
  })
  const positionForm = reactive({
    id: '',
    org_unit_id: '',
    title: '',
    responsibilities: '',
    headcount: 1,
    agent_id: null as string | null,
    sort_order: 0,
  })

  function findUnit(nodes: OrgUnit[], id: string): OrgUnit | null {
    for (const node of nodes) {
      if (node.id === id) return node
      const hit = findUnit(node.children, id)
      if (hit) return hit
    }
    return null
  }

  function collectIds(node: OrgUnit): Set<string> {
    const ids = new Set<string>([node.id])
    node.children.forEach(child => collectIds(child).forEach(id => ids.add(id)))
    return ids
  }

  const parentOptions = computed(() => {
    const flat = tree.value?.flat ?? []
    if (!unitForm.id) return flat
    const current = tree.value ? findUnit(tree.value.items, unitForm.id) : null
    const blocked = current ? collectIds(current) : new Set([unitForm.id])
    return flat.filter(unit => !blocked.has(unit.id))
  })

  function sourceLabel(source: string): string {
    if (source === 'knowledge') return '知识库推导'
    if (source === 'generated') return '自动生成'
    return '手动维护'
  }

  async function loadAgents(): Promise<void> {
    try {
      agents.value = await declarativeApi.list()
    } catch {
      agents.value = []
    }
  }

  async function reload(): Promise<void> {
    loading.value = true
    if (state.value !== 'ready') state.value = 'loading'
    try {
      const data = await orgApi.tree(includeArchived.value)
      tree.value = data
      state.value = data.items.length ? 'ready' : 'empty'
      if (selectedId.value) {
        const hit = findUnit(data.items, selectedId.value)
        if (hit) {
          selected.value = hit
        } else {
          selectedId.value = ''
          selected.value = null
        }
      }
    } catch (error) {
      const info = extractErrorMessage(error)
      state.value = classifyError(error)
      ElMessage.error(info.message)
    } finally {
      loading.value = false
    }
  }

  function selectUnit(data: OrgUnit): void {
    selectedId.value = data.id
    selected.value = data
  }

  function openUnitDialog(unit?: OrgUnit): void {
    unitForm.id = unit?.id ?? ''
    unitForm.name = unit?.name ?? ''
    unitForm.kind = unit?.kind ?? 'department'
    unitForm.parent_id = unit?.parent_id ?? selected.value?.id ?? null
    unitForm.code = unit?.code ?? ''
    unitForm.description = unit?.description ?? ''
    unitForm.sort_order = unit?.sort_order ?? 0
    unitDialogVisible.value = true
  }

  async function saveUnit(): Promise<void> {
    saving.value = true
    try {
      const payload = {
        name: unitForm.name.trim(),
        kind: unitForm.kind,
        parent_id: unitForm.parent_id || null,
        description: unitForm.description.trim() || null,
        sort_order: unitForm.sort_order,
        ...(unitForm.code.trim() ? { code: unitForm.code.trim() } : {}),
      }
      const saved = unitForm.id
        ? await orgApi.updateUnit(unitForm.id, payload)
        : await orgApi.createUnit(payload)
      unitDialogVisible.value = false
      selectedId.value = saved.id
      ElMessage.success('已保存')
      await reload()
      const hit = tree.value ? findUnit(tree.value.items, saved.id) : null
      selected.value = hit
    } catch (error) {
      ElMessage.error(extractErrorMessage(error).message)
    } finally {
      saving.value = false
    }
  }

  async function setUnitStatus(unit: OrgUnit, status: OrgUnitStatus): Promise<void> {
    try {
      await orgApi.updateUnit(unit.id, { status })
      ElMessage.success(status === 'archived' ? '已归档' : '已恢复')
      await reload()
    } catch (error) {
      ElMessage.error(extractErrorMessage(error).message)
    }
  }

  async function removeUnit(unit: OrgUnit): Promise<void> {
    try {
      await ElMessageBox.confirm(
        `确定删除「${unit.name}」？有子单元或岗位时需先处理。`,
        '删除组织单元',
        { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    try {
      await orgApi.deleteUnit(unit.id)
      if (selectedId.value === unit.id) {
        selectedId.value = ''
        selected.value = null
      }
      ElMessage.success('已删除')
      await reload()
    } catch (error) {
      ElMessage.error(extractErrorMessage(error).message)
    }
  }

  function openPositionDialog(position?: OrgPosition): void {
    positionForm.id = position?.id ?? ''
    positionForm.org_unit_id = position?.org_unit_id ?? selected.value?.id ?? ''
    positionForm.title = position?.title ?? ''
    positionForm.responsibilities = position?.responsibilities ?? ''
    positionForm.headcount = position?.headcount ?? 1
    positionForm.agent_id = position?.agent_id ?? null
    positionForm.sort_order = position?.sort_order ?? 0
    positionDialogVisible.value = true
  }

  async function savePosition(): Promise<void> {
    saving.value = true
    try {
      const payload = {
        org_unit_id: positionForm.org_unit_id,
        title: positionForm.title.trim(),
        responsibilities: positionForm.responsibilities.trim() || null,
        headcount: positionForm.headcount,
        agent_id: positionForm.agent_id || null,
        sort_order: positionForm.sort_order,
      }
      if (positionForm.id) {
        await orgApi.updatePosition(positionForm.id, payload)
      } else {
        await orgApi.createPosition(payload)
      }
      positionDialogVisible.value = false
      ElMessage.success('已保存')
      await reload()
    } catch (error) {
      ElMessage.error(extractErrorMessage(error).message)
    } finally {
      saving.value = false
    }
  }

  async function removePosition(position: OrgPosition): Promise<void> {
    try {
      await ElMessageBox.confirm(`确定删除岗位「${position.title}」？`, '删除岗位', {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    try {
      await orgApi.deletePosition(position.id)
      ElMessage.success('已删除')
      await reload()
    } catch (error) {
      ElMessage.error(extractErrorMessage(error).message)
    }
  }

  onMounted(async () => {
    await Promise.all([reload(), loadAgents()])
  })
</script>

<style scoped>
  .org-page {
    padding-bottom: 24px;
  }

  .page-header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }

  .page-actions {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  .stats {
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 12px;
    margin: 16px 0;
  }

  .stat-card {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 14px 16px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
    background: var(--ds-glass);
  }

  .stat-label {
    color: var(--ds-text-3);
    font-size: 12px;
  }

  .stat-value {
    color: var(--ds-text-1);
    font-size: 24px;
    line-height: 1.1;
  }

  .org-layout {
    display: grid;
    grid-template-columns: minmax(300px, 360px) minmax(0, 1fr);
    gap: 16px;
    align-items: start;
  }

  .tree-card,
  .detail-card {
    border-radius: var(--ds-radius-card);
  }

  .card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .tree-node {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    padding: 2px 0;
  }

  .tree-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .tree-count {
    color: var(--ds-text-3);
    font-size: 12px;
  }

  .detail-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 16px;
    flex-wrap: wrap;
  }

  .detail-title {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 18px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .detail-meta {
    display: flex;
    gap: 14px;
    margin-top: 6px;
    color: var(--ds-text-3);
    font-size: 12px;
    flex-wrap: wrap;
  }

  .detail-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }

  .detail-desc {
    margin: 14px 0 18px;
    color: var(--ds-text-2);
    line-height: 1.7;
    white-space: pre-wrap;
  }

  .positions-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 10px;
    flex-wrap: wrap;
  }

  .section-title {
    margin-right: 8px;
    font-weight: 600;
    color: var(--ds-text-1);
  }

  .mono {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }

  .muted {
    color: var(--ds-text-3);
  }

  .full {
    width: 100%;
  }

  @media (max-width: 960px) {
    .stats {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    .org-layout {
      grid-template-columns: minmax(0, 1fr);
    }
  }
</style>
