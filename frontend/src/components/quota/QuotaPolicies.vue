<template>
  <div class="qp">
    <el-card
      shadow="never"
      class="qp-env"
    >
      <div class="env-row">
        <div class="env-text">
          <strong>额度环境</strong>
          <p class="hint">
            切换环境会套用该环境的小院总额度模板，人工改过的策略不会被覆盖。
          </p>
        </div>
        <el-select
          v-model="environment"
          :disabled="!canManage || savingEnv"
          size="small"
          style="width: 180px"
          @change="onEnvironmentChange"
        >
          <el-option
            v-for="item in overview?.environments ?? []"
            :key="item.key"
            :label="item.label"
            :value="item.key"
          />
        </el-select>
      </div>
      <el-alert
        v-if="currentEnvironment"
        :title="currentEnvironment.description"
        type="info"
        :closable="false"
        show-icon
        class="env-desc"
      />
      <div class="legacy-row">
        <span>旧的「模型调用次数」配额：</span>
        <el-select
          v-model="legacyMode"
          :disabled="!canManage"
          size="small"
          style="width: 200px"
          @change="onLegacyModeChange"
        >
          <el-option
            label="超限拦截"
            value="enforce"
          />
          <el-option
            label="只提醒不拦（推荐）"
            value="warn"
          />
          <el-option
            label="完全不看"
            value="off"
          />
        </el-select>
        <span class="hint">Token 额度接管硬约束后，旧次数上限建议保留为只提醒。</span>
      </div>
    </el-card>

    <el-card
      v-for="section in sections"
      :key="section.key"
      shadow="never"
      class="qp-section"
    >
      <template #header>
        <div class="sec-head">
          <span>{{ section.label }}</span>
          <div class="sec-actions">
            <span class="hint">{{ section.hint }}</span>
            <el-button
              v-if="canManage"
              size="small"
              @click="openEditor(section.key)"
            >
              新增额度
            </el-button>
          </div>
        </div>
      </template>
      <el-table
        :data="section.rows"
        size="small"
        empty-text="暂无策略"
      >
        <el-table-column
          label="对象"
          min-width="160"
        >
          <template #default="{ row }">
            {{ row.scope_key || '全小院' }}
          </template>
        </el-table-column>
        <el-table-column
          label="指标"
          width="100"
        >
          <template #default="{ row }">
            {{ row.metric_label }}
          </template>
        </el-table-column>
        <el-table-column
          label="窗口"
          width="80"
        >
          <template #default="{ row }">
            {{ row.window_label }}
          </template>
        </el-table-column>
        <el-table-column
          label="额度"
          width="150"
        >
          <template #default="{ row }">
            <span>{{ formatValue(row.metric, row.used) }} / {{ formatValue(row.metric, row.effective_limit) }}</span>
            <div
              v-if="row.granted > 0"
              class="grant-tip"
            >
              含临时额度 +{{ formatValue(row.metric, row.granted) }}
            </div>
          </template>
        </el-table-column>
        <el-table-column
          label="使用率"
          min-width="140"
        >
          <template #default="{ row }">
            <el-progress
              :percentage="Math.min(100, row.usage_percent)"
              :status="progressStatus(row.usage_percent)"
              :stroke-width="10"
            />
          </template>
        </el-table-column>
        <el-table-column
          label="超出后"
          width="120"
        >
          <template #default="{ row }">
            <el-tag
              :type="row.on_exceed === 'warn' ? 'info' : row.on_exceed === 'block' ? 'danger' : 'warning'"
              size="small"
            >
              {{ row.on_exceed_label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          label="来源"
          width="80"
        >
          <template #default="{ row }">
            {{ row.source === 'template' ? '环境模板' : '手动' }}
          </template>
        </el-table-column>
        <el-table-column
          v-if="canManage"
          label="操作"
          width="190"
        >
          <template #default="{ row }">
            <el-button
              link
              size="small"
              @click="openEditor((row as QuotaPolicyItem).scope_type, row as QuotaPolicyItem)"
            >
              编辑
            </el-button>
            <el-button
              link
              size="small"
              @click="resetOne(row as QuotaPolicyItem)"
            >
              清零
            </el-button>
            <el-button
              link
              size="small"
              type="danger"
              @click="removeOne(row as QuotaPolicyItem)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </el-card>

    <el-dialog
      v-model="editorVisible"
      :title="editingId ? '编辑额度' : '新增额度'"
      width="520px"
      append-to-body
    >
      <el-form label-width="100px">
        <el-form-item label="作用范围">
          <el-select
            v-model="form.scope_type"
            :disabled="Boolean(editingId)"
            style="width: 100%"
          >
            <el-option
              label="全小院"
              value="yard"
            />
            <el-option
              label="单个 Agent"
              value="agent"
            />
            <el-option
              label="Agent 分组"
              value="agent_group"
            />
            <el-option
              label="模型"
              value="model"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          v-if="form.scope_type === 'agent'"
          label="选择 Agent"
        >
          <el-select
            v-model="form.scope_key"
            filterable
            style="width: 100%"
          >
            <el-option
              v-for="a in overview?.options.agents ?? []"
              :key="a.code"
              :label="a.group ? `${a.name}（${a.group}）` : a.name"
              :value="a.code"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          v-else-if="form.scope_type === 'agent_group'"
          label="分组名"
        >
          <el-select
            v-model="form.scope_key"
            allow-create
            filterable
            default-first-option
            style="width: 100%"
          >
            <el-option
              v-for="g in groupOptions"
              :key="g"
              :label="g"
              :value="g"
            />
          </el-select>
        </el-form-item>
        <el-form-item
          v-else-if="form.scope_type === 'model'"
          label="模型"
        >
          <el-select
            v-model="form.scope_key"
            allow-create
            filterable
            default-first-option
            style="width: 100%"
          >
            <el-option
              v-for="m in overview?.options.models ?? []"
              :key="m"
              :label="m"
              :value="m"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="指标">
          <el-select
            v-model="form.metric"
            style="width: 100%"
            @change="onMetricChange"
          >
            <el-option
              label="Token 用量"
              value="tokens"
            />
            <el-option
              label="调用次数"
              value="calls"
            />
            <el-option
              label="费用（元）"
              value="cost"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="统计窗口">
          <el-radio-group v-model="form.window">
            <el-radio value="day">
              每天
            </el-radio>
            <el-radio value="month">
              每月
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item :label="limitLabel">
          <el-input-number
            v-model="form.limit_value"
            :min="0"
            :step="stepForMetric"
            style="width: 180px"
          />
          <span class="hint">{{ limitHint }}</span>
        </el-form-item>
        <el-form-item label="超出后">
          <el-radio-group v-model="form.on_exceed">
            <el-radio value="warn">
              只提醒
            </el-radio>
            <el-radio value="block">
              直接拦截
            </el-radio>
            <el-radio value="approve">
              拦截并允许申请
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="备注">
          <el-input
            v-model="form.note"
            maxlength="200"
            placeholder="例如：诊断类助手日常巡检用"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editorVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="saving"
          @click="save"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { quotaApi, type QuotaMetric, type QuotaOverview, type QuotaPolicyItem, type QuotaScopeType } from '@/api/quota'
  import { governanceApi } from '@/api/v3'

  defineProps<{ canManage: boolean }>()

  const overview = ref<QuotaOverview | null>(null)
  const environment = ref('')
  const legacyMode = ref('warn')
  const savingEnv = ref(false)
  const saving = ref(false)

  const SECTIONS: { key: QuotaScopeType; label: string; hint: string }[] = [
    { key: 'yard', label: '全小院总额度', hint: '所有调用的兜底额度' },
    { key: 'agent_group', label: '按 Agent 分组', hint: '一类助手共享，例如「诊断」「营销」' },
    { key: 'agent', label: '按单个 Agent', hint: '针对单个助手的更严格限制' },
    { key: 'model', label: '按模型', hint: '贵模型单独限额' },
    { key: 'supplier', label: '按供应商', hint: '按供应商限额（接口可配）' },
  ]

  const sections = computed(() =>
    SECTIONS.map(section => ({
      ...section,
      rows: (overview.value?.policies ?? []).filter(p => p.scope_type === section.key),
    })).filter(section => section.rows.length || section.key === 'yard' || section.key === 'agent' || section.key === 'agent_group' || section.key === 'model'),
  )

  const currentEnvironment = computed(() =>
    (overview.value?.environments ?? []).find(e => e.key === environment.value),
  )

  const groupOptions = computed(() => {
    const set = new Set<string>()
    for (const agent of overview.value?.options.agents ?? []) {
      if (agent.group) set.add(agent.group)
    }
    return [...set]
  })

  const editorVisible = ref(false)
  const editingId = ref('')
  const form = reactive({
    scope_type: 'yard' as QuotaScopeType,
    scope_key: '',
    metric: 'tokens' as QuotaMetric,
    window: 'month' as 'day' | 'month',
    limit_value: 1_000_000,
    on_exceed: 'approve' as 'warn' | 'block' | 'approve',
    note: '',
  })

  const limitLabel = computed(() =>
    form.metric === 'tokens' ? '额度（Token）' : form.metric === 'calls' ? '额度（次）' : '额度（元）',
  )
  const stepForMetric = computed(() => (form.metric === 'tokens' ? 100_000 : form.metric === 'calls' ? 100 : 10))
  const limitHint = computed(() =>
    form.metric === 'cost'
      ? '金额按供应商单价估算；未填单价的供应商用默认常量，误差较大'
      : '填 0 表示不允许该对象继续调用',
  )

  function formatValue(metric: QuotaMetric, value: number): string {
    if (metric === 'cost') return `¥${Number(value).toFixed(2)}`
    if (metric === 'tokens') {
      if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
      if (value >= 1000) return `${(value / 1000).toFixed(1)}K`
      return String(Math.round(value))
    }
    return String(Math.round(value))
  }

  function progressStatus(percent: number) {
    if (percent >= 100) return 'exception'
    if (percent >= 80) return 'warning'
    return undefined
  }

  async function load() {
    try {
      overview.value = await quotaApi.overview()
      environment.value = overview.value.environment
      legacyMode.value = overview.value.legacy_calls_mode || 'warn'
    } catch (e) {
      ElMessage.error('额度数据加载失败：' + (e instanceof Error ? e.message : String(e)))
    }
  }

  async function onEnvironmentChange(value: string) {
    savingEnv.value = true
    try {
      overview.value = await quotaApi.setEnvironment(value)
      ElMessage.success('已切换环境并套用模板')
    } catch (e) {
      ElMessage.error('切换环境失败：' + (e instanceof Error ? e.message : String(e)))
      await load()
    } finally {
      savingEnv.value = false
    }
  }

  async function onLegacyModeChange(value: string) {
    try {
      await governanceApi.updateSetting('quota', { legacy_calls_mode: value })
      ElMessage.success('已更新旧调用额度的处理方式')
    } catch (e) {
      ElMessage.error('更新失败：' + (e instanceof Error ? e.message : String(e)))
      await load()
    }
  }

  function openEditor(scope: QuotaScopeType, row?: QuotaPolicyItem) {
    editingId.value = row?.id ?? ''
    form.scope_type = row?.scope_type ?? scope
    form.scope_key = row?.scope_key ?? ''
    form.metric = row?.metric ?? 'tokens'
    form.window = row?.window ?? 'month'
    form.limit_value = row?.limit_value ?? (form.window === 'day' ? 100_000 : 1_000_000)
    form.on_exceed = row?.on_exceed ?? 'approve'
    form.note = row?.note ?? ''
    editorVisible.value = true
  }

  function onMetricChange() {
    form.limit_value = form.metric === 'tokens' ? (form.window === 'day' ? 100_000 : 1_000_000) : form.metric === 'calls' ? 500 : 10
  }

  async function save() {
    if (form.scope_type !== 'yard' && !form.scope_key.trim()) {
      ElMessage.warning('请选择这条额度作用的对象')
      return
    }
    saving.value = true
    try {
      await quotaApi.upsertPolicy({
        scope_type: form.scope_type,
        scope_key: form.scope_key.trim(),
        metric: form.metric,
        window: form.window,
        limit_value: form.limit_value,
        on_exceed: form.on_exceed,
        note: form.note || null,
      })
      ElMessage.success('额度已保存')
      editorVisible.value = false
      await load()
    } catch (e) {
      ElMessage.error('保存失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      saving.value = false
    }
  }

  async function resetOne(row: QuotaPolicyItem) {
    try {
      await ElMessageBox.confirm(
        `把该策略当前窗口的已用量清零？上限与临时额度不变。`,
        '清零已用',
        { type: 'warning', confirmButtonText: '清零', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    try {
      await quotaApi.resetPolicy(row.id)
      ElMessage.success('已清零')
      await load()
    } catch (e) {
      ElMessage.error('清零失败：' + (e instanceof Error ? e.message : String(e)))
    }
  }

  async function removeOne(row: QuotaPolicyItem) {
    try {
      await ElMessageBox.confirm('删除后该对象不再受此额度限制，确定删除？', '删除额度', {
        type: 'warning',
        confirmButtonText: '删除',
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
    try {
      await quotaApi.deletePolicy(row.id)
      ElMessage.success('已删除')
      await load()
    } catch (e) {
      ElMessage.error('删除失败：' + (e instanceof Error ? e.message : String(e)))
    }
  }

  onMounted(load)
  defineExpose({ reload: load })
</script>

<style scoped>
  .qp {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .env-row,
  .legacy-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
  }
  .legacy-row {
    margin-top: 10px;
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .env-text strong {
    font-size: 13px;
    color: var(--ds-text-1);
  }
  .hint {
    font-size: 11.5px;
    color: var(--ds-text-3);
    line-height: 1.6;
    margin: 2px 0 0;
  }
  .env-desc {
    margin-top: 10px;
  }
  .sec-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .sec-actions {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .grant-tip {
    font-size: 11px;
    color: #d97706;
  }
</style>
