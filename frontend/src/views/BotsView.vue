<script setup lang="ts">
// IM 机器人管理页（Wave C 最小可用版）：
// 机器人卡列表（状态灯/启停/测试/编辑/删除）+ 添加配置对话框 + 会话审批抽屉
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  botsApi,
  supervisorApi,
  type BotConversationOut,
  type BotMessageOut,
  type BotOut,
  type SupervisorCandidate,
} from '@/api/modules'

const bots = ref<BotOut[]>([])
const loading = ref(false)

const PLATFORMS = [
  { value: 'wecom_aibot', label: '企业微信（智能机器人）' },
  { value: 'dingtalk', label: '钉钉（Stream 模式）' },
  { value: 'feishu', label: '飞书（需安装 lark-oapi）' },
]
const CRED_FIELDS: Record<string, { key: string; label: string }[]> = {
  wecom_aibot: [
    { key: 'bot_id', label: 'Bot ID' },
    { key: 'secret', label: 'Secret' },
  ],
  dingtalk: [
    { key: 'client_id', label: 'Client ID（AppKey）' },
    { key: 'client_secret', label: 'Client Secret' },
  ],
  feishu: [
    { key: 'app_id', label: 'App ID' },
    { key: 'app_secret', label: 'App Secret' },
  ],
}

const dialogVisible = ref(false)
const editingId = ref<string | null>(null)
const form = reactive({
  platform: 'wecom_aibot',
  name: '',
  binding_mode: 'auto',
  bound_agent_id: '',
  require_approval: true,
  creds: {} as Record<string, string>,
})

/** 可绑定的已发布助手 / 启用班组（与总控路由候选一致） */
const candidates = ref<SupervisorCandidate[]>([])
const candidatesLoading = ref(false)
const bindingOptions = computed(() => {
  const kind = form.binding_mode === 'team' ? 'team' : 'agent'
  return candidates.value.filter(c => c.kind === kind)
})

// 切换绑定类型时清掉不属于新类型的旧选择
watch(
  () => form.binding_mode,
  () => {
    if (!form.bound_agent_id) return
    // 候选尚未加载时保留已有绑定，避免打开编辑弹窗即清空
    if (!candidates.value.length) return
    if (!bindingOptions.value.some(c => c.id === form.bound_agent_id)) form.bound_agent_id = ''
  },
)

function bindingLabel(row: BotOut): string {
  if (row.binding_mode === 'auto') return '智能路由'
  const c = candidates.value.find(
    x => x.id === row.bound_agent_id || x.code === row.bound_agent_id || x.name === row.bound_agent_id,
  )
  if (c) return `${c.kind === 'team' ? '班组' : '助手'}：${c.name}`
  return row.bound_agent_id ? `未找到：${row.bound_agent_id}` : '未指定'
}

async function loadCandidates() {
  candidatesLoading.value = true
  try {
    candidates.value = (await supervisorApi.candidates()).candidates
  } catch {
    candidates.value = []
  } finally {
    candidatesLoading.value = false
  }
}

const drawerVisible = ref(false)
const convs = ref<BotConversationOut[]>([])
const activeConv = ref<BotConversationOut | null>(null)
const msgs = ref<BotMessageOut[]>([])

const STATUS_TEXT: Record<string, string> = {
  connecting: '连接中',
  online: '在线',
  offline: '离线',
  error: '异常',
  configured: '已配置（未连接）',
}

/** 连接状态三态展示：停用优先于后端状态，未知状态不伪装成离线 */
function statusText(row: BotOut): string {
  if (!row.enabled) return '已停用'
  return STATUS_TEXT[row.status] ?? '未知'
}

function statusDot(row: BotOut): string {
  return row.enabled ? row.status : 'disabled'
}

function platformLabel(p: string): string {
  return PLATFORMS.find((x) => x.value === p)?.label ?? p
}

async function refresh() {
  loading.value = true
  try {
    bots.value = await botsApi.list()
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, {
    platform: 'wecom_aibot',
    name: '',
    binding_mode: 'auto',
    bound_agent_id: '',
    require_approval: true,
    creds: {},
  })
  void loadCandidates()
  dialogVisible.value = true
}

function openEdit(row: BotOut) {
  editingId.value = row.id
  Object.assign(form, {
    platform: row.platform,
    name: row.name,
    binding_mode: row.binding_mode,
    bound_agent_id: row.bound_agent_id ?? '',
    require_approval: row.require_approval,
    creds: {}, // 留空 = 不改动已存凭据
  })
  void loadCandidates()
  dialogVisible.value = true
}

async function submit() {
  if (form.binding_mode !== 'auto' && !form.bound_agent_id) {
    ElMessage.warning(`请先选择要绑定的${form.binding_mode === 'team' ? '班组' : '助手'}`)
    return
  }
  try {
    if (editingId.value) {
      const payload: Record<string, unknown> = {
        name: form.name,
        binding_mode: form.binding_mode,
        bound_agent_id: form.binding_mode === 'auto' ? null : form.bound_agent_id,
        require_approval: form.require_approval,
      }
      if (Object.values(form.creds).some((v) => v.trim())) payload.credentials = { ...form.creds }
      await botsApi.update(editingId.value, payload)
    } else {
      await botsApi.create({
        platform: form.platform,
        name: form.name,
        credentials: { ...form.creds },
        binding_mode: form.binding_mode,
        bound_agent_id: form.binding_mode === 'auto' ? null : form.bound_agent_id,
        require_approval: form.require_approval,
      })
    }
    dialogVisible.value = false
    ElMessage.success('已保存')
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message || '保存失败')
  }
}

async function toggle(row: BotOut) {
  try {
    await botsApi.update(row.id, { enabled: !row.enabled })
    await refresh()
  } catch (e) {
    ElMessage.error((e as Error).message || '操作失败')
  }
}

async function testOne(row: BotOut) {
  const r = await botsApi.test(row.id)
  if (r.ok) ElMessage.success('凭据检查通过；长连接状态以状态列为准')
  else ElMessage.warning(r.problems.join('；'))
  await refresh()
}

async function removeOne(row: BotOut) {
  await ElMessageBox.confirm(`删除机器人「${row.name}」？会话与账本将保留。`, '确认', { type: 'warning' })
  await botsApi.remove(row.id)
  ElMessage.success('已删除')
  await refresh()
}

async function openDrawer() {
  convs.value = await botsApi.conversations()
  activeConv.value = null
  msgs.value = []
  drawerVisible.value = true
}

async function viewMsgs(c: BotConversationOut) {
  activeConv.value = c
  msgs.value = await botsApi.messages(c.id)
}

async function review(c: BotConversationOut, approve: boolean) {
  if (approve) await botsApi.approve(c.id)
  else await botsApi.revoke(c.id)
  ElMessage.success(approve ? '已批准' : '已吊销')
  await openDrawer()
}

function approvedText(c: BotConversationOut): { text: string; type: 'success' | 'warning' | 'danger' } {
  if (c.approved_at) return { text: '已批准', type: 'success' }
  if (c.revoked_at) return { text: '已吊销', type: 'danger' }
  return { text: '待批准', type: 'warning' }
}

onMounted(() => {
  void refresh()
  void loadCandidates()
})
</script>

<template>
  <div class="bots-page">
    <div class="head">
      <h2>IM 机器人</h2>
      <div>
        <el-button @click="openDrawer">会话审批</el-button>
        <el-button type="primary" @click="openCreate">添加机器人</el-button>
      </div>
    </div>

    <el-table v-loading="loading" :data="bots" class="ds-table">
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column label="平台" min-width="170">
        <template #default="{ row }">{{ platformLabel(row.platform) }}</template>
      </el-table-column>
      <el-table-column label="状态" min-width="150">
        <template #default="{ row }">
          <span class="dot" :class="statusDot(row as BotOut)" />
          {{ statusText(row as BotOut) }}
          <span v-if="row.status_reason" class="reason">{{ row.status_reason }}</span>
        </template>
      </el-table-column>
      <el-table-column label="绑定" min-width="140">
        <template #default="{ row }">{{ bindingLabel(row as BotOut) }}</template>
      </el-table-column>
      <el-table-column label="需批准" width="80">
        <template #default="{ row }">{{ row.require_approval ? '是' : '否' }}</template>
      </el-table-column>
      <el-table-column label="启用" width="90">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" @change="toggle(row as BotOut)" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="210">
        <template #default="{ row }">
          <el-button size="small" @click="testOne(row as BotOut)">测试</el-button>
          <el-button size="small" @click="openEdit(row as BotOut)">编辑</el-button>
          <el-button size="small" type="danger" plain @click="removeOne(row as BotOut)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 添加/编辑对话框 -->
    <el-dialog
      v-model="dialogVisible"
      append-to-body
      :title="editingId ? '编辑机器人' : '添加机器人'"
      width="520px"
    >
      <el-form label-width="110px">
        <el-form-item label="平台">
          <el-select v-model="form.platform" :disabled="!!editingId" style="width: 100%">
            <el-option v-for="p in PLATFORMS" :key="p.value" :value="p.value" :label="p.label" />
          </el-select>
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="form.name" placeholder="例如：大棚助手" maxlength="60" />
        </el-form-item>
        <el-form-item v-for="f in CRED_FIELDS[form.platform]" :key="f.key" :label="f.label">
          <el-input v-model="form.creds[f.key]" type="password" show-password
                    :placeholder="editingId ? '留空则不改动已存凭据' : ''" />
        </el-form-item>
        <el-form-item label="绑定方式">
          <el-radio-group v-model="form.binding_mode">
            <el-radio value="auto">智能路由</el-radio>
            <el-radio value="assistant">指定助手</el-radio>
            <el-radio value="team">指定班组</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item
          v-if="form.binding_mode !== 'auto'"
          :label="form.binding_mode === 'team' ? '绑定班组' : '绑定助手'"
        >
          <el-select
            v-model="form.bound_agent_id"
            filterable
            clearable
            placeholder="选择已发布的助手 / 启用的班组"
            style="width: 100%"
            :loading="candidatesLoading"
          >
            <el-option
              v-for="c in bindingOptions"
              :key="c.id"
              :value="c.id"
              :label="c.name"
            >
              <span>{{ c.name }}</span>
              <span class="option-blurb">{{ c.blurb }}</span>
            </el-option>
          </el-select>
          <div
            v-if="!bindingOptions.length"
            class="binding-empty"
          >
            暂无可绑定的{{ form.binding_mode === 'team' ? '班组' : '已发布助手' }}，请先到对应页面创建并发布。
          </div>
        </el-form-item>
        <el-form-item label="新会话需批准">
          <el-switch v-model="form.require_approval" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" @click="submit">保存</el-button>
      </template>
    </el-dialog>

    <!-- 会话审批 -->
    <el-dialog
      v-model="drawerVisible"
      append-to-body
      title="会话审批与消息账本"
      width="620px"
      align-center
    >
      <el-table :data="convs" @row-click="viewMsgs">
        <el-table-column prop="display_name" label="会话" min-width="130">
          <template #default="{ row }">{{ row.display_name || row.platform }}</template>
        </el-table-column>
        <el-table-column label="类型" width="70">
          <template #default="{ row }">{{ row.external_conv_type === 'group' ? '群聊' : '私聊' }}</template>
        </el-table-column>
        <el-table-column label="审批" width="90">
          <template #default="{ row }">
            <el-tag :type="approvedText(row as BotConversationOut).type" size="small">{{ approvedText(row as BotConversationOut).text }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="msg_count" label="消息" width="70" />
        <el-table-column label="操作" width="130">
          <template #default="{ row }">
            <el-button v-if="!row.approved_at" size="small" type="success" plain @click.stop="review(row as BotConversationOut, true)">批准</el-button>
            <el-button v-else size="small" type="danger" plain @click.stop="review(row as BotConversationOut, false)">吊销</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template v-if="activeConv">
        <h4 class="msgs-title">{{ activeConv.display_name || activeConv.id }} · 最近消息（仅存摘要）</h4>
        <el-table :data="msgs" size="small">
          <el-table-column label="方向" width="70">
            <template #default="{ row }">{{ row.direction === 'in' ? '入' : '出' }}</template>
          </el-table-column>
          <el-table-column prop="content_sha12" label="摘要" width="120" />
          <el-table-column prop="length" label="长度" width="70" />
          <el-table-column label="路由" min-width="110">
            <template #default="{ row }">
              <el-tag size="small" :type="row.ok ? 'info' : 'danger'">{{ row.routed_by || '-' }}</el-tag>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.bots-page {
  padding: 4px;
}
.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 14px;
}
.head h2 {
  margin: 0;
  color: var(--ds-text, inherit);
  font-size: 18px;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  margin-right: 6px;
  background: var(--el-color-info);
}
.dot.online {
  background: var(--el-color-success);
}
.dot.connecting {
  background: var(--el-color-warning);
}
.dot.configured {
  background: var(--el-color-primary);
}
.dot.error {
  background: var(--el-color-danger);
}
.dot.disabled {
  background: var(--el-color-info-light-5, #b1b3b8);
}
.option-blurb {
  margin-left: 12px;
  color: var(--ds-text-muted, #999);
  font-size: 12px;
}
.binding-empty {
  margin-top: 4px;
  font-size: 12px;
  color: var(--el-color-warning);
  line-height: 1.5;
}
.reason {
  color: var(--ds-text-muted, #999);
  font-size: 12px;
  margin-left: 6px;
}
.msgs-title {
  margin: 18px 0 8px;
  font-size: 13px;
  color: var(--ds-text-muted, #888);
}
</style>
