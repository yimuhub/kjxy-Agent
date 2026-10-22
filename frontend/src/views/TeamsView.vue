<template>
  <div class="teams">
    <div class="page-head">
      <h2 class="page-title">
        协作班组
      </h2>
      <div class="page-actions">
        <el-button
          v-if="canManage"
          type="primary"
          size="small"
          @click="openCreate"
        >
          新建班组
        </el-button>
      </div>
    </div>
    <p class="page-sub">
      多个专业助手按角色接力：前序成员输出作为后序成员的上下文，审核员负责把关最终结论。
    </p>

    <!-- 班组卡片墙 -->
    <AsyncState
      :state="listState"
      empty-text="还没有协作班组，管理员可在 Agent 工厂一键装配或手动新建"
      @retry="load"
    >
      <el-row :gutter="16">
        <el-col
          v-for="t in teams"
          :key="t.id"
          :xs="24"
          :sm="12"
          :lg="8"
        >
          <el-card
            class="team-card"
            shadow="hover"
          >
            <template #header>
              <div class="tc-head">
                <span
                  class="tc-name"
                  :title="t.name"
                >{{ t.name }}</span>
                <el-tooltip
                  v-if="canManage"
                  content="停用后不能发起运行与续跑"
                  placement="top"
                >
                  <el-switch
                    :model-value="t.enabled"
                    size="small"
                    :loading="busyTeamId === t.id"
                    @change="(v: boolean) => toggleEnabled(t, v)"
                  />
                </el-tooltip>
                <el-tag
                  v-else
                  size="small"
                  :type="t.enabled ? 'success' : 'info'"
                  effect="plain"
                >
                  {{ t.enabled ? '启用中' : '已停用' }}
                </el-tag>
              </div>
            </template>
            <p
              class="tc-desc"
              :title="t.description ?? ''"
            >
              {{ t.description || '（无描述）' }}
            </p>
            <!-- 成员角色链徽标条：审核类角色（critic）特殊色 -->
            <div class="role-chain">
              <template
                v-for="(m, i) in t.members"
                :key="`${m.agent_id}-${i}`"
              >
                <span
                  v-if="i > 0"
                  class="role-arr"
                >→</span>
                <el-tag
                  size="small"
                  :type="m.is_critic ? 'danger' : 'primary'"
                  :effect="m.is_critic ? 'dark' : 'plain'"
                  class="role-chip"
                  :title="m.instruction"
                >
                  {{ m.role_name }}
                </el-tag>
              </template>
            </div>
            <div class="tc-foot">
              <el-button
                type="primary"
                size="small"
                :disabled="!t.enabled"
                @click="openRunDialog(t)"
              >
                跑一次
              </el-button>
              <template v-if="canManage">
                <el-button
                  size="small"
                  @click="openEdit(t)"
                >
                  编辑
                </el-button>
                <el-button
                  size="small"
                  type="danger"
                  plain
                  @click="removeTeam(t)"
                >
                  删除
                </el-button>
              </template>
            </div>
          </el-card>
        </el-col>
      </el-row>
    </AsyncState>

    <!-- 新建 / 编辑班组（tenant_admin/platform_admin） -->
    <el-dialog
      v-model="editVisible"
      :title="editingTeamId ? '编辑班组' : '新建班组'"
      width="760px"
      top="6vh"
    >
      <el-form
        :model="editForm"
        label-width="88px"
      >
        <el-form-item
          label="名称"
          required
        >
          <el-input
            v-model="editForm.name"
            maxlength="100"
            placeholder="同租户内不可重名"
            style="width: 320px"
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="editForm.description"
            type="textarea"
            :rows="2"
            maxlength="4000"
            placeholder="这支班组解决什么问题"
          />
        </el-form-item>
        <el-form-item label="成员">
          <div class="member-editor">
            <div
              v-for="(m, i) in editForm.members"
              :key="i"
              class="me-row"
            >
              <span class="me-idx">{{ i + 1 }}</span>
              <el-select
                v-model="m.agent_id"
                placeholder="选择已发布助手"
                filterable
                class="me-agent"
              >
                <el-option
                  v-for="a in publishedAgents"
                  :key="a.id"
                  :label="`${a.emoji ?? ''} ${a.name}（${a.agent_code}）`"
                  :value="a.id"
                />
              </el-select>
              <el-input
                v-model="m.role_name"
                placeholder="角色名，如 解读员"
                maxlength="40"
                class="me-role"
              />
              <el-tooltip
                content="审核员负责审视前序输出并给出修订结论"
                placement="top"
              >
                <el-checkbox v-model="m.is_critic">
                  审核
                </el-checkbox>
              </el-tooltip>
              <el-button
                link
                :disabled="i === 0"
                @click="moveMember(i, -1)"
              >
                上移
              </el-button>
              <el-button
                link
                :disabled="i === editForm.members.length - 1"
                @click="moveMember(i, 1)"
              >
                下移
              </el-button>
              <el-button
                link
                type="danger"
                :disabled="editForm.members.length <= 2"
                @click="editForm.members.splice(i, 1)"
              >
                移除
              </el-button>
              <el-input
                v-model="m.instruction"
                type="textarea"
                :rows="2"
                maxlength="2000"
                placeholder="该成员在这支班组里的任务说明"
                class="me-inst"
              />
            </div>
            <el-button
              size="small"
              :disabled="editForm.members.length >= 8"
              @click="addMember"
            >
              添加成员（2-8 个，按接力顺序）
            </el-button>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="saveTeam"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
    <!-- 跑一次：输入问题 → 同步接力 → 运行抽屉 -->
    <el-dialog
      v-model="runVisible"
      :title="`跑一次：${runTeam?.name ?? ''}`"
      width="560px"
    >
      <el-input
        v-model="runInput"
        type="textarea"
        :rows="4"
        maxlength="8000"
        show-word-limit
        placeholder="输入要交给班组接力处理的问题"
      />
      <template #footer>
        <el-button @click="runVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="running"
          :disabled="!runInput.trim()"
          @click="submitRun"
        >
          发起接力
        </el-button>
      </template>
    </el-dialog>

    <!-- 运行抽屉：成员横向步骤条 + 成员卡（可下钻内部步骤）+ 答案 -->
    <el-drawer
      v-model="drawerVisible"
      :title="`运行详情：${drawerTeam?.name ?? ''}`"
      size="720px"
      @closed="onDrawerClosed"
    >
      <div
        v-if="currentRun"
        class="drawer-body"
      >
        <div class="dr-toolbar">
          <el-select
            v-model="historyRunId"
            size="small"
            class="hist-select"
            placeholder="查看历史运行"
            @change="openHistory"
          >
            <el-option
              v-for="r in runsList"
              :key="r.id"
              :label="`${formatTime(r.created_at)} · ${runStatusLabel(r.status)}`"
              :value="r.id"
            />
          </el-select>
          <el-tag
            size="small"
            :type="runStatusTag(currentRun.status)"
          >
            {{ runStatusLabel(currentRun.status) }}
          </el-tag>
          <el-button
            v-if="currentRun.status === 'failed'"
            size="small"
            type="warning"
            :loading="retrying"
            @click="doRetry"
          >
            断点续跑
          </el-button>
        </div>

        <div class="q-box">
          <span class="q-label">问题</span>
          <pre class="q-text">{{ currentRun.input }}</pre>
        </div>

        <!-- 纯 CSS 横向步骤条：圆点+连线，点击节点下钻该成员步骤 -->
        <div class="step-bar">
          <template
            v-for="(m, i) in drawerMembers"
            :key="`bar-${m.index}`"
          >
            <span
              v-if="i > 0"
              class="s-line"
              :class="{ done: drawerMembers[i - 1].status === 'ok' }"
            />
            <button
              class="s-node"
              :class="[`n-${m.status}`, { active: expandedIndex === m.index }]"
              :title="`点击${expandedIndex === m.index ? '收起' : '查看'}「${m.role_name}」步骤`"
              @click="toggleMember(m.index)"
            >
              <i class="s-dot" />
              <span class="s-name">{{ m.role_name }}</span>
            </button>
          </template>
        </div>

        <!-- 成员卡：输入/输出折叠 + 失败续跑 + 内部步骤简表 -->
        <div class="member-list">
          <div
            v-for="m in drawerMembers"
            :key="`m-${m.index}`"
            class="member-card"
            :class="`mc-${m.status}`"
          >
            <div
              class="mc-head"
              @click="toggleMember(m.index)"
            >
              <i class="mc-dot" />
              <b>{{ `${m.index + 1}. ${m.role_name}` }}</b>
              <el-tag
                size="small"
                :type="memberStatusTag(m.status)"
              >
                {{ memberStatusLabel(m.status) }}
              </el-tag>
              <span class="mc-dur">{{ memberDuration(m) }}</span>
              <span class="mc-flex" />
              <el-button
                v-if="m.status === 'error'"
                size="small"
                type="warning"
                :loading="retrying"
                @click.stop="doRetry"
              >
                续跑
              </el-button>
              <el-button
                link
                size="small"
                @click.stop="toggleMember(m.index)"
              >
                {{ expandedIndex === m.index ? '收起' : '展开' }}
              </el-button>
            </div>
            <div
              v-if="expandedIndex === m.index"
              class="mc-body"
            >
              <div class="sec-label">
                输入摘要
              </div>
              <pre class="pv">{{ m.input_preview || '（无）' }}</pre>
              <template v-if="m.output">
                <div class="sec-label">
                  输出
                </div>
                <pre class="pv">{{ m.output }}</pre>
              </template>
              <div
                v-if="m.error_text"
                class="mc-error"
              >
                失败原因：{{ m.error_text }}
              </div>
              <template v-if="m.task_id">
                <div class="sec-label">
                  内部步骤（{{ m.task_id }}）
                </div>
                <el-skeleton
                  v-if="stepsLoading === m.task_id"
                  :rows="2"
                  animated
                />
                <p
                  v-else-if="stepsOf(m.task_id) === null"
                  class="note"
                >
                  暂无步骤记录（单轮执行的成员或早期运行不落步骤行）。
                </p>
                <table
                  v-else
                  class="steps-table"
                >
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>类型</th>
                      <th>工具</th>
                      <th>耗时</th>
                      <th>状态</th>
                      <th>输出摘要</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="s in stepsOf(m.task_id)"
                      :key="s.step_index"
                      :class="{ 'row-error': s.status === 'error' }"
                    >
                      <td>{{ s.step_index }}</td>
                      <td>{{ stepTypeLabel(s.step_type) }}</td>
                      <td>{{ s.tool_code || '—' }}</td>
                      <td>{{ s.duration_ms != null ? `${s.duration_ms} ms` : '—' }}</td>
                      <td>{{ s.status }}</td>
                      <td class="st-out">{{ outputBrief(s) }}</td>
                    </tr>
                  </tbody>
                </table>
              </template>
            </div>
          </div>
        </div>

        <el-alert
          v-if="currentRun.status === 'failed' && currentRun.last_error"
          type="error"
          :title="`接力中断：${currentRun.last_error}`"
          :closable="false"
          show-icon
          class="mt"
        />

        <!-- 最终答案大卡 -->
        <el-card
          v-if="currentRun.answer"
          class="answer-card mt"
          shadow="never"
        >
          <template #header>
            班组最终结论
          </template>
          <pre class="answer-text">{{ currentRun.answer }}</pre>
        </el-card>
      </div>
      <el-skeleton
        v-else
        :rows="6"
        animated
      />
    </el-drawer>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  declarativeApi,
  teamApi,
  type DeclarativeAgent,
  type TeamDefinition,
  type TeamMemberEvent,
  type TeamMemberRunStatus,
  type TeamMemberSpec,
  type TeamRun,
  type TeamRunMember,
  type TeamRunMemberStep,
  type TeamRunStatus,
  type TeamRunSummary,
} from '@/api/modules'
import { extractErrorMessage } from '@/api/client'
import { openSseStream, type SseHandle } from '@/utils/sse'
import { formatTime } from '@/utils/format'
import { classifyError, friendlyErrorMessage, type ViewState } from '@/utils/status'
import { useAuthStore } from '@/stores/auth'
import AsyncState from '@/components/AsyncState.vue'

const auth = useAuthStore()
const canManage = computed(() => auth.isSpaceAdmin())

/* ---------- 班组列表 ---------- */
const teams = ref<TeamDefinition[]>([])
const listState = ref<ViewState>('loading')
const busyTeamId = ref<string | null>(null)

async function load() {
  listState.value = 'loading'
  try {
    teams.value = await teamApi.list()
    listState.value = teams.value.length ? 'ready' : 'empty'
  } catch (e) {
    listState.value = classifyError(e)
  }
}

/* ---------- 新建 / 编辑班组（管理类：tenant_admin/platform_admin） ---------- */
const editVisible = ref(false)
const editingTeamId = ref<string | null>(null)
const saving = ref(false)
const publishedAgents = ref<DeclarativeAgent[]>([])
const editForm = reactive<{
  name: string
  description: string
  members: TeamMemberSpec[]
}>({ name: '', description: '', members: [] })

function blankMember(): TeamMemberSpec {
  return { agent_id: '', role_name: '', instruction: '', is_critic: false }
}

async function loadPublishedAgents() {
  if (publishedAgents.value.length) return
  try {
    const all = await declarativeApi.list()
    publishedAgents.value = all.filter(a => a.status === 'published')
  } catch {
    publishedAgents.value = []
    ElMessage.warning('成员助手列表加载失败，可稍后重试')
  }
}

function openCreate() {
  editingTeamId.value = null
  editForm.name = ''
  editForm.description = ''
  editForm.members = [blankMember(), blankMember()]
  editVisible.value = true
  void loadPublishedAgents()
}

function openEdit(t: TeamDefinition) {
  editingTeamId.value = t.id
  editForm.name = t.name
  editForm.description = t.description ?? ''
  editForm.members = t.members.map(m => ({ ...m }))
  editVisible.value = true
  void loadPublishedAgents()
}

function addMember() {
  if (editForm.members.length < 8) editForm.members.push(blankMember())
}

function moveMember(i: number, delta: number) {
  const j = i + delta
  if (j < 0 || j >= editForm.members.length) return
  const arr = editForm.members
  ;[arr[i], arr[j]] = [arr[j], arr[i]]
}

async function saveTeam() {
  const name = editForm.name.trim()
  if (!name) {
    ElMessage.warning('请填写班组名称')
    return
  }
  if (editForm.members.length < 2 || editForm.members.length > 8) {
    ElMessage.warning('班组成员需 2-8 个')
    return
  }
  if (editForm.members.some(m => !m.agent_id || !m.role_name.trim())) {
    ElMessage.warning('每位成员都要选择已发布助手并填写角色名')
    return
  }
  if (editForm.members.some(m => !m.instruction.trim())) {
    ElMessage.warning('每位成员都要填写任务说明')
    return
  }
  saving.value = true
  const payload = {
    name,
    description: editForm.description.trim() || undefined,
    members: editForm.members.map(m => ({ ...m, role_name: m.role_name.trim() })),
  }
  try {
    if (editingTeamId.value) {
      await teamApi.update(editingTeamId.value, payload)
      ElMessage.success('班组已更新')
    } else {
      await teamApi.create(payload)
      ElMessage.success('班组已创建')
    }
    editVisible.value = false
    await load()
  } catch {
    /* 重名 409 等错误 toast 已由 client 拦截器统一提示 */
  } finally {
    saving.value = false
  }
}

async function toggleEnabled(t: TeamDefinition, v: boolean) {
  busyTeamId.value = t.id
  try {
    await teamApi.update(t.id, { enabled: v })
    t.enabled = v
    ElMessage.success(v ? '已启用' : '已停用')
  } catch {
    /* 失败保持原状态，拦截器已提示 */
  } finally {
    busyTeamId.value = null
  }
}

async function removeTeam(t: TeamDefinition) {
  try {
    await ElMessageBox.confirm(
      `确定删除班组「${t.name}」吗？历史运行记录会保留。`,
      '删除班组',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await teamApi.remove(t.id)
    ElMessage.success('已删除')
    await load()
  } catch {
    /* 拦截器已提示 */
  }
}

/* ---------- 跑一次 ---------- */
const runVisible = ref(false)
const runTeam = ref<TeamDefinition | null>(null)
const runInput = ref('')
const running = ref(false)

function openRunDialog(t: TeamDefinition) {
  runTeam.value = t
  runInput.value = ''
  runVisible.value = true
}

async function submitRun() {
  const team = runTeam.value
  const input = runInput.value.trim()
  if (!team || !input || running.value) return
  running.value = true
  try {
    const run = await teamApi.run(team.id, input)
    runVisible.value = false
    await openDrawer(team, run)
  } catch {
    // 失败口径：HTTP 抛原始错误但 run 已停在 failed 断点——
    // 拦截器已 toast；这里拉最新一次运行打开抽屉，让用户看到断点并续跑
    try {
      const rs = await teamApi.runs(team.id)
      if (rs.length) {
        const detail = await teamApi.runDetail(rs[0].id)
        runVisible.value = false
        await openDrawer(team, detail)
      }
    } catch {
      /* 兜底失败保持对话框可重试 */
    }
  } finally {
    running.value = false
  }
}

/* ---------- 运行抽屉 + SSE ---------- */
const drawerVisible = ref(false)
const drawerTeam = ref<TeamDefinition | null>(null)
const currentRun = ref<TeamRun | null>(null)
const runsList = ref<TeamRunSummary[]>([])
const historyRunId = ref<string>('')
const expandedIndex = ref(-1)
const retrying = ref(false)
/** task_id -> 步骤数组；'none' 表示无步骤行（steps 端点 404） */
const stepsCache = reactive<Record<string, TeamRunMemberStep[] | 'none'>>({})
const stepsLoading = ref<string | null>(null)
let sse: SseHandle | null = null
let pollTimer: number | undefined

const drawerMembers = computed<TeamRunMember[]>(() => currentRun.value?.members ?? [])

async function openDrawer(team: TeamDefinition, run: TeamRun) {
  drawerTeam.value = team
  currentRun.value = run
  historyRunId.value = run.id
  expandedIndex.value = -1
  drawerVisible.value = true
  void refreshRunsList(team.id)
  if (run.status === 'running') subscribeSse(run.id)
}

async function refreshRunsList(teamId: string) {
  try {
    runsList.value = await teamApi.runs(teamId)
  } catch {
    runsList.value = []
  }
}

async function openHistory(runId: string) {
  if (!runId || runId === currentRun.value?.id) return
  closeStream()
  stopPoll()
  try {
    const detail = await teamApi.runDetail(runId)
    currentRun.value = detail
    expandedIndex.value = -1
    if (detail.status === 'running') subscribeSse(detail.id)
  } catch {
    /* 拦截器已提示 */
  }
}

function memberEventApply(runId: string, ev: TeamMemberEvent) {
  const run = currentRun.value
  if (!run || run.id !== runId || !run.members) return
  const m = run.members.find(x => x.index === ev.member_index)
  if (!m) return
  m.status = ev.status
  m.error_text = ev.error_text
  if (ev.agent_run_id) m.task_id = ev.agent_run_id
}

function subscribeSse(runId: string) {
  closeStream()
  sse = openSseStream({
    url: teamApi.streamUrl(runId),
    onFrame: f => {
      if (f.event === 'member-event') {
        try {
          memberEventApply(runId, JSON.parse(f.data) as TeamMemberEvent)
        } catch {
          /* 忽略坏帧 */
        }
      } else if (f.event === 'team-end') {
        // 服务端收尾：主动断流避免 EOF 重连，然后静默刷新拿终态与全文输出
        closeStream()
        void refreshRunQuiet(runId)
      }
    },
    onFallback: () => startPoll(runId),
  })
}

async function refreshRunQuiet(runId: string) {
  if (!drawerVisible.value || currentRun.value?.id !== runId) return
  try {
    currentRun.value = await teamApi.runDetail(runId)
    if (drawerTeam.value) void refreshRunsList(drawerTeam.value.id)
  } catch {
    /* 静默 */
  }
}

/** SSE 重连失败降级：抽屉保持打开时每 5s 静默刷新一次 */
function startPoll(runId: string) {
  stopPoll()
  pollTimer = window.setInterval(async () => {
    if (!drawerVisible.value || currentRun.value?.id !== runId) {
      stopPoll()
      return
    }
    try {
      const d = await teamApi.runDetail(runId)
      currentRun.value = d
      if (d.status !== 'running') stopPoll()
    } catch {
      /* 下一轮再试 */
    }
  }, 5000)
}

function stopPoll() {
  if (pollTimer !== undefined) {
    window.clearInterval(pollTimer)
    pollTimer = undefined
  }
}

function closeStream() {
  sse?.close()
  sse = null
}

function onDrawerClosed() {
  closeStream()
  stopPoll()
}

/* ---------- 断点续跑（已 ok 成员不重跑） ---------- */
async function doRetry() {
  const run = currentRun.value
  if (!run || retrying.value) return
  retrying.value = true
  try {
    const done = await teamApi.retry(run.id)
    currentRun.value = done
    ElMessage.success('续跑完成')
    if (drawerTeam.value) void refreshRunsList(drawerTeam.value.id)
  } catch (e) {
    const { code, message } = extractErrorMessage(e)
    if (code === 'TEAM_RUN_ALREADY_COMPLETED') {
      ElMessage.info('该运行已完成，无需续跑，已为你刷新状态')
      await refreshRunQuiet(run.id)
    } else if (code === 'TEAM_RUN_STATE_CONFLICT') {
      ElMessage.info('该运行仍在执行中，稍后即可查看最新进度')
    } else {
      ElMessage.error(friendlyErrorMessage(code, message))
    }
  } finally {
    retrying.value = false
  }
}

/* ---------- 成员步骤下钻 ---------- */
function toggleMember(index: number) {
  const next = expandedIndex.value === index ? -1 : index
  expandedIndex.value = next
  if (next !== -1) {
    const m = drawerMembers.value.find(x => x.index === index)
    if (m?.task_id && !(m.task_id in stepsCache)) void loadSteps(m.task_id)
  }
}

async function loadSteps(taskId: string) {
  stepsLoading.value = taskId
  try {
    stepsCache[taskId] = await teamApi.memberSteps(taskId)
  } catch {
    // 单轮执行成员没有步骤行（404 RUN_NOT_FOUND）：如实标注，不打扰
    stepsCache[taskId] = 'none'
  } finally {
    if (stepsLoading.value === taskId) stepsLoading.value = null
  }
}

function stepsOf(taskId: string): TeamRunMemberStep[] | null {
  const v = stepsCache[taskId]
  return Array.isArray(v) ? v : null
}

function memberDuration(m: TeamRunMember): string {
  const steps = m.task_id ? stepsCache[m.task_id] : undefined
  if (!Array.isArray(steps)) return ''
  const total = steps.reduce((acc, s) => acc + (s.duration_ms ?? 0), 0)
  return total > 0 ? `共 ${total} ms` : ''
}

function outputBrief(s: TeamRunMemberStep): string {
  if (s.output_json == null) return '—'
  const text = typeof s.output_json === 'string' ? s.output_json : JSON.stringify(s.output_json)
  return text.length > 120 ? `${text.slice(0, 120)}…` : text
}

/* ---------- 展示映射 ---------- */
function runStatusLabel(s: TeamRunStatus): string {
  return { running: '执行中', completed: '已完成', failed: '已中断' }[s] ?? s
}
function runStatusTag(s: TeamRunStatus): 'success' | 'warning' | 'danger' {
  return s === 'completed' ? 'success' : s === 'running' ? 'warning' : 'danger'
}
function memberStatusLabel(s: TeamMemberRunStatus): string {
  return { pending: '等待', running: '进行中', ok: '完成', error: '失败' }[s] ?? s
}
function memberStatusTag(s: TeamMemberRunStatus): 'success' | 'warning' | 'danger' | 'info' {
  return s === 'ok' ? 'success' : s === 'error' ? 'danger' : s === 'running' ? 'warning' : 'info'
}
function stepTypeLabel(t: TeamRunMemberStep['step_type']): string {
  return { llm_call: '模型调用', tool_call: '工具调用', final: '最终输出' }[t] ?? t
}

onMounted(load)
onUnmounted(() => {
  closeStream()
  stopPoll()
})
</script>
<style scoped>
/* 头部：左标题右操作区（.page-title/.page-actions 由 design-system 全局定义），此处仅页面微调 */
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.page-title {
  margin: 0 0 4px; /* 有副标题，收紧下边距 */
}
.page-sub {
  margin: 0 0 16px;
  color: var(--ds-text-3);
  font-size: 13px;
}
.team-card {
  margin-bottom: 16px;
}
.team-card:hover {
  border-color: var(--ds-border-strong);
  background: var(--ds-glass-strong);
}
.tc-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.tc-name {
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.tc-desc {
  margin: 0 0 10px;
  min-height: 36px;
  color: var(--ds-text-2);
  font-size: 13px;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.role-chain {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  margin-bottom: 12px;
}
.role-arr {
  color: var(--ds-text-3);
}
.tc-foot {
  display: flex;
  gap: 8px;
}
.member-editor {
  width: 100%;
}
.me-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  padding: 8px;
  margin-bottom: 8px;
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-control);
}
.me-idx {
  width: 20px;
  color: var(--ds-text-3);
  text-align: center;
}
.me-agent {
  width: 220px;
}
.me-role {
  width: 150px;
}
.me-inst {
  flex: 1 1 100%;
}

/* ---------- 运行抽屉 ---------- */
.dr-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.hist-select {
  width: 220px;
}
.q-box {
  background: var(--ds-glass);
  border: 1px solid var(--ds-border);
  border-radius: var(--ds-radius-control);
  padding: 8px 12px;
  margin-bottom: 16px;
}
.q-label {
  font-size: 12px;
  color: var(--ds-text-3);
}
.q-text,
.pv,
.answer-text {
  white-space: pre-wrap;
  word-break: break-all;
  margin: 4px 0 0;
  font-family: inherit;
  font-size: 13px;
  color: var(--ds-text-1);
}

/* 纯 CSS 横向步骤条：圆点 + 连线（照 DevicesView bar 思路，零依赖） */
.step-bar {
  display: flex;
  align-items: flex-start;
  flex-wrap: wrap;
  row-gap: 12px;
  margin: 4px 0 18px;
}
.s-line {
  height: 2px;
  width: 44px;
  margin-top: 8px;
  background: var(--ds-border-strong);
  flex: none;
}
.s-line.done {
  background: var(--ds-success);
}
.s-node {
  border: none;
  background: transparent;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  width: 88px;
  padding: 0;
}
.s-node.active .s-name {
  font-weight: 700;
}
.s-dot {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--ds-text-3);
  display: inline-block;
}
.n-ok .s-dot {
  background: var(--ds-success);
}
.n-error .s-dot {
  background: var(--ds-danger);
}
.n-running .s-dot {
  background: var(--ds-warning);
  animation: team-pulse 1s ease-in-out infinite;
}
.n-pending .s-dot {
  background: var(--ds-text-3);
}
.n-ok .s-name {
  color: var(--ds-success);
}
.n-error .s-name {
  color: var(--ds-danger);
}
.n-running .s-name {
  color: var(--ds-warning);
}
.n-pending .s-name {
  color: var(--ds-text-3);
}
.s-name {
  font-size: 12px;
}
@keyframes team-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(251, 191, 36, 0.5);
  }
  50% {
    box-shadow: 0 0 0 7px rgba(251, 191, 36, 0);
  }
}

.member-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.member-card {
  background: var(--ds-glass);
  border: 1px solid var(--ds-border);
  border-left: 3px solid var(--ds-border-strong);
  border-radius: var(--ds-radius-control);
  padding: 0 12px;
}
.mc-ok {
  border-left-color: var(--ds-success);
}
.mc-error {
  border-left-color: var(--ds-danger);
}
.mc-running {
  border-left-color: var(--ds-warning);
}
.mc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  cursor: pointer;
  font-size: 13px;
}
.mc-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--ds-text-3);
  flex: none;
}
.mc-ok .mc-dot {
  background: var(--ds-success);
}
.mc-error .mc-dot {
  background: var(--ds-danger);
}
.mc-running .mc-dot {
  background: var(--ds-warning);
  animation: team-pulse 1s ease-in-out infinite;
}
.mc-dur {
  color: var(--ds-text-3);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.mc-flex {
  flex: 1;
}
.mc-body {
  border-top: 1px dashed var(--ds-border);
  padding: 8px 0 12px;
}
.sec-label {
  font-size: 12px;
  color: var(--ds-text-3);
  margin: 8px 0 2px;
}
.mc-error,
.member-card .mc-error {
  margin-top: 8px;
}
.mc-body .mc-error {
  color: var(--ds-danger);
  background: rgba(248, 113, 113, 0.12);
  border-radius: var(--ds-radius-control);
  padding: 6px 8px;
  font-size: 13px;
}
.pv {
  color: var(--ds-text-2);
  max-height: 200px;
  overflow: auto;
  background: var(--ds-glass);
  border-radius: var(--ds-radius-control);
  padding: 8px;
}
.note {
  color: var(--ds-text-3);
  font-size: 12px;
}

/* 成员内部步骤简表 */
.steps-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.steps-table th,
.steps-table td {
  border: 1px solid var(--ds-border);
  padding: 4px 6px;
  text-align: left;
  vertical-align: top;
}
.steps-table th {
  background: var(--ds-glass);
  color: var(--ds-text-2);
  white-space: nowrap;
}
.steps-table .row-error {
  background: rgba(248, 113, 113, 0.12);
  color: var(--ds-danger);
}
.st-out {
  word-break: break-all;
  max-width: 260px;
  color: var(--ds-text-2);
}
.answer-card :deep(.el-card__header) {
  font-weight: 600;
}
.answer-text {
  color: var(--ds-text-1);
}
.mt {
  margin-top: 12px;
}
</style>
