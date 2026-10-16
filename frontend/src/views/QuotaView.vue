<template>
  <div class="quota">
    <h2 class="page-title">配额用量</h2>
    <el-alert v-for="w in warnings" :key="w.kind" type="warning" :title="w.message" show-icon class="warn" />
    <el-card class="section">
      <template #header>租户配额（{{ tenantId }}）</template>
      <AsyncState :state="quotaState" empty-text="暂无配额信息" @retry="loadQuota">
        <el-table :data="rows" stripe>
          <el-table-column prop="label" label="配额类型" width="180" />
          <el-table-column prop="used" label="已用" width="120" />
          <el-table-column prop="limit" label="上限" width="120" />
          <el-table-column prop="remaining" label="剩余" width="120" />
          <el-table-column label="使用率"><template #default="{ row }"><el-progress :percentage="row.usage_percent" :status="progressStatus(row.usage_percent)" :stroke-width="12" /></template></el-table-column>
          <el-table-column label="状态" width="140"><template #default="{ row }"><el-tag v-if="row.usage_percent >= 90" type="danger" size="small">即将用尽</el-tag><el-tag v-else type="success" size="small">充足</el-tag></template></el-table-column>
        </el-table>
      </AsyncState>
    </el-card>
  </div>
</template>
<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { tenantApi } from '@/api/modules'
import AsyncState from '@/components/AsyncState.vue'
import { classifyError, type ViewState } from '@/utils/status'
interface QuotaItem { limit: number; used: number; remaining: number; usage_percent: number }
interface QuotaWarning { kind: string; level: string; message: string }
interface QuotaRow extends QuotaItem { kind: string; label: string }
const KIND_LABELS: Record<string, string> = { llm_calls: 'LLM 调用', knowledge_docs: '知识库文档', tasks: '任务' }
const tenantId = ref('')
const quotas = reactive<Record<string, QuotaItem>>({})
const warnings = ref<QuotaWarning[]>([])
const quotaState = ref<ViewState>('loading')
const rows = computed<QuotaRow[]>(() => Object.entries(quotas).map(([kind, item]) => ({ kind, label: KIND_LABELS[kind] ?? kind, ...item })))
function progressStatus(percent: number): 'success' | 'warning' | 'exception' | undefined {
  if (percent >= 90) return 'exception'
  if (percent >= 70) return 'warning'
  return undefined
}
async function loadQuota() {
  quotaState.value = 'loading'
  try {
    const data = await tenantApi.getQuota()
    tenantId.value = data?.tenant_id ?? ''
    Object.assign(quotas, data?.quotas ?? {})
    warnings.value = data?.warnings ?? []
    quotaState.value = rows.value.length ? 'ready' : 'empty'
  } catch (e) {
    quotaState.value = classifyError(e)
  }
}
onMounted(loadQuota)
</script>
<style scoped>
.quota { padding: 4px; max-width: 960px; }
.page-title { margin: 0 0 16px; }
.warn { margin-bottom: 12px; }
.section { margin-bottom: 16px; }
</style>
