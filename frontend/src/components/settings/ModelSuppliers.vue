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
          prop="model_id"
          label="模型"
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
          min-width="300"
        >
          <template #default="{ row }">
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
            >
              <el-option
                v-for="m in modelOptions"
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
              @click="probeModels(true)"
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
        <el-form-item label="模型白名单">
          <el-input
            v-model="form.models"
            placeholder="逗号分隔，留空=全量"
          />
        </el-form-item>
        <el-form-item label="优先级">
          <el-input-number
            v-model="form.priority"
            :min="0"
          />
          <span class="note">越大越优先（全局兜底时）</span>
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
  </el-card>
</template>

<script setup lang="ts">
  /** 模型供应商管理（v3.25 从系统设置迁入「模型中心」，逻辑逐字保留）。 */
  import { onMounted, reactive, ref } from 'vue'
  import { Lock } from '@element-plus/icons-vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { extractErrorMessage } from '@/api/client'
  import { llmSupplierApi } from '@/api/modules'
  import type { LlmProviderTemplate, LlmSupplier, LlmTestResult } from '@/api/modules'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  const emit = defineEmits<{ changed: [] }>()

  const suppliers = ref<LlmSupplier[]>([])
  const supplierState = ref<ViewState>('loading')
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
    priority: 0,
    scope: 'yard' as 'yard' | 'global',
    // function-calling 能力位：仅编辑时提交（创建接口不接受该字段）
    supports_tools: false,
  })

  async function probeModels(force = false) {
    if (probing.value) return
    if (!force && !form.base_url.trim()) return
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
      modelOptions.value = res.models
      if (res.source === 'remote') {
        probeMessage.value = `已获取 ${res.models.length} 个模型`
        manualModel.value = false
        if (!form.model_id && res.models.length) form.model_id = res.models[0].id
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

  /** 地址或密钥失焦时自动触发一次探测（force=false 避免空地址乱请求） */
  function onProbeTrigger() {
    void probeModels(false)
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
    emit('changed')
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
      priority: 0,
      scope: 'yard' as const,
      supports_tools: false,
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
      priority: row.priority,
      scope: 'yard' as const,
      supports_tools: row.supports_tools ?? false,
    })
    templateKey.value = ''
    templateNote.value = ''
    formTest.value = null
    modelOptions.value = []
    manualModel.value = false
    probeMessage.value = ''
    dialogVisible.value = true
    // 编辑态：进表单即自动探测一次（用已存密钥，失败则回落到手动输入）
    void probeModels(false)
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
    saving.value = true
    try {
      if (editingId.value) {
        const payload: Record<string, unknown> = {
          name: form.name.trim(),
          provider_type: form.provider_type,
          base_url: form.base_url.trim(),
          model_id: form.model_id.trim(),
          models: form.models.trim() || null,
          priority: form.priority,
          supports_tools: form.supports_tools,
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
          priority: form.priority,
          scope: form.scope,
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
</style>
