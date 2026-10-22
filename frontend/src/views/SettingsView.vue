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
        label="MCP 工具服务器"
        name="mcp"
      >
        <el-card class="section">
          <template #header>
            <div class="card-header">
              <span>MCP 工具服务器（{{ mcpServers.length }}）</span>
              <div class="page-actions">
                <el-button
                  size="small"
                  @click="loadMcp"
                >
                  刷新
                </el-button>
                <el-button
                  size="small"
                  type="primary"
                  @click="openMcpCreate"
                >
                  添加 MCP 服务器
                </el-button>
              </div>
            </div>
          </template>
          <AsyncState
            :state="mcpState"
            empty-text="暂无 MCP 服务器，点击「添加 MCP 服务器」接入外部工具"
            @retry="loadMcp"
          >
            <div class="mcp-list">
              <el-card
                v-for="s in mcpServers"
                :key="s.id"
                class="mcp-card"
                shadow="hover"
              >
                <div class="mcp-row">
                  <span class="mcp-name">{{ s.name }}</span>
                  <el-tag
                    v-if="s.enabled"
                    type="success"
                    size="small"
                  >
                    启用
                  </el-tag>
                  <el-tag
                    v-else
                    type="info"
                    size="small"
                  >
                    停用
                  </el-tag>
                  <el-tooltip
                    v-if="s.last_error"
                    :content="`最近一次失败：${s.last_error}`"
                    placement="top"
                  >
                    <el-tag
                      type="danger"
                      size="small"
                    >
                      连接异常 {{ s.consecutive_failures }} 次
                    </el-tag>
                  </el-tooltip>
                </div>
                <div class="mcp-meta">
                  <code class="masked-key">{{ s.url_masked || '—' }}</code>
                  <span class="note">已缓存 {{ s.tool_count }} 个工具</span>
                  <span class="note">上次测试：{{ mcpTime(s.last_test_at) }}</span>
                </div>
                <div class="mcp-actions">
                  <el-button
                    size="small"
                    :loading="testingMcpId === s.id"
                    @click="testMcp(s)"
                  >
                    测试连接
                  </el-button>
                  <el-button
                    size="small"
                    @click="showMcpTools(s)"
                  >
                    查看工具
                  </el-button>
                  <el-button
                    size="small"
                    :type="s.enabled ? 'info' : 'success'"
                    @click="toggleMcp(s)"
                  >
                    {{ s.enabled ? '停用' : '启用' }}
                  </el-button>
                  <el-button
                    size="small"
                    type="danger"
                    @click="removeMcp(s)"
                  >
                    删除
                  </el-button>
                </div>
              </el-card>
            </div>
          </AsyncState>
          <div class="secret-note">
            <el-icon><Lock /></el-icon>
            <span>URL 与鉴权头加密存储，接口只回掩码；连接级连续失败 5 次自动停用（熔断）；助手发布涉 MCP 工具一律进平台审核。</span>
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

    <!-- W5a 添加 MCP 服务器（鉴权头行编辑同设备连接器样式） -->
    <el-dialog
      v-model="mcpDialogVisible"
      title="添加 MCP 服务器"
      width="620px"
    >
      <el-form
        :model="mcpForm"
        label-width="110px"
      >
        <el-form-item
          label="名称"
          required
        >
          <el-input
            v-model="mcpForm.name"
            placeholder="逻辑名，如 agrimet-mcp"
            maxlength="120"
          />
        </el-form-item>
        <el-form-item
          label="端点 URL"
          required
        >
          <el-input
            v-model="mcpForm.url"
            placeholder="https://mcp.example.com/mcp"
          />
          <div class="note">
            提交后加密存储，界面只回掩码（https://host/***）；localhost/内网元数据地址将被拒绝
          </div>
        </el-form-item>
        <el-form-item label="传输方式">
          <el-select
            v-model="mcpForm.transport"
            style="width: 100%"
          >
            <el-option
              label="streamable-HTTP（MCP 2025-03-26）"
              value="streamable_http"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="鉴权头">
          <div class="full-col">
            <div
              v-for="(h, i) in mcpHeaderRows"
              :key="i"
              class="header-row"
            >
              <el-input
                v-model="h.k"
                placeholder="头名 如 Authorization"
              />
              <el-input
                v-model="h.v"
                placeholder="值（保存后仅显掩码）"
                show-password
              />
              <el-button
                link
                type="danger"
                @click="mcpHeaderRows.splice(i, 1)"
              >
                删
              </el-button>
            </div>
            <el-button
              link
              type="primary"
              @click="mcpHeaderRows.push({ k: '', v: '' })"
            >
              + 添加头
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="mcpDialogVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="mcpSaving"
          @click="saveMcp"
        >
          保存
        </el-button>
      </template>
    </el-dialog>

    <!-- MCP 已缓存工具清单（来自连接测试快照） -->
    <el-dialog
      v-model="mcpToolsVisible"
      :title="`缓存工具：${mcpToolsServer?.name || ''}（${mcpToolsList.length}）`"
      width="640px"
    >
      <el-table
        v-if="mcpToolsList.length"
        :data="mcpToolsList"
        size="small"
        max-height="380"
      >
        <el-table-column
          prop="name"
          label="工具名"
          width="220"
        />
        <el-table-column
          prop="description"
          label="描述"
          show-overflow-tooltip
        />
      </el-table>
      <el-empty
        v-else
        description="尚无缓存工具：先「测试连接」拉取工具清单"
      />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { Lock } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { llmApi, llmSupplierApi, mcpApi, toolApi } from '@/api/modules'
import type {
  LlmProviderTemplate,
  LlmSupplier,
  LlmTestResult,
  McpServerItem,
  McpToolItem,
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
  // function-calling 能力位：仅编辑时提交（创建接口不接受该字段）
  supports_tools: false,
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
    supports_tools: false,
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
    supports_tools: row.supports_tools ?? false,
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

// ---------- W5a MCP 工具服务器 ----------

const mcpServers = ref<McpServerItem[]>([])
const mcpState = ref<ViewState>('loading')
const mcpDialogVisible = ref(false)
const mcpSaving = ref(false)
const testingMcpId = ref('')
const mcpForm = reactive({ name: '', url: '', transport: 'streamable_http' })
const mcpHeaderRows = ref<{ k: string; v: string }[]>([])
const mcpToolsVisible = ref(false)
const mcpToolsServer = ref<McpServerItem | null>(null)
const mcpToolsList = ref<McpToolItem[]>([])

function mcpTime(iso: string | null): string {
  return iso ? iso.replace('T', ' ').slice(0, 19) : '从未'
}

async function loadMcp() {
  mcpState.value = 'loading'
  try {
    mcpServers.value = await mcpApi.list()
    mcpState.value = mcpServers.value.length ? 'ready' : 'empty'
  } catch (e) {
    mcpState.value = classifyError(e)
  }
}

function openMcpCreate() {
  Object.assign(mcpForm, { name: '', url: '', transport: 'streamable_http' })
  mcpHeaderRows.value = []
  mcpDialogVisible.value = true
}

async function saveMcp() {
  if (!mcpForm.name.trim() || !mcpForm.url.trim()) {
    ElMessage.warning('请填写名称与端点 URL')
    return
  }
  const headers: Record<string, string> = {}
  for (const h of mcpHeaderRows.value) if (h.k.trim()) headers[h.k.trim()] = h.v
  mcpSaving.value = true
  try {
    await mcpApi.create({
      name: mcpForm.name.trim(),
      url: mcpForm.url.trim(),
      headers: Object.keys(headers).length ? headers : null,
      transport: mcpForm.transport,
    })
    ElMessage.success('已添加。工具已入白名单体系，助手发布涉 MCP 工具将进平台审核')
    mcpDialogVisible.value = false
    await loadMcp()
  } catch (e) {
    ElMessage.error(`保存失败：${(e as Error).message}`)
  } finally {
    mcpSaving.value = false
  }
}

async function testMcp(s: McpServerItem) {
  testingMcpId.value = s.id
  try {
    const r = await mcpApi.test(s.id)
    ElMessage.success(
      `「${s.name}」连接成功，缓存 ${r.tool_count} 个工具${r.has_more ? '（上游还有更多，当前仅取一页）' : ''}`,
    )
  } catch (e) {
    ElMessage.error(`「${s.name}」连接失败：${(e as Error).message}`)
  } finally {
    testingMcpId.value = ''
    await loadMcp()
  }
}

async function toggleMcp(s: McpServerItem) {
  try {
    await mcpApi.patch(s.id, { enabled: !s.enabled })
    ElMessage.success(s.enabled ? `已停用「${s.name}」` : `已启用「${s.name}」（失败计数已清零）`)
    await loadMcp()
  } catch (e) {
    ElMessage.error(`操作失败：${(e as Error).message}`)
  }
}

async function removeMcp(s: McpServerItem) {
  try {
    await ElMessageBox.confirm(
      `确定删除 MCP 服务器「${s.name}」？其缓存工具将同步对所有助手不可见。`,
      '删除确认',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await mcpApi.remove(s.id)
    ElMessage.success('已删除')
    await loadMcp()
  } catch (e) {
    ElMessage.error(`删除失败：${(e as Error).message}`)
  }
}

async function showMcpTools(s: McpServerItem) {
  mcpToolsServer.value = s
  try {
    const r = await mcpApi.tools(s.id)
    mcpToolsList.value = r.tools
  } catch (e) {
    mcpToolsList.value = []
    ElMessage.error(`读取工具缓存失败：${(e as Error).message}`)
  }
  mcpToolsVisible.value = true
}

onMounted(() => {
  loadLlm()
  loadTools()
  loadSuppliers()
  loadTemplates()
  loadMcp()
})
</script>

<style scoped>
.settings {
  padding: 4px;
  max-width: 1080px;
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
  color: var(--ds-text-2);
  font-size: 13px;
}
.note {
  color: var(--ds-text-2);
  font-size: 12px;
  margin-left: 8px;
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
.settings-tabs {
  margin-bottom: 8px;
}
.mcp-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.mcp-card :deep(.el-card__body) {
  padding: 14px 16px;
}
.mcp-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.mcp-name {
  font-weight: 600;
  color: var(--ds-text-1);
}
.mcp-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 6px;
}
.mcp-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.full-col {
  width: 100%;
}
.header-row {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}
</style>
