<template>
  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>智能体模型（{{ filtered.length }} / {{ items.length }}）</span>
        <div class="page-actions">
          <el-input
            v-model="keyword"
            placeholder="搜索助手名称或编码"
            clearable
            size="small"
            style="width: 220px"
          />
          <el-switch
            v-model="onlyCustom"
            size="small"
            active-text="只看已指定"
          />
          <el-button
            size="small"
            :loading="loading"
            @click="load"
          >
            刷新
          </el-button>
        </div>
      </div>
    </template>

    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="hint"
      title="主用模型直接下拉即改（按「供应商 → 模型」分组，只列已接入的模型）；「兜底」配主用失败后的备用顺序；「专属能力」给单个助手单独指定图像/视频模型。保存后下一次调用即生效。"
    />

    <el-table
      v-col-resize
      v-loading="loading"
      :data="filtered"
      stripe
      max-height="560"
      empty-text="暂无可用助手"
    >
      <el-table-column
        label="助手"
        min-width="190"
      >
        <template #default="{ row }">
          <div class="agent-cell">
            <span class="agent-name">{{ row.name }}</span>
            <code class="note">{{ row.code }}</code>
          </div>
        </template>
      </el-table-column>
      <el-table-column
        label="类型"
        width="96"
      >
        <template #default="{ row }">
          <el-tag
            size="small"
            effect="plain"
            :type="row.kind === 'yard' ? 'primary' : 'info'"
          >
            {{ row.kind === 'yard' ? '自建助手' : '平台助手' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        label="当前策略"
        width="120"
      >
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="row.mode === 'custom' ? 'success' : row.mode === 'legacy' ? 'warning' : 'info'"
            effect="plain"
          >
            {{ modeLabel(row.mode) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        label="主用模型（下拉即改）"
        min-width="290"
      >
        <template #default="{ row }">
          <div class="agent-model-cell">
            <SupplierModelSelect
              :suppliers="suppliers"
              :modalities="TEXT_MODALITIES"
              allow-default
              :supplier-id="row.bindings[0]?.supplier_id ?? ''"
              :model-id="row.bindings[0]?.supplier_model_id ?? ''"
              :fallback-label="headLabel(row as LlmAgentModelItem)"
              :placeholder="
                row.resolved
                  ? `跟随中心：${row.resolved.supplier_name} · ${row.resolved.model_name}`
                  : '跟随中心（点此指定）'
              "
              @change="(choice: ModelChoice) => savePrimary(row as LlmAgentModelItem, choice)"
            />
            <span
              v-if="row.bindings[0]?.available === false"
              class="note danger"
            >所选供应商不可用</span>
            <span
              v-else-if="row.mode === 'legacy' && row.legacy"
              class="note"
            >
              旧设置：{{ row.legacy.supplier_name }} ·
              {{ row.legacy.model_name || row.legacy.model_id }}
            </span>
          </div>
        </template>
      </el-table-column>
      <el-table-column
        label="专属能力"
        min-width="180"
      >
        <template #default="{ row }">
          <div class="cap-tags">
            <el-tag
              v-for="cap in capTags(row as LlmAgentModelItem)"
              :key="cap.purpose"
              size="small"
              type="success"
              effect="plain"
            >
              {{ cap.label }}：{{ cap.bindings[0].supplier_name
              }}{{ cap.bindings[0].model_name ? ` · ${cap.bindings[0].model_name}` : '' }}
            </el-tag>
            <span
              v-if="!hasCapability(row as LlmAgentModelItem)"
              class="note"
            >跟随全局</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column
        label="兜底"
        min-width="130"
      >
        <template #default="{ row }">
          <span
            v-if="row.bindings.length > 1"
            class="note"
          >{{
            fallbackNames(row as LlmAgentModelItem)
          }}</span>
          <span
            v-else
            class="note"
          >—</span>
        </template>
      </el-table-column>
      <el-table-column
        label="操作"
        width="250"
      >
        <template #default="{ row }">
          <el-button
            size="small"
            text
            type="primary"
            @click="openCapabilities(row as LlmAgentModelItem)"
          >
            专属能力
          </el-button>
          <el-button
            size="small"
            text
            @click="openFallback(row as LlmAgentModelItem)"
          >
            兜底
          </el-button>
          <el-button
            size="small"
            text
            :loading="testingCode === row.code"
            @click="testAgent(row as LlmAgentModelItem)"
          >
            试跑
          </el-button>
          <el-button
            v-if="row.mode !== 'follow'"
            size="small"
            text
            type="warning"
            @click="resetFollow(row as LlmAgentModelItem)"
          >
            回到跟随
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </el-card>

  <el-card class="section">
    <template #header>
      <div class="card-header">
        <span>视觉能力默认模型（{{ visual.length }}）</span>
        <span class="note">图像/视频不走对话模型，单独在这里配置；不配则海报自动降级模板、视频工具提示未接入。</span>
      </div>
    </template>
    <div class="visual-grid">
      <div
        v-for="item in visual"
        :key="item.purpose"
        class="visual-card"
      >
        <div class="visual-head">
          <strong>{{ item.label }}</strong>
          <el-tag
            size="small"
            :type="item.bindings.length ? 'success' : 'info'"
            effect="plain"
          >
            {{ item.bindings.length ? '已指定' : '未指定' }}
          </el-tag>
        </div>
        <SupplierModelSelect
          :suppliers="suppliers"
          :modalities="modalitiesForVisual(item.purpose)"
          :supplier-id="item.bindings[0]?.supplier_id ?? ''"
          :model-id="item.bindings[0]?.supplier_model_id ?? ''"
          :fallback-label="item.bindings[0]?.supplier_name ?? ''"
          :placeholder="item.purpose === 'image_generation' ? '选择图像模型' : '选择视频模型'"
          @change="(choice: ModelChoice) => saveVisual(item, choice)"
        />
        <div
          v-if="item.bindings.length"
          class="visual-body"
        >
          <div
            v-for="(binding, i) in item.bindings"
            :key="binding.id"
            class="visual-row"
          >
            <span>{{ i === 0 ? '主用' : `兜底 ${i}` }}：{{ binding.supplier_name }}</span>
            <code class="note">{{
              binding.model_name || binding.model_key || binding.model_id
            }}</code>
          </div>
        </div>
        <div
          v-else-if="item.resolved"
          class="note"
        >
          当前生效：{{ item.resolved.supplier_name }} · {{ item.resolved.model_name }}（{{
            item.resolved.level_label
          }}）
        </div>
        <div
          v-else
          class="note"
        >
          {{
            item.purpose === 'image_generation'
              ? '还没有可用的图像模型：到「供应商」里给已接入的供应商补一个模态为「图像」的模型，这里就能选到。'
              : '还没有可用的视频模型：在供应商模型里把模态设为「视频」后，这里即可下拉选择。'
          }}
        </div>
        <div class="visual-actions">
          <el-button
            v-if="item.bindings.length"
            size="small"
            text
            type="danger"
            @click="clearVisual(item)"
          >
            清除
          </el-button>
          <el-button
            size="small"
            text
            type="primary"
            @click="openVisual(item)"
          >
            配置兜底
          </el-button>
        </div>
      </div>
    </div>
  </el-card>

  <!-- 兜底顺序 -->
  <el-dialog
    v-model="dialogVisible"
    append-to-body
    :title="`绑定模型：${dialogTitle}`"
    width="min(760px, 94vw)"
  >
    <ModelBindings
      v-if="dialogVisible"
      :scope-type="dialogScopeType"
      :scope-key="dialogScopeKey"
      :title="dialogTitle"
      hint="留空并保存即取消该层的单独绑定，回落到下一档；保存会接管助手编辑页的旧设置。"
      :suppliers="dialogSuppliers"
      :modalities="dialogModalities"
      :max-fallback="maxFallback"
      @saved="onSaved"
    />
    <template #footer>
      <el-button @click="dialogVisible = false">
        关闭
      </el-button>
    </template>
  </el-dialog>

  <!-- 助手专属能力：单个助手单独指定图像/视频模型 -->
  <el-dialog
    v-model="capVisible"
    append-to-body
    :title="`专属能力：${capTitle}`"
    width="min(720px, 94vw)"
  >
    <el-alert
      type="info"
      :closable="false"
      show-icon
      class="hint"
      title="只影响这个助手：给它单独指定图像/视频模型，其他助手仍跟随「视觉能力默认模型」。下拉即保存。"
    />
    <div
      v-for="cap in capItems"
      :key="cap.purpose"
      class="cap-row"
    >
      <div class="cap-head">
        <strong>{{ cap.label }}</strong>
        <el-tag
          v-if="cap.bindings.length"
          size="small"
          type="success"
          effect="plain"
        >
          已指定
        </el-tag>
        <el-tag
          v-else
          size="small"
          type="info"
          effect="plain"
        >
          跟随全局
        </el-tag>
      </div>
      <div class="cap-picker">
        <SupplierModelSelect
          :suppliers="suppliers"
          :modalities="modalitiesForVisual(cap.purpose)"
          :supplier-id="cap.bindings[0]?.supplier_id ?? ''"
          :model-id="cap.bindings[0]?.supplier_model_id ?? ''"
          :placeholder="cap.purpose === 'image_generation' ? '选择图像模型' : '选择视频模型'"
          @change="(choice: ModelChoice) => saveCapability(cap.purpose, choice)"
        />
        <el-button
          v-if="cap.bindings.length"
          size="small"
          text
          type="danger"
          @click="clearCapability(cap.purpose)"
        >
          清除
        </el-button>
      </div>
    </div>
    <template #footer>
      <el-button @click="capVisible = false">
        关闭
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  /** 模型中心 → 智能体模型（v3.26，v3.37 全面改为下拉）：
   * 主用模型直接下拉改、「兜底」配顺序、「专属能力」给单个助手绑图像/视频模型。
   */
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { llmBindingApi } from '@/api/modules'
  import type {
    LlmAgentCapabilityItem,
    LlmAgentModelItem,
    LlmSupplier,
    LlmVisualModelItem,
  } from '@/api/modules'
  import ModelBindings from './ModelBindings.vue'
  import SupplierModelSelect from './SupplierModelSelect.vue'
  import type { ModelChoice } from '@/composables/useSupplierModels'
  import { modalitiesForVisual } from '@/utils/modelPurpose'

  const props = defineProps<{ suppliers: LlmSupplier[] }>()
  const emit = defineEmits<{ (event: 'changed'): void }>()

  const TEXT_MODALITIES = ['text']
  const VISUAL_PURPOSES = ['image_generation', 'video_generation']

  const items = ref<LlmAgentModelItem[]>([])
  const visual = ref<LlmVisualModelItem[]>([])
  const maxFallback = ref(3)
  const loading = ref(false)
  const testingCode = ref('')
  const keyword = ref('')
  const onlyCustom = ref(false)

  const dialogVisible = ref(false)
  const dialogScopeType = ref<'agent' | 'purpose'>('agent')
  const dialogScopeKey = ref('')
  const dialogTitle = ref('')
  const dialogSuppliers = ref<LlmSupplier[]>([])
  const dialogModalities = ref<string[]>([])

  const capVisible = ref(false)
  const capCode = ref('')
  const capTitle = ref('')

  const filtered = computed(() => {
    const word = keyword.value.trim().toLowerCase()
    return items.value.filter(item => {
      if (onlyCustom.value && item.mode === 'follow') return false
      if (!word) return true
      return item.name.toLowerCase().includes(word) || item.code.toLowerCase().includes(word)
    })
  })

  const capRow = computed(() => items.value.find(item => item.code === capCode.value))
  const capItems = computed<LlmAgentCapabilityItem[]>(() => {
    const found = capRow.value?.capabilities ?? []
    return VISUAL_PURPOSES.map(purpose => {
      const hit = found.find(cap => cap.purpose === purpose)
      return (
        hit ?? {
          purpose: purpose as LlmAgentCapabilityItem['purpose'],
          label: purpose === 'image_generation' ? '图像生成' : '视频生成',
          bindings: [],
        }
      )
    })
  })

  function headLabel(row: LlmAgentModelItem): string {
    const head = row.bindings[0]
    if (!head) return ''
    return `${head.supplier_name}${head.model_name ? ` · ${head.model_name}` : ''}`
  }

  function capTags(row: LlmAgentModelItem): LlmAgentCapabilityItem[] {
    return (row.capabilities ?? []).filter(cap => cap.bindings.length > 0)
  }

  function hasCapability(row: LlmAgentModelItem): boolean {
    return (row.capabilities ?? []).some(cap => cap.bindings.length > 0)
  }

  function fallbackNames(row: LlmAgentModelItem): string {
    return row.bindings
      .slice(1)
      .map(binding => binding.supplier_name)
      .join(' → ')
  }

  function modeLabel(mode: LlmAgentModelItem['mode']): string {
    if (mode === 'custom') return '指定模型'
    if (mode === 'legacy') return '旧设置（待接管）'
    return '跟随中心'
  }

  async function load(): Promise<void> {
    loading.value = true
    try {
      const res = await llmBindingApi.agents()
      items.value = res.items
      visual.value = res.visual
      maxFallback.value = res.max_fallback
    } catch {
      items.value = []
      visual.value = []
    } finally {
      loading.value = false
    }
  }

  /** 拖出兜底条目（保留顺序与模型行，priority 由后端按顺序重排） */
  function toEntries(bindings: LlmAgentModelItem['bindings']) {
    return bindings.map((item, index) => ({
      supplier_id: item.supplier_id,
      supplier_model_id: item.supplier_model_id ?? null,
      model_name: item.model_name,
      priority: (bindings.length - index) * 10,
      enabled: true,
    }))
  }

  async function savePrimary(row: LlmAgentModelItem, choice: ModelChoice): Promise<void> {
    const rest = toEntries(row.bindings.slice(1)).filter(
      entry => entry.supplier_id !== choice.supplier_id,
    )
    const entries = [
      {
        supplier_id: choice.supplier_id,
        supplier_model_id: choice.supplier_model_id || null,
        model_name: choice.model_name || null,
        priority: 0,
        enabled: true,
      },
      ...rest,
    ].slice(0, maxFallback.value)
    try {
      await llmBindingApi.replace({
        scope_type: 'agent',
        scope_key: row.code,
        entries: entries.map((entry, index) => ({
          ...entry,
          priority: (entries.length - index) * 10,
        })),
      })
      ElMessage.success(`${row.name} 的主用模型已更新`)
      await load()
      emit('changed')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  async function saveVisual(item: LlmVisualModelItem, choice: ModelChoice): Promise<void> {
    const rest = item.bindings.slice(1).map((binding, index) => ({
      supplier_id: binding.supplier_id,
      supplier_model_id: binding.supplier_model_id ?? null,
      model_name: binding.model_name,
      enabled: true,
      priority: (item.bindings.length - index - 1) * 10,
    }))
    const entries = [
      {
        supplier_id: choice.supplier_id,
        supplier_model_id: choice.supplier_model_id || null,
        model_name: choice.model_name || null,
        enabled: true,
        priority: 0,
      },
      ...rest,
    ].slice(0, maxFallback.value)
    try {
      await llmBindingApi.replace({
        scope_type: 'purpose',
        scope_key: item.purpose,
        entries: entries.map((entry, index) => ({
          ...entry,
          priority: (entries.length - index) * 10,
        })),
      })
      ElMessage.success(`${item.label}默认模型已更新`)
      await load()
      emit('changed')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  async function clearVisual(item: LlmVisualModelItem): Promise<void> {
    try {
      await llmBindingApi.replace({
        scope_type: 'purpose',
        scope_key: item.purpose,
        entries: [],
      })
      ElMessage.success(`已清除${item.label}默认模型`)
      await load()
      emit('changed')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  async function saveCapability(purpose: string, choice: ModelChoice): Promise<void> {
    if (!capCode.value) return
    try {
      await llmBindingApi.replace({
        scope_type: 'agent_purpose',
        scope_key: `${capCode.value}::${purpose}`,
        entries: [
          {
            supplier_id: choice.supplier_id,
            supplier_model_id: choice.supplier_model_id || null,
            model_name: choice.model_name || null,
            priority: 10,
            enabled: true,
          },
        ],
      })
      ElMessage.success('专属能力模型已更新')
      await load()
      emit('changed')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  async function clearCapability(purpose: string): Promise<void> {
    if (!capCode.value) return
    try {
      await llmBindingApi.replace({
        scope_type: 'agent_purpose',
        scope_key: `${capCode.value}::${purpose}`,
        entries: [],
      })
      ElMessage.success('已清除专属能力模型，回落到视觉默认模型')
      await load()
      emit('changed')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  function openCapabilities(row: LlmAgentModelItem): void {
    capCode.value = row.code
    capTitle.value = `${row.name}（${row.code}）`
    capVisible.value = true
  }

  function openFallback(row: LlmAgentModelItem): void {
    dialogScopeType.value = 'agent'
    dialogScopeKey.value = row.code
    dialogTitle.value = `${row.name}（${row.code}）`
    dialogSuppliers.value = props.suppliers
    dialogModalities.value = []
    dialogVisible.value = true
  }

  function openVisual(item: LlmVisualModelItem): void {
    dialogScopeType.value = 'purpose'
    dialogScopeKey.value = item.purpose
    dialogTitle.value = item.label
    dialogSuppliers.value = props.suppliers
    dialogModalities.value = modalitiesForVisual(item.purpose)
    dialogVisible.value = true
  }

  async function testAgent(row: LlmAgentModelItem): Promise<void> {
    testingCode.value = row.code
    try {
      const res = await llmBindingApi.test({ scope_type: 'agent', scope_key: row.code })
      if (res.ok) {
        ElMessage.success(
          `${row.name} 链路可用：${res.model || '未知模型'}，延迟 ${res.latency_ms ?? 0} ms`,
        )
      } else {
        ElMessage.error(`${row.name} 链路不通：${res.note || '请检查供应商配置'}`)
      }
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      testingCode.value = ''
    }
  }

  async function resetFollow(row: LlmAgentModelItem): Promise<void> {
    try {
      await ElMessageBox.confirm(
        `清除「${row.name}」的单独模型设置，回到跟随中心？`,
        '回到跟随中心',
        { type: 'warning', confirmButtonText: '确定', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    try {
      await llmBindingApi.replace({ scope_type: 'agent', scope_key: row.code, entries: [] })
      ElMessage.success('已回到跟随中心')
      await load()
      emit('changed')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  async function onSaved(): Promise<void> {
    dialogVisible.value = false
    await load()
    emit('changed')
  }

  onMounted(() => {
    void load()
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
    gap: 12px;
    flex-wrap: wrap;
  }
  .page-actions {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .hint {
    margin-bottom: 12px;
  }
  .agent-cell {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .agent-name {
    font-weight: 600;
  }
  .agent-model-cell {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .cap-tags {
    display: flex;
    flex-direction: column;
    gap: 4px;
    align-items: flex-start;
  }
  .note {
    color: var(--ds-text-2);
    font-size: 12px;
  }
  .danger {
    color: var(--el-color-danger);
  }
  .visual-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
    gap: 12px;
  }
  .visual-card {
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }
  .visual-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .visual-body {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .visual-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 13px;
  }
  .visual-actions {
    display: flex;
    gap: 4px;
    justify-content: flex-end;
  }
  .cap-row {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 10px 0;
    border-bottom: 1px solid var(--ds-border);
  }
  .cap-row:last-child {
    border-bottom: none;
  }
  .cap-head {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .cap-picker {
    display: flex;
    align-items: center;
    gap: 6px;
  }
</style>
