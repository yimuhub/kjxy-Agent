<template>
  <div class="settings">
    <h2 class="page-title">
      系统设置
    </h2>

    <el-tabs
      v-model="activeTab"
      class="settings-tabs"
    >
      <el-tab-pane
        label="模型配置"
        name="config"
      >
        <el-card class="section">
          <template #header>
            模型配置状态
          </template>
          <AsyncState
            :state="llmState"
            empty-text="暂无模型配置信息"
            @retry="loadLlm"
          >
            <el-descriptions
              :column="2"
              border
            >
              <el-descriptions-item label="LLM_PROVIDER">
                {{ llm.provider || '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="MODEL">
                {{ llm.model || '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="是否已配置">
                <el-tag :type="llm.configured ? 'success' : 'danger'">
                  {{ llm.configured ? '已配置' : '未配置' }}
                </el-tag>
                <span
                  v-if="!llm.configured"
                  class="note"
                >（LLM_NOT_CONFIGURED：真实模型未配置）</span>
              </el-descriptions-item>
              <el-descriptions-item label="健康状态">
                <el-tag :type="llm.healthy ? 'success' : 'warning'">
                  {{ llm.healthy ? '健康' : '异常' }}
                </el-tag>
              </el-descriptions-item>
            </el-descriptions>
            <div class="secret-note">
              <el-icon><Lock /></el-icon>
              <span>出于安全考虑，此处绝不展示密钥明文（后端 /llm/status 契约不返回密钥）。</span>
            </div>
          </AsyncState>
        </el-card>
      </el-tab-pane>

      <el-tab-pane
        label="模型供应商"
        name="suppliers"
      >
        <el-card class="section">
          <template #header>
            <div class="card-header">
              <span>模型供应商（{{ suppliers.length }}）</span>
              <div>
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
                    type="warning"
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
                label="操作"
                min-width="300"
              >
                <template #default="{ row }">
                  <el-button
                    v-if="!row.readonly"
                    size="small"
                    @click="testRow(row)"
                  >
                    测试连通
                  </el-button>
                  <el-button
                    v-if="!row.is_active"
                    size="small"
                    type="warning"
                    :disabled="row.status !== 1"
                    @click="setActiveRow(row)"
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
                    @click="toggleRow(row)"
                  >
                    {{ row.status === 1 ? '禁用' : '启用' }}
                  </el-button>
                  <el-button
                    v-if="!row.readonly"
                    size="small"
                    @click="openEdit(row)"
                  >
                    编辑
                  </el-button>
                  <el-button
                    v-if="!row.readonly"
                    size="small"
                    type="danger"
                    @click="removeRow(row)"
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
            <span>API Key 加密存储，接口与界面一律只展示打码形式，绝不返回明文。</span>
          </div>
        </el-card>
      </el-tab-pane>

      <el-tab-pane
        label="可用工具"
        name="tools"
      >
        <el-card class="section">
          <template #header>
            可用工具（{{ tools.length }}）
          </template>
          <AsyncState
            :state="toolState"
            empty-text="暂无可用工具"
            @retry="loadTools"
          >
            <el-table
              :data="tools"
              stripe
            >
              <el-table-column
                prop="name"
                label="名称"
              />
              <el-table-column
                prop="code"
                label="编码"
              />
              <el-table-column
                prop="version"
                label="版本"
                width="100"
              />
              <el-table-column
                prop="risk_level"
                label="风险等级"
                width="160"
              >
                <template #default="{ row }">
                  <el-tag>{{ row.risk_level }}</el-tag>
                </template>
              </el-table-column>
              <el-table-column
                prop="description"
                label="描述"
                show-overflow-tooltip
              />
            </el-table>
          </AsyncState>
        </el-card>
      </el-tab-pane>

      <el-tab-pane
        label="前端配置"
        name="frontend"
      >
        <el-card class="section">
          <template #header>
            前端配置
          </template>
          <el-descriptions
            :column="2"
            border
          >
            <el-descriptions-item label="VITE_API_BASE">
              {{ apiBase }}
            </el-descriptions-item>
            <el-descriptions-item label="Mock 模式">
              <el-tag :type="mockEnabled ? 'warning' : 'info'">
                {{ mockEnabled ? '开启（演示 fixture）' : '关闭（真实后端）' }}
              </el-tag>
            </el-descriptions-item>
          </el-descriptions>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 新增 / 编辑供应商 -->
    <el-dialog
      v-model="dialogVisible"
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
          />
        </el-form-item>
        <el-form-item
          label="模型名"
          required
        >
          <el-input
            v-model="form.model_id"
            placeholder="上游物理模型名，如 deepseek-chat"
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
          />
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
        <el-form-item
          v-if="!editingId"
          label="可见范围"
        >
          <el-radio-group v-model="form.scope">
            <el-radio value="tenant">
              仅本使用空间
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
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Lock } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { llmApi, llmSupplierApi, toolApi } from '@/api/modules'
import type {
  LlmProviderTemplate,
  LlmSupplier,
  LlmTestResult,
} from '@/api/modules'
import { isMockEnabled } from '@/api/mock'
import AsyncState from '@/components/AsyncState.vue'
import { classifyError, type ViewState } from '@/utils/status'
import type { LlmStatus, ToolDefinition } from '@/types/api'

const activeTab = ref('config')

// ---------- 既有：模型配置 / 工具 / 前端配置 ----------

const llm = reactive<LlmStatus>({})
const tools = ref<ToolDefinition[]>([])
const llmState = ref<ViewState>('loading')
const toolState = ref<ViewState>('loading')
const apiBase = import.meta.env.VITE_API_BASE ?? '/api/v1'
const mockEnabled = isMockEnabled()

async function loadLlm() {
  llmState.value = 'loading'
  try {
    Object.assign(llm, await llmApi.status())
    llmState.value = 'ready'
  } catch (e) {
    llmState.value = classifyError(e)
  }
}

async function loadTools() {
  toolState.value = 'loading'
  try {
    tools.value = await toolApi.list()
    toolState.value = tools.value.length ? 'ready' : 'empty'
  } catch (e) {
    toolState.value = classifyError(e)
  }
}

// ---------- 模型供应商 ----------

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

const form = reactive({
  name: '',
  provider_type: 'deepseek',
  base_url: '',
  model_id: '',
  api_key: '',
  models: '',
  priority: 0,
  scope: 'tenant' as 'tenant' | 'global',
})

function typeLabel(t: string): string {
  const found = templates.value.find(x => x.provider_type === t)
  return found ? found.label : t
}

function testAlert(r: LlmTestResult, name: string) {
  const models = r.models.length ? `上游模型：${r.models.slice(0, 10).join('、')}${r.models.length > 10 ? '…' : ''}` : ''
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
    scope: 'tenant' as const,
  })
  templateKey.value = ''
  templateNote.value = ''
  formTest.value = null
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
    scope: 'tenant' as const,
  })
  templateKey.value = ''
  templateNote.value = ''
  formTest.value = null
  dialogVisible.value = true
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
    rowTest.value = { ok: false, title: `「${row.name}」测试请求失败`, desc: (e as Error).message }
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
      ElMessage.success(`已启用「${row.name}」`)
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

onMounted(() => {
  loadLlm()
  loadTools()
  loadSuppliers()
  loadTemplates()
})
</script>

<style scoped>
.settings {
  padding: 4px;
  max-width: 1080px;
}
.page-title {
  margin: 0 0 16px;
}
.section {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.secret-note {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 16px;
  color: #909399;
  font-size: 13px;
}
.note {
  color: #909399;
  font-size: 12px;
  margin-left: 8px;
}
.masked-key {
  font-family: ui-monospace, monospace;
  font-size: 12px;
}
.test-result {
  margin-top: 12px;
}
.settings-tabs {
  margin-bottom: 8px;
}
</style>
