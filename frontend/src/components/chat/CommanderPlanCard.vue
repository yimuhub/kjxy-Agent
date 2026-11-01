<template>
  <div class="commander-plan">
    <div class="head">
      <span class="kind">指挥官计划</span>
      <el-tag
        size="small"
        :type="stateTagType"
        effect="plain"
      >
        {{ stateLabel }}
      </el-tag>
      <span class="counts">
        {{ viewRows.length }} 个子任务 · {{ helperCount }} 个助手
      </span>
    </div>

    <p class="goal">
      <span class="goal-label">目标</span>
      {{ goal || '未提供目标说明' }}
    </p>

    <el-alert
      v-if="planFallback"
      :type="fallbackAlertType"
      :closable="false"
      show-icon
      :title="fallbackNote"
    />

    <el-alert
      v-if="degraded"
      type="warning"
      :closable="false"
      show-icon
      :title="`上游限流，已降级为 ${effectiveParallel || 2} 个并发；未开始的子任务会排队执行。`"
    />
    <p
      v-else-if="maxParallel > 0"
      class="parallel-note"
    >
      计划最多 {{ maxParallel }} 个助手并行（可在设置中调整）
    </p>

    <ul
      v-if="viewRows.length"
      class="subtask-list"
    >
      <li
        v-for="(task, index) in viewRows"
        :key="task.id"
        class="subtask"
        :class="`task-${task.status || 'pending'}`"
      >
        <span class="seq">{{ index + 1 }}</span>

        <!-- 编辑态：标题 / 指令 / 期望产出 / 换助手 -->
        <div
          v-if="editing"
          class="task-main edit-main"
        >
          <el-input
            v-model="task.title"
            size="small"
            placeholder="子任务标题（留空则取指令前 20 字）"
            class="edit-title"
          />
          <el-input
            v-model="task.instruction"
            type="textarea"
            :rows="2"
            size="small"
            placeholder="指令（必填）：这一步要让助手具体做什么"
            :class="{ 'is-invalid': task.instruction.trim() === '' }"
          />
          <el-input
            v-model="task.expected_output"
            size="small"
            placeholder="期望产出（可选）"
          />
          <el-select
            :model-value="task.agent_id ?? ''"
            size="small"
            class="agent-select"
            placeholder="指派助手"
            :loading="agentsLoading"
            @change="onAgentChange(task, $event)"
          >
            <el-option
              label="指挥官代执行"
              value=""
            />
            <el-option
              v-for="agent in agentOptions"
              :key="agent.id"
              :label="`${agent.name} · ${agent.code}`"
              :value="agent.id"
            />
          </el-select>
          <span
            v-if="task.instruction.trim() === ''"
            class="row-error"
          >
            指令不能为空
          </span>
        </div>

        <!-- 只读态：沿用原有展示 -->
        <div
          v-else
          class="task-main"
        >
          <div class="task-title">
            {{ task.title || '未命名子任务' }}
          </div>
          <p
            v-if="task.instruction"
            class="task-instruction"
          >
            {{ task.instruction }}
          </p>
          <div class="task-meta">
            <span>指派助手：{{ task.agent_name || task.agent_code || '待指派' }}</span>
            <span v-if="task.depends_on.length">依赖：{{ dependsLabel(task.depends_on) }}</span>
          </div>
        </div>

        <el-tag
          v-if="!editing"
          size="small"
          :type="subtaskTagType(task.status)"
          class="task-tag"
        >
          {{ subtaskLabel(task.status) }}
        </el-tag>

        <!-- 仅在计划未开始时出现：上移 / 下移 / 编辑 / 删除 -->
        <div
          v-if="canEdit"
          class="row-actions"
        >
          <el-button
            size="small"
            text
            :disabled="editing ? index === 0 : false"
            @click="moveRow(index, -1)"
          >
            上移
          </el-button>
          <el-button
            size="small"
            text
            :disabled="editing ? index === viewRows.length - 1 : false"
            @click="moveRow(index, 1)"
          >
            下移
          </el-button>
          <el-button
            size="small"
            text
            :disabled="editing"
            @click="startEdit"
          >
            编辑
          </el-button>
          <el-button
            size="small"
            text
            type="danger"
            @click="removeRow(index)"
          >
            删除
          </el-button>
        </div>
      </li>
    </ul>
    <p
      v-else
      class="note"
    >
      计划里暂时没有可展示的子任务。
    </p>

    <p
      v-if="run?.error"
      class="inline-error"
      role="alert"
    >
      {{ run.error }}
    </p>

    <div
      v-if="canEdit"
      class="actions"
    >
      <template v-if="editing">
        <el-button
          size="small"
          type="primary"
          :loading="saving"
          @click="savePlan"
        >
          保存修改
        </el-button>
        <el-button
          size="small"
          :disabled="saving"
          @click="cancelEdit"
        >
          取消编辑
        </el-button>
        <span class="note">保存后按新顺序 1-based 位置重建依赖</span>
      </template>
      <template v-else>
        <el-button
          size="small"
          @click="startEdit"
        >
          编辑计划
        </el-button>
        <span class="note">可删任务、改指令、换助手、调先后；开始执行后不可再改</span>
      </template>
    </div>

    <div
      v-if="canStart"
      class="actions"
    >
      <el-button
        size="small"
        type="primary"
        :loading="run?.busy"
        @click="emit('execute', runId)"
      >
        开始执行
      </el-button>
      <span class="note">确认后再派发给助手，执行进度会实时显示</span>
    </div>

    <CommanderRunCard
      v-if="started && !hasSummary"
      :run="run"
      @retry="emit('retry', runId)"
      @cancel="emit('cancel', runId)"
    />
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import {
    commanderApi,
    type CommanderPlanPayload,
    type CommanderPlanSubtask,
    type CommanderPlanUpdateSubtask,
    type CommanderRun,
  } from '@/api/commander'
  import { declarativeApi } from '@/api/modules'
  import CommanderRunCard from '@/components/chat/CommanderRunCard.vue'
  import type { CommanderRunView } from '@/stores/chat'

  /**
   * 指挥官计划卡：目标 + 子任务（序号/标题/助手/依赖）。
   * 计划默认先预览，用户点「开始执行」才派发；执行开始后内嵌运行卡展示实时状态。
   * 未开始时可在卡内直接改计划（删任务 / 改指令 / 换助手 / 调先后），保存走 PUT .../plan。
   */
  const props = withDefaults(
    defineProps<{
      plan: CommanderPlanPayload
      run?: CommanderRunView | null
      /** 同一 run 已有汇总消息：运行卡由汇总消息承载，计划卡不再内嵌一份 */
      hasSummary?: boolean
      /** 帧/消息载荷未带 preview 时的兜底（来自设置 preview_plan） */
      defaultPreview?: boolean
    }>(),
    { run: null, hasSummary: false, defaultPreview: true },
  )

  const emit = defineEmits<{
    (event: 'execute', runId: string): void
    (event: 'retry', runId: string): void
    (event: 'cancel', runId: string): void
    /** 计划保存成功：带后端回包（或本地兜底），由面板写回 store */
    (event: 'plan-updated', runId: string, run: CommanderRun): void
  }>()

  const runId = computed(() => props.plan.run_id)

  const goal = computed(() => props.run?.goal || props.plan.goal || '')
  const planFallback = computed(() => props.run?.planFallback ?? props.plan.plan_fallback ?? false)
  /** 降级原因：clarify_disabled（模型想澄清但开关关闭）/ parse_failed / llm_error */
  const planFallbackReason = computed(
    () => props.run?.planFallbackReason || props.plan.plan_fallback_reason || '',
  )
  /**
   * 降级说明：这是「计划层退化成单任务」，不是任务失败——单任务照样把活干完，
   * 所以默认用中性 info；只有运行本身失败才回到警示色，避免和结果自相矛盾。
   */
  const fallbackNote = computed(() => {
    const reason = planFallbackReason.value
    if (reason === 'clarify_disabled') {
      return '目标里有信息没写全，已按最合理假设按单任务执行；结果不符合预期时可补充具体要求再来一次。'
    }
    if (reason === 'llm_error') {
      return '本轮规划模型暂时不可用，已改为单任务直接执行；交付结果不受影响。'
    }
    return '本轮计划没能拆成多步，已改为单任务直接执行；交付结果不受影响。'
  })
  const fallbackAlertType = computed<'warning' | 'info'>(() => {
    const status = props.run?.status ?? props.plan.status
    return status === 'failed' ? 'warning' : 'info'
  })
  /**
   * 后端当前的计划载荷没有 preview 字段（设置里是 preview_plan），
   * 因此优先级是：载荷 preview → 设置兜底；执行开始后由 run.started 关掉按钮。
   */
  const preview = computed(() => props.plan.preview ?? props.defaultPreview)
  const started = computed(() => {
    if (props.run) return props.run.started
    // 历史计划没有运行视图时，用载荷里的状态兜底判断是否已经开跑
    const status = props.plan.status
    return Boolean(status && status !== 'queued' && status !== 'preview')
  })
  const canStart = computed(() => !started.value && preview.value && !props.run?.busy)
  /** 编辑与「开始执行」同一门槛：已开始 / 已有汇总 / 请求在途一律只读 */
  const canEdit = computed(() => canStart.value && !props.hasSummary)
  const degraded = computed(() => props.run?.degraded ?? props.plan.degraded ?? false)
  const maxParallel = computed(() => props.run?.maxParallel || props.plan.max_parallel || 0)
  const effectiveParallel = computed(
    () => props.run?.effectiveParallel || props.plan.effective_parallel || 0,
  )

  /** 行模型：读态与编辑态共用，字段补全后模板不必再兜空 */
  interface PlanRow {
    id: string
    seq: number
    title: string
    instruction: string
    expected_output: string
    agent_id: string | null
    agent_name: string
    agent_code: string
    depends_on: string[]
    status: string
  }

  function toRow(item: CommanderPlanSubtask, index: number): PlanRow {
    return {
      id: item.id || `t${index + 1}`,
      seq: item.seq ?? index + 1,
      title: item.title ?? '',
      instruction: item.instruction ?? '',
      expected_output: item.expected_output ?? '',
      agent_id: item.agent_id === undefined ? null : item.agent_id,
      agent_name: item.agent_name ?? '',
      agent_code: item.agent_code ?? '',
      depends_on: (item.depends_on ?? []).map(String),
      status: item.status || 'pending',
    }
  }

  const rows = computed<PlanRow[]>(() => {
    const source = props.run?.subtasks?.length
      ? props.run.subtasks
      : props.plan.subtasks ?? []
    return source.map((item, index) => toRow(item, index))
  })

  /** 编辑草稿：进入编辑时从当前行克隆，取消即丢弃 */
  const editRows = ref<PlanRow[]>([])
  const editing = computed(() => editRows.value.length > 0)
  const saving = ref(false)

  const viewRows = computed<PlanRow[]>(() => (editing.value ? editRows.value : rows.value))

  /* ---------------- 换助手：本小院已发布助手 ---------------- */

  interface AgentOption {
    id: string
    name: string
    code: string
  }

  const agentOptions = ref<AgentOption[]>([])
  const agentsLoading = ref(false)
  let agentsLoaded = false

  /** 运行详情里的子任务带完整指令；计划帧/历史消息可能只有标题，编辑前补一次 */
  const detailRows = ref<PlanRow[] | null>(null)

  async function loadDetail(): Promise<void> {
    try {
      const run = await commanderApi.getRun(runId.value)
      if (run?.subtasks?.length) detailRows.value = run.subtasks.map(toRow)
    } catch {
      // 详情拉取失败不阻断：先用计划行兜底，保存时服务端仍会校验
    }
  }

  async function loadAgents(): Promise<void> {
    if (agentsLoaded || agentsLoading.value) return
    agentsLoading.value = true
    try {
      const list = await declarativeApi.list()
      agentOptions.value = list
        .filter(agent => agent.status === 'published')
        .map(agent => ({ id: agent.id, name: agent.name, code: agent.agent_code }))
      agentsLoaded = true
    } catch {
      // 取不到助手列表时仍保留「指挥官代执行」，不阻断计划编辑
      agentOptions.value = []
    } finally {
      agentsLoading.value = false
    }
  }

  /**
   * 行上没带 agent_id 时按 agent_code 反查已发布助手的 id；
   * 查不到就落到「指挥官代执行」（null），与后端 null 语义一致。
   */
  function resolveAgentId(row: PlanRow): string | null {
    if (row.agent_id) return row.agent_id
    if (!row.agent_code) return null
    return agentOptions.value.find(agent => agent.code === row.agent_code)?.id ?? null
  }

  function onAgentChange(row: PlanRow, value: unknown): void {
    const picked = String(value ?? '')
    if (!picked) {
      row.agent_id = null
      row.agent_name = '指挥官代执行'
      row.agent_code = ''
      return
    }
    const agent = agentOptions.value.find(item => item.id === picked)
    row.agent_id = picked
    row.agent_name = agent?.name ?? row.agent_name
    row.agent_code = agent?.code ?? row.agent_code
  }

  /* ---------------- 编辑动作 ---------------- */

  function startEdit(): void {
    if (!canEdit.value || editing.value) return
    const base = detailRows.value?.length ? detailRows.value : rows.value
    editRows.value = base.map(row => ({
      ...row,
      depends_on: [...row.depends_on],
    }))
    // 计划帧未必带 agent_id：助手列表到位后按 agent_code 反查，避免下拉把已指派行显示成「代执行」
    void loadAgents().then(() => {
      for (const row of editRows.value) {
        if (!row.agent_id) row.agent_id = resolveAgentId(row)
      }
    })
  }

  function cancelEdit(): void {
    if (saving.value) return
    editRows.value = []
  }

  /** 上移/下移等动作从只读态点进来时自动进入编辑（改动仍要「保存修改」才生效） */
  function ensureEditing(): PlanRow[] | null {
    if (!canEdit.value) return null
    if (!editing.value) startEdit()
    return editRows.value
  }

  function moveRow(index: number, delta: number): void {
    const list = ensureEditing()
    if (!list) return
    const target = index + delta
    if (target < 0 || target >= list.length) return
    const [row] = list.splice(index, 1)
    list.splice(target, 0, row)
  }

  function removeRow(index: number): void {
    const list = ensureEditing()
    if (!list) return
    if (list.length <= 1) {
      ElMessage.warning('至少要保留 1 个子任务')
      return
    }
    list.splice(index, 1)
  }

  /** 依赖展示：数字位置加 #，历史 id 原样 */
  function dependsLabel(list: string[]): string {
    return list.map(item => (/^\d+$/.test(item) ? `#${item}` : item)).join('、')
  }

  /** 本地兜底 run：后端回包缺 subtasks 时也能让卡片立刻展示保存后的顺序 */
  function runFromDraft(subtasks: CommanderPlanUpdateSubtask[]): CommanderRun {
    return {
      id: runId.value,
      goal: goal.value,
      status: props.run?.status ?? 'preview',
      subtasks: subtasks.map((item, index) => ({
        id: editRows.value[index]?.id || `t${index + 1}`,
        seq: index + 1,
        title: item.title,
        instruction: item.instruction,
        expected_output: item.expected_output,
        agent_id: item.agent_id,
        agent_name: editRows.value[index]?.agent_name ?? '',
        agent_code: editRows.value[index]?.agent_code ?? '',
        depends_on: item.depends_on.map(String),
        status: 'pending',
      })),
    }
  }

  async function savePlan(): Promise<void> {
    if (!canEdit.value || saving.value) return
    const draft = editRows.value
    if (!draft.length) {
      ElMessage.warning('至少要保留 1 个子任务')
      return
    }
    if (draft.some(row => !row.instruction.trim())) {
      ElMessage.warning('每个子任务都需要填写指令')
      return
    }
    // depends_on 按「新顺序的 1-based 位置」重建：旧依赖存的是 seq，先映射到行 id 再映射新位置
    const positionOf = new Map(draft.map((row, index) => [row.id, index + 1]))
    const oldSeqToId = new Map<number, string>()
    for (const row of draft) {
      if (typeof row.seq === 'number') oldSeqToId.set(row.seq, row.id)
    }
    const subtasks: CommanderPlanUpdateSubtask[] = draft.map(row => {
      const self = positionOf.get(row.id)
      return {
        title: row.title.trim() || row.instruction.trim().slice(0, 20),
        instruction: row.instruction.trim(),
        expected_output: row.expected_output.trim() || undefined,
        agent_id: row.agent_id ?? null,
        depends_on: row.depends_on
          .map(dep => oldSeqToId.get(Number(dep)))
          .map(id => (id ? positionOf.get(id) : undefined))
          .filter((pos): pos is number => typeof pos === 'number' && pos !== self),
      }
    })
    saving.value = true
    try {
      const run = await commanderApi.updatePlan(runId.value, subtasks)
      const applied = run?.subtasks?.length ? run : runFromDraft(subtasks)
      emit('plan-updated', runId.value, applied)
      editRows.value = []
      ElMessage.success('计划已更新')
    } catch {
      // 400/409 等统一由 client toast，保留编辑态让用户改完再存
    } finally {
      saving.value = false
    }
  }

  onMounted(() => {
    if (!canEdit.value) return
    void loadAgents()
    void loadDetail()
  })

  const helperCount = computed(() => {
    const names = new Set<string>()
    for (const row of viewRows.value) {
      const name = (row.agent_name || row.agent_code || '').trim()
      if (name) names.add(name)
    }
    return names.size
  })

  const SUBTASK_LABELS: Record<string, string> = {
    pending: '等待',
    running: '进行中',
    ok: '完成',
    error: '失败',
    skipped: '跳过',
  }

  function subtaskLabel(value?: string): string {
    return SUBTASK_LABELS[value || 'pending'] ?? value ?? '等待'
  }

  function subtaskTagType(value?: string): 'info' | 'primary' | 'success' | 'danger' | 'warning' {
    if (value === 'running') return 'primary'
    if (value === 'ok') return 'success'
    if (value === 'error') return 'danger'
    if (value === 'skipped') return 'warning'
    return 'info'
  }

  const stateLabel = computed(() => {
    if (!started.value) return '待确认'
    const map: Record<string, string> = {
      queued: '排队中',
      running: '执行中',
      completed: '已完成',
      partial: '部分完成',
      failed: '失败',
      cancelled: '已取消',
    }
    return map[props.run?.status ?? 'running'] ?? props.run?.status ?? '执行中'
  })

  const stateTagType = computed<'primary' | 'success' | 'warning' | 'danger' | 'info'>(() => {
    if (!started.value) return 'primary'
    const status = props.run?.status ?? 'running'
    if (status === 'completed') return 'success'
    if (status === 'partial') return 'warning'
    if (status === 'failed') return 'danger'
    if (status === 'cancelled') return 'info'
    return 'primary'
  })
</script>

<style scoped>
  .commander-plan {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 12px 14px;
    border: 1px solid var(--ds-border);
    border-radius: 12px;
    background: var(--ds-glass);
  }

  .head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .kind {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .counts {
    margin-left: auto;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }

  .goal {
    margin: 0;
    font-size: 13px;
    line-height: 1.7;
    color: var(--ds-text-1);
    word-break: break-word;
  }
  .goal-label {
    display: inline-block;
    margin-right: 6px;
    padding: 0 6px;
    border-radius: 5px;
    font-size: 11px;
    line-height: 18px;
    color: var(--ds-text-3);
    background: var(--ds-field);
    border: 1px solid var(--ds-border);
  }
  .parallel-note {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }

  .subtask-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .subtask {
    display: flex;
    align-items: flex-start;
    flex-wrap: wrap;
    gap: 10px;
    padding: 8px 10px;
    border: 1px solid var(--ds-border);
    border-radius: 9px;
    background: var(--ds-field);
  }
  .subtask.task-running {
    border-color: color-mix(in srgb, var(--ds-accent) 45%, transparent);
  }
  .subtask.task-error {
    border-color: color-mix(in srgb, var(--ds-danger) 45%, transparent);
  }
  .seq {
    flex: none;
    width: 22px;
    height: 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    font-size: 11px;
    color: var(--ds-text-2);
    background: var(--ds-glass-strong);
    border: 1px solid var(--ds-border);
    font-variant-numeric: tabular-nums;
  }
  .task-main {
    flex: 1;
    min-width: 0;
  }
  .task-title {
    font-size: 13px;
    color: var(--ds-text-1);
    word-break: break-word;
  }
  /* 计划帧通常不带指令；编辑保存后回包会带上，这里展示便于核对改了什么 */
  .task-instruction {
    margin: 3px 0 0;
    font-size: 12px;
    line-height: 1.65;
    color: var(--ds-text-2);
    word-break: break-word;
  }
  .task-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    margin-top: 3px;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
  .task-tag {
    flex: none;
  }

  /* 编辑态：纵向排布，助手下拉独占一行，避免挤压标题 */
  .edit-main {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .edit-title {
    max-width: 420px;
  }
  .edit-main :deep(.el-textarea.is-invalid .el-textarea__inner) {
    border-color: var(--ds-danger);
  }
  .agent-select {
    width: 240px;
    max-width: 100%;
  }
  .row-error {
    font-size: 11.5px;
    color: var(--ds-danger);
  }

  .row-actions {
    flex: none;
    display: flex;
    align-items: center;
    gap: 2px;
  }
  .row-actions :deep(.el-button) {
    margin-left: 0;
    padding: 2px 6px;
  }

  .actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }
  .note {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .inline-error {
    margin: 0;
    font-size: 12px;
    color: var(--ds-danger);
  }
</style>
