<template>
  <el-dialog
    v-model="visible"
    title="一句话创建技能"
    width="min(720px, 94vw)"
    :close-on-click-modal="!busy"
    :close-on-press-escape="!busy"
    destroy-on-close
    class="skill-wizard"
  >
    <!-- 第 1 步：描述需求 -->
    <section
      v-if="stage === 'input' || stage === 'requesting'"
      class="block"
    >
      <p class="hint">
        用一句话说明你要的技能，例如「我需要一个病虫害防治的 skill」。
        系统会先检索已有技能，没有合适的再按模板生成一个私有草稿。
      </p>
      <el-input
        v-model="utterance"
        type="textarea"
        :rows="3"
        maxlength="500"
        show-word-limit
        placeholder="我需要一个病虫害防治的 skill"
        aria-label="一句话描述需要的技能"
        @keydown.enter.meta.prevent="start"
        @keydown.enter.ctrl.prevent="start"
      />
      <div class="actions">
        <el-button @click="visible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="stage === 'requesting'"
          :disabled="!utterance.trim()"
          @click="start"
        >
          检索候选
        </el-button>
      </div>
    </section>

    <!-- 第 2 步：候选列表（可安装，或直接生成） -->
    <section
      v-else-if="stage === 'matched'"
      class="block"
    >
      <div class="row-between">
        <span class="utterance">「{{ utterance }}」</span>
        <span class="muted">找到 {{ matches.length }} 个候选</span>
      </div>
      <p
        v-if="requestNote"
        class="note"
      >
        {{ requestNote }}
      </p>
      <p
        v-if="offline"
        class="note warn"
      >
        当前离线，仅展示本地已缓存的候选。
      </p>

      <ul class="match-list">
        <li
          v-for="m in matches"
          :key="m.item_key"
          class="match-item"
        >
          <div class="match-main">
            <div class="match-name">
              {{ m.name }}
            </div>
            <p class="match-summary">
              {{ m.summary || '暂无摘要' }}
            </p>
            <div class="match-meta">
              <span>来源：{{ sourceLabel(m.source) }}</span>
              <span>许可：{{ m.license || '未声明' }}</span>
              <span>
                风险：
                <el-tag
                  size="small"
                  :type="riskTagType(m.risk_level)"
                  effect="plain"
                >
                  {{ riskLabel(m.risk_level) }}
                </el-tag>
              </span>
              <span v-if="typeof m.score === 'number'">匹配度：{{ Math.round(m.score * 100) }}%</span>
            </div>
          </div>
          <el-button
            size="small"
            type="primary"
            :loading="installingKey === m.item_key"
            :disabled="m.installed"
            @click="installMatch(m)"
          >
            {{ m.installed ? '已安装' : '安装' }}
          </el-button>
        </li>
      </ul>

      <p
        v-if="installNote"
        class="note"
      >
        {{ installNote }}
      </p>

      <div class="actions">
        <el-button @click="reset">
          重新描述
        </el-button>
        <el-button
          type="primary"
          plain
          @click="startGenerate"
        >
          直接生成新技能
        </el-button>
      </div>
    </section>

    <!-- 第 3 步：生成中（检索 → 生成 → 校验） -->
    <section
      v-else-if="stage === 'generating'"
      class="block"
      aria-live="polite"
    >
      <ul class="stage-list">
        <li
          v-for="row in stageRows"
          :key="row.key"
          class="stage-row"
          :class="row.state"
        >
          <el-icon class="stage-icon">
            <Loading
              v-if="row.state === 'running'"
              class="is-loading"
            />
            <CircleCheck v-else-if="row.state === 'done'" />
            <CircleClose v-else-if="row.state === 'failed'" />
            <Clock v-else />
          </el-icon>
          <span class="stage-label">{{ row.label }}</span>
          <span class="stage-state">{{ STAGE_STATE_TEXT[row.state] }}</span>
        </li>
      </ul>
      <p class="note">
        正在联网检索领域知识并按技能模板生成私有草稿，失败会给出原因且不落库。
      </p>
    </section>

    <!-- 第 4 步：生成结果 -->
    <section
      v-else-if="stage === 'created' && generated"
      class="block"
    >
      <div class="result-card">
        <header class="result-head">
          <div>
            <h4 class="result-name">
              {{ generated.skill.name }}
            </h4>
            <p class="result-code">
              {{ generated.skill.code }} · v{{ generated.skill.version }}
            </p>
          </div>
          <div class="badges">
            <el-tag
              size="small"
              type="info"
              effect="plain"
            >
              私有
            </el-tag>
            <el-tag
              size="small"
              :type="skillStatusTagType(generated.skill.status)"
              effect="plain"
            >
              {{ skillStatusLabel(generated.skill.status) }}
            </el-tag>
            <el-tag
              v-if="generatedRisk"
              size="small"
              :type="riskTagType(generatedRisk)"
              effect="plain"
            >
              {{ riskLabel(generatedRisk) }}
            </el-tag>
          </div>
        </header>

        <p class="note">
          已生成私有草稿，仅你和小院负责人可见。绑定到助手后即可在对话中调用。
        </p>
        <p
          v-if="generated.note"
          class="note"
        >
          {{ generated.note }}
        </p>

        <el-collapse
          v-if="generated.sources?.length"
          class="sources"
        >
          <el-collapse-item
            :title="`引用来源（${generated.sources.length}）`"
            name="sources"
          >
            <ul class="source-list">
              <li
                v-for="(s, i) in generated.sources"
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
        <p
          v-else
          class="note"
        >
          本次生成无外部引用来源（未联网或未检索到可用资料）。
        </p>

        <p
          v-if="submitNote"
          class="note"
        >
          {{ submitNote }}
        </p>

        <div class="actions">
          <el-button @click="visible = false">
            完成
          </el-button>
          <el-button
            type="primary"
            plain
            :loading="submitting"
            :disabled="submitted || generated.skill.status === 'pending_review'"
            @click="submitForReview"
          >
            {{ submitted || generated.skill.status === 'pending_review' ? '已提交审核' : '提交审核' }}
          </el-button>
          <el-button
            type="primary"
            @click="goBind"
          >
            去绑定助手
          </el-button>
        </div>
      </div>
    </section>

    <!-- 失败：只展示后端 note / 本地原因，不弹全局错误 -->
    <section
      v-else
      class="block"
    >
      <el-alert
        type="error"
        show-icon
        :closable="false"
        title="没有生成成功"
        :description="failureNote"
      />
      <div class="actions">
        <el-button @click="reset">
          重新描述
        </el-button>
        <el-button
          type="primary"
          @click="retryFromFailure"
        >
          重试
        </el-button>
      </div>
    </section>
  </el-dialog>
</template>

<script setup lang="ts">
  import { computed, ref, watch } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage } from 'element-plus'
  import { CircleCheck, CircleClose, Clock, Loading } from '@element-plus/icons-vue'
  import {
    skillApi,
    skillApiErrorText,
    skillLabApi,
    type SkillAgentGenerateResult,
    type SkillMatch,
  } from '@/api/v3'
  import { riskLabel, riskTagType, sourceLabel } from './libraryMeta'
  import { skillStatusLabel, skillStatusTagType } from './skillMeta'

  /**
   * 一句话创建技能向导：检索候选 → 安装或直接生成 → 生成结果。
   *
   * 全部请求走静默（不弹全局 toast）：接口未就绪（404）时在向导内展示原因，
   * 用户可以重试或改走「上传 Skill 文件」，不影响页面其它功能。
   */
  const props = defineProps<{
    modelValue: boolean
  }>()

  const emit = defineEmits<{
    (e: 'update:modelValue', value: boolean): void
    /** 生成成功：父级据此刷新「我的」列表 */
    (e: 'created', code: string): void
  }>()

  type Stage = 'input' | 'requesting' | 'matched' | 'generating' | 'created' | 'failed'
  type StepState = 'pending' | 'running' | 'done' | 'failed'

  const router = useRouter()

  const stage = ref<Stage>('input')
  const utterance = ref('')
  const matches = ref<SkillMatch[]>([])
  const requestNote = ref('')
  const offline = ref(false)
  const generated = ref<SkillAgentGenerateResult | null>(null)
  const failureNote = ref('')
  const installNote = ref('')
  const submitNote = ref('')
  const installingKey = ref('')
  const submitting = ref(false)
  const submitted = ref(false)
  /** 失败发生在哪一步：决定「重试」是重新检索还是重新生成 */
  const failureFrom = ref<'request' | 'generate'>('request')

  const steps = ref<Record<'retrieve' | 'generate' | 'validate', StepState>>({
    retrieve: 'pending',
    generate: 'pending',
    validate: 'pending',
  })

  let timers: number[] = []

  const STAGE_STATE_TEXT: Record<StepState, string> = {
    pending: '等待中',
    running: '进行中…',
    done: '已完成',
    failed: '失败',
  }

  const visible = computed({
    get: () => props.modelValue,
    set: (value: boolean) => emit('update:modelValue', value),
  })

  const busy = computed(() => stage.value === 'requesting' || stage.value === 'generating')

  const stageRows = computed(() => [
    { key: 'retrieve', label: '检索本地 / GitHub / Gitee 已有技能', state: steps.value.retrieve },
    { key: 'generate', label: '按技能模板生成定义', state: steps.value.generate },
    { key: 'validate', label: '校验工具白名单与风险等级', state: steps.value.validate },
  ])

  const generatedRisk = computed(() => {
    const spec = generated.value?.spec
    if (!spec) return ''
    const guardrails =
      typeof spec.guardrails === 'object' && spec.guardrails !== null
        ? (spec.guardrails as Record<string, unknown>)
        : {}
    const risk = guardrails.risk_level ?? spec.risk_level ?? spec.risk
    return typeof risk === 'string' ? risk : ''
  })

  function clearTimers() {
    timers.forEach(id => window.clearTimeout(id))
    timers = []
  }

  function resetSteps() {
    steps.value = { retrieve: 'pending', generate: 'pending', validate: 'pending' }
  }

  function reset() {
    clearTimers()
    stage.value = 'input'
    matches.value = []
    requestNote.value = ''
    offline.value = false
    generated.value = null
    failureNote.value = ''
    installNote.value = ''
    submitNote.value = ''
    installingKey.value = ''
    submitting.value = false
    submitted.value = false
    failureFrom.value = 'request'
    resetSteps()
  }

  /** 打开向导时清空上一轮结果（保留输入文本以便微调后重试） */
  watch(
    () => props.modelValue,
    open => {
      if (!open) {
        clearTimers()
        return
      }
      reset()
      utterance.value = ''
    },
  )

  async function start() {
    const text = utterance.value.trim()
    if (!text || busy.value) return
    reset()
    utterance.value = text
    stage.value = 'requesting'
    try {
      const res = await skillLabApi.request(text)
      matches.value = res.matches ?? []
      requestNote.value = res.note ?? ''
      offline.value = res.offline === true
      stage.value = 'matched'
      // 没有候选：不必让用户再点一次，直接进入生成
      if (!matches.value.length) {
        requestNote.value = requestNote.value || '没有找到可安装的现成技能，正在按你的描述生成。'
        await startGenerate()
      }
    } catch (e) {
      failureFrom.value = 'request'
      failureNote.value = skillApiErrorText(e, '技能检索')
      stage.value = 'failed'
    }
  }

  async function startGenerate() {
    if (busy.value) return
    clearTimers()
    stage.value = 'generating'
    steps.value = { retrieve: 'done', generate: 'running', validate: 'pending' }
    timers.push(
      window.setTimeout(() => {
        steps.value = { retrieve: 'done', generate: 'done', validate: 'running' }
      }, 1800),
    )
    try {
      const res = await skillLabApi.generate(utterance.value.trim(), true)
      steps.value = { retrieve: 'done', generate: 'done', validate: 'done' }
      generated.value = res
      stage.value = 'created'
      ElMessage.success('已生成私有技能草稿')
      emit('created', res.skill.code)
    } catch (e) {
      // 把当前进行中的步骤标红，如实告诉用户卡在哪一步
      const next = { ...steps.value }
      if (next.generate === 'running') next.generate = 'failed'
      if (next.validate === 'running') next.validate = 'failed'
      steps.value = next
      failureFrom.value = 'generate'
      failureNote.value = skillApiErrorText(e, '技能生成')
      stage.value = 'failed'
    } finally {
      clearTimers()
    }
  }

  function retryFromFailure() {
    failureNote.value = ''
    if (failureFrom.value === 'generate') {
      void startGenerate()
      return
    }
    void start()
  }

  async function installMatch(match: SkillMatch) {
    if (installingKey.value || !match.item_key) return
    installingKey.value = match.item_key
    installNote.value = ''
    try {
      const res = await skillLabApi.install({ item_key: match.item_key })
      const target = matches.value.find(m => m.item_key === match.item_key)
      if (target) target.installed = true
      const pending =
        res.needs_review === true ||
        res.review_status === 'pending_review' ||
        res.skill_status === 'pending_review'
      installNote.value = pending
        ? `「${match.name}」已安装，等待专家审核后启用；通过后可在「我的智能体」编辑里绑定。`
        : `「${match.name}」已安装，可在「我的智能体」编辑里绑定使用。`
      ElMessage.success(installNote.value)
    } catch (e) {
      installNote.value = skillApiErrorText(e, '技能安装')
    } finally {
      installingKey.value = ''
    }
  }

  async function submitForReview() {
    const skill = generated.value?.skill
    if (!skill || submitting.value || submitted.value) return
    submitting.value = true
    submitNote.value = ''
    try {
      await skillApi.review(skill.id, 'submit_public', undefined, { skipErrorToast: true })
      submitted.value = true
      submitNote.value = '已提交审核，负责人通过后会转为小院公共，其他成员即可使用。'
      ElMessage.success('已提交审核')
    } catch (e) {
      submitNote.value = skillApiErrorText(e, '提交审核')
    } finally {
      submitting.value = false
    }
  }

  function goBind() {
    visible.value = false
    void router.push('/agents/custom')
  }
</script>

<style scoped>
  .block {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .hint {
    margin: 0;
    font-size: 13px;
    line-height: 1.7;
    color: var(--ds-text-2);
  }
  .row-between {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .utterance {
    font-size: 13px;
    color: var(--ds-text-1);
    word-break: break-word;
  }
  .muted {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .note {
    margin: 0;
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }
  .note.warn {
    color: var(--ds-warning);
  }
  .actions {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    flex-wrap: wrap;
  }
  .match-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
    max-height: 380px;
    overflow: auto;
  }
  .match-item {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    padding: 12px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    background: var(--ds-glass);
  }
  .match-main {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .match-name {
    font-size: 14px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .match-summary {
    margin: 0;
    font-size: 12.5px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }
  .match-meta {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 12px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .stage-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .stage-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
    color: var(--ds-text-3);
  }
  .stage-row.running {
    color: var(--ds-accent-text);
  }
  .stage-row.done {
    color: var(--ds-text-1);
  }
  .stage-row.failed {
    color: var(--ds-danger);
  }
  .stage-state {
    margin-left: auto;
    font-size: 12px;
  }
  .result-card {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 16px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card, 12px);
    background: var(--ds-glass);
  }
  .result-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .result-name {
    margin: 0;
    font-size: 15px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .result-code {
    margin: 4px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .badges {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }
  .source-list {
    margin: 0;
    padding-left: 18px;
    display: flex;
    flex-direction: column;
    gap: 4px;
    font-size: 12.5px;
  }
  .source-list a {
    color: var(--ds-accent-text);
    word-break: break-all;
  }
</style>
