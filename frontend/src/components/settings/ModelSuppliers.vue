<template>
  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>模型供应商（{{ suppliers.length }}）</span>
        <div class="page-actions">
          <el-button
            size="small"
            @click="loadSuppliers"
          >
            刷新
          </el-button>
          <el-button
            size="small"
            type="primary"
            @click="openCreate"
          >
            新增供应商
          </el-button>
        </div>
      </div>
    </template>
    <AsyncState
      :state="supplierState"
      empty-text="暂无供应商，点击「新增供应商」添加"
      @retry="loadSuppliers"
    >
      <el-table
        :data="suppliers"
        stripe
      >
        <el-table-column
          prop="name"
          label="名称"
          width="150"
        />
        <el-table-column
          prop="provider_type"
          label="类型"
          width="130"
        >
          <template #default="{ row }">
            {{ typeLabel(row.provider_type) }}
          </template>
        </el-table-column>
        <el-table-column
          label="模型"
          min-width="210"
        >
          <template #default="{ row }">
            <div
              v-if="statOf((row as LlmSupplier).id)"
              class="model-stats"
            >
              <span class="model-count">{{ statOf((row as LlmSupplier).id)?.count }}</span>
              <ModalityTag
                v-for="m in statOf((row as LlmSupplier).id)?.modalities ?? []"
                :key="m"
                :modality="m"
              />
            </div>
            <span
              v-else
              class="note"
            >—</span>
          </template>
        </el-table-column>
        <el-table-column
          prop="model_id"
          label="默认模型"
          width="160"
          show-overflow-tooltip
        />
        <el-table-column
          prop="status"
          label="状态"
          width="90"
        >
          <template #default="{ row }">
            <el-tag :type="row.status === 1 ? 'success' : 'info'">
              {{ row.status === 1 ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          prop="response_time_ms"
          label="延迟"
          width="90"
        >
          <template #default="{ row }">
            {{ row.response_time_ms != null ? row.response_time_ms + ' ms' : '—' }}
          </template>
        </el-table-column>
        <el-table-column
          prop="api_key_masked"
          label="API Key（已打码）"
          width="150"
        >
          <template #default="{ row }">
            <code class="masked-key">{{ row.api_key_masked }}</code>
          </template>
        </el-table-column>
        <el-table-column
          prop="is_active"
          label="当前生效"
          width="90"
        >
          <template #default="{ row }">
            <el-tag
              v-if="row.is_active"
              type="primary"
            >
              生效中
            </el-tag>
            <span
              v-else
              class="note"
            >—</span>
          </template>
        </el-table-column>
        <el-table-column
          label="连续失败"
          width="100"
        >
          <template #default="{ row }">
            <el-tooltip
              v-if="row.consecutive_failures"
              :content="row.last_error || '运行时或探测连续失败'"
              placement="top"
            >
              <el-tag
                type="danger"
                size="small"
              >
                {{ row.consecutive_failures }} 次
              </el-tag>
            </el-tooltip>
            <span
              v-else
              class="note"
            >健康</span>
          </template>
        </el-table-column>
        <el-table-column
          label="操作"
          min-width="360"
        >
          <template #default="{ row }">
            <el-button
              size="small"
              type="primary"
              plain
              @click="openModels(row as LlmSupplier)"
            >
              模型
            </el-button>
            <el-button
              v-if="!row.readonly"
              size="small"
              @click="testRow(row as LlmSupplier)"
            >
              测试连通
            </el-button>
            <el-button
              v-if="!row.is_active"
              size="small"
              type="warning"
              :disabled="row.status !== 1"
              @click="setActiveRow(row as LlmSupplier)"
            >
              设为当前
            </el-button>
            <el-tag
              v-if="row.readonly"
              size="small"
              type="info"
            >
              平台共享·只读
            </el-tag>
            <el-button
              v-if="!row.readonly"
              size="small"
              :type="row.status === 1 ? 'info' : 'success'"
              @click="toggleRow(row as LlmSupplier)"
            >
              {{ row.status === 1 ? '禁用' : '启用' }}
            </el-button>
            <el-button
              v-if="!row.readonly"
              size="small"
              @click="openEdit(row as LlmSupplier)"
            >
              编辑
            </el-button>
            <el-button
              v-if="!row.readonly"
              size="small"
              type="danger"
              @click="removeRow(row as LlmSupplier)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
      <div
        v-if="rowTest"
        class="test-result"
      >
        <el-alert
          :title="rowTest.title"
          :type="rowTest.ok ? 'success' : 'error'"
          :description="rowTest.desc"
          show-icon
          :closable="false"
        />
      </div>
    </AsyncState>
    <div class="secret-note">
      <el-icon><Lock /></el-icon>
      <span>API Key 加密存储，接口与界面一律只展示打码形式，绝不返回明文。禁用/启用与「设为当前」即时生效，无需重启后端。</span>
    </div>

    <!-- 新增 / 编辑供应商 -->
    <el-dialog
      v-model="dialogVisible"
      append-to-body
      :title="editingId ? '编辑供应商' : '新增供应商'"
      width="620px"
    >
      <el-form
        :model="form"
        label-width="110px"
      >
        <el-form-item label="预置模板">
          <el-select
            v-model="templateKey"
            placeholder="选择模板快速填充"
            clearable
            style="width: 100%"
            @change="applyTemplate"
          >
            <el-option
              v-for="t in templates"
              :key="t.provider_type"
              :label="t.label"
              :value="t.provider_type"
            />
          </el-select>
          <div
            v-if="templateNote"
            class="note"
          >
            {{ templateNote }}
          </div>
        </el-form-item>
        <el-form-item
          label="名称"
          required
        >
          <el-input
            v-model="form.name"
            placeholder="逻辑名，如 deepseek-main"
            maxlength="40"
          />
        </el-form-item>
        <el-form-item
          label="类型"
          required
        >
          <el-select
            v-model="form.provider_type"
            style="width: 100%"
          >
            <el-option
              v-for="t in templates"
              :key="t.provider_type"
              :label="t.label"
              :value="t.provider_type"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          label="Base URL"
          required
        >
          <el-input
            v-model="form.base_url"
            placeholder="https://api.deepseek.com"
            @blur="onProbeTrigger"
          />
        </el-form-item>
        <el-form-item
          :label="editingId ? 'API Key（轮换）' : 'API Key'"
          :required="!editingId"
        >
          <el-input
            v-model="form.api_key"
            type="password"
            show-password
            :placeholder="editingId ? '留空则不修改' : '上游 API Key，加密存储'"
            @blur="onProbeTrigger"
          />
          <span class="note">填写后自动获取上游模型列表</span>
        </el-form-item>
        <el-form-item
          label="默认模型"
          required
        >
          <div class="model-picker">
            <el-select
              v-if="!manualModel"
              v-model="form.model_id"
              filterable
              :loading="probing"
              placeholder="填写地址与密钥后自动获取"
              class="model-select"
              @change="onDefaultModelChange"
            >
              <el-option
                v-for="m in defaultModelOptions"
                :key="m.id"
                :label="m.label"
                :value="m.id"
              />
            </el-select>
            <el-input
              v-else
              v-model="form.model_id"
              placeholder="上游物理模型名，如 deepseek-chat"
            />
            <el-button
              text
              :loading="probing"
              @click="probeModels()"
            >
              {{ probing ? '获取中' : '刷新列表' }}
            </el-button>
            <el-button
              text
              @click="manualModel = !manualModel"
            >
              {{ manualModel ? '用下拉选择' : '手动输入' }}
            </el-button>
          </div>
          <span
            v-if="probeMessage"
            class="note"
          >{{ probeMessage }}</span>
        </el-form-item>
        <el-form-item label="可用模型">
          <div class="model-checklist">
            <div class="checklist-toolbar">
              <span class="note">勾选后随供应商一起导入/启用；取消勾选会停用该模型（不删除已绑定的历史）</span>
              <span class="checklist-actions">
                <el-button
                  text
                  size="small"
                  :disabled="!selectableModels.length"
                  @click="selectAllModels"
                >
                  全选
                </el-button>
                <el-button
                  text
                  size="small"
                  :disabled="!form.available_models.length"
                  @click="clearSelectedModels"
                >
                  仅保留默认
                </el-button>
              </span>
            </div>
            <el-checkbox-group
              v-if="selectableModels.length"
              v-model="form.available_models"
              class="model-checklist-group"
              @change="onAvailableModelsChange"
            >
              <el-checkbox
                v-for="m in selectableModels"
                :key="m.id"
                :value="m.id"
              >
                {{ m.label }}
              </el-checkbox>
            </el-checkbox-group>
            <span
              v-else
              class="note"
            >填写地址与密钥后自动获取；也可手动填写默认模型，保存时会自动加入可用列表。</span>
          </div>
        </el-form-item>
        <el-form-item label="模型白名单">
          <el-input
            v-model="form.models"
            placeholder="逗号分隔，留空=全量；可用模型勾选后会自动维护"
          />
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number
            v-model="form.priority"
            :min="0"
          />
          <span class="note">越大越优先（全局兜底时）</span>
        </el-form-item>
        <el-form-item label="计费单价">
          <div class="price-row">
            <el-input-number
              v-model="form.price_in_per_1k"
              :min="0"
              :precision="4"
              :step="0.1"
              placeholder="输入"
              controls-position="right"
              style="width: 160px"
            />
            <span class="note">元 / 千输入 token</span>
            <el-input-number
              v-model="form.price_out_per_1k"
              :min="0"
              :precision="4"
              :step="0.1"
              placeholder="输出"
              controls-position="right"
              style="width: 160px"
            />
            <span class="note">元 / 千输出 token（供金额额度估算）</span>
          </div>
        </el-form-item>
        <el-form-item v-if="editingId">
          <template #label>
            <el-tooltip
              content="开启后该供应商的助手运行进入工具调用循环"
              placement="top"
            >
              <span>工具调用能力</span>
            </el-tooltip>
          </template>
          <el-switch v-model="form.supports_tools" />
          <span class="note">声明「支持 function-calling」，开启后该供应商的助手运行进入工具调用循环</span>
        </el-form-item>
        <el-form-item
          v-if="!editingId"
          label="可见范围"
        >
          <el-radio-group v-model="form.scope">
            <el-radio value="yard">
              仅本小院
            </el-radio>
            <el-radio value="global">
              全局共享（需平台管理员）
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="连通测试">
          <el-button
            :loading="testing"
            @click="testForm"
          >
            测试连通
          </el-button>
          <div
            v-if="formTest"
            class="test-result"
          >
            <el-alert
              :title="formTest.title"
              :type="formTest.ok ? 'success' : 'error'"
              :description="formTest.desc"
              show-icon
              :closable="false"
            />
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="saveForm"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <SupplierModelsDrawer
      v-model="modelsDrawerVisible"
      :supplier="modelsSupplier"
      @changed="onModelsChanged"
    />
  </el-card>
</template>

<script setup lang="ts">
  /** 模型供应商管理（v3.25 从系统设置迁入「模型中心」，逻辑逐字保留）。 */
  import { computed, onMounted, reactive, ref } from 'vue'
  import { Lock } from '@element-plus/icons-vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { extractErrorMessage } from '@/api/client'
  import { llmSupplierApi } from '@/api/modules'
  import type { LlmProviderTemplate, LlmSupplier, LlmTestResult } from '@/api/modules'
  import { supplierModelApi } from '@/api/modelChannels'
  import type { SupplierModel } from '@/api/modelChannels'
  import AsyncState from '@/components/AsyncState.vue'
  import ModalityTag from '@/components/settings/ModalityTag.vue'
  import SupplierModelsDrawer from '@/components/settings/SupplierModelsDrawer.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  const emit = defineEmits<{ changed: [] }>()

  const suppliers = ref<LlmSupplier[]>([])
  const supplierState = ref<ViewState>('loading')
  /** 供应商 id → 模型数量与主要模态（接口未就绪时为空，不影响供应商列表） */
  const modelStats = ref<Record<string, { count: number; modalities: string[] }>>({})
  const modelsDrawerVisible = ref(false)
  const modelsSupplier = ref<LlmSupplier | null>(null)
  const templates = ref<LlmProviderTemplate[]>([])
  const dialogVisible = ref(false)
  const editingId = ref<string | null>(null)
  const saving = ref(false)
  const testing = ref(false)
  const templateKey = ref('')
  const templateNote = ref('')
  const formTest = ref<{ ok: boolean; title: string; desc: string } | null>(null)
  const rowTest = ref<{ ok: boolean; title: string; desc: string } | null>(null)

  /* 默认模型下拉（v1.3 §A）：填完地址/密钥自动获取上游模型列表 */
  const modelOptions = ref<{ id: string; label: string }[]>([])
  /** 可用模型复选列表：上游探测结果 + 已落库模型（编辑态） */
  const selectableModels = computed(() => {
    const seen = new Set<string>()
    const out: { id: string; label: string }[] = []
    for (const item of modelOptions.value) {
      if (item.id && !seen.has(item.id)) {
        seen.add(item.id)
        out.push(item)
      }
    }
    for (const mid of form.available_models) {
      if (mid && !seen.has(mid)) {
        seen.add(mid)
        out.push({ id: mid, label: mid })
      }
    }
    return out
  })
  const defaultModelOptions = computed(() => {
    const seen = new Set<string>()
    const out: { id: string; label: string }[] = []
    for (const item of [...selectableModels.value, ...modelOptions.value]) {
      if (item.id && !seen.has(item.id)) {
        seen.add(item.id)
        out.push(item)
      }
    }
    if (form.model_id && !seen.has(form.model_id)) {
      out.unshift({ id: form.model_id, label: form.model_id })
    }
    return out
  })
  const probing = ref(false)
  const manualModel = ref(false)
  const probeMessage = ref('')

  const form = reactive({
    name: '',
    provider_type: 'deepseek',
    base_url: '',
    model_id: '',
    api_key: '',
    models: '',
    available_models: [] as string[],
    priority: 0,
    scope: 'yard' as 'yard' | 'global',
    // function-calling 能力位：仅编辑时提交（创建接口不接受该字段）
    supports_tools: false,
    price_in_per_1k: null as number | null,
    price_out_per_1k: null as number | null,
  })

  async function probeModels() {
    if (probing.value) return
    if (!form.base_url.trim()) {
      probeMessage.value = '请先填写 Base URL，再获取上游模型列表'
      manualModel.value = true
      return
    }
    probing.value = true
    probeMessage.value = ''
    try {
      const payload: {
        provider_type: string
        base_url: string
        api_key?: string
        supplier_id?: string
      } = {
        provider_type: form.provider_type,
        base_url: form.base_url.trim(),
      }
      // 编辑态且本次没填新 key → 用已存密钥探测
      if (editingId.value && !form.api_key.trim()) payload.supplier_id = editingId.value
      else if (form.api_key.trim()) payload.api_key = form.api_key.trim()
      const res = await llmSupplierApi.probeModels(payload)
      mergeModelOptions(res.models)
      if (res.source === 'remote') {
        probeMessage.value = `已获取 ${res.models.length} 个模型`
        manualModel.value = false
        if (!form.model_id && res.models.length) form.model_id = res.models[0].id
        if (!form.available_models.length && form.model_id) {
          form.available_models = [form.model_id]
        }
      } else if (res.source === 'unsupported') {
        probeMessage.value = res.message || '该供应商不支持模型列表，请手动填写'
        manualModel.value = true
      } else {
        probeMessage.value = res.message || '获取失败，请手动填写或检查地址与密钥'
        manualModel.value = true
      }
    } catch (e) {
      probeMessage.value = extractErrorMessage(e).message || '获取失败，请手动填写或检查地址与密钥'
      manualModel.value = true
    } finally {
      probing.value = false
    }
  }

  function mergeModelOptions(models: { id: string; label: string }[]) {
    const merged = new Map(modelOptions.value.map(item => [item.id, item]))
    for (const item of models) {
      if (item.id) merged.set(item.id, item)
    }
    modelOptions.value = [...merged.values()]
  }

  async function loadSupplierModels(supplierId: string) {
    try {
      const models = await supplierModelApi.list(supplierId)
      mergeModelOptions(
        models
          .map(m => ({
            id: m.upstream_model_id || m.model_key || '',
            label: m.display_name || m.upstream_model_id || m.model_key || '',
          }))
          .filter(m => m.id),
      )
      const enabled = models
        .filter(m => Number(m.status ?? 1) === 1)
        .map(m => m.upstream_model_id || m.model_key || '')
        .filter(Boolean)
      if (enabled.length) {
        form.available_models = [...new Set([form.model_id, ...enabled].filter(Boolean))]
      }
    } catch {
      // 模型明细读不到时保留探测结果与手动输入，不阻塞编辑
    }
  }

  function onDefaultModelChange(value: string) {
    if (value && !form.available_models.includes(value)) {
      form.available_models = [value, ...form.available_models]
    }
  }

  function onAvailableModelsChange(value: Array<string | number | boolean>) {
    const selected = value.map(item => String(item)).filter(Boolean)
    form.available_models = selected
    if (!form.model_id || !selected.includes(form.model_id)) {
      form.model_id = selected[0] ?? ''
    }
  }

  function selectAllModels() {
    const ids = selectableModels.value.map(item => item.id)
    form.available_models = ids
    if (!form.model_id || !ids.includes(form.model_id)) {
      form.model_id = ids[0] ?? ''
    }
  }

  function clearSelectedModels() {
    form.available_models = form.model_id ? [form.model_id] : []
  }

  /** 地址或密钥失焦时自动触发一次探测（force=false 避免空地址乱请求） */
  function onProbeTrigger() {
    void probeModels()
  }

  function typeLabel(t: string): string {
    const found = templates.value.find(x => x.provider_type === t)
    return found ? found.label : t
  }

  function testAlert(r: LlmTestResult, name: string) {
    const models = r.models.length
      ? `上游模型：${r.models.slice(0, 10).join('、')}${r.models.length > 10 ? '…' : ''}`
      : ''
    return {
      ok: r.ok,
      title: r.ok ? `「${name}」连通成功，延迟 ${r.latency_ms} ms` : `「${name}」连通失败`,
      desc: [r.note, models].filter(Boolean).join('；'),
    }
  }

  async function loadSuppliers() {
    supplierState.value = 'loading'
    rowTest.value = null
    try {
      suppliers.value = await llmSupplierApi.list()
      supplierState.value = suppliers.value.length ? 'ready' : 'empty'
    } catch (e) {
      supplierState.value = classifyError(e)
    }
    await loadModelStats()
    emit('changed')
  }

  /** 拉取每个供应商的模型数与主要模态；单个失败只留空，不拖垮供应商列表 */
  async function loadModelStats() {
    if (!suppliers.value.length) {
      modelStats.value = {}
      return
    }
    const entries = await Promise.all(
      suppliers.value.map(async supplier => {
        try {
          const models = await supplierModelApi.list(supplier.id)
          return [supplier.id, summarizeModels(models)] as const
        } catch {
          return [supplier.id, null] as const
        }
      }),
    )
    const next: Record<string, { count: number; modalities: string[] }> = {}
    entries.forEach(([id, stat]) => {
      if (stat) next[id] = stat
    })
    modelStats.value = next
  }

  function summarizeModels(models: SupplierModel[]): { count: number; modalities: string[] } {
    const modalities: string[] = []
    for (const model of models) {
      const key = modelModality(model)
      if (key && !modalities.includes(key)) modalities.push(key)
    }
    return { count: models.length, modalities: modalities.slice(0, 3) }
  }

  function modelModality(model: SupplierModel): string {
    if (model.modality) return model.modality
    const all = Array.from(
      new Set(
        [...(model.input_modalities ?? []), ...(model.output_modalities ?? [])].filter(Boolean),
      ),
    )
    if (all.length > 1) return 'multimodal'
    return all[0] || ''
  }

  function statOf(id?: string): { count: number; modalities: string[] } | undefined {
    if (!id) return undefined
    return modelStats.value[id]
  }

  function openModels(row: LlmSupplier): void {
    modelsSupplier.value = row
    modelsDrawerVisible.value = true
  }

  async function onModelsChanged(): Promise<void> {
    await loadSuppliers()
  }

  async function loadTemplates() {
    try {
      templates.value = await llmSupplierApi.templates()
    } catch {
      templates.value = []
    }
  }

  function resetForm() {
    Object.assign(form, {
      name: '',
      provider_type: 'deepseek',
      base_url: '',
      model_id: '',
      api_key: '',
      models: '',
      available_models: [] as string[],
      priority: 0,
      scope: 'yard' as const,
      supports_tools: false,
      price_in_per_1k: null as number | null,
      price_out_per_1k: null as number | null,
    })
    templateKey.value = ''
    templateNote.value = ''
    formTest.value = null
    modelOptions.value = []
    manualModel.value = false
    probeMessage.value = ''
  }

  function applyTemplate() {
    const t = templates.value.find(x => x.provider_type === templateKey.value)
    if (!t) {
      templateNote.value = ''
      return
    }
    form.provider_type = t.provider_type
    if (t.base_url) form.base_url = t.base_url
    if (t.model_id) form.model_id = t.model_id
    templateNote.value = t.note
  }

  function openCreate() {
    editingId.value = null
    resetForm()
    dialogVisible.value = true
  }

  function openEdit(row: LlmSupplier) {
    editingId.value = row.id
    Object.assign(form, {
      name: row.name,
      provider_type: row.provider_type,
      base_url: row.base_url,
      model_id: row.model_id,
      api_key: '',
      models: row.models ?? '',
      available_models: row.model_id ? [row.model_id] : [],
      priority: row.priority,
      scope: 'yard' as const,
      supports_tools: row.supports_tools ?? false,
      price_in_per_1k: row.price_in_per_1k ?? null,
      price_out_per_1k: row.price_out_per_1k ?? null,
    })
    templateKey.value = ''
    templateNote.value = ''
    formTest.value = null
    modelOptions.value = []
    manualModel.value = false
    probeMessage.value = ''
    dialogVisible.value = true
    // 编辑态：先读已落库模型，再用已存密钥探测一次上游目录
    void loadSupplierModels(row.id)
    void probeModels()
  }

  async function saveForm() {
    if (!form.name.trim() || !form.base_url.trim() || !form.model_id.trim()) {
      ElMessage.warning('请填写名称、Base URL 和模型名')
      return
    }
    if (!editingId.value && !form.api_key) {
      ElMessage.warning('请填写 API Key')
      return
    }
    const selectedModels = [
      ...new Set(
        [form.model_id.trim(), ...form.available_models.map(id => id.trim())].filter(Boolean),
      ),
    ]
    saving.value = true
    try {
      if (editingId.value) {
        const payload: Record<string, unknown> = {
          name: form.name.trim(),
          provider_type: form.provider_type,
          base_url: form.base_url.trim(),
          model_id: form.model_id.trim(),
          models: form.models.trim() || null,
          available_models: selectedModels,
          priority: form.priority,
          supports_tools: form.supports_tools,
          price_in_per_1k: form.price_in_per_1k,
          price_out_per_1k: form.price_out_per_1k,
        }
        if (form.api_key) payload.api_key = form.api_key
        await llmSupplierApi.update(editingId.value, payload)
        ElMessage.success('已更新')
      } else {
        await llmSupplierApi.create({
          name: form.name.trim(),
          provider_type: form.provider_type,
          base_url: form.base_url.trim(),
          model_id: form.model_id.trim(),
          api_key: form.api_key,
          models: form.models.trim() || undefined,
          available_models: selectedModels,
          priority: form.priority,
          scope: form.scope,
          price_in_per_1k: form.price_in_per_1k,
          price_out_per_1k: form.price_out_per_1k,
        })
        ElMessage.success('已新增')
      }
      dialogVisible.value = false
      await loadSuppliers()
    } catch (e) {
      ElMessage.error(`保存失败：${(e as Error).message}`)
    } finally {
      saving.value = false
    }
  }

  async function testForm() {
    if (!form.base_url.trim() || !form.model_id.trim() || !form.api_key) {
      ElMessage.warning('测试需要填写 Base URL、模型名和 API Key')
      return
    }
    testing.value = true
    try {
      const r = await llmSupplierApi.testCandidate({
        provider_type: form.provider_type,
        base_url: form.base_url.trim(),
        model_id: form.model_id.trim(),
        api_key: form.api_key,
      })
      formTest.value = testAlert(r, form.name || form.model_id)
    } catch (e) {
      formTest.value = { ok: false, title: '测试请求失败', desc: (e as Error).message }
    } finally {
      testing.value = false
    }
  }

  async function testRow(row: LlmSupplier) {
    try {
      const { result } = await llmSupplierApi.test(row.id)
      rowTest.value = testAlert(result, row.name)
      await loadSuppliers()
    } catch (e) {
      rowTest.value = {
        ok: false,
        title: `「${row.name}」测试请求失败`,
        desc: (e as Error).message,
      }
    }
  }

  async function setActiveRow(row: LlmSupplier) {
    try {
      await llmSupplierApi.setActive(row.id)
      ElMessage.success(`已将「${row.name}」设为当前生效供应商（即时生效，无需重启）`)
      await loadSuppliers()
    } catch (e) {
      ElMessage.error(`设置失败：${(e as Error).message}`)
    }
  }

  async function toggleRow(row: LlmSupplier) {
    try {
      if (row.status === 1) {
        await llmSupplierApi.disable(row.id)
        ElMessage.success(`已禁用「${row.name}」`)
      } else {
        await llmSupplierApi.enable(row.id)
        ElMessage.success(`已启用「${row.name}」（连续失败计数已清零）`)
      }
      await loadSuppliers()
    } catch (e) {
      ElMessage.error(`操作失败：${(e as Error).message}`)
    }
  }

  async function removeRow(row: LlmSupplier) {
    try {
      await ElMessageBox.confirm(`确定删除供应商「${row.name}」？该操作不可恢复。`, '删除确认', {
        type: 'warning',
      })
    } catch {
      return
    }
    try {
      await llmSupplierApi.remove(row.id)
      ElMessage.success('已删除')
      await loadSuppliers()
    } catch (e) {
      ElMessage.error(`删除失败：${(e as Error).message}`)
    }
  }

  defineExpose({ loadSuppliers, suppliers })

  onMounted(() => {
    void loadTemplates()
    void loadSuppliers()
  })
</script>

<style scoped>
  .price-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .section {
    margin-bottom: 16px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .note {
    color: var(--ds-text-2);
    font-size: 12px;
  }
  .secret-note {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 16px;
    color: var(--ds-text-2);
    font-size: 13px;
  }
  .masked-key {
    font-family: ui-monospace, monospace;
    font-size: 12px;
    color: var(--ds-text-2);
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: 6px;
    padding: 1px 6px;
  }
  .test-result {
    margin-top: 12px;
  }
  .model-stats {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px;
  }
  .model-count {
    font-weight: 600;
    color: var(--ds-text-1);
    margin-right: 2px;
  }
  .model-picker {
    display: flex;
    align-items: center;
    gap: 8px;
    width: 100%;
  }
  .model-select {
    flex: 1;
    min-width: 0;
  }
  .model-checklist {
    width: 100%;
  }
  .checklist-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    flex-wrap: wrap;
  }
  .checklist-actions {
    display: inline-flex;
    gap: 4px;
  }
  .model-checklist-group {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 6px 12px;
    margin-top: 8px;
    max-height: 220px;
    overflow-y: auto;
    padding: 8px;
    border: 1px solid var(--ds-border);
    border-radius: 8px;
  }
</style>
