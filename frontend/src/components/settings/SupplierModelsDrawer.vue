<template>
  <el-dialog
    v-model="visible"
    append-to-body
    align-center
    draggable
    width="min(1180px, 96vw)"
    class="supplier-models-dialog"
    :close-on-click-modal="false"
  >
    <template #header>
      <div class="drawer-head">
        <div class="drawer-title-wrap">
          <span class="drawer-title">供应商模型 · {{ supplier?.name || '未选择供应商' }}</span>
          <span class="note">
            {{ supplier?.base_url || '—' }} · 一个供应商可添加多个不同模态的模型
          </span>
        </div>
        <div class="drawer-head-actions">
          <el-tag
            v-if="readonly"
            type="info"
            size="small"
          >
            平台共享·只读
          </el-tag>
          <el-tag
            v-else
            type="info"
            effect="plain"
            size="small"
          >
            共 {{ stats.total }} 个 · 启用 {{ stats.enabled }}
          </el-tag>
        </div>
      </div>
    </template>

    <div class="toolbar">
      <el-input
        v-model="keyword"
        placeholder="搜索模型名 / 模型 Key / 上游 ID"
        clearable
        aria-label="搜索模型"
        class="search"
      >
        <template #prefix>
          <el-icon><Search /></el-icon>
        </template>
      </el-input>
      <el-select
        v-model="modalityFilter"
        placeholder="全部模态"
        clearable
        aria-label="按模态筛选"
        class="modality-filter"
      >
        <el-option
          v-for="item in MODALITY_OPTIONS"
          :key="item.value"
          :label="item.label"
          :value="item.value"
        />
      </el-select>
      <div class="toolbar-actions">
        <el-button
          size="small"
          :disabled="readonly || !supplier?.id"
          @click="openWizard"
        >
          导入
        </el-button>
        <el-button
          size="small"
          :disabled="readonly || !supplier?.id"
          :loading="probing"
          @click="openProbe"
        >
          从上游探测
        </el-button>
        <el-button
          size="small"
          type="primary"
          :disabled="readonly || !supplier?.id"
          @click="openCreate"
        >
          新增模型
        </el-button>
        <el-button
          size="small"
          :disabled="readonly || !supplier?.id"
          :loading="batchTesting"
          @click="batchTest"
        >
          批量测试
        </el-button>
        <el-button
          size="small"
          @click="load()"
        >
          刷新
        </el-button>
      </div>
    </div>

    <AsyncState
      :state="state"
      empty-text="该供应商还没有模型。一个供应商可添加多个不同模态的模型。"
      @retry="load()"
    >
      <template #empty-action>
        <el-button
          v-if="!readonly"
          type="primary"
          plain
          @click="openWizard"
        >
          导入模型
        </el-button>
      </template>

      <div class="table-scroll">
        <el-table
          v-col-resize
          :data="filtered"
          :row-key="rowKey"
          border
          stripe
          class="model-table"
          empty-text="没有符合筛选条件的模型"
          @selection-change="onSelectionChange"
        >
          <el-table-column
            type="selection"
            width="44"
            reserve-selection
          />
          <el-table-column
            label="模型名 / 上游 ID"
            min-width="200"
          >
            <template #default="{ row }">
              <div class="model-cell">
                <span class="model-name">{{ displayName(row as SupplierModel) }}</span>
                <span class="note model-key">
                  {{ modelKeyText(row as SupplierModel) }}
                </span>
              </div>
            </template>
          </el-table-column>
          <el-table-column
            label="模态"
            width="100"
          >
            <template #default="{ row }">
              <ModalityTag :modality="primaryModality(row as SupplierModel)" />
            </template>
          </el-table-column>
          <el-table-column
            label="协议"
            width="130"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ protocolLabel((row as SupplierModel).protocol) }}
            </template>
          </el-table-column>
          <el-table-column
            label="上下文"
            width="80"
          >
            <template #default="{ row }">
              {{ formatContext((row as SupplierModel).context_window) }}
            </template>
          </el-table-column>
          <el-table-column
            label="单价"
            min-width="150"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ formatPrice(row as SupplierModel) }}
            </template>
          </el-table-column>
          <el-table-column
            label="测试状态"
            min-width="130"
          >
            <template #default="{ row }">
              <el-tooltip
                :content="(row as SupplierModel).last_error || '尚未测试'"
                placement="top"
              >
                <span class="test-cell">
                  <el-tag
                    :type="testMeta(row as SupplierModel).type"
                    effect="plain"
                    size="small"
                  >
                    {{ testMeta(row as SupplierModel).label }}
                  </el-tag>
                  <span
                    v-if="(row as SupplierModel).latency_ms != null"
                    class="note"
                  >
                    {{ (row as SupplierModel).latency_ms }} ms
                  </span>
                </span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column
            label="启用"
            width="70"
          >
            <template #default="{ row }">
              <el-switch
                :model-value="isEnabled(row as SupplierModel)"
                :disabled="readonly || !(row as SupplierModel).id"
                :aria-label="`启用 ${displayName(row as SupplierModel)}`"
                @change="toggleStatus(row as SupplierModel, $event)"
              />
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="190"
          >
            <template #default="{ row }">
              <el-button
                size="small"
                text
                type="primary"
                :loading="testingIds.includes((row as SupplierModel).id ?? '')"
                :disabled="!(row as SupplierModel).id"
                @click="testModel(row as SupplierModel)"
              >
                测试
              </el-button>
              <el-button
                size="small"
                text
                :disabled="readonly || !(row as SupplierModel).id"
                @click="openEdit(row as SupplierModel)"
              >
                编辑
              </el-button>
              <el-button
                size="small"
                text
                type="danger"
                :disabled="readonly || !(row as SupplierModel).id"
                @click="removeModel(row as SupplierModel)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </div>
      <div
        v-if="selected.length"
        class="selection-note"
      >
        已勾选 {{ selected.length }} 个模型
      </div>
    </AsyncState>

    <div class="secret-note">
      <el-icon><Lock /></el-icon>
      <span>模型目录只保存元数据，不保存上游密钥；导入前会先 dry-run 预览，冲突默认不覆盖。</span>
    </div>

    <!-- 新增 / 编辑模型 -->
    <el-dialog
      v-model="editVisible"
      append-to-body
      align-center
      :title="editingId ? '编辑模型' : '新增模型'"
      width="min(760px, 94vw)"
      class="model-edit-dialog"
    >
      <el-form
        :model="form"
        label-width="110px"
      >
        <el-form-item
          label="模型 Key"
          required
        >
          <el-input
            v-model="form.model_key"
            placeholder="客户端使用的逻辑 Key，如 deepseek-chat"
            :disabled="!!editingId"
            maxlength="100"
            @blur="onModelIdentityBlur"
          />
          <span
            v-if="editingId"
            class="note"
          >模型 Key 创建后不可修改</span>
        </el-form-item>
        <el-form-item label="上游模型 ID">
          <el-input
            v-model="form.upstream_model_id"
            placeholder="留空则与模型 Key 相同"
            :disabled="!!editingId"
            maxlength="160"
            @blur="onModelIdentityBlur"
          />
        </el-form-item>
        <el-form-item label="展示名">
          <el-input
            v-model="form.display_name"
            placeholder="留空则使用模型 Key"
            maxlength="120"
          />
        </el-form-item>
        <el-form-item label="模态">
          <div class="modality-row">
            <el-select
              v-model="form.modality"
              style="width: 220px"
            >
              <el-option
                v-for="item in MODALITY_OPTIONS"
                :key="item.value"
                :label="item.label"
                :value="item.value"
              />
            </el-select>
            <el-button
              text
              size="small"
              :loading="detecting"
              :disabled="!supplierId"
              @click="detectModality(true)"
            >
              {{ detecting ? '识别中' : '自动识别' }}
            </el-button>
            <span
              v-if="detectMessage"
              class="note detect-note"
              :class="{ 'detect-warn': detectSource === 'unknown' }"
            >{{ detectMessage }}</span>
          </div>
        </el-form-item>
        <el-form-item label="协议">
          <el-select
            v-model="form.protocol"
            style="width: 100%"
          >
            <el-option
              v-for="item in PROTOCOL_OPTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="上下文窗口">
          <el-input-number
            v-model="form.context_window"
            :min="0"
            :step="1000"
            controls-position="right"
            style="width: 200px"
          />
          <span class="note">选填，留空表示不配置</span>
        </el-form-item>
        <el-form-item
          v-if="!editingId"
          label="最大输出"
        >
          <el-input-number
            v-model="form.max_output_tokens"
            :min="0"
            :step="1000"
            controls-position="right"
            style="width: 200px"
          />
          <span class="note">选填，留空表示不限</span>
        </el-form-item>
        <el-form-item label="计费单价">
          <span class="note price-hint">选填，留空表示不配置价格</span>
          <div class="price-row">
            <el-input-number
              v-model="form.price_input"
              :min="0"
              :precision="6"
              :step="0.001"
              controls-position="right"
              style="width: 150px"
            />
            <span class="note">输入</span>
            <el-input-number
              v-model="form.price_output"
              :min="0"
              :precision="6"
              :step="0.001"
              controls-position="right"
              style="width: 150px"
            />
            <span class="note">输出</span>
          </div>
          <div class="price-row">
            <el-select
              v-model="form.currency"
              style="width: 110px"
            >
              <el-option
                label="CNY"
                value="CNY"
              />
              <el-option
                label="USD"
                value="USD"
              />
            </el-select>
            <el-select
              v-model="form.unit"
              style="width: 190px"
            >
              <el-option
                label="元 / 千 token"
                value="per_1k_tokens"
              />
              <el-option
                label="元 / 百万 token"
                value="per_1m_tokens"
              />
            </el-select>
          </div>
        </el-form-item>
        <el-form-item label="启用">
          <el-switch v-model="form.enabled" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="saveModel"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- 上游探测 -->
    <el-dialog
      v-model="probeVisible"
      append-to-body
      title="从上游探测模型"
      width="720px"
    >
      <el-alert
        type="info"
        :closable="false"
        show-icon
        :title="probeMessage || '会使用已保存的供应商地址与密钥读取上游模型列表，不会写入任何配置。'"
        class="probe-hint"
      />
      <el-table
        v-col-resize
        v-if="probeModels.length"
        :data="probeModels"
        max-height="380"
        stripe
        @selection-change="onProbeSelection"
      >
        <el-table-column
          type="selection"
          width="44"
        />
        <el-table-column
          prop="id"
          label="上游模型 ID"
          min-width="220"
          show-overflow-tooltip
        />
        <el-table-column
          prop="label"
          label="展示名"
          min-width="160"
          show-overflow-tooltip
        />
        <el-table-column
          label="猜测模态"
          width="120"
        >
          <template #default="{ row }">
            <ModalityTag :modality="(row as ProbeModel).guessed_modality" />
          </template>
        </el-table-column>
      </el-table>
      <el-empty
        v-else
        :description="probing ? '正在读取上游模型列表…' : '上游没有返回模型'"
        :image-size="72"
      />
      <template #footer>
        <el-button @click="probeVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="probeImporting"
          :disabled="readonly || !probeSelected.length"
          @click="importProbe"
        >
          导入选中（{{ probeSelected.length }}）
        </el-button>
      </template>
    </el-dialog>

    <ModelImportWizard
      v-if="supplier?.id"
      v-model="wizardVisible"
      :supplier-id="supplier.id"
      :supplier-name="supplier.name"
      :readonly="readonly"
      @done="onWizardDone"
    />
    <template #footer>
      <el-button @click="visible = false">
        关闭
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  /** 供应商多模型管理抽屉：列表 / 导入向导 / 上游探测 / 单模型 CRUD 与测试。 */
  import { computed, reactive, ref, watch } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { Lock, Search } from '@element-plus/icons-vue'
  import { extractErrorMessage } from '@/api/client'
  import {
    MODALITY_OPTIONS,
    PROTOCOL_OPTIONS,
    modalityKey,
    protocolLabel,
    supplierModelApi,
  } from '@/api/modelChannels'
  import type {
    BatchModelTestResult,
    ModelPricing,
    ProbeModel,
    SupplierModel,
    SupplierModelInput,
    SupplierModelUpdate,
    TagType,
  } from '@/api/modelChannels'
  import type { LlmSupplier } from '@/api/modules'
  import AsyncState from '@/components/AsyncState.vue'
  import ModalityTag from '@/components/settings/ModalityTag.vue'
  import ModelImportWizard from '@/components/settings/ModelImportWizard.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  const props = defineProps<{
    modelValue: boolean
    supplier: LlmSupplier | null
  }>()

  const emit = defineEmits<{
    'update:modelValue': [value: boolean]
    changed: []
  }>()

  const visible = computed({
    get: () => props.modelValue,
    set: value => emit('update:modelValue', value),
  })

  const supplierId = computed(() => props.supplier?.id ?? '')
  const readonly = computed(() => props.supplier?.readonly === true)

  const models = ref<SupplierModel[]>([])
  const state = ref<ViewState>('loading')
  const keyword = ref('')
  const modalityFilter = ref('')
  const selected = ref<SupplierModel[]>([])
  const testingIds = ref<string[]>([])
  const batchTesting = ref(false)
  const probing = ref(false)
  const probeImporting = ref(false)

  const wizardVisible = ref(false)
  const editVisible = ref(false)
  const editingId = ref<string | null>(null)
  const saving = ref(false)
  const detecting = ref(false)
  const detectSource = ref<'local' | 'catalog' | 'unknown' | ''>('')
  const detectMessage = ref('')

  const probeVisible = ref(false)
  const probeModels = ref<ProbeModel[]>([])
  const probeMessage = ref('')
  const probeSelected = ref<ProbeModel[]>([])

  const form = reactive({
    model_key: '',
    upstream_model_id: '',
    display_name: '',
    modality: 'text',
    protocol: 'openai_chat',
    context_window: null as number | null,
    max_output_tokens: null as number | null,
    price_input: null as number | null,
    price_output: null as number | null,
    currency: 'CNY',
    unit: 'per_1k_tokens',
    enabled: true,
  })

  const filtered = computed(() => {
    const query = keyword.value.trim().toLowerCase()
    const wanted = modalityKey(modalityFilter.value)
    return models.value.filter(model => {
      if (wanted && modalityKey(primaryModality(model)) !== wanted) return false
      if (!query) return true
      return [model.model_key, model.upstream_model_id, model.display_name].some(value =>
        String(value ?? '')
          .toLowerCase()
          .includes(query),
      )
    })
  })

  const stats = computed(() => ({
    total: models.value.length,
    enabled: models.value.filter(isEnabled).length,
  }))

  watch(
    () => props.modelValue,
    open => {
      if (open) void load()
    },
  )

  watch(
    () => props.supplier?.id,
    () => {
      if (props.modelValue) void load()
    },
  )

  async function load(showSkeleton = true): Promise<void> {
    if (!supplierId.value) {
      models.value = []
      state.value = 'empty'
      return
    }
    if (showSkeleton) state.value = 'loading'
    try {
      models.value = await supplierModelApi.list(supplierId.value)
      state.value = models.value.length ? 'ready' : 'empty'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  function displayName(model: SupplierModel): string {
    return model.display_name || model.model_key || model.upstream_model_id || '未命名模型'
  }

  function modelKeyText(model: SupplierModel): string {
    const key = model.model_key || '—'
    if (model.upstream_model_id && model.upstream_model_id !== model.model_key) {
      return `${key} · 上游 ${model.upstream_model_id}`
    }
    return key
  }

  function primaryModality(model: SupplierModel): string {
    if (model.modality) return model.modality
    const modalities = Array.from(
      new Set([
        ...(model.input_modalities ?? []),
        ...(model.output_modalities ?? []),
      ].filter(Boolean)),
    )
    if (modalities.length > 1) return 'multimodal'
    return modalities[0] || ''
  }

  function formatContext(value?: number | null): string {
    if (!value || value <= 0) return '—'
    if (value >= 1000) return `${Math.round(value / 1000)}K`
    return String(value)
  }

  function currencySymbol(currency?: string | null): string {
    if (currency === 'USD') return '$'
    if (!currency || currency === 'CNY') return '¥'
    return `${currency} `
  }

  function formatPrice(model: SupplierModel): string {
    const pricing = model.pricing
    if (!pricing || (pricing.input == null && pricing.output == null)) return '—'
    const symbol = currencySymbol(pricing.currency)
    const unit = pricing.unit?.includes('1m') ? '百万' : '千'
    const parts: string[] = []
    if (pricing.input != null) parts.push(`入 ${symbol}${pricing.input}`)
    if (pricing.output != null) parts.push(`出 ${symbol}${pricing.output}`)
    return `${parts.join(' / ')} · ${unit} token`
  }

  function testMeta(model: SupplierModel): { label: string; type: TagType } {
    if (model.test_ok === true) return { label: '通过', type: 'success' }
    const failed =
      model.test_ok === false ||
      (model.test_ok === undefined || model.test_ok === null ? !!model.last_error : false)
    if (failed) return { label: '失败', type: 'danger' }
    return { label: '未测', type: 'info' }
  }

  function isEnabled(model: SupplierModel): boolean {
    const status = model.status
    if (status === undefined || status === null || status === '') return true
    if (typeof status === 'number') return status === 1
    return ['1', 'active', 'enabled', 'ok'].includes(String(status).toLowerCase())
  }

  function rowKey(row: SupplierModel, index = 0): string {
    return row.id || row.model_key || `row-${index}`
  }

  function onSelectionChange(rows: SupplierModel[]): void {
    selected.value = rows
  }

  async function testModel(model: SupplierModel): Promise<void> {
    if (!supplierId.value || !model.id) return
    const modelId = model.id
    testingIds.value = [...testingIds.value, modelId]
    try {
      const res = await supplierModelApi.test(supplierId.value, modelId)
      model.test_ok = res.ok === true
      model.latency_ms = res.latency_ms ?? model.latency_ms
      model.last_test_at = new Date().toISOString()
      model.last_error = res.ok ? null : res.message || res.detail || '测试失败'
      if (res.ok) {
        ElMessage.success(
          `${displayName(model)} 测试通过${res.latency_ms != null ? `，延迟 ${res.latency_ms} ms` : ''}`,
        )
      } else {
        ElMessage.error(
          `${displayName(model)} 测试失败：${res.message || res.detail || '请检查供应商配置'}`,
        )
      }
      emit('changed')
    } catch (e) {
      model.test_ok = false
      model.last_error = extractErrorMessage(e).message
    } finally {
      testingIds.value = testingIds.value.filter(id => id !== modelId)
    }
  }

  function applyTestResults(results: BatchModelTestResult[]): void {
    const byId = new Map<string, BatchModelTestResult>()
    results.forEach(result => {
      if (result.model_id) byId.set(result.model_id, result)
    })
    models.value.forEach(model => {
      const result = model.id ? byId.get(model.id) : undefined
      if (!result) return
      model.test_ok = result.ok === true
      model.latency_ms = result.latency_ms ?? model.latency_ms
      model.last_test_at = new Date().toISOString()
      model.last_error = result.ok ? null : result.message || result.detail || '测试失败'
    })
  }

  async function batchTest(): Promise<void> {
    if (!supplierId.value) return
    let ids = selected.value
      .map(model => model.id)
      .filter((id): id is string => !!id)
    if (!ids.length) {
      if (!filtered.value.length) {
        ElMessage.warning('当前没有可测试的模型')
        return
      }
      try {
        await ElMessageBox.confirm(
          `未勾选模型，将测试当前筛选出的 ${filtered.value.length} 个模型，是否继续？`,
          '批量测试',
          { type: 'info' },
        )
      } catch {
        return
      }
      ids = filtered.value.map(model => model.id).filter((id): id is string => !!id)
    }
    if (!ids.length) {
      ElMessage.warning('选中的模型缺少 ID，无法测试')
      return
    }
    batchTesting.value = true
    try {
      const results = await supplierModelApi.batchTest(supplierId.value, ids)
      applyTestResults(results)
      const passed = results.filter(result => result.ok === true).length
      ElMessage.success(`批量测试完成：${passed} / ${results.length} 个模型通过`)
      emit('changed')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      batchTesting.value = false
    }
  }

  async function toggleStatus(
    model: SupplierModel,
    value: string | number | boolean,
  ): Promise<void> {
    if (!supplierId.value || !model.id) return
    const enable = value === true
    try {
      await supplierModelApi.update(supplierId.value, model.id, { status: enable ? 1 : 2 })
      model.status = enable ? 1 : 2
      ElMessage.success(`已${enable ? '启用' : '停用'}「${displayName(model)}」`)
      emit('changed')
    } catch {
      await load(false)
    }
  }

  function resetForm(): void {
    Object.assign(form, {
      model_key: '',
      upstream_model_id: '',
      display_name: '',
      modality: 'text',
      protocol: 'openai_chat',
      context_window: null as number | null,
      max_output_tokens: null as number | null,
      price_input: null,
      price_output: null,
      currency: 'CNY',
      unit: 'per_1k_tokens',
      enabled: true,
    })
  }

  function openCreate(): void {
    editingId.value = null
    resetForm()
    detectSource.value = ''
    detectMessage.value = ''
    editVisible.value = true
  }

  function openEdit(model: SupplierModel): void {
    editingId.value = model.id ?? null
    Object.assign(form, {
      model_key: model.model_key ?? '',
      upstream_model_id: model.upstream_model_id ?? '',
      display_name: model.display_name ?? '',
      modality: model.modality || 'text',
      protocol: model.protocol || 'openai_chat',
      context_window: model.context_window ?? null,
      max_output_tokens: model.max_output_tokens ?? null,
      price_input: model.pricing?.input ?? null,
      price_output: model.pricing?.output ?? null,
      currency: model.pricing?.currency || 'CNY',
      unit: model.pricing?.unit || 'per_1k_tokens',
      enabled: isEnabled(model),
    })
    detectSource.value = ''
    detectMessage.value = ''
    editVisible.value = true
    // 已存的 text 可能只是旧版本默认值：打开时再识别一次，非 text 的显式选择不覆盖
    void detectModality(false)
  }

  const DETECT_LABELS: Record<string, string> = {
    text: '文本',
    image: '图像',
    video: '视频',
    audio: '音频',
    multimodal: '多模态',
    embedding: '向量',
    rerank: '重排',
    asr: '语音识别',
    tts: '语音合成',
  }

  function protocolForModality(modality: string): string {
    return (
      {
        image: 'openai_images',
        video: 'openai_video',
        embedding: 'openai_embedding',
        asr: 'openai_audio',
        tts: 'openai_audio',
      }[modality] || 'openai_chat'
    )
  }

  async function detectModality(force = true): Promise<void> {
    if (!supplierId.value || detecting.value) return
    const modelKey = form.model_key.trim()
    const upstream = form.upstream_model_id.trim()
    if (!modelKey && !upstream) return
    detecting.value = true
    detectSource.value = ''
    detectMessage.value = ''
    try {
      const res = await supplierModelApi.detectModality(supplierId.value, {
        model_key: modelKey,
        upstream_model_id: upstream,
      })
      detectSource.value = res.source
      const label = DETECT_LABELS[res.modality] || res.modality
      if (res.source === 'local' || res.source === 'catalog') {
        const shouldApply = force || !form.modality || form.modality === 'text'
        if (shouldApply) {
          form.modality = res.modality
          if (!form.protocol || form.protocol === 'openai_chat') {
            form.protocol = protocolForModality(res.modality)
          }
        }
        detectMessage.value =
          res.source === 'local' ? `已按模型名识别为「${label}」` : `已从上游目录识别为「${label}」`
      } else {
        detectMessage.value = res.message || '未能自动识别，请手动选择模态'
      }
    } catch (e) {
      detectSource.value = 'unknown'
      detectMessage.value = `自动识别失败：${extractErrorMessage(e).message}`
    } finally {
      detecting.value = false
    }
  }

  function onModelIdentityBlur(): void {
    if (!editingId.value) void detectModality(false)
  }

  function buildPricing(): ModelPricing | null {
    const hasInput = form.price_input != null
    const hasOutput = form.price_output != null
    if (!hasInput && !hasOutput) return null
    const pricing: ModelPricing = { unit: form.unit, currency: form.currency }
    if (hasInput) pricing.input = form.price_input
    if (hasOutput) pricing.output = form.price_output
    return pricing
  }

  async function saveModel(): Promise<void> {
    if (!supplierId.value) return
    const key = form.model_key.trim()
    if (!key) {
      ElMessage.warning('请填写模型 Key')
      return
    }
    const pricing = buildPricing()
    saving.value = true
    try {
      if (editingId.value) {
        const payload: SupplierModelUpdate = {
          display_name: form.display_name.trim() || key,
          modality: form.modality,
          protocol: form.protocol,
          context_window: form.context_window,
          pricing,
          status: form.enabled ? 1 : 2,
        }
        await supplierModelApi.update(supplierId.value, editingId.value, payload)
        ElMessage.success('模型已更新')
      } else {
        const payload: SupplierModelInput = {
          model_key: key,
          upstream_model_id: form.upstream_model_id.trim() || key,
          display_name: form.display_name.trim() || key,
          modality: form.modality,
          protocol: form.protocol,
          capabilities: {},
          context_window: form.context_window,
          max_output_tokens: form.max_output_tokens,
          pricing,
        }
        await supplierModelApi.create(supplierId.value, payload)
        ElMessage.success('模型已新增')
      }
      editVisible.value = false
      await load(false)
      emit('changed')
    } catch (e) {
      ElMessage.error(`保存失败：${extractErrorMessage(e).message}`)
    } finally {
      saving.value = false
    }
  }

  async function removeModel(model: SupplierModel): Promise<void> {
    const modelId = model.id
    const currentSupplier = supplierId.value
    if (!currentSupplier || !modelId) return
    try {
      await ElMessageBox.confirm(
        `确定删除模型「${displayName(model)}」？删除后绑定到该模型的配置会失效。`,
        '删除确认',
        { type: 'warning' },
      )
    } catch {
      return
    }
    try {
      await supplierModelApi.remove(currentSupplier, modelId)
      ElMessage.success('已删除')
      await load(false)
      emit('changed')
    } catch (e) {
      ElMessage.error(`删除失败：${extractErrorMessage(e).message}`)
    }
  }

  function openWizard(): void {
    wizardVisible.value = true
  }

  async function onWizardDone(): Promise<void> {
    await load(false)
    emit('changed')
  }

  function openProbe(): void {
    probeVisible.value = true
    probeModels.value = []
    probeSelected.value = []
    probeMessage.value = ''
    void runProbe()
  }

  async function runProbe(): Promise<void> {
    if (!supplierId.value || probing.value) return
    probing.value = true
    try {
      const res = await supplierModelApi.probe({ supplier_id: supplierId.value })
      probeModels.value = Array.isArray(res.models) ? res.models : []
      probeMessage.value =
        res.message ||
        (probeModels.value.length
          ? `已获取 ${probeModels.value.length} 个上游模型，勾选后导入。`
          : '上游没有返回模型，可改用导入向导粘贴或上传。')
    } catch (e) {
      probeModels.value = []
      probeMessage.value = extractErrorMessage(e).message || '探测失败，请检查供应商配置'
    } finally {
      probing.value = false
    }
  }

  function onProbeSelection(rows: ProbeModel[]): void {
    probeSelected.value = rows
  }

  async function importProbe(): Promise<void> {
    if (!supplierId.value || !probeSelected.value.length) return
    const payloadModels: SupplierModelInput[] = probeSelected.value.map(model => ({
      model_key: model.id,
      upstream_model_id: model.id,
      display_name: model.label || model.id,
      modality: model.guessed_modality || 'text',
      protocol: protocolForModality(model.guessed_modality || 'text'),
      capabilities: {},
      context_window: null,
      max_output_tokens: null,
    }))
    probeImporting.value = true
    try {
      const res = await supplierModelApi.importModels(
        supplierId.value,
        { schema_version: 1, models: payloadModels },
        false,
      )
      const created = res.created ?? 0
      const updated = res.updated ?? 0
      const skipped = res.skipped ?? 0
      ElMessage.success(`导入完成：新增 ${created}，更新 ${updated}，跳过 ${skipped}`)
      probeVisible.value = false
      await load(false)
      emit('changed')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      probeImporting.value = false
    }
  }
</script>

<style scoped>
  .drawer-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .drawer-title-wrap {
    display: flex;
    flex-direction: column;
    gap: 4px;
    min-width: 0;
  }
  .drawer-title {
    font-size: 16px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .drawer-head-actions {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
  }
  .search {
    width: 260px;
  }
  .modality-filter {
    width: 140px;
  }
  .toolbar-actions {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-left: auto;
  }
  .table-scroll {
    overflow-x: auto;
  }
  .model-table {
    min-width: 1094px;
  }
  .model-table :deep(.el-table__cell) {
    white-space: nowrap;
  }
  .model-table :deep(.el-table__header th) {
    white-space: nowrap;
  }
  .modality-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .detect-note {
    color: var(--ds-text-2);
  }
  .detect-warn {
    color: var(--el-color-warning);
  }
  .price-hint {
    display: block;
    width: 100%;
    margin-bottom: 4px;
  }
  .supplier-models-dialog {
    max-width: 96vw;
  }
  .supplier-models-dialog :deep(.el-dialog__body) {
    padding-top: 8px;
  }
  .model-edit-dialog :deep(.el-form-item__label) {
    white-space: nowrap;
  }
  .model-cell {
    display: flex;
    flex-direction: column;
    gap: 2px;
    min-width: 0;
  }
  .model-name {
    font-weight: 500;
    color: var(--ds-text-1);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .model-key {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .test-cell {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  .note {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .price-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }
  .selection-note {
    margin-top: 8px;
    font-size: 12px;
    color: var(--ds-text-2);
  }
  .secret-note {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 16px;
    color: var(--ds-text-2);
    font-size: 13px;
  }
  .probe-hint {
    margin-bottom: 12px;
  }
  @media (max-width: 640px) {
    .search,
    .modality-filter {
      width: 100%;
    }
    .toolbar-actions {
      margin-left: 0;
    }
  }
</style>
