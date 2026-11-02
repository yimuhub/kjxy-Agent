<template>
  <div class="settings-commander">
    <AsyncState
      :state="state"
      empty-text="暂无指挥官配置"
      @retry="loadAll"
    >
      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>指挥官 Agent</span>
            <el-button
              v-if="canManage"
              size="small"
              type="primary"
              :loading="saving"
              @click="save"
            >
              保存
            </el-button>
          </div>
        </template>

        <el-alert
          v-if="!canManage"
          type="info"
          :closable="false"
          show-icon
          title="仅小院管理员可修改指挥官设置。"
          class="hint"
        />

        <!-- 运行概况：只读观测数据，加载失败静默占位，不打断设置编辑 -->
        <div class="stats">
          <div class="stats-head">
            <span class="stats-title">运行概况</span>
            <span class="stats-range">近 30 天</span>
            <el-button
              class="stats-refresh"
              link
              size="small"
              :loading="statsState === 'loading'"
              @click="loadStats"
            >
              刷新
            </el-button>
          </div>
          <p
            v-if="statsState === 'loading'"
            class="stats-hint"
          >
            正在读取运行数据…
          </p>
          <p
            v-else-if="statsState === 'error'"
            class="stats-hint"
          >
            运行概况暂时不可用，稍后刷新即可
          </p>
          <p
            v-else-if="statsState === 'empty'"
            class="stats-hint"
          >
            暂无运行数据
          </p>
          <div
            v-else
            class="stats-grid"
          >
            <div
              v-for="item in statItems"
              :key="item.label"
              class="stat"
            >
              <span class="stat-value">{{ item.value }}</span>
              <span class="stat-label">{{ item.label }}</span>
            </div>
          </div>
        </div>

        <el-form
          label-width="180px"
          class="settings-form"
        >
          <el-form-item label="启用指挥官">
            <el-switch
              v-model="cfg.enabled"
              :disabled="!canManage"
            />
            <span class="note">关闭后对话页不再提供「指挥官」模式切换</span>
          </el-form-item>

          <el-form-item label="显示名称">
            <el-input
              v-model="cfg.display_name"
              maxlength="40"
              show-word-limit
              :disabled="!canManage"
              style="width: 260px"
            />
          </el-form-item>

          <el-form-item label="头像">
            <el-input
              v-model="cfg.emoji"
              maxlength="8"
              :disabled="!canManage"
              placeholder="🧭"
              style="width: 120px"
            />
            <span class="note">填一个 emoji 作为对话页头像</span>
          </el-form-item>

          <el-form-item label="新会话默认进入指挥官">
            <el-switch
              v-model="defaultCommander"
              :disabled="!canManage"
            />
            <span class="note">只影响新建会话的默认模式，单条消息仍可随时切换</span>
          </el-form-item>

          <el-form-item label="同时执行 Agent 数">
            <el-radio-group
              v-model="parallelChoice"
              :disabled="!canManage"
              @change="onParallelChoice"
            >
              <el-radio-button
                v-for="opt in PARALLEL_OPTIONS"
                :key="opt"
                :value="opt"
              >
                {{ opt === 'custom' ? '自定义' : opt }}
              </el-radio-button>
            </el-radio-group>
            <el-input-number
              v-if="parallelChoice === 'custom'"
              v-model="cfg.max_parallel"
              :min="1"
              :max="10"
              :disabled="!canManage"
              class="num-inline"
            />
            <span class="note">默认 3；自定义范围 1–10</span>
          </el-form-item>

          <el-form-item label="上游异常兜底并发">
            <el-input-number
              v-model="cfg.fallback_parallel"
              :min="1"
              :max="10"
              :disabled="!canManage"
            />
            <span class="note">遇到限流/超时自动降级后的并发，默认 2</span>
          </el-form-item>

          <el-form-item label="单轮子任务上限">
            <el-input-number
              v-model="cfg.max_subtasks"
              :min="2"
              :max="12"
              :disabled="!canManage"
            />
            <span class="note">2–12，默认 6；控制单轮成本</span>
          </el-form-item>

          <el-form-item label="单子任务超时">
            <el-input-number
              v-model="cfg.subtask_timeout_seconds"
              :min="30"
              :max="600"
              :step="10"
              :disabled="!canManage"
            />
            <span class="note">秒，默认 180（上限 600）</span>
          </el-form-item>

          <el-form-item label="单轮总超时">
            <el-input-number
              v-model="cfg.run_timeout_seconds"
              :min="60"
              :max="1800"
              :step="30"
              :disabled="!canManage"
            />
            <span class="note">秒，默认 600（上限 1800）；超时返回已完成部分</span>
          </el-form-item>

          <el-form-item label="先预览计划再执行">
            <el-switch
              v-model="cfg.preview_plan"
              :disabled="!canManage"
            />
            <span class="note">默认关闭：计划生成即自动开始执行；开启后需先确认计划卡</span>
          </el-form-item>

          <el-form-item label="目标不明确时先问清">
            <el-switch
              v-model="cfg.clarify_enabled"
              :disabled="!canManage"
            />
            <span class="note">默认关闭：先按最合理假设开跑；开启后信息不足会先问 1–2 个问题</span>
          </el-form-item>

          <el-form-item label="允许自动创建助手">
            <el-switch
              v-model="cfg.auto_create_agent"
              :disabled="!canManage"
            />
            <span class="note">没有合适助手时按子任务目标自动生成草稿</span>
          </el-form-item>

          <el-form-item label="每天自动创建助手上限">
            <el-input-number
              v-model="cfg.daily_agent_limit"
              :min="1"
              :max="50"
              :disabled="!canManage"
            />
            <span class="note">1–50，默认 10；超出后回退到最接近的已有助手</span>
          </el-form-item>

          <el-form-item label="失败后自主补救">
            <el-switch
              v-model="cfg.auto_remediate"
              :disabled="!canManage"
            />
            <span class="note">
              子任务失败时自动重试、换助手、新建助手，最后指挥官亲自调工具执行；全程不打扰你
            </span>
          </el-form-item>

          <el-form-item label="最多自主尝试轮数">
            <el-input-number
              v-model="cfg.remediate_rounds"
              :min="1"
              :max="10"
              :disabled="!canManage || !cfg.auto_remediate"
            />
            <span class="note">1–10，默认 5；轮尽仍失败才报告能力缺口与具体原因</span>
          </el-form-item>

          <el-form-item label="自动发布新助手">
            <el-switch
              v-model="cfg.auto_publish_agent"
              :disabled="!canManage"
              @change="onAutoPublishChange"
            />
            <span class="note">保护型开关：开启后新助手会自动发布到智能体库，需负责人确认</span>
          </el-form-item>

          <el-form-item label="汇总质检（critic）">
            <el-switch
              v-model="cfg.critic_enabled"
              :disabled="!canManage"
            />
            <span class="note">对最终汇总做一次结构/事实检查，会增加少量 token 消耗</span>
          </el-form-item>

          <el-form-item label="单轮 Token 预算">
            <el-input-number
              v-model="cfg.token_budget"
              :min="1000"
              :max="2000000"
              :step="10000"
              :disabled="!canManage"
            />
            <span class="note">默认 120000；超出后停止新子任务并汇总已有结果</span>
          </el-form-item>

          <el-divider content-position="left">
            测评与进化
          </el-divider>

          <el-form-item label="当前人格">
            <el-radio-group
              v-model="cfg.persona"
              :disabled="!canManage"
            >
              <el-radio-button value="aggressive">
                进取型
              </el-radio-button>
              <el-radio-button value="balanced">
                平衡型
              </el-radio-button>
              <el-radio-button value="steady">
                稳健保守型
              </el-radio-button>
            </el-radio-group>
            <span class="note">周测评会给出建议；手动修改立即生效</span>
          </el-form-item>

          <el-form-item label="锁定人格">
            <el-switch
              v-model="cfg.persona_locked"
              :disabled="!canManage"
            />
            <span class="note">锁定后测评仍出报告，但不会自动改变人格标签</span>
          </el-form-item>

          <el-form-item label="进化锁">
            <el-switch
              v-model="cfg.evolution_lock"
              :disabled="!canManage"
            />
            <span class="note">
              一键暂停人格自动调整与后续自动进化；解锁后恢复
            </span>
          </el-form-item>

          <el-form-item label="每周自动测评">
            <el-switch
              v-model="cfg.evaluator_enabled"
              :disabled="!canManage"
            />
            <span class="note">每周一为最近有运行的小院生成一份监督测评报告</span>
          </el-form-item>

          <el-form-item label="测评可调整人格">
            <el-switch
              v-model="cfg.evaluator_auto_persona"
              :disabled="!canManage || cfg.persona_locked || cfg.evolution_lock"
            />
            <span class="note">关闭后只出报告，不自动切换人格；锁定人格或进化锁优先生效</span>
          </el-form-item>

          <el-form-item label="测评模型">
            <SupplierModelSelect
              :suppliers="suppliers"
              :modalities="TEXT_MODALITIES"
              allow-default
              value-mode="id"
              :supplier-id="cfg.evaluator_supplier_id || ''"
              :model-id="cfg.evaluator_supplier_model_id || ''"
              :disabled="!canManage"
              placeholder="跟随规划模型（点此指定）"
              @update:supplier-id="onEvaluatorSupplierPicked"
              @update:model-id="onEvaluatorModelPicked"
            />
            <span class="note block-note">
              测评独立计费，用途码为 commander_eval；留空则跟随规划/汇总模型
            </span>
          </el-form-item>

          <el-form-item label="规划/汇总模型">
            <SupplierModelSelect
              :suppliers="suppliers"
              :modalities="TEXT_MODALITIES"
              allow-default
              value-mode="id"
              :supplier-id="cfg.supplier_id || ''"
              :model-id="cfg.supplier_model_id || ''"
              :disabled="!canManage"
              placeholder="跟随模型中心（点此指定）"
              @update:supplier-id="onSupplierPicked"
              @update:model-id="onModelPicked"
            />
            <span class="note block-note">
              留空表示跟随模型中心的默认对话模型；只列已启用且模态匹配的模型
            </span>
          </el-form-item>

          <!-- 组件自带「大模型 / 技能与工具」两个小标题，这里不再加分隔标题 -->
          <el-form-item label-width="0">
            <CommanderAbilities
              :can-manage="canManage"
              class="abilities-block"
            />
          </el-form-item>

          <el-divider content-position="left">
            经验资产
          </el-divider>

          <el-form-item label="自动沉淀工作流">
            <el-switch
              v-model="cfg.auto_learn_workflows"
              :disabled="!canManage"
            />
            <span class="note">
              成功运行后提炼步骤模板；同类任务规划时优先复用
            </span>
          </el-form-item>

          <el-form-item label="记录失败黑名单">
            <el-switch
              v-model="cfg.auto_record_failures"
              :disabled="!canManage"
            />
            <span class="note">
              失败场景独立累计；关掉工作流沉淀也照样规避同一个坑
            </span>
          </el-form-item>

          <el-form-item label="自动萃取技能">
            <el-switch
              v-model="cfg.auto_extract_skills"
              :disabled="!canManage"
            />
            <span class="note">
              从 Worker 稳定成功的样本萃取技能；至少 2 个样本且成功率 ≥80% 才转正
            </span>
          </el-form-item>

          <el-form-item label="失败入黑名单阈值">
            <el-input-number
              v-model="cfg.failure_blacklist_threshold"
              :min="1"
              :max="10"
              :disabled="!canManage || !cfg.auto_record_failures"
            />
            <span class="note">同场景同错误累计达到该次数后，规划前提示规避方案</span>
          </el-form-item>

          <el-divider content-position="left">
            提示词模块
          </el-divider>

          <el-form-item label="执行风格模块">
            <el-checkbox-group
              v-model="cfg.prompt_modules"
              :disabled="!canManage"
            >
              <el-checkbox
                v-for="item in PROMPT_MODULE_OPTIONS"
                :key="item.value"
                :value="item.value"
              >
                {{ item.label }}
              </el-checkbox>
            </el-checkbox-group>
            <span class="note block-note">
              核心 JSON 契约与人格段始终生效；勾选模块按顺序追加，全不选只保留核心约束
            </span>
          </el-form-item>

          <el-form-item label="提示词字符预算">
            <el-input-number
              v-model="cfg.prompt_max_chars"
              :min="2000"
              :max="8000"
              :step="500"
              :disabled="!canManage"
            />
            <el-button
              class="num-inline"
              size="small"
              :loading="promptPreviewLoading"
              @click="previewPrompt"
            >
              预览提示词
            </el-button>
            <span class="note">超出预算的可选模块会被截断，避免提示词随记忆与人格一起膨胀</span>
          </el-form-item>
        </el-form>

        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="指挥官会把任务做完再交付：失败会自己换助手、新建助手或亲自调工具补救，轮尽才报告原因；你只需对成品提意见。"
        />
      </el-card>
    </AsyncState>

    <el-dialog
      v-model="promptPreviewVisible"
      title="规划系统提示词预览"
      width="760px"
      append-to-body
    >
      <div
        v-if="promptPreview"
        class="prompt-preview-meta"
      >
        <span>{{ promptPreview.chars }} / {{ promptPreview.max_chars }} 字符</span>
        <span>约 {{ promptPreview.tokens }} tokens</span>
        <span>
          模块：{{ promptPreview.modules.filter(item => item.enabled).map(item => item.title).join('、') || '仅核心' }}
        </span>
      </div>
      <pre class="prompt-preview-body">{{ promptPreview?.system || '' }}</pre>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import {
    commanderApi,
    emptyCommanderStats,
    normalizeCommanderSettings,
    type CommanderPromptModule,
    type CommanderSettings,
    type CommanderStats,
  } from '@/api/commander'
  import { llmSupplierApi, type LlmSupplier } from '@/api/modules'
  import {
    commanderWorkspaceApi,
    type CommanderPromptPreview,
  } from '@/api/commanderWorkspace'
  import AsyncState from '@/components/AsyncState.vue'
  import CommanderAbilities from '@/components/commander/CommanderAbilities.vue'
  import SupplierModelSelect from '@/components/settings/SupplierModelSelect.vue'
  import { useAuthStore } from '@/stores/auth'
  import { classifyError, type ViewState } from '@/utils/status'

  /** 指挥官设置（v3.38）：并发/开关/超时/预算 + 规划汇总模型绑定 */
  const auth = useAuthStore()
  const canManage = computed(() => auth.isYardAdmin())

  const state = ref<ViewState>('loading')
  const saving = ref(false)
  const suppliers = ref<LlmSupplier[]>([])
  const cfg = reactive<CommanderSettings>(normalizeCommanderSettings())

  const PROMPT_MODULE_OPTIONS: Array<{ value: CommanderPromptModule; label: string }> = [
    { value: 'delivery', label: '交付优先' },
    { value: 'cost', label: '成本纪律' },
    { value: 'concise', label: '简洁表达' },
    { value: 'risk', label: '风险预防' },
  ]
  const promptPreviewVisible = ref(false)
  const promptPreviewLoading = ref(false)
  const promptPreview = ref<CommanderPromptPreview | null>(null)

  /** 运行概况：独立状态，失败只在本区块显示占位文案 */
  const stats = ref<CommanderStats>(emptyCommanderStats())
  const statsState = ref<'loading' | 'ready' | 'empty' | 'error'>('loading')


  function formatRate(value: number): string {
    if (!Number.isFinite(value) || value <= 0) return '0%'
    return `${Math.round(Math.min(1, value) * 100)}%`
  }

  function formatDuration(ms: number): string {
    if (!Number.isFinite(ms) || ms <= 0) return '—'
    return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`
  }

  const statItems = computed(() => {
    const s = stats.value
    return [
      { label: '运行数', value: String(s.runs.total) },
      {
        label: '子任务成功率',
        value: s.subtasks.total > 0 ? formatRate(s.subtasks.success_rate) : '—',
      },
      { label: '计划成功率', value: formatRate(s.plan.success_rate) },
      { label: '平均并发', value: s.avg_effective_parallel.toFixed(1) },
      { label: '降级次数', value: String(s.degraded_runs) },
      { label: '自动创建助手数', value: String(s.auto_created_agents) },
      { label: 'P90 耗时', value: formatDuration(s.latency_ms.p90) },
    ]
  })

  async function loadStats(): Promise<void> {
    statsState.value = 'loading'
    try {
      const next = await commanderApi.getStats(30)
      stats.value = next
      statsState.value = next.runs.total > 0 ? 'ready' : 'empty'
    } catch {
      // 观测数据取不到不影响设置编辑：静默降级为占位文案
      statsState.value = 'error'
    }
  }

  /** 文本类能力只列 text/multimodal，避免把图像/视频模型绑进规划 */
  const TEXT_MODALITIES = ['text', 'multimodal']
  const PARALLEL_OPTIONS = ['3', '5', '8', '10', 'custom'] as const
  type ParallelChoice = (typeof PARALLEL_OPTIONS)[number]
  const parallelChoice = ref<ParallelChoice>('3')

  const defaultCommander = computed({
    get: () => cfg.default_mode,
    set: (value: boolean) => {
      cfg.default_mode = value
    },
  })

  function syncParallelChoice(): void {
    const text = String(cfg.max_parallel)
    parallelChoice.value = (PARALLEL_OPTIONS as readonly string[]).includes(text)
      ? (text as ParallelChoice)
      : 'custom'
  }

  function onParallelChoice(value: string | number | boolean | undefined): void {
    const text = String(value ?? '')
    if (text && text !== 'custom') cfg.max_parallel = Number(text)
  }

  function onSupplierPicked(value: string): void {
    cfg.supplier_id = value || null
  }

  function onModelPicked(value: string): void {
    cfg.supplier_model_id = value || null
  }

  function onEvaluatorSupplierPicked(value: string): void {
    cfg.evaluator_supplier_id = value || null
  }

  function onEvaluatorModelPicked(value: string): void {
    cfg.evaluator_supplier_model_id = value || null
  }

  async function loadSuppliers(): Promise<void> {
    try {
      suppliers.value = await llmSupplierApi.list()
    } catch {
      suppliers.value = []
    }
  }

  async function loadAll(): Promise<void> {
    state.value = 'loading'
    void loadStats()
    try {
      const [settings] = await Promise.all([commanderApi.getSettings(), loadSuppliers()])
      Object.assign(cfg, settings)
      syncParallelChoice()
      state.value = 'ready'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  /**
   * 自动发布是保护型开关：打开时先弹确认，用户在确认框点取消就恢复为关闭，
   * 避免「点了一下就已生效」却没看清风险。
   */
  async function onAutoPublishChange(value: string | number | boolean): Promise<void> {
    if (value !== true) return
    try {
      await ElMessageBox.confirm(
        '新助手会自动发布到智能体库，需负责人确认。开启后将跳过人工发布流程，请确认已评估风险。',
        '自动发布新助手',
        {
          type: 'warning',
          confirmButtonText: '确认开启',
          cancelButtonText: '保持关闭',
        },
      )
    } catch {
      cfg.auto_publish_agent = false
    }
  }

  async function save(): Promise<void> {
    if (!canManage.value) return
    saving.value = true
    try {
      const saved = await commanderApi.updateSettings({ ...cfg })
      Object.assign(cfg, saved)
      syncParallelChoice()
      ElMessage.success('指挥官设置已保存')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      saving.value = false
    }
  }

  /** 预览按已保存设置生成；提示改过模块后先保存再看效果 */
  async function previewPrompt(): Promise<void> {
    promptPreviewLoading.value = true
    try {
      promptPreview.value = await commanderWorkspaceApi.promptPreview()
      promptPreviewVisible.value = true
    } catch {
      ElMessage.error('提示词预览加载失败，请稍后重试')
    } finally {
      promptPreviewLoading.value = false
    }
  }

  onMounted(() => {
    void loadAll()
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
  .hint {
    margin-bottom: 12px;
  }
  .note {
    margin-left: 10px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .num-inline {
    margin-left: 10px;
  }
  .block-note {
    display: block;
    margin: 4px 0 0;
    line-height: 1.6;
  }

  /* 运行概况：只读面板，与表单用细分割线隔开 */
  .stats {
    margin-bottom: 14px;
    padding: 12px 14px;
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-field);
  }
  .stats-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .stats-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .stats-range {
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
  .stats-refresh {
    margin-left: auto;
  }
  .stats-hint {
    margin: 8px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .stats-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(118px, 1fr));
    gap: 8px 12px;
    margin-top: 10px;
  }
  .stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .stat-value {
    font-size: 16px;
    font-weight: 600;
    color: var(--ds-text-1);
    font-variant-numeric: tabular-nums;
  }
  .stat-label {
    font-size: 11.5px;
    color: var(--ds-text-3);
  }

  .prompt-preview-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 6px 16px;
    margin-bottom: 10px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .prompt-preview-body {
    margin: 0;
    max-height: 60vh;
    overflow: auto;
    padding: 12px 14px;
    border: 1px solid var(--ds-border);
    border-radius: 8px;
    background: var(--ds-field);
    font-family: var(--ds-font-mono, ui-monospace, monospace);
    font-size: 12px;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
  }
</style>
