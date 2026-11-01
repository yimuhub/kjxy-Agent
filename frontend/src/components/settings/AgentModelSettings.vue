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
      title="一个地方配完：跟随中心（走能力/全局默认）/ 指定模型（本助手主用 + 兜底）/ 助手编辑页的旧设置会在这里被接管。保存后该助手下一次调用即用新模型，无需重启。"
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
        min-width="200"
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
        width="100"
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
        width="150"
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
        label="主用"
        min-width="200"
      >
        <template #default="{ row }">
          <template v-if="row.bindings.length">
            <span>{{ row.bindings[0].supplier_name }}</span>
            <code class="note">{{ row.bindings[0].model_name || row.bindings[0].model_id }}</code>
            <el-tag
              v-if="row.bindings[0].available === false"
              size="small"
              type="danger"
              effect="plain"
            >
              不可用
            </el-tag>
          </template>
          <template v-else-if="row.legacy">
            <span>{{ row.legacy.supplier_name }}</span>
            <code class="note">{{ row.legacy.model_name || row.legacy.model_id }}</code>
          </template>
          <span
            v-else-if="row.resolved"
            class="note"
          >继承：{{ row.resolved.supplier_name }} · {{ row.resolved.model_name }}（{{ row.resolved.level_label }}）</span>
          <span
            v-else
            class="note"
          >暂无可用供应商</span>
        </template>
      </el-table-column>
      <el-table-column
        label="兜底"
        min-width="150"
      >
        <template #default="{ row }">
          <template v-if="row.bindings.length > 1">
            <span class="note">
              {{ fallbackNames(row as LlmAgentModelItem) }}
            </span>
          </template>
          <span
            v-else
            class="note"
          >—</span>
        </template>
      </el-table-column>
      <el-table-column
        label="操作"
        width="220"
      >
        <template #default="{ row }">
          <el-button
            size="small"
            text
            type="primary"
            @click="openBinding(row as LlmAgentModelItem)"
          >
            {{ row.mode === 'follow' ? '指定模型' : '调整' }}
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
        <span class="note">图像/视频不走对话模型，单独在这里配置；不配则海报自动降级模板。</span>
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
            <code class="note">{{ binding.model_name || binding.model_id }}</code>
          </div>
        </div>
        <div
          v-else-if="item.resolved"
          class="note"
        >
          当前生效：{{ item.resolved.supplier_name }} · {{ item.resolved.model_name }}（{{ item.resolved.level_label }}）
        </div>
        <div
          v-else
          class="note"
        >
          {{ item.purpose === 'image_generation'
            ? '还没有可用的图像供应商：海报会降级为本地模板。到「供应商」页新增「图像生成」类型即可。'
            : '视频生成通道已预留，暂无内置调用工具。' }}
        </div>
        <el-button
          size="small"
          text
          type="primary"
          @click="openVisual(item)"
        >
          配置
        </el-button>
      </div>
    </div>
  </el-card>

  <el-dialog
    v-model="dialogVisible"
    append-to-body
    :title="`绑定模型：${dialogTitle}`"
    width="720px"
  >
    <ModelBindings
      v-if="dialogVisible"
      :scope-type="dialogScopeType"
      :scope-key="dialogScopeKey"
      :title="dialogTitle"
      hint="留空并保存即取消该层的单独绑定，回落到下一档；保存会接管助手编辑页的旧设置。"
      :suppliers="dialogSuppliers"
      :max-fallback="maxFallback"
      @saved="onSaved"
    />
    <template #footer>
      <el-button @click="dialogVisible = false">
        关闭
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  /** 模型中心 → 智能体模型（v3.26）：一个地方配完「跟随中心 / 指定模型」。 */
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { llmBindingApi } from '@/api/modules'
  import type { LlmAgentModelItem, LlmSupplier, LlmVisualModelItem } from '@/api/modules'
  import ModelBindings from './ModelBindings.vue'

  const props = defineProps<{ suppliers: LlmSupplier[] }>()
  const emit = defineEmits<{ (event: 'changed'): void }>()

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

  const filtered = computed(() => {
    const word = keyword.value.trim().toLowerCase()
    return items.value.filter(item => {
      if (onlyCustom.value && item.mode === 'follow') return false
      if (!word) return true
      return item.name.toLowerCase().includes(word) || item.code.toLowerCase().includes(word)
    })
  })

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

  function openBinding(row: LlmAgentModelItem): void {
    dialogScopeType.value = 'agent'
    dialogScopeKey.value = row.code
    dialogTitle.value = `${row.name}（${row.code}）`
    dialogSuppliers.value = props.suppliers
    dialogVisible.value = true
  }

  function openVisual(item: LlmVisualModelItem): void {
    dialogScopeType.value = 'purpose'
    dialogScopeKey.value = item.purpose
    dialogTitle.value = item.label
    // 视觉能力只列对应类型的供应商：对话供应商绑上来也调不通
    const allowed
      = item.purpose === 'image_generation'
        ? ['openai_images', 'dashscope_image']
        : ['openai_video']
    dialogSuppliers.value = props.suppliers.filter(s => allowed.includes(s.provider_type))
    dialogVisible.value = true
  }

  async function testAgent(row: LlmAgentModelItem): Promise<void> {
    testingCode.value = row.code
    try {
      const res = await llmBindingApi.test({ scope_type: 'agent', scope_key: row.code })
      if (res.ok) {
        ElMessage.success(`${row.name} 链路可用：${res.model || '未知模型'}，延迟 ${res.latency_ms ?? 0} ms`)
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
  .note {
    color: var(--ds-text-2);
    font-size: 12px;
  }
  .visual-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
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
</style>
