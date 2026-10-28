<template>
  <section
    class="quick-bar"
    :aria-label="`${title}一句话办理`"
  >
    <div class="qa-head">
      <el-icon class="qa-icon">
        <MagicStick />
      </el-icon>
      <span class="qa-title">一句话办理</span>
      <span class="qa-hint">{{ hint }}</span>
      <el-button
        link
        class="qa-example-toggle"
        @click="showExamples = !showExamples"
      >
        {{ showExamples ? '收起示例' : '看示例' }}
      </el-button>
    </div>
    <div class="qa-row">
      <el-input
        v-model="text"
        :placeholder="placeholder"
        :disabled="disabled"
        clearable
        maxlength="500"
        @keyup.enter="parse"
      >
        <template #prefix>
          <el-icon><ChatLineRound /></el-icon>
        </template>
      </el-input>
      <el-button
        type="primary"
        :loading="loading"
        :disabled="disabled || !text.trim()"
        @click="parse"
      >
        解析
      </el-button>
    </div>
    <div
      v-if="showExamples"
      class="qa-examples"
    >
      <button
        v-for="ex in examples"
        :key="ex"
        type="button"
        class="qa-example"
        @click="useExample(ex)"
      >
        {{ ex }}
      </button>
    </div>
  </section>

  <el-dialog
    v-model="dialog"
    title="确认办理"
    width="560px"
    align-center
    class="quick-action-dialog"
  >
    <template v-if="previewData">
      <p class="qa-summary">
        {{ previewData.summary }}
      </p>
      <el-alert
        v-if="previewData.missing.length"
        class="qa-missing"
        type="warning"
        :closable="false"
        show-icon
        :title="`还缺：${previewData.missing.join('、')}`"
        description="补全后再试，或点「用表单填写」在原表单里继续。解析阶段没有写入任何数据。"
      />
      <el-descriptions
        v-else
        :column="1"
        border
        size="small"
        class="qa-desc"
      >
        <el-descriptions-item
          v-for="row in detailRows"
          :key="row.label"
          :label="row.label"
        >
          {{ row.value }}
        </el-descriptions-item>
      </el-descriptions>
      <p class="qa-note">
        解析来源：{{ previewData.source === 'llm' ? '模型补充' : '规则' }}；确认前不会写入任何数据。
      </p>
    </template>
    <template #footer>
      <el-button @click="dialog = false">
        取消
      </el-button>
      <el-button
        v-if="previewData?.missing.length"
        @click="useForm"
      >
        用表单填写
      </el-button>
      <el-button
        v-else
        type="primary"
        :loading="confirming"
        @click="confirm"
      >
        确认办理
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
  import { computed, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { ChatLineRound, MagicStick } from '@element-plus/icons-vue'
  import { quickActionApi, type QuickActionPreview, type QuickModule } from '@/api/modules'

  const props = withDefaults(
    defineProps<{
      module: QuickModule
      title: string
      placeholder: string
      hint?: string
      examples?: string[]
      plotId?: string
      disabled?: boolean
    }>(),
    {
      hint: '说一句就办，先预览后确认',
      examples: () => [],
      plotId: undefined,
      disabled: false,
    },
  )

  const emit = defineEmits<{
    (e: 'created', result: { action: string; created: Record<string, unknown> }): void
    (e: 'fallback', data: { action: string; payload: Record<string, unknown> }): void
  }>()

  const text = ref('')
  const loading = ref(false)
  const confirming = ref(false)
  const showExamples = ref(false)
  const dialog = ref(false)
  const previewData = ref<QuickActionPreview | null>(null)

  const KIND_LABELS: Record<string, string> = {
    sowing: '播种/移栽',
    fertilizing: '施肥',
    irrigation: '灌溉',
    pesticide: '用药',
    weeding: '除草',
    scouting: '巡田',
    harvest: '采收',
    other: '其他',
  }
  const PRIORITY_LABELS: Record<string, string> = {
    low: '低',
    normal: '普通',
    high: '高',
    urgent: '紧急',
  }

  function localToday(): string {
    const d = new Date()
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  }

  const detailRows = computed<Array<{ label: string; value: string }>>(() => {
    const data = previewData.value
    if (!data) return []
    const p = data.payload
    const str = (key: string) => {
      const value = p[key]
      return value === null || value === undefined || value === '' ? '—' : String(value)
    }
    if (data.action === 'plot.create') {
      return [
        { label: '地块名称', value: str('name') },
        { label: '作物', value: str('crop') },
        { label: '面积（亩）', value: str('area_mu') },
        { label: '土壤', value: str('soil_type') },
      ]
    }
    if (data.action === 'record.create') {
      return [
        { label: '地块', value: str('plot_name') },
        { label: '类型', value: KIND_LABELS[str('kind')] ?? str('kind') },
        { label: '日期', value: str('occurred_on') },
        { label: '用量', value: p.quantity ? `${str('quantity')}${str('unit')}` : '—' },
        { label: '操作人', value: str('operator_name') },
      ]
    }
    if (data.action === 'diagnosis.create') {
      const env = p.environment as Record<string, unknown> | null | undefined
      return [
        { label: '地块', value: str('plot_name') },
        { label: '作物', value: str('crop') },
        { label: '症状', value: str('symptoms') },
        { label: '环境', value: env?.recent_weather ? String(env.recent_weather) : '—' },
      ]
    }
    if (data.action === 'alert.convert') {
      return [
        { label: '告警', value: str('alert_title') },
        { label: '到期', value: str('due_on') },
        { label: '责任人', value: str('assignee_name') },
        { label: '优先级', value: PRIORITY_LABELS[str('priority')] ?? str('priority') },
      ]
    }
    return [
      { label: '标题', value: str('title') },
      { label: '地块', value: str('plot_name') },
      { label: '优先级', value: PRIORITY_LABELS[str('priority')] ?? str('priority') },
      { label: '到期', value: str('due_on') },
      { label: '责任人', value: str('assignee_name') },
    ]
  })

  function useExample(example: string) {
    text.value = example
    showExamples.value = false
  }

  async function parse() {
    const value = text.value.trim()
    if (!value || loading.value) return
    loading.value = true
    try {
      previewData.value = await quickActionApi.preview({
        module: props.module,
        text: value,
        plot_id: props.plotId || undefined,
        today: localToday(),
      })
      dialog.value = true
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      loading.value = false
    }
  }

  async function confirm() {
    const data = previewData.value
    if (!data || confirming.value) return
    confirming.value = true
    try {
      const result = await quickActionApi.confirm({
        module: data.module,
        action: data.action,
        payload: data.payload,
        fingerprint: data.fingerprint,
      })
      ElMessage.success(result.summary)
      emit('created', { action: result.action, created: result.created })
      dialog.value = false
      text.value = ''
      previewData.value = null
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      confirming.value = false
    }
  }

  function useForm() {
    const data = previewData.value
    if (!data) return
    emit('fallback', { action: data.action, payload: data.payload })
    dialog.value = false
  }
</script>

<style scoped>
  .quick-bar {
    margin-bottom: 14px;
    padding: 12px 14px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
  }
  .qa-head {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 8px;
    font-size: 12.5px;
    color: var(--ds-text-3);
  }
  .qa-head .qa-icon {
    color: var(--ds-accent-text);
  }
  .qa-head .qa-title {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .qa-head .qa-example-toggle {
    margin-left: auto;
  }
  .qa-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .qa-examples {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin-top: 10px;
  }
  .qa-example {
    padding: 4px 10px;
    font-size: 12px;
    color: var(--ds-text-2);
    background: var(--ds-bg-1);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
    cursor: pointer;
  }
  .qa-example:hover {
    color: var(--ds-accent-text);
    border-color: var(--ds-border-strong);
  }
  .qa-summary {
    margin: 0 0 12px;
    font-size: 14px;
    font-weight: 600;
    line-height: 1.6;
    color: var(--ds-text-1);
  }
  .qa-missing {
    margin-bottom: 8px;
  }
  .qa-note {
    margin: 12px 0 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-3);
  }
  @media (max-width: 640px) {
    .qa-row {
      flex-direction: column;
      align-items: stretch;
    }
  }
</style>
