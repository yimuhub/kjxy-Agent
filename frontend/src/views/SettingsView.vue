<template>
  <div class="settings">
    <h2 class="page-title">
      系统设置
    </h2>

    <el-alert
      v-if="auth.hasPermission('llm:manage')"
      type="info"
      :closable="false"
      show-icon
      class="moved-hint"
    >
      <template #title>
        模型配置与模型供应商已迁到
        <router-link
          to="/model-center"
          class="moved-link"
        >模型中心</router-link>
        ，并可按能力、按助手绑定模型与兜底顺序。
      </template>
    </el-alert>

    <el-tabs
      v-model="activeTab"
      class="settings-tabs"
    >
      <el-tab-pane
        lazy
        label="角色与权限"
        name="roles"
      >
        <SettingsRoles :is-owner="isOwner" />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="工作台视图"
        name="menu"
      >
        <SettingsMenuProfiles />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="注册与安全"
        name="registration"
      >
        <SettingsRegistration :is-owner="isOwner" />
      </el-tab-pane>

      <el-tab-pane
        v-if="canManageBranding"
        lazy
        label="品牌与门户"
        name="branding"
      >
        <SettingsBranding />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="小院档案"
        name="yard_profile"
      >
        <SettingsYardProfile />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="网络与搜索"
        name="network"
      >
        <SettingsNetwork />
        <SettingsSkillLab />
        <SettingsAgentGenerate />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="指挥官"
        name="commander"
      >
        <SettingsCommander />
      </el-tab-pane>

      <el-tab-pane
        v-if="canManageSettings"
        lazy
        label="对话内媒体"
        name="media"
      >
        <SettingsMedia />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="桌面伙伴"
        name="desktop_pet"
      >
        <SettingsDesktopPet />
      </el-tab-pane>

      <el-tab-pane
        v-if="isPlatformAdmin"
        lazy
        label="集群节点"
        name="cluster"
      >
        <SettingsCluster />
      </el-tab-pane>

      <el-tab-pane
        v-if="isOwner"
        lazy
        label="系统日志"
        name="system_logs"
      >
        <SettingsSystemLogs />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="技能与插件"
        name="skills"
      >
        <SettingsOfficialLibrary />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="语音转写"
        name="asr"
      >
        <SettingsAsr />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="自我总结"
        name="self_improve"
      >
        <SettingsSelfImprove />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="设备分析"
        name="device_analysis"
      >
        <SettingsDeviceAnalysis />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="Agent 自动执行"
        name="farm_agent"
      >
        <SettingsFarmAgent />
        <SettingsPushChannels />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="单点登录"
        name="sso"
      >
        <SettingsSso :can-manage="isOwner" />
      </el-tab-pane>

      <el-tab-pane
        lazy
        label="协作方"
        name="a2a"
      >
        <SettingsA2a :can-manage="isOwner" />
      </el-tab-pane>

      <el-tab-pane
        v-if="isOwner"
        lazy
        label="敏感授权"
        name="grants"
      >
        <SettingsGrants />
      </el-tab-pane>

      <el-tab-pane
        lazy
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
        lazy
        label="可用工具"
        name="tools"
      >
        <ToolsCatalog />
      </el-tab-pane>

      <el-tab-pane
        lazy
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
            v-col-resize
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
        lazy
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
        lazy
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

    <!-- W5a 添加 MCP 服务器（鉴权头行编辑同设备连接器样式） -->
    <el-dialog
      v-model="mcpDialogVisible"
      append-to-body
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
      append-to-body
      :title="`缓存工具：${mcpToolsServer?.name || ''}（${mcpToolsList.length}）`"
      width="640px"
    >
      <el-table
        v-col-resize
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
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Lock } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import QRCode from 'qrcode'
import { mcpApi, pairingApi, type PairDeviceOut } from '@/api/modules'
import type { McpServerItem, McpToolItem } from '@/api/modules'
import { isMockEnabled } from '@/api/mock'
import { extractErrorMessage } from '@/api/client'
import AsyncState from '@/components/AsyncState.vue'
import ToolsCatalog from '@/components/settings/ToolsCatalog.vue'
import MenuDiyEditor from '@/components/layout/MenuDiyEditor.vue'
import SettingsRoles from '@/components/settings/SettingsRoles.vue'
import SettingsMenuProfiles from '@/components/settings/SettingsMenuProfiles.vue'
import SettingsRegistration from '@/components/settings/SettingsRegistration.vue'
import SettingsNetwork from '@/components/settings/SettingsNetwork.vue'
import SettingsSkillLab from '@/components/settings/SettingsSkillLab.vue'
  import SettingsAgentGenerate from '@/components/settings/SettingsAgentGenerate.vue'
  import SettingsYardProfile from '@/components/settings/SettingsYardProfile.vue'
import SettingsCommander from '@/components/settings/SettingsCommander.vue'
import SettingsMedia from '@/components/settings/SettingsMedia.vue'
import SettingsDesktopPet from '@/components/settings/SettingsDesktopPet.vue'
import SettingsCluster from '@/components/settings/SettingsCluster.vue'
import SettingsSystemLogs from '@/components/settings/SettingsSystemLogs.vue'
import SettingsOfficialLibrary from '@/components/settings/SettingsOfficialLibrary.vue'
import SettingsAsr from '@/components/settings/SettingsAsr.vue'
import SettingsSelfImprove from '@/components/settings/SettingsSelfImprove.vue'
import SettingsDeviceAnalysis from '@/components/settings/SettingsDeviceAnalysis.vue'
import SettingsFarmAgent from '@/components/settings/SettingsFarmAgent.vue'
import SettingsPushChannels from '@/components/settings/SettingsPushChannels.vue'
import SettingsSso from '@/components/settings/SettingsSso.vue'
import SettingsA2a from '@/components/settings/SettingsA2a.vue'
import SettingsGrants from '@/components/settings/SettingsGrants.vue'
import SettingsBranding from '@/components/settings/SettingsBranding.vue'
import { useAuthStore } from '@/stores/auth'
import { classifyError, type ViewState } from '@/utils/status'


const route = useRoute()
const auth = useAuthStore()
/** 负责人可改系统角色与保护设置；管理员只能维护自定义角色与普通设置 */
const isOwner = computed(
  () => auth.roles.includes('yard_owner') || auth.roles.includes('platform_admin'),
)
/** 集群节点注册表仅平台管理员可见（跨小院只读探测） */
const isPlatformAdmin = computed(() => auth.roles.includes('platform_admin'))
/** 品牌与门户：平台级权限，超管直通或经 platform_grants 逐人授予（v3.67） */
const canManageBranding = computed(() => auth.hasPermission('branding:manage'))
/** 对话内媒体：图片/视频开关与按人额度属于管理型设置，与后端 SETTINGS_PERMISSIONS 对齐 */
const canManageSettings = computed(() => auth.hasPermission('settings:manage'))

/** 与下方 el-tab-pane 的 name 一一对应；默认落第一个面板，避免落到不存在的 tab 只剩标签 */
const TAB_NAMES = [
  'roles',
  'menu',
  'registration',
  'branding',
  'yard_profile',
  'network',
  'commander',
  'media',
  'desktop_pet',
  'cluster',
  'system_logs',
  'skills',
  'asr',
  'self_improve',
  'device_analysis',
  'farm_agent',
  'sso',
  'a2a',
  'grants',
  'mcp',
  'tools',
  'pairing',
  'menu-diy',
  'frontend',
] as const
type TabName = (typeof TAB_NAMES)[number]

const availableTabs = computed<TabName[]>(() => {
  // 桌面伙伴页在浏览器里也要能进：桌面窗口开关自动置灰，
  // 但「页面内悬浮伙伴」「任务气泡」「皮肤」在网页端同样生效。
  let allowed: TabName[] = isOwner.value
    ? [...TAB_NAMES]
    : TAB_NAMES.filter(name => name !== 'grants' && name !== 'system_logs')
  // 品牌与门户是平台级权限，没有就整块不出现（也不能通过 ?tab=branding 直达）
  if (!canManageBranding.value) allowed = allowed.filter(name => name !== 'branding')
  if (!canManageSettings.value) allowed = allowed.filter(name => name !== 'media')
  if (!isPlatformAdmin.value) allowed = allowed.filter(name => name !== 'cluster')
  return allowed
})

function resolveTab(raw: unknown): TabName {
  return typeof raw === 'string' && (availableTabs.value as string[]).includes(raw)
    ? (raw as TabName)
    : 'roles'
}

const activeTab = ref<TabName>(resolveTab(route.query.tab))
watch(
  () => route.query.tab,
  raw => {
    activeTab.value = resolveTab(raw)
  },
)

// ---------- 既有：模型配置 / 工具 / 前端配置 ----------

const apiBase = import.meta.env.VITE_API_BASE ?? '/api/v1'
const mockEnabled = isMockEnabled()

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
.moved-hint {
  margin: 0 0 12px;
}
.moved-link {
  color: var(--el-color-primary);
  font-weight: 600;
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
