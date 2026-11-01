<template>
  <el-dialog
    v-model="visible"
    append-to-body
    :title="`导入模型 · ${supplierName || '供应商'}`"
    width="880px"
    class="import-wizard"
    :close-on-click-modal="false"
  >
    <el-steps
      :active="step"
      finish-status="success"
      align-center
      class="wizard-steps"
    >
      <el-step title="选择来源" />
      <el-step title="预览差异" />
      <el-step title="完成导入" />
    </el-steps>

    <!-- 第一步：选择导入方式 -->
    <div v-if="step === 0">
      <el-radio-group
        v-model="method"
        class="method-group"
      >
        <el-radio-button value="paste">
          粘贴 JSON / YAML / CSV
        </el-radio-button>
        <el-radio-button value="file">
          上传文件
        </el-radio-button>
        <el-radio-button value="url">
          URL 导入
        </el-radio-button>
        <el-radio-button value="probe">
          从上游探测
        </el-radio-button>
      </el-radio-group>

      <template v-if="method === 'paste'">
        <div class="field-head">
          <span class="field-label">模型清单</span>
          <el-button
            size="small"
            text
            type="primary"
            @click="fillSample"
          >
            填充示例
          </el-button>
        </div>
        <el-input
          v-model="rawText"
          type="textarea"
          :rows="12"
          resize="vertical"
          class="code-input"
          aria-label="粘贴模型清单"
          placeholder="支持 JSON 数组、{ models: [...] }、YAML（- model_key: ...）或 CSV（首行表头）"
        />
        <div class="note">
          每条至少需要 model_key；未填的 modality / protocol / context_window / pricing 会按默认值补齐。
        </div>
      </template>

      <template v-else-if="method === 'file'">
        <el-upload
          :auto-upload="false"
          :show-file-list="false"
          accept=".json,.yaml,.yml,.csv,.txt"
          :on-change="onFileChange"
        >
          <el-button type="primary">
            选择文件
          </el-button>
        </el-upload>
        <span
          v-if="fileName"
          class="note file-name"
        >{{ fileName }}</span>
        <div class="note">
          支持 .json / .yaml / .yml / .csv / .txt，单文件不超过 2MB。文件只在本机浏览器内读取解析，不会上传到第三方。
        </div>
      </template>

      <template v-else-if="method === 'url'">
        <el-input
          v-model="sourceUrl"
          placeholder="https://example.com/models.json"
          clearable
          aria-label="模型目录 URL"
        >
          <template #prepend>
            URL
          </template>
        </el-input>
        <div class="note">
          仅支持 https 地址；服务端会拉取该目录并在下一步返回差异预览，不会自动写入。
        </div>
      </template>

      <template v-else>
        <div class="probe-head">
          <el-button
            type="primary"
            :loading="probeLoading"
            @click="runProbe"
          >
            {{ probeLoading ? '探测中…' : '开始探测上游模型' }}
          </el-button>
          <span
            v-if="probeMessage"
            class="note"
          >{{ probeMessage }}</span>
        </div>
        <el-table
          v-col-resize
          v-if="probeModels.length"
          :data="probeModels"
          max-height="320"
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
            min-width="240"
            show-overflow-tooltip
          />
          <el-table-column
            prop="label"
            label="展示名"
            min-width="180"
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
          :description="probeLoading ? '正在读取上游模型列表…' : '尚未探测到模型'"
          :image-size="72"
        />
      </template>

      <el-alert
        v-if="parseError"
        type="error"
        :title="parseError"
        :closable="false"
        show-icon
        class="mt"
      />
    </div>

    <!-- 第二步：dry-run 预览 -->
    <div v-else-if="step === 1">
      <div class="summary-row">
        <el-tag type="success">
          新增 {{ previewCounts.create }}
        </el-tag>
        <el-tag type="warning">
          更新 {{ previewCounts.update }}
        </el-tag>
        <el-tag type="info">
          跳过 {{ previewCounts.skip }}
        </el-tag>
        <el-tag type="danger">
          冲突 {{ previewCounts.conflict }}
        </el-tag>
        <el-tag type="danger">
          错误 {{ previewCounts.error }}
        </el-tag>
      </div>
      <el-table
        v-col-resize
        :data="previewItems"
        max-height="360"
        stripe
        empty-text="没有需要处理的模型"
      >
        <el-table-column
          label="模型 Key"
          min-width="220"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            {{ (row as ImportPreviewItem).model_key || '—' }}
          </template>
        </el-table-column>
        <el-table-column
          label="动作"
          width="100"
        >
          <template #default="{ row }">
            <el-tag
              :type="actionMeta((row as ImportPreviewItem).action).type"
              effect="plain"
            >
              {{ actionMeta((row as ImportPreviewItem).action).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="冲突处理"
          width="180"
        >
          <template #default="{ row, $index }">
            <el-radio-group
              v-if="(row as ImportPreviewItem).action === 'conflict' && method !== 'url'"
              v-model="conflictChoice[conflictKey(row as ImportPreviewItem, $index)]"
              size="small"
            >
              <el-radio-button value="skip">
                跳过
              </el-radio-button>
              <el-radio-button value="overwrite">
                覆盖
              </el-radio-button>
            </el-radio-group>
            <span
              v-else
              class="note"
            >—</span>
          </template>
        </el-table-column>
        <el-table-column
          label="原因"
          min-width="220"
          show-overflow-tooltip
        >
          <template #default="{ row }">
            {{ (row as ImportPreviewItem).error || '—' }}
          </template>
        </el-table-column>
      </el-table>
      <el-alert
        type="info"
        :closable="false"
        show-icon
        class="mt"
        :title="method === 'url'
          ? 'URL 导入不支持逐行选择，冲突由服务端默认策略处理（默认不覆盖）。'
          : '默认跳过冲突项；选择覆盖时会附带 overwrite=true，若后端尚未支持该字段则仍按跳过处理。'"
      />
    </div>

    <!-- 第三步：导入结果 -->
    <div v-else>
      <el-result
        :icon="resultHasError ? 'warning' : 'success'"
        :title="resultHasError ? '导入完成，但有部分失败' : '导入完成'"
        :sub-title="commitMessage"
      />
      <el-descriptions
        :column="3"
        border
      >
        <el-descriptions-item label="新增">
          {{ resultCounts.created }}
        </el-descriptions-item>
        <el-descriptions-item label="更新">
          {{ resultCounts.updated }}
        </el-descriptions-item>
        <el-descriptions-item label="跳过">
          {{ resultCounts.skipped }}
        </el-descriptions-item>
      </el-descriptions>
      <div
        v-if="resultErrors.length"
        class="errors"
      >
        <div class="field-label">
          失败明细（{{ resultErrors.length }}）
        </div>
        <ul class="error-list">
          <li
            v-for="(msg, i) in resultErrors"
            :key="i"
          >
            {{ msg }}
          </li>
        </ul>
      </div>
      <div
        v-else
        class="note"
      >
        本次没有失败项。
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">
        {{ step === 2 ? '关闭' : '取消' }}
      </el-button>
      <el-button
        v-if="step === 1"
        @click="step = 0"
      >
        上一步
      </el-button>
      <el-button
        v-if="step === 0"
        type="primary"
        :loading="previewing"
        :disabled="readonly"
        @click="goPreview"
      >
        下一步：预览差异
      </el-button>
      <el-button
        v-if="step === 1"
        type="primary"
        :loading="committing"
        :disabled="readonly"
        @click="commit"
      >
        确认导入
      </el-button>
      <el-button
        v-if="step === 2"
        type="primary"
        @click="visible = false"
      >
        完成
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  /** 三步导入向导：选择来源 → dry-run 预览 → 确认导入。 */
  import { computed, reactive, ref, watch } from 'vue'
  import { ElMessage } from 'element-plus'
  import { extractErrorMessage } from '@/api/client'
  import { supplierModelApi } from '@/api/modelChannels'
  import type {
    ImportCommitResult,
    ImportPreviewItem,
    ImportPreviewSummary,
    ModelImportResult,
    ProbeModel,
    SupplierModelInput,
  } from '@/api/modelChannels'
  import ModalityTag from '@/components/settings/ModalityTag.vue'

  const props = withDefaults(
    defineProps<{
      modelValue: boolean
      supplierId: string
      supplierName?: string
      readonly?: boolean
    }>(),
    { supplierName: '', readonly: false },
  )

  const emit = defineEmits<{
    'update:modelValue': [value: boolean]
    done: []
  }>()

  const visible = computed({
    get: () => props.modelValue,
    set: value => emit('update:modelValue', value),
  })

  type ImportMethod = 'paste' | 'file' | 'url' | 'probe'

  const step = ref(0)
  const method = ref<ImportMethod>('paste')
  const rawText = ref('')
  const fileName = ref('')
  const sourceUrl = ref('')
  const parseError = ref('')

  const previewing = ref(false)
  const committing = ref(false)
  const previewItems = ref<ImportPreviewItem[]>([])
  const previewSummary = ref<ImportPreviewSummary | null>(null)
  const parsedModels = ref<SupplierModelInput[]>([])
  const conflictChoice = reactive<Record<string, 'skip' | 'overwrite'>>({})

  const probeLoading = ref(false)
  const probeModels = ref<ProbeModel[]>([])
  const probeMessage = ref('')
  const selectedProbe = ref<ProbeModel[]>([])

  const commitResult = ref<ImportCommitResult | null>(null)

  const SAMPLE = JSON.stringify(
    {
      schema_version: 1,
      models: [
        {
          model_key: 'deepseek-chat',
          upstream_model_id: 'deepseek-chat',
          display_name: 'DeepSeek Chat',
          modality: 'text',
          protocol: 'openai_chat',
          capabilities: { tools: true },
          context_window: 128000,
          max_output_tokens: 8192,
          pricing: { unit: 'per_1k_tokens', input: 0.001, output: 0.002, currency: 'CNY' },
        },
        {
          model_key: 'qwen-vl-max',
          upstream_model_id: 'qwen-vl-max',
          display_name: '通义千问 VL',
          modality: 'image',
          protocol: 'openai_chat',
          capabilities: { vision: true },
          context_window: 32000,
          pricing: { unit: 'per_1k_tokens', input: 0.02, output: 0.02, currency: 'CNY' },
        },
        {
          model_key: 'bge-m3',
          upstream_model_id: 'bge-m3',
          display_name: 'BGE M3',
          modality: 'embedding',
          protocol: 'openai_embedding',
          capabilities: {},
          context_window: 8192,
          pricing: { unit: 'per_1k_tokens', input: 0.0005, output: 0, currency: 'CNY' },
        },
      ],
    },
    null,
    2,
  )

  watch(
    () => props.modelValue,
    open => {
      if (!open) return
      step.value = 0
      method.value = 'paste'
      rawText.value = ''
      fileName.value = ''
      sourceUrl.value = ''
      parseError.value = ''
      previewItems.value = []
      previewSummary.value = null
      parsedModels.value = []
      commitResult.value = null
      probeModels.value = []
      probeMessage.value = ''
      selectedProbe.value = []
      Object.keys(conflictChoice).forEach(key => delete conflictChoice[key])
    },
  )

  const previewCounts = computed(() => ({
    create: previewSummary.value?.create ?? 0,
    update: previewSummary.value?.update ?? 0,
    skip: previewSummary.value?.skip ?? 0,
    conflict: previewSummary.value?.conflict ?? 0,
    error: previewSummary.value?.error ?? 0,
  }))

  const resultCounts = computed(() => ({
    created: commitResult.value?.created ?? 0,
    updated: commitResult.value?.updated ?? 0,
    skipped: commitResult.value?.skipped ?? 0,
  }))

  const resultErrors = computed(() => {
    const errors = commitResult.value?.errors
    return (Array.isArray(errors) ? errors : []).map(errorText).filter(Boolean)
  })
  const resultHasError = computed(() => resultErrors.value.length > 0)
  const commitMessage = computed(() => {
    const supplier = commitResult.value?.supplier_id
    return supplier ? `模型已写入供应商 ${supplier}` : '模型目录已更新，可在列表中继续测试或编辑。'
  })

  function errorText(item: string | { model_key?: string; error?: string }): string {
    if (typeof item === 'string') return item
    const parts = [item.model_key, item.error].filter(Boolean)
    return parts.join('：') || '未知错误'
  }

  function actionMeta(action?: string): {
    label: string
    type: 'success' | 'warning' | 'info' | 'danger'
  } {
    switch (action) {
      case 'create':
        return { label: '新增', type: 'success' }
      case 'update':
        return { label: '更新', type: 'warning' }
      case 'skip':
        return { label: '跳过', type: 'info' }
      case 'conflict':
        return { label: '冲突', type: 'danger' }
      case 'error':
        return { label: '错误', type: 'danger' }
      default:
        return { label: action || '未知', type: 'info' }
    }
  }

  function fillSample(): void {
    method.value = 'paste'
    rawText.value = SAMPLE
    parseError.value = ''
  }

  function text(value: unknown): string {
    if (typeof value === 'string') return value.trim()
    if (typeof value === 'number' && Number.isFinite(value)) return String(value)
    return ''
  }

  function numberValue(value: unknown): number | null {
    if (typeof value === 'number' && Number.isFinite(value)) return value
    if (typeof value === 'string' && value.trim()) {
      const n = Number(value.trim())
      return Number.isFinite(n) ? n : null
    }
    return null
  }

  function objectValue(value: unknown): Record<string, unknown> {
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      return value as Record<string, unknown>
    }
    return {}
  }

  function normalizePricing(obj: Record<string, unknown>) {
    const nested = objectValue(obj.pricing)
    return {
      unit: text(nested.unit) || text(obj.pricing_unit) || 'per_1k_tokens',
      input:
        numberValue(nested.input) ??
        numberValue(obj.price_input) ??
        numberValue(obj.pricing_input) ??
        numberValue(obj.input_price) ??
        0,
      output:
        numberValue(nested.output) ??
        numberValue(obj.price_output) ??
        numberValue(obj.pricing_output) ??
        numberValue(obj.output_price) ??
        0,
      currency: text(nested.currency) || text(obj.currency) || 'CNY',
    }
  }

  function normalizeModel(raw: unknown, index: number): SupplierModelInput {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      throw new Error(`第 ${index + 1} 条不是有效的模型对象`)
    }
    const obj = raw as Record<string, unknown>
    const modelKey =
      text(obj.model_key) || text(obj.upstream_model_id) || text(obj.id) || text(obj.name)
    if (!modelKey) {
      throw new Error(`第 ${index + 1} 条缺少 model_key（或 upstream_model_id）`)
    }
    return {
      model_key: modelKey,
      upstream_model_id: text(obj.upstream_model_id) || modelKey,
      display_name: text(obj.display_name) || text(obj.label) || modelKey,
      modality: text(obj.modality) || 'text',
      protocol: text(obj.protocol) || 'openai_chat',
      capabilities: objectValue(obj.capabilities),
      context_window: numberValue(obj.context_window) ?? 128000,
      max_output_tokens: numberValue(obj.max_output_tokens),
      pricing: normalizePricing(obj),
    }
  }

  function stripQuotes(value: string): string {
    if (
      value.length >= 2 &&
      ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'")))
    ) {
      return value.slice(1, -1)
    }
    return value
  }

  function coerceYamlValue(value: string): unknown {
    if (value === 'true') return true
    if (value === 'false') return false
    if (value === 'null' || value === '~') return null
    const unquoted = stripQuotes(value)
    if (/^-?\d+(\.\d+)?$/.test(unquoted)) return Number(unquoted)
    if (unquoted.startsWith('{') || unquoted.startsWith('[')) {
      try {
        return JSON.parse(unquoted)
      } catch {
        return unquoted
      }
    }
    return unquoted
  }

  function writeYamlPath(
    target: Record<string, unknown>,
    path: string,
    value: unknown,
  ): void {
    const parts = path.split('.').filter(Boolean)
    if (!parts.length) return
    let node = target
    for (let i = 0; i < parts.length - 1; i += 1) {
      const key = parts[i]
      const existing = node[key]
      if (!existing || typeof existing !== 'object' || Array.isArray(existing)) {
        node[key] = {}
      }
      node = node[key] as Record<string, unknown>
    }
    node[parts[parts.length - 1]] = value
  }

  /** 极简 YAML 子集：仅支持「- key: value」列表与 pricing 这类一层嵌套 */
  function assignYaml(
    target: Record<string, unknown>,
    line: string,
    nestedPrefix: string,
  ): string | null {
    const idx = line.indexOf(':')
    if (idx <= 0) return null
    const key = line.slice(0, idx).trim()
    const rawValue = line.slice(idx + 1).trim()
    if (!key) return null
    const fullKey = nestedPrefix ? `${nestedPrefix}.${key}` : key
    if (!rawValue) return fullKey
    writeYamlPath(target, fullKey, coerceYamlValue(rawValue))
    return null
  }

  function parseYaml(raw: string): unknown[] {
    const items: Record<string, unknown>[] = []
    let current: Record<string, unknown> | null = null
    let nestedKey = ''
    for (const rawLine of raw.split(/\r?\n/)) {
      const line = rawLine.replace(/\t/g, '  ')
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) continue
      const itemMatch = trimmed.match(/^-\s*(.*)$/)
      if (itemMatch) {
        if (current) items.push(current)
        current = {}
        nestedKey = ''
        const rest = itemMatch[1].trim()
        if (rest) {
          const nested = assignYaml(current, rest, '')
          if (nested !== null) nestedKey = nested
        }
        continue
      }
      if (!current) {
        if (/^[\w.-]+:\s*$/.test(trimmed)) continue
        throw new Error('YAML 需为模型列表（每项以「- 」开头）')
      }
      const nested = assignYaml(current, trimmed, nestedKey)
      if (nested !== null) nestedKey = nested
    }
    if (current) items.push(current)
    if (!items.length) throw new Error('YAML 中没有解析到模型条目')
    return items
  }

  function splitCsvLine(line: string): string[] {
    const out: string[] = []
    let current = ''
    let quoted = false
    for (let i = 0; i < line.length; i += 1) {
      const ch = line[i]
      if (quoted) {
        if (ch === '"') {
          if (line[i + 1] === '"') {
            current += '"'
            i += 1
          } else {
            quoted = false
          }
        } else {
          current += ch
        }
      } else if (ch === '"') {
        quoted = true
      } else if (ch === ',') {
        out.push(current.trim())
        current = ''
      } else {
        current += ch
      }
    }
    out.push(current.trim())
    return out
  }

  function parseCsv(raw: string): Record<string, string>[] {
    const lines = raw
      .split(/\r?\n/)
      .filter(line => line.trim() && !line.trim().startsWith('#'))
    if (lines.length < 2) throw new Error('CSV 至少需要表头与一行数据')
    const headers = splitCsvLine(lines[0])
    if (!headers.some(h => /model_key|upstream_model_id/i.test(h))) {
      throw new Error('CSV 表头需包含 model_key 或 upstream_model_id')
    }
    return lines.slice(1).map(line => {
      const cells = splitCsvLine(line)
      const row: Record<string, string> = {}
      headers.forEach((header, i) => {
        if (header) row[header] = cells[i] ?? ''
      })
      return row
    })
  }

  function extractRows(data: unknown, depth = 0): unknown[] {
    if (Array.isArray(data)) return data
    if (data && typeof data === 'object') {
      const obj = data as Record<string, unknown>
      if (Array.isArray(obj.models)) return obj.models
      if (obj.data !== undefined && depth < 2) return extractRows(obj.data, depth + 1)
      if ('model_key' in obj || 'upstream_model_id' in obj) return [obj]
    }
    throw new Error('JSON 中未找到 models 数组')
  }

  function parseModels(raw: string): SupplierModelInput[] {
    const trimmed = raw.trim()
    if (!trimmed) throw new Error('请先粘贴或上传模型清单')
    let rows: unknown[]
    if (trimmed.startsWith('[') || trimmed.startsWith('{')) {
      try {
        rows = extractRows(JSON.parse(trimmed))
      } catch (e) {
        if (e instanceof SyntaxError) throw new Error('JSON 格式有误，请检查括号、引号与逗号')
        throw e
      }
    } else {
      const firstLine =
        trimmed
          .split(/\r?\n/)
          .find(line => line.trim() && !line.trim().startsWith('#'))
          ?.trim() ?? ''
      if (firstLine.includes(',') && /model_key|upstream_model_id|display_name/i.test(firstLine)) {
        rows = parseCsv(trimmed)
      } else {
        rows = parseYaml(trimmed)
      }
    }
    const models = rows.map((row, index) => normalizeModel(row, index))
    if (!models.length) throw new Error('没有解析到可用模型')
    return models
  }

  function buildFromProbe(): SupplierModelInput[] {
    return selectedProbe.value.map(model => ({
      model_key: model.id,
      upstream_model_id: model.id,
      display_name: model.label || model.id,
      modality: model.guessed_modality || 'text',
      protocol: 'openai_chat',
      capabilities: {},
      context_window: 128000,
      max_output_tokens: null,
      pricing: { unit: 'per_1k_tokens', input: 0, output: 0, currency: 'CNY' },
    }))
  }

  async function onFileChange(uploadFile: { raw?: File }): Promise<void> {
    const file = uploadFile.raw
    if (!file) return
    if (file.size > 2 * 1024 * 1024) {
      ElMessage.warning('文件超过 2MB，请拆分后再导入')
      return
    }
    fileName.value = file.name
    parseError.value = ''
    try {
      rawText.value = await file.text()
      ElMessage.success(`已读取 ${file.name}`)
    } catch {
      parseError.value = '文件读取失败，请确认是纯文本文件'
    }
  }

  async function runProbe(): Promise<void> {
    if (!props.supplierId || probeLoading.value) return
    probeLoading.value = true
    probeMessage.value = ''
    selectedProbe.value = []
    try {
      const res = await supplierModelApi.probe({ supplier_id: props.supplierId })
      probeModels.value = Array.isArray(res.models) ? res.models : []
      const source = res.source ? `来源：${res.source}` : ''
      probeMessage.value =
        res.message ||
        (probeModels.value.length
          ? `已获取 ${probeModels.value.length} 个模型`
          : ['上游没有返回模型，可改用粘贴或文件导入', source].filter(Boolean).join('；'))
    } catch (e) {
      probeModels.value = []
      probeMessage.value = extractErrorMessage(e).message || '探测失败，请检查供应商地址与密钥'
    } finally {
      probeLoading.value = false
    }
  }

  function onProbeSelection(rows: ProbeModel[]): void {
    selectedProbe.value = rows
  }

  function conflictKey(item: ImportPreviewItem, index: number): string {
    return item.model_key || `row-${index}`
  }

  function summarize(items: ImportPreviewItem[]): ImportPreviewSummary {
    const summary: ImportPreviewSummary = { create: 0, update: 0, skip: 0, conflict: 0, error: 0 }
    for (const item of items) {
      const action = String(item.action ?? '')
      if (action && summary[action] !== undefined) {
        summary[action] = (summary[action] ?? 0) + 1
      }
    }
    return summary
  }

  function applyPreview(res: ModelImportResult): void {
    previewItems.value = Array.isArray(res.items) ? res.items : []
    const raw = res.summary
    previewSummary.value = raw
      ? {
          create: raw.create ?? raw.created ?? 0,
          update: raw.update ?? raw.updated ?? 0,
          skip: raw.skip ?? raw.skipped ?? 0,
          conflict: raw.conflict ?? raw.conflicts ?? 0,
          error: raw.error ?? raw.errors ?? 0,
        }
      : summarize(previewItems.value)
    Object.keys(conflictChoice).forEach(key => delete conflictChoice[key])
    previewItems.value.forEach((item, index) => {
      if (item.action === 'conflict') {
        conflictChoice[conflictKey(item, index)] = 'skip'
      }
    })
  }

  async function goPreview(): Promise<void> {
    parseError.value = ''
    if (method.value === 'url') {
      const url = sourceUrl.value.trim()
      if (!/^https:\/\//i.test(url)) {
        parseError.value = '仅支持 https:// 开头的地址'
        return
      }
      previewing.value = true
      try {
        const res = await supplierModelApi.importFromUrl(props.supplierId, url, true)
        parsedModels.value = []
        applyPreview(res)
        step.value = 1
      } catch {
        /* 错误 toast 已由 client 统一处理 */
      } finally {
        previewing.value = false
      }
      return
    }

    let models: SupplierModelInput[]
    try {
      models = method.value === 'probe' ? buildFromProbe() : parseModels(rawText.value)
      if (method.value === 'probe' && !models.length) {
        throw new Error('请先勾选要导入的上游模型')
      }
    } catch (e) {
      parseError.value = (e as Error).message
      return
    }
    parsedModels.value = models
    previewing.value = true
    try {
      const res = await supplierModelApi.importModels(
        props.supplierId,
        { schema_version: 1, models },
        true,
      )
      applyPreview(res)
      step.value = 1
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      previewing.value = false
    }
  }

  async function commit(): Promise<void> {
    if (committing.value) return
    committing.value = true
    try {
      let res: ModelImportResult
      if (method.value === 'url') {
        res = await supplierModelApi.importFromUrl(
          props.supplierId,
          sourceUrl.value.trim(),
          false,
        )
      } else {
        const models = parsedModels.value.filter(
          model => conflictChoice[model.model_key] !== 'skip',
        )
        if (!models.length) {
          ElMessage.warning('所有模型都被标记为跳过，没有需要导入的内容')
          return
        }
        const overwrite = Object.values(conflictChoice).includes('overwrite')
        const payload: { schema_version: number; models: SupplierModelInput[]; overwrite?: boolean } =
          { schema_version: 1, models }
        if (overwrite) payload.overwrite = true
        res = await supplierModelApi.importModels(props.supplierId, payload, false)
      }
      commitResult.value = res
      step.value = 2
      emit('done')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      committing.value = false
    }
  }
</script>

<style scoped>
  .wizard-steps {
    margin-bottom: 20px;
  }
  .method-group {
    margin-bottom: 14px;
    flex-wrap: wrap;
  }
  .field-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 6px;
  }
  .field-label {
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .code-input :deep(textarea) {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 12px;
    line-height: 1.6;
  }
  .note {
    margin-top: 6px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .file-name {
    margin-left: 10px;
  }
  .probe-head {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }
  .summary-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }
  .mt {
    margin-top: 12px;
  }
  .errors {
    margin-top: 14px;
  }
  .error-list {
    margin: 6px 0 0;
    padding-left: 18px;
    font-size: 12px;
    color: var(--ds-danger);
    max-height: 180px;
    overflow: auto;
  }
</style>
