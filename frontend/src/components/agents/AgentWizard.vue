<template>
  <el-dialog
    v-model="visible"
    append-to-body
    title="⚡ 一句话生成智能体"
    width="720px"
    top="6vh"
    :close-on-click-modal="false"
    @closed="onClosed"
  >
    <!-- 第 1 步：一句话描述 -->
    <div
      v-if="step === 1"
      class="wizard-step"
    >
      <p
        v-if="policy"
        class="policy-line"
      >
        <template v-if="!policy.enabled">
          本小院已关闭一句话生成服务，请联系负责人在「系统设置」开启。
        </template>
        <template v-else-if="policy.remaining >= 0">
          今日已生成 {{ policy.used }}/{{ policy.limit }} 次，剩余 {{ policy.remaining }} 次。
        </template>
        <template v-else> 今日已生成 {{ policy.used }} 次（不限量）。 </template>
      </p>

      <el-input
        v-model="utterance"
        type="textarea"
        :rows="4"
        maxlength="2000"
        show-word-limit
        :disabled="policy?.enabled === false"
        placeholder="例：帮我做一个病虫害防治智能体，能查小院知识库、给出防治建议和用药提醒"
      />

      <div class="example-chips">
        <span class="chips-label">试试：</span>
        <el-tag
          v-for="ex in EXAMPLES"
          :key="ex"
          class="example-chip"
          effect="plain"
          @click="utterance = ex"
        >
          {{ ex }}
        </el-tag>
      </div>

      <p
        v-if="previewNote"
        class="preview-note"
      >
        {{ previewNote }}
      </p>

      <div class="options-row">
        <span class="opt-label">可见性</span>
        <el-radio-group
          v-model="visibility"
          size="small"
        >
          <el-radio value="private">私有（仅自己可见）</el-radio>
          <el-radio
            value="public"
            :disabled="!canPublic"
          >
            公开（全小院可用）
          </el-radio>
        </el-radio-group>
        <span
          v-if="!canPublic"
          class="opt-hint"
        >公开需小院管理员权限</span>
      </div>
      <div class="options-row">
        <span class="opt-label">联网取证</span>
        <el-switch
          v-model="useWeb"
          :disabled="policy?.use_web === false"
        />
        <span class="opt-hint">生成前先检索公开资料作依据（失败自动降级为离线生成）</span>
      </div>

      <p
        v-if="errorText"
        class="error-text"
        role="status"
      >
        {{ errorText }}
      </p>
    </div>

    <!-- 第 2 步：生成中（SSE 里程碑 + 轮询兜底） -->
    <div
      v-else-if="step === 2"
      class="wizard-step"
    >
      <el-progress
        :percentage="progressPct"
        :stroke-width="10"
      />
      <ul class="stage-list">
        <li
          v-for="st in STAGES"
          :key="st.key"
          :class="{ done: stageIndex >= st.index, active: stageIndex === st.index }"
        >
          <span class="stage-dot">{{ stageIndex > st.index ? '✓' : st.index + 1 }}</span>
          {{ st.label }}
          <em v-if="stageMessages[st.key]">{{ stageMessages[st.key] }}</em>
        </li>
      </ul>
      <p class="poll-hint">生成约需 30~60 秒，可关闭弹窗稍后回来，草稿不会丢失。</p>
      <p
        v-if="errorText"
        class="error-text"
        role="status"
      >
        {{ errorText }}
      </p>
    </div>

    <!-- 第 3 步：结果预览 -->
    <div
      v-else-if="step === 3 && summary"
      class="wizard-step"
    >
      <el-alert
        :title="`草稿「${summary.name}」已生成（${summary.status === 'draft' ? '草稿' : summary.status}，未发布）`"
        :type="summary.note ? 'warning' : 'success'"
        :closable="false"
        show-icon
        class="result-alert"
      />
      <p
        v-if="summary.offline"
        class="opt-hint offline-hint"
      >
        本次生成未能联网取证（离线生成），建议人工核对内容后再发布。
      </p>

      <div class="result-cols">
        <div class="blueprint-col">
          <template v-if="blueprint">
            <div class="bp-section">
              <h5>使命</h5>
              <p>{{ blueprint.mission }}</p>
            </div>
            <div class="bp-section">
              <h5>服务对象</h5>
              <p>{{ blueprint.audience }}</p>
            </div>
            <div class="bp-section">
              <h5>职责边界</h5>
              <ul>
                <li
                  v-for="(item, i) in blueprint.in_scope || []"
                  :key="`in-${i}`"
                >
                  做：{{ item }}
                </li>
                <li
                  v-for="(item, i) in blueprint.out_of_scope || []"
                  :key="`out-${i}`"
                  class="out"
                >
                  不做：{{ item }}
                </li>
              </ul>
            </div>
            <div class="bp-section">
              <h5>工作流</h5>
              <ol>
                <li
                  v-for="(st, i) in blueprint.workflow || []"
                  :key="`wf-${i}`"
                >
                  {{ st.action }}
                  <el-tag
                    v-if="st.tool"
                    size="small"
                    effect="plain"
                  >
                    工具 {{ st.tool }}
                  </el-tag>
                </li>
              </ol>
            </div>
            <div class="bp-section">
              <h5>护栏</h5>
              <ul>
                <li
                  v-for="(g, i) in blueprint.guardrails || []"
                  :key="`g-${i}`"
                >
                  {{ g }}
                </li>
              </ul>
            </div>
            <div class="bp-section">
              <h5>产出格式</h5>
              <p>{{ blueprint.output_format }}</p>
            </div>
            <el-collapse
              v-if="summary.sources?.length"
              class="bp-sources"
            >
              <el-collapse-item
                :title="`参考来源（${summary.sources.length}）`"
                name="sources"
              >
                <ul class="source-list">
                  <li
                    v-for="(s, i) in summary.sources"
                    :key="`${s.url}-${i}`"
                  >
                    <a
                      :href="s.url"
                      target="_blank"
                      rel="noopener noreferrer"
                    >{{ s.title || s.url }}</a>
                  </li>
                </ul>
              </el-collapse-item>
            </el-collapse>
          </template>
          <el-empty
            v-else
            description="蓝图加载中或不可用"
            :image-size="60"
          />
        </div>

        <div class="skills-col">
          <div class="skill-block">
            <h5>已匹配技能（{{ matchedSelected.length }}/{{ summary.matched.length }}）</h5>
            <p class="opt-hint">生成时自动绑定；取消勾选保存后即从智能体解绑。</p>
            <el-checkbox-group v-model="matchedSelected">
              <el-checkbox
                v-for="m in summary.matched"
                :key="m.code"
                :value="m.code"
              >
                <span class="match-name">{{ m.name }}</span>
                <span class="match-kind">{{ m.kind === 'plugin' ? '插件' : '技能' }}</span>
              </el-checkbox>
            </el-checkbox-group>
            <p
              v-if="!summary.matched.length"
              class="opt-hint"
            >
              本院暂无可匹配的技能/插件。
            </p>
          </div>
          <div class="skill-block">
            <h5>待补技能（{{ requirementsLocal.length }}）</h5>
            <p class="opt-hint">生成时未找到匹配能力，可去「插件与技能库」补充；点 × 可删除该项。</p>
            <div class="req-tags">
              <el-tag
                v-for="(r, i) in requirementsLocal"
                :key="`${r.need}-${i}`"
                closable
                effect="plain"
                type="warning"
                @close="removeRequirement(i)"
              >
                {{ r.need }}
              </el-tag>
            </div>
            <p
              v-if="!requirementsLocal.length"
              class="opt-hint"
            >
              没有待补项。
            </p>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <template v-if="step === 1">
        <el-button @click="visible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="submitting"
          :disabled="!canSubmit"
          @click="start"
        >
          开始生成
        </el-button>
      </template>
      <template v-else-if="step === 2">
        <el-button @click="visible = false">后台运行，稍后再来</el-button>
      </template>
      <template v-else-if="step === 3">
        <el-button @click="backToStep1">重新生成</el-button>
        <el-button
          type="primary"
          @click="finish"
        >
          保存并继续编辑
        </el-button>
      </template>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  import { computed, onBeforeUnmount, ref, watch } from 'vue'
  import { ElMessage } from 'element-plus'
  import {
    agentGenerateApi,
    type AgentGeneratePolicy,
    type AgentGenerateTaskSummary,
    type AgentSkillRequirement,
  } from '@/api/v3'
  import { declarativeApi } from '@/api/modules'
  import { extractErrorMessage } from '@/api/client'
  import { openSseStream, type SseHandle } from '@/utils/sse'
  import { useAuthStore } from '@/stores/auth'

  /**
   * 一句话生成智能体三步向导（v3.5）：
   * 1 描述（同步预览候选 + 配额）→ 2 SSE 四阶段进度（断线降级 5s 轮询）→
   * 3 六段蓝图预览 + 已匹配技能（可解绑）+ 待补技能（可删除）。
   * 完成后 emit('created', agentId) 由宿主页打开编辑弹窗。
   */
  const props = defineProps<{ modelValue: boolean }>()
  const emit = defineEmits<{ (e: 'update:modelValue', v: boolean): void; (e: 'created', agentId: string): void }>()

  const auth = useAuthStore()
  const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'

  const EXAMPLES = [
    '帮我做一个病虫害防治智能体，能查知识库并给防治建议',
    '做一个每周汇总设备告警并给处理优先级的助手',
    '我要一个帮种植户算灌溉时间和水量的智能体',
  ]

  const STAGES = [
    { key: 'retrieving', index: 0, label: '检索公开资料' },
    { key: 'designing', index: 1, label: '按固定框架设计职责' },
    { key: 'matching', index: 2, label: '匹配本院已有技能' },
    { key: 'persisting', index: 3, label: '保存草稿' },
  ] as const

  const visible = computed({
    get: () => props.modelValue,
    set: (v: boolean) => emit('update:modelValue', v),
  })

  const step = ref<1 | 2 | 3>(1)
  const policy = ref<AgentGeneratePolicy | null>(null)
  const utterance = ref('')
  const visibility = ref<'private' | 'public'>('private')
  const useWeb = ref(true)
  const submitting = ref(false)
  const errorText = ref('')

  const previewNote = ref('')
  let previewTimer: number | undefined

  const progressPct = ref(0)
  const stageIndex = ref(-1)
  const stageMessages = ref<Record<string, string>>({})
  let taskId = ''
  let stream: SseHandle | null = null
  let pollTimer: number | undefined
  let pollFailures = 0

  const summary = ref<AgentGenerateTaskSummary | null>(null)
  interface BlueprintView {
    mission?: string
    audience?: string
    in_scope?: string[]
    out_of_scope?: string[]
    workflow?: { action?: string; tool?: string | null }[]
    guardrails?: string[]
    output_format?: string
  }
  const blueprint = ref<BlueprintView | null>(null)
  const matchedSelected = ref<string[]>([])
  const requirementsLocal = ref<AgentSkillRequirement[]>([])

  const canPublic = computed(() => auth.isYardAdmin?.() === true || auth.roles?.includes('yard_admin') || auth.roles?.includes('platform_admin'))
  const canSubmit = computed(
    () =>
      policy.value?.enabled !== false &&
      utterance.value.trim().length >= 4 &&
      !submitting.value,
  )

  watch(
    () => props.modelValue,
    open => {
      if (open) void init()
    },
  )

  watch(utterance, text => {
    if (previewTimer) window.clearTimeout(previewTimer)
    previewNote.value = ''
    if (step.value !== 1 || text.trim().length < 4) return
    previewTimer = window.setTimeout(async () => {
      try {
        const res = await agentGenerateApi.preview(text.trim())
        previewNote.value = res.note
        policy.value = res.policy
      } catch {
        /* 预览失败不打扰主流程，生成时仍会校验 */
      }
    }, 500)
  })

  async function init(): Promise<void> {
    step.value = 1
    errorText.value = ''
    summary.value = null
    blueprint.value = null
    previewNote.value = ''
    if (!utterance.value) visibility.value = canPublic.value ? 'private' : 'private'
    try {
      policy.value = await agentGenerateApi.policy()
      useWeb.value = policy.value.use_web !== false
    } catch {
      policy.value = null
    }
  }

  function onClosed(): void {
    stream?.close()
    stream = null
    if (pollTimer) {
      window.clearInterval(pollTimer)
      pollTimer = undefined
    }
  }

  function fail(text: string): void {
    errorText.value = text
    step.value = 2
    if (pollTimer) {
      window.clearInterval(pollTimer)
      pollTimer = undefined
    }
  }

  async function start(): Promise<void> {
    errorText.value = ''
    submitting.value = true
    try {
      const res = await agentGenerateApi.generate({
        utterance: utterance.value.trim(),
        visibility: visibility.value,
        use_web: useWeb.value,
      })
      taskId = res.task_id
      step.value = 2
      stageIndex.value = 0
      progressPct.value = 5
      watchTask(taskId)
    } catch (err) {
      const { code, message } = extractErrorMessage(err)
      errorText.value =
        code === 'FORBIDDEN'
          ? '将智能体设为公开需要小院管理员权限；请改选私有后重试。'
          : message || '发起生成失败，请稍后重试。'
    } finally {
      submitting.value = false
    }
  }

  /** 订阅 SSE 里程碑；重连失败降级 5s 轮询任务行 */
  function watchTask(id: string): void {
    stream = openSseStream({
      url: `${API_BASE}/tasks/${id}/events/stream`,
      onFrame: frame => {
        if (frame.event === 'task-status' || frame.event === 'end') {
          let statusPayload: { status?: string } = {}
          try {
            statusPayload = JSON.parse(frame.data)
          } catch {
            return
          }
          const s = statusPayload.status
          if (s === 'succeeded') void onTaskDone()
          if (s === 'failed') void onTaskDone()
          return
        }
        if (frame.event !== 'task-event') return
        let data: {
          message?: string
          progress?: number
          payload_json?: { milestone?: string }
          milestone?: string
        } = {}
        try {
          data = JSON.parse(frame.data)
        } catch {
          return
        }
        const milestone = data.payload_json?.milestone ?? data.milestone
        if (!milestone) return
        if (milestone === 'completed') {
          void onTaskDone()
          return
        }
        if (milestone === 'failed') {
          fail(data.message || '生成失败，可返回修改后重试。')
          return
        }
        const idx = STAGES.findIndex(s => s.key === milestone)
        if (idx >= 0) {
          stageIndex.value = idx
          stageMessages.value = { ...stageMessages.value, [milestone]: data.message || '' }
        }
        if (typeof data.progress === 'number') progressPct.value = Math.round(data.progress * 100)
      },
      onFallback: () => startPolling(id),
    })
  }

  function startPolling(id: string): void {
    if (pollTimer) return
    pollFailures = 0
    pollTimer = window.setInterval(async () => {
      try {
        const row = await agentGenerateApi.task(id)
        pollFailures = 0
        if (row.status === 'succeeded' || row.status === 'failed') {
          if (pollTimer) {
            window.clearInterval(pollTimer)
            pollTimer = undefined
          }
          await onTaskDone()
        }
      } catch {
        pollFailures += 1
        if (pollFailures >= 5) {
          fail('生成状态刷新失败，请稍后重试。')
        }
      }
    }, 5000)
  }

  async function onTaskDone(): Promise<void> {
    stream?.close()
    stream = null
    if (pollTimer) {
      window.clearInterval(pollTimer)
      pollTimer = undefined
    }
    try {
      const row = await agentGenerateApi.task(taskId)
      if (row.status !== 'succeeded' || !row.result_json) {
        fail(row.error_message || '生成失败，可返回修改后重试。')
        return
      }
      summary.value = row.result_json
      matchedSelected.value = row.result_json.matched.map(m => m.code)
      requirementsLocal.value = [...row.result_json.skill_requirements]
      progressPct.value = 100
      const agents = await declarativeApi.list()
      const found = agents.find(a => a.id === row.result_json?.agent_id)
      blueprint.value = (found?.blueprint as BlueprintView | null) ?? null
      step.value = 3
    } catch (err) {
      fail(extractErrorMessage(err).message || '获取生成结果失败。')
    }
  }

  async function removeRequirement(index: number): Promise<void> {
    const agentId = summary.value?.agent_id
    if (!agentId) return
    const next = requirementsLocal.value.filter((_, i) => i !== index)
    try {
      await declarativeApi.update(agentId, { skill_requirements: next })
      requirementsLocal.value = next
    } catch (err) {
      ElMessage.error(extractErrorMessage(err).message || '删除待补项失败')
    }
  }

  async function persistMatched(): Promise<void> {
    const agentId = summary.value?.agent_id
    if (!agentId) return
    const current = summary.value?.matched.map(m => m.code) ?? []
    if (matchedSelected.value.length === current.length) return
    await declarativeApi.update(agentId, { bound_skills: [...matchedSelected.value] })
  }

  function backToStep1(): void {
    step.value = 1
    errorText.value = ''
    void init()
  }

  async function finish(): Promise<void> {
    const agentId = summary.value?.agent_id
    if (!agentId) {
      visible.value = false
      return
    }
    try {
      await persistMatched()
    } catch (err) {
      ElMessage.error(extractErrorMessage(err).message || '保存技能绑定失败')
      return
    }
    emit('created', agentId)
    visible.value = false
  }

  onBeforeUnmount(onClosed)
</script>

<style scoped>
  .wizard-step {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .policy-line {
    margin: 0;
    font-size: 12.5px;
    color: var(--ds-text-3);
  }
  .example-chips {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }
  .chips-label {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .example-chip {
    cursor: pointer;
  }
  .preview-note {
    margin: 0;
    font-size: 12.5px;
    color: var(--ds-accent-text, var(--ds-text-2));
  }
  .options-row {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .opt-label {
    font-size: 13px;
    color: var(--ds-text-2);
    min-width: 56px;
  }
  .opt-hint {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .offline-hint {
    margin: 0;
  }
  .error-text {
    margin: 0;
    font-size: 12.5px;
    color: var(--ds-danger);
  }
  .stage-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .stage-list li {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 13px;
    color: var(--ds-text-3);
  }
  .stage-list li.active {
    color: var(--ds-text-1);
    font-weight: 600;
  }
  .stage-list li.done {
    color: var(--ds-text-2);
  }
  .stage-dot {
    display: inline-flex;
    width: 18px;
    height: 18px;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    border: 1px solid var(--ds-border);
    font-size: 11px;
  }
  .stage-list li.done .stage-dot {
    background: var(--ds-success, #3f9d68);
    border-color: transparent;
    color: #fff;
  }
  .stage-list em {
    font-style: normal;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .poll-hint {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .result-alert {
    margin-bottom: 4px;
  }
  .result-cols {
    display: grid;
    grid-template-columns: 1.2fr 1fr;
    gap: 16px;
  }
  @media (max-width: 720px) {
    .result-cols {
      grid-template-columns: 1fr;
    }
  }
  .blueprint-col,
  .skills-col {
    display: flex;
    flex-direction: column;
    gap: 10px;
    min-width: 0;
  }
  .bp-section {
    border: 1px solid var(--ds-border);
    border-radius: 8px;
    padding: 8px 10px;
  }
  .bp-section h5,
  .skill-block h5 {
    margin: 0 0 4px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .bp-section p,
  .bp-section ul,
  .bp-section ol {
    margin: 0;
    padding-left: 18px;
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--ds-text-2);
    word-break: break-word;
  }
  .bp-section li.out {
    color: var(--ds-text-3);
  }
  .skill-block {
    border: 1px solid var(--ds-border);
    border-radius: 8px;
    padding: 8px 10px;
  }
  .skill-block .opt-hint {
    margin: 0 0 6px;
  }
  .match-name {
    font-size: 12.5px;
  }
  .match-kind {
    margin-left: 6px;
    font-size: 11px;
    color: var(--ds-text-3);
  }
  .req-tags {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .source-list {
    margin: 0;
    padding-left: 18px;
    font-size: 12px;
  }
  .source-list a {
    color: var(--ds-accent-text);
    word-break: break-all;
  }
</style>
