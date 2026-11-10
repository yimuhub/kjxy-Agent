<template>
  <div
    class="abilities"
    :class="{ 'is-compact': compact }"
    data-commander-abilities
  >
    <div class="abilities-head">
      <h4>大模型</h4>
      <span class="abilities-note">
        {{
          modelLoadError
            ? '模型绑定加载失败，请重试'
            : modelFollowing
              ? '跟随模型中心的小院默认模型'
              : `已单独指定：${modelSummary}`
        }}
      </span>
    </div>
    <div class="model-row">
      <SupplierModelSelect
        v-model:supplier-id="modelSupplier"
        v-model:model-id="modelChoice"
        :suppliers="suppliers"
        :modalities="TEXT_MODALITIES"
        :disabled="!canManage || modelLoading"
        placeholder="给指挥官挑一个大模型"
        class="model-select"
        @change="onModelChoice"
      />
      <el-button
        size="small"
        type="primary"
        :disabled="!canManage || modelSaving || !modelDirty"
        :loading="modelSaving"
        @click="saveModel"
      >
        保存模型
      </el-button>
      <el-button
        size="small"
        link
        :disabled="!canManage || modelSaving || modelFollowing"
        @click="followModel"
      >
        跟随默认
      </el-button>
    </div>

    <div class="abilities-head">
      <h4>技能与工具</h4>
      <div class="identity-chips">
        <span class="identity-chip identity-chip--skill">
          技能 {{ identity.skill_count }}/{{ identity.available_skill_count }}
        </span>
        <span class="identity-chip identity-chip--tool">
          工具 {{ identity.tool_count }}/{{ identity.available_tool_count }}
        </span>
        <el-button
          size="small"
          type="primary"
          plain
          :disabled="!canManage || identityLoading"
          :loading="identitySaving"
          @click="attachAllIdentity"
        >
          全部挂上
        </el-button>
      </div>
    </div>
    <p class="abilities-note">
      指挥官自己就带联网搜索、知识检索、文档/表格/PPT/图片/视频生成、技能生成与自建助手；
      亲自执行时按子任务取用最相关的 3 项技能规范，其余技能只进目录，不会把提示词撑爆
    </p>

    <div class="picker-block">
      <span class="picker-label">技能清单</span>
      <BindableExtensionPicker
        v-model="boundDraft"
        kind="skill"
        :options="skillOptions"
        :stale-codes="staleSkillCodes"
        :error="extensionsError"
        :loading="extensionsLoading"
        empty-text="暂无可绑定的技能，可在「插件与技能库」安装或导入后重试"
        @retry="loadBindableExtensions"
      />
    </div>

    <div class="picker-block">
      <span class="picker-label">插件清单</span>
      <BindableExtensionPicker
        v-model="boundDraft"
        kind="plugin"
        :options="pluginOptions"
        :stale-codes="stalePluginCodes"
        :error="extensionsError"
        :loading="extensionsLoading"
        empty-text="暂无可绑定的工作流插件"
        @retry="loadBindableExtensions"
      />
    </div>

    <div class="picker-block">
      <div class="tool-toolbar">
        <span class="picker-label">工具清单</span>
        <el-input
          v-model="toolKeyword"
          size="small"
          clearable
          placeholder="按名称或说明过滤"
          class="tool-search"
        />
        <span class="tool-count">已选 {{ toolDraft.length }}</span>
        <el-button
          size="small"
          link
          type="primary"
          :disabled="!canManage || !filteredToolOptions.length"
          @click="selectAllTools"
        >
          全选
        </el-button>
        <el-button
          size="small"
          link
          :disabled="!canManage || !toolDraft.length"
          @click="toolDraft = []"
        >
          清空
        </el-button>
      </div>
      <div class="tool-list">
        <el-checkbox-group
          v-model="toolDraft"
          :disabled="!canManage"
        >
          <el-checkbox
            v-for="item in filteredToolOptions"
            :key="item.code"
            :value="item.code"
            class="tool-item"
          >
            {{ item.name }}
            <span class="tool-summary">{{ item.summary }}</span>
          </el-checkbox>
        </el-checkbox-group>
        <p
          v-if="!filteredToolOptions.length"
          class="tool-empty"
        >
          {{ toolOptions.length ? '没有匹配的工具，换个关键词试试' : '暂无可用工具' }}
        </p>
      </div>
    </div>

    <div class="abilities-foot">
      <el-button
        type="primary"
        size="small"
        :disabled="!canManage || !identityDirty"
        :loading="identitySaving"
        @click="saveIdentity"
      >
        保存技能与工具
      </el-button>
      <span
        v-if="identityDirty"
        class="abilities-note"
      >
        有未保存的调整
      </span>
      <span
        v-else-if="!canManage"
        class="abilities-note"
      >
        只有小院管理员能调整指挥官的能力
      </span>
      <span
        v-else-if="identity.status === 'absent'"
        class="abilities-note"
      >
        首次让指挥官干活时会自动建立它的身份档案
      </span>
    </div>
  </div>
</template>

<script setup lang="ts">
  /**
   * 指挥官能力面板（v3.52）：选大模型 + 挂技能/插件/工具。
   *
   * 系统设置「指挥官」页与页面悬浮伙伴共用这一份实现——同一个后端契约、同一套校验，
   * 避免两处各自演进又出现「设置页能改、伙伴里没有」的缺口。
   */
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import BindableExtensionPicker from '@/components/agents/BindableExtensionPicker.vue'
  import SupplierModelSelect from '@/components/settings/SupplierModelSelect.vue'
  import type { ModelChoice } from '@/composables/useSupplierModels'
  import { agentsApi, type BindablePlugin, type BindableSkill } from '@/api/v3'
  import { llmBindingApi, llmSupplierApi, type LlmSupplier } from '@/api/modules'
  import {
    commanderApi,
    emptyCommanderIdentity,
    type CommanderIdentity,
  } from '@/api/commander'

  /** 指挥官身份行的 agent_code：与后端 identity.COMMANDER_AGENT_CODE 同源 */
  const COMMANDER_AGENT_CODE = 'commander'
  const TEXT_MODALITIES = ['text']

  const props = withDefaults(
    defineProps<{
      canManage: boolean
      /** 伙伴面板里的窄版：清单收进滚动区，字号收紧 */
      compact?: boolean
    }>(),
    { compact: false },
  )

  /* ---------- 大模型（agent 层绑定） ---------- */
  const suppliers = ref<LlmSupplier[]>([])
  const modelSupplier = ref('')
  const modelChoice = ref('')
  /** 载入时的原值，用来判断是否真的改了 */
  const savedSupplier = ref('')
  const savedChoice = ref('')
  const modelLoading = ref(false)
  const modelSaving = ref(false)
  /** 提示语用人看得懂的名字，别把模型行 id 甩给用户 */
  const readableSupplierName = ref('')
  const readableModelName = ref('')

  const modelLoadError = ref(false)
  const modelFollowing = computed(
    () => !modelLoadError.value && !savedSupplier.value && !savedChoice.value,
  )

  const modelSummary = computed(() => {
    const name =
      suppliers.value.find(item => item.id === (modelSupplier.value || savedSupplier.value))
        ?.name ?? readableSupplierName.value
    const model = readableModelName.value
    if (name && model) return `${name} · ${model}`
    return model || name || '未指定'
  })

  function onModelChoice(choice: ModelChoice): void {
    readableSupplierName.value = choice.supplier_name
    readableModelName.value = choice.model_name || choice.supplier_model_id
  }

  const modelDirty = computed(
    () => modelSupplier.value !== savedSupplier.value || modelChoice.value !== savedChoice.value,
  )

  async function loadSuppliers(): Promise<void> {
    try {
      suppliers.value = await llmSupplierApi.list()
    } catch {
      suppliers.value = []
    }
  }

  async function loadModel(): Promise<void> {
    modelLoading.value = true
    modelLoadError.value = false
    try {
      const data = await llmBindingApi.list({
        scope_type: 'agent',
        scope_key: COMMANDER_AGENT_CODE,
      })
      const first = (data.items ?? [])[0]
      savedSupplier.value = first?.supplier_id ?? ''
      savedChoice.value = first?.supplier_model_id ?? first?.model_name ?? ''
      modelSupplier.value = savedSupplier.value
      modelChoice.value = savedChoice.value
      readableSupplierName.value = first?.supplier_name ?? ''
      readableModelName.value = first?.model_name ?? first?.model_key ?? savedChoice.value
    } catch {
      // 加载失败 ≠ 未绑定：保留 error 态，避免把「未知」误导成「跟随模型中心」
      modelLoadError.value = true
      savedSupplier.value = ''
      savedChoice.value = ''
    } finally {
      modelLoading.value = false
    }
  }

  async function saveModel(): Promise<void> {
    if (!props.canManage || !modelDirty.value) return
    modelSaving.value = true
    try {
      await llmBindingApi.replace({
        scope_type: 'agent',
        scope_key: COMMANDER_AGENT_CODE,
        entries: modelChoice.value
          ? [
              {
                supplier_id: modelSupplier.value,
                supplier_model_id: modelChoice.value,
              },
            ]
          : [],
      })
      // 回读一次：绑定值与提示里的模型名以后端为准
      await Promise.all([loadModel(), loadIdentity()])
      ElMessage.success('指挥官的大模型已更新，下一次调用即生效')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      modelSaving.value = false
    }
  }

  /** 清空 agent 层绑定 = 回到模型中心的小院默认 */
  async function followModel(): Promise<void> {
    if (!props.canManage || modelFollowing.value) return
    modelSaving.value = true
    try {
      await llmBindingApi.replace({
        scope_type: 'agent',
        scope_key: COMMANDER_AGENT_CODE,
        entries: [],
      })
      modelSupplier.value = ''
      modelChoice.value = ''
      savedSupplier.value = ''
      savedChoice.value = ''
      readableSupplierName.value = ''
      readableModelName.value = ''
      await loadIdentity()
      ElMessage.success('指挥官已改为跟随模型中心的默认模型')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      modelSaving.value = false
    }
  }

  /* ---------- 技能与工具挂载 ---------- */
  const identity = ref<CommanderIdentity>(emptyCommanderIdentity())
  /** bound_skills 存储契约：技能与插件共用一份数组（与助手编辑弹窗一致） */
  const boundDraft = ref<string[]>([])
  const toolDraft = ref<string[]>([])
  const toolKeyword = ref('')
  const bindableSkills = ref<BindableSkill[]>([])
  const bindablePlugins = ref<BindablePlugin[]>([])
  const identityLoading = ref(false)
  const identitySaving = ref(false)
  const extensionsLoading = ref(false)
  const extensionsError = ref(false)

  const skillOptions = computed(() =>
    bindableSkills.value.map(item => ({
      code: item.code,
      name: item.name || item.code,
      summary: item.summary || '',
      source: item.source,
      risk: item.risk_level,
    })),
  )

  const pluginOptions = computed(() =>
    bindablePlugins.value.map(item => ({
      code: item.code,
      name: item.name || item.code,
      summary: item.summary || '',
      source: item.source,
      risk: item.risk_level,
    })),
  )

  /** 已下架的历史绑定：候选集加载失败时不判失效，避免把全部技能标成灰色 */
  const staleSkillCodes = computed(() => {
    if (extensionsLoading.value || extensionsError.value) return []
    const known = new Set(skillOptions.value.map(item => item.code))
    return boundDraft.value.filter(code => code.startsWith('skill:') && !known.has(code))
  })

  const stalePluginCodes = computed(() => {
    if (extensionsLoading.value || extensionsError.value) return []
    const known = new Set(pluginOptions.value.map(item => item.code))
    return boundDraft.value.filter(code => code.startsWith('plugin:') && !known.has(code))
  })

  const toolOptions = computed(() =>
    identity.value.tools.map(item => ({
      code: item.code,
      name: item.name || item.code,
      summary: item.summary,
    })),
  )

  const filteredToolOptions = computed(() => {
    const keyword = toolKeyword.value.trim().toLowerCase()
    if (!keyword) return toolOptions.value
    return toolOptions.value.filter(
      item =>
        item.code.toLowerCase().includes(keyword) ||
        item.name.toLowerCase().includes(keyword) ||
        item.summary.toLowerCase().includes(keyword),
    )
  })

  /** 与后端已保存清单比对，决定「保存」是否可用 */
  const identityDirty = computed(() => {
    // 比较前排序：集合语义，勾选顺序不影响「有没有改」（FE-5）
    const savedSkills = identity.value.skills
      .map(item => item.code)
      .slice()
      .sort()
      .join(',')
    const savedTools = identity.value.tools
      .filter(item => item.bound)
      .map(item => item.code)
      .slice()
      .sort()
      .join(',')
    const draftSkills = boundDraft.value.slice().sort().join(',')
    const draftTools = toolDraft.value.slice().sort().join(',')
    return draftSkills !== savedSkills || draftTools !== savedTools
  })

  function applyIdentity(next: CommanderIdentity): void {
    identity.value = next
    boundDraft.value = next.skills.map(item => item.code)
    toolDraft.value = next.tools.filter(item => item.bound).map(item => item.code)
  }

  async function loadIdentity(): Promise<void> {
    identityLoading.value = true
    try {
      applyIdentity(await commanderApi.getIdentity())
    } catch {
      applyIdentity(emptyCommanderIdentity())
    } finally {
      identityLoading.value = false
    }
  }

  async function loadBindableExtensions(): Promise<void> {
    extensionsLoading.value = true
    extensionsError.value = false
    try {
      const data = await agentsApi.bindableExtensions()
      bindableSkills.value = data.skills ?? []
      bindablePlugins.value = data.plugins ?? []
    } catch {
      bindableSkills.value = []
      bindablePlugins.value = []
      extensionsError.value = true
    } finally {
      extensionsLoading.value = false
    }
  }

  function selectAllTools(): void {
    const known = new Set(toolDraft.value)
    toolDraft.value = [
      ...toolDraft.value,
      ...filteredToolOptions.value.map(item => item.code).filter(code => !known.has(code)),
    ]
  }

  async function saveIdentity(): Promise<void> {
    if (!props.canManage) return
    identitySaving.value = true
    try {
      applyIdentity(
        await commanderApi.updateIdentity({
          bound_skills: boundDraft.value,
          allowed_tools: toolDraft.value,
        }),
      )
      ElMessage.success('指挥官的技能与工具已保存')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      identitySaving.value = false
    }
  }

  async function attachAllIdentity(): Promise<void> {
    if (!props.canManage) return
    identitySaving.value = true
    try {
      applyIdentity(await commanderApi.attachAllIdentity())
      await loadBindableExtensions()
      ElMessage.success('已把小院当前的技能与工具全部挂给指挥官')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      identitySaving.value = false
    }
  }

  onMounted(() => {
    void loadSuppliers()
    void loadModel()
    void loadIdentity()
    void loadBindableExtensions()
  })

  /** 伙伴面板切进来时可能要重新拉一次最新档案 */
  defineExpose({ reload: loadIdentity, reloadModel: loadModel })
</script>

<style scoped>
  .abilities {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .abilities-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    flex-wrap: wrap;
  }

  .abilities-head h4 {
    margin: 0;
    font-size: 13px;
    color: var(--ds-text-1);
  }

  .abilities-note {
    margin: 0;
    font-size: 11.5px;
    line-height: 1.6;
    color: var(--ds-text-3);
  }

  .model-row {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .model-select {
    flex: 1;
    min-width: 220px;
  }

  .identity-chips {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .identity-chip {
    padding: 2px 9px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill, 999px);
    font-size: 11.5px;
    color: var(--ds-text-2);
  }

  .identity-chip--skill {
    border-color: var(--ds-accent-2);
    color: var(--ds-accent-text);
  }

  .identity-chip--tool {
    border-color: var(--ds-success);
    color: var(--ds-success);
  }

  .picker-block {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .picker-label {
    font-size: 12px;
    color: var(--ds-text-2);
  }

  .tool-toolbar {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }

  .tool-search {
    width: 180px;
  }

  .tool-count {
    font-size: 11.5px;
    color: var(--ds-text-3);
  }

  .tool-list {
    width: 100%;
    max-height: 236px;
    overflow-y: auto;
    padding: 10px 12px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control, 8px);
    background: var(--ds-field);
  }

  .tool-list :deep(.el-checkbox-group) {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
    gap: 2px 14px;
  }

  /* el-checkbox 默认锁 32px 高，多行说明必须放开高度，否则整列叠字 */
  .tool-item {
    height: auto;
    margin-right: 0;
    padding: 3px 0;
    align-items: flex-start;
  }

  .tool-item :deep(.el-checkbox__label) {
    white-space: normal;
    line-height: 1.5;
    font-size: 12.5px;
  }

  .tool-summary {
    display: block;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }

  .tool-empty {
    margin: 2px 0 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }

  .abilities-foot {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }

  /* 伙伴面板里的窄版：清单更矮，避免把对话区挤没 */
  .is-compact .tool-list {
    max-height: 150px;
  }

  .is-compact .tool-list :deep(.el-checkbox-group) {
    grid-template-columns: 1fr;
  }
</style>
