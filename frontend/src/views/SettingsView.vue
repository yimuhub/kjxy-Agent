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
        label="角色与权限"
        name="roles"
      >
        <SettingsRoles :is-owner="isOwner" />
      </el-tab-pane>

      <el-tab-pane
        label="注册与安全"
        name="registration"
      >
        <SettingsRegistration :is-owner="isOwner" />
      </el-tab-pane>

      <el-tab-pane
        label="网络与搜索"
        name="network"
      >
        <SettingsNetwork />
        <SettingsSkillLab />
        <SettingsAgentGenerate />
      </el-tab-pane>

      <el-tab-pane
        label="自我总结"
        name="self_improve"
      >
        <SettingsSelfImprove />
      </el-tab-pane>

      <el-tab-pane
        label="单点登录"
        name="sso"
      >
        <SettingsSso :can-manage="isOwner" />
      </el-tab-pane>

      <el-tab-pane
        label="协作方"
        name="a2a"
      >
        <SettingsA2a :can-manage="isOwner" />
      </el-tab-pane>

      <el-tab-pane
        v-if="isOwner"
        label="敏感授权"
        name="grants"
      >
        <SettingsGrants />
      </el-tab-pane>

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
              <el-descriptions-item label="模型供应商">
                {{ llm.provider || '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="模型名称">
                {{ llm.model || '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="是否已配置">
                <el-tag :type="llm.configured ? 'success' : 'danger'">
                  {{ llm.configured ? '已配置' : '未配置' }}
                </el-tag>
                <span
                  v-if="!llm.configured"
                  class="note"
                >真实模型尚未配置，当前仅能使用演示能力。</span>
              </el-descriptions-item>
              <el-descriptions-item label="运行模式">
                <el-tag :type="llmModeTag">
                  {{ llmModeLabel }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="就绪状态">
                <el-tag :type="llmHealth.type">
                  {{ llmHealth.text }}
                </el-tag>
              </el-descriptions-item>
              <el-descriptions-item
                v-if="llm.reason"
                label="配置说明"
                :span="2"
              >
                <span class="note">{{ llm.reason }}</span>
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
        label="手机接入"
        name="pairing"
      >
        <el-card class="section">
          <template #header>
            <div class="card-head">
              <span>手机扫码接入（局域网）</span>
              <el-button
                type="primary"
                :loading="pairLoading"
                @click="genPairCode"
              >
                生成配对二维码
              </el-button>
            </div>
          </template>
          <p class="pair-tip">
            手机与本机需在同一局域网。用系统相机或浏览器扫码即可配对；
            桌面本地引擎需先在「连接服务器」旁开启局域网共享（--lan）。
          </p>
          <div
            v-if="pairQr"
            class="pair-qr-wrap"
          >
            <img
              :src="pairQr"
              alt="配对二维码"
              class="pair-qr"
              :class="{ 'is-expired': pairExpires <= 0 }"
            >
            <div class="pair-meta">
              <div>{{ pairExpires > 0 ? `${pairExpires} 秒后过期` : '已过期，请重新生成' }}</div>
              <div class="pair-url">
                {{ pairUrlText }}
              </div>
            </div>
          </div>
          <el-table
            :data="pairDevices"
            stripe
            class="pair-table"
          >
            <el-table-column
              prop="name"
              label="设备"
            />
            <el-table-column
              label="配对时间"
              width="180"
            >
              <template #default="{ row }">
                {{ fmtPairTime(row.created_at) }}
              </template>
            </el-table-column>
            <el-table-column
              label="令牌到期"
              width="180"
            >
              <template #default="{ row }">
                {{ fmtPairTime(row.expires_at) }}
              </template>
            </el-table-column>
            <el-table-column
              label="状态"
              width="100"
            >
              <template #default="{ row }">
                <el-tag :type="row.revoked_at ? 'danger' : 'success'">
                  {{ row.revoked_at ? '已吊销' : '在用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              label="操作"
              width="100"
            >
              <template #default="{ row }">
                <el-button
                  v-if="!row.revoked_at"
                  text
                  type="danger"
                  @click="revokePairDevice(row.id)"
                >
                  吊销
                </el-button>
              </template>
            </el-table-column>
          </el-table>
        </el-card>
      </el-tab-pane>

      <el-tab-pane
        label="导航菜单 DIY"
        name="menu-diy"
      >
        <el-card class="section">
          <template #header>
            左侧导航栏自由 DIY 拖拽配置
          </template>
          <MenuDiyEditor />
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
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { Lock } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import QRCode from 'qrcode'
import { llmApi, llmSupplierApi, mcpApi, pairingApi, toolApi, type PairDeviceOut } from '@/api/modules'
import type {
  LlmProviderTemplate,
  LlmSupplier,
  LlmTestResult,
  McpServerItem,
  McpToolItem,
} from '@/api/modules'
import { isMockEnabled } from '@/api/mock'
import { extractErrorMessage } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import MenuDiyEditor from '@/components/layout/MenuDiyEditor.vue'
import SettingsRoles from '@/components/settings/SettingsRoles.vue'
import SettingsRegistration from '@/components/settings/SettingsRegistration.vue'
import SettingsNetwork from '@/components/settings/SettingsNetwork.vue'
import SettingsSkillLab from '@/components/settings/SettingsSkillLab.vue'
  import SettingsAgentGenerate from '@/components/settings/SettingsAgentGenerate.vue'
import SettingsSelfImprove from '@/components/settings/SettingsSelfImprove.vue'
import SettingsSso from '@/components/settings/SettingsSso.vue'
import SettingsA2a from '@/components/settings/SettingsA2a.vue'
import SettingsGrants from '@/components/settings/SettingsGrants.vue'
import { useAuthStore } from '@/stores/auth'
import { classifyError, type ViewState } from '@/utils/status'
import type { LlmStatus, ToolDefinition } from '@/types/api'

const route = useRoute()
const auth = useAuthStore()
const activeTab = ref(typeof route.query.tab === 'string' ? route.query.tab : 'config')
/** 负责人可改系统角色与保护设置；管理员只能维护自定义角色与普通设置 */
const isOwner = computed(
  () => auth.roles.includes('yard_owner') || auth.roles.includes('platform_admin'),
)

// ---------- 既有：模型配置 / 工具 / 前端配置 ----------

const llm = reactive<LlmStatus>({})
const tools = ref<ToolDefinition[]>([])
const llmState = ref<ViewState>('loading')
const toolState = ref<ViewState>('loading')
const apiBase = import.meta.env.VITE_API_BASE ?? '/api/v1'
const mockEnabled = isMockEnabled()

const llmModeLabel = computed(() => {
  if (llm.mode === 'live') return '真实模型'
  if (llm.mode === 'mock') return '演示 / Mock'
  return '未知'
})
const llmModeTag = computed<'success' | 'info' | 'warning'>(() => {
  if (llm.mode === 'live') return 'success'
  if (llm.mode === 'mock') return 'info'
  return 'warning'
})
/** /llm/status 不做主动探测；就绪度由 configured + mode 推导，避免误报“健康”。 */
const llmHealth = computed<{ type: 'success' | 'warning' | 'danger'; text: string }>(() => {
  if (!llm.configured) return { type: 'danger', text: '未就绪' }
  if (llm.mode === 'unknown') return { type: 'warning', text: '配置异常' }
  if (llm.mode === 'mock') return { type: 'warning', text: '仅演示可用' }
  return { type: 'success', text: '已配置（未主动探测）' }
})

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

/* ---------- 默认模型下拉（v1.3 §A）：填完地址/密钥自动获取上游模型列表 ---------- */
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
    const payload: { provider_type: string; base_url: string; api_key?: string; supplier_id?: string } = {
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

/* ---------- 手机接入（Wave B）：一次性配对码 + 二维码 + 设备台账 ---------- */
const pairQr = ref('')
const pairUrlText = ref('')
const pairExpires = ref(0)
const pairLoading = ref(false)
const pairDevices = ref<PairDeviceOut[]>([])
let pairTimer: number | undefined

async function genPairCode() {
  pairLoading.value = true
  try {
    const res = await pairingApi.createCode()
    pairUrlText.value = res.pair_url
    pairQr.value = await QRCode.toDataURL(res.pair_url, { margin: 1, width: 220 })
    pairExpires.value = res.expires_in
    if (pairTimer) window.clearInterval(pairTimer)
    pairTimer = window.setInterval(() => {
      if (pairExpires.value > 0) pairExpires.value -= 1
    }, 1000)
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '生成配对码失败')
  } finally {
    pairLoading.value = false
  }
}

async function loadPairDevices() {
  try {
    pairDevices.value = await pairingApi.devices()
  } catch {
    // 网络失败不打扰：表格保持原样
  }
}

async function revokePairDevice(id: string) {
  try {
    await ElMessageBox.confirm('吊销后该设备需重新扫码才能接入。', '吊销设备', { type: 'warning' })
  } catch {
    return
  }
  try {
    await pairingApi.revoke(id)
    ElMessage.success('已吊销')
    await loadPairDevices()
  } catch (e) {
    ElMessage.error(extractErrorMessage(e).message || '吊销失败')
  }
}

function fmtPairTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString()
}

onUnmounted(() => {
  if (pairTimer) window.clearInterval(pairTimer)
})

onMounted(() => {
  loadLlm()
  loadTools()
  loadSuppliers()
  loadTemplates()
  loadMcp()
  void loadPairDevices()
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
.pair-tip {
  color: var(--ds-text-2);
  font-size: 13px;
  margin: 0 0 12px;
}
.pair-qr-wrap {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
}
.pair-qr {
  width: 220px;
  height: 220px;
  border-radius: var(--ds-radius-md, 12px);
  background: #fff;
  padding: 8px;
}
/* 过期自动置灰：不可再被扫出有效配对，按钮仍可点（重新生成） */
.pair-qr.is-expired {
  filter: grayscale(1);
  opacity: 0.45;
}
.pair-meta {
  font-size: 13px;
  color: var(--ds-text-1);
}
.pair-url {
  margin-top: 6px;
  font-family: ui-monospace, monospace;
  font-size: 12px;
  color: var(--ds-text-2);
  word-break: break-all;
  max-width: 420px;
}
.pair-table {
  margin-top: 8px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
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
