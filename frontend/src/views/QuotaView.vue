<template>
  <div class="quota">
    <h2 class="page-title">
      用量额度
    </h2>
    <el-alert
      v-for="w in warnings"
      :key="w.kind"
      type="warning"
      :title="w.message"
      show-icon
      class="warn"
    />
    <el-card class="section">
      <template #header>
        用量额度（{{ unitsName }}）
      </template>
      <AsyncState
        :state="quotaState"
        empty-text="暂无配额信息"
        @retry="loadQuota"
      >
        <el-table
          :data="rows"
          stripe
        >
          <el-table-column
            prop="label"
            label="额度类型"
            width="180"
          />
          <el-table-column
            prop="used"
            label="已用"
            width="120"
          />
          <el-table-column
            prop="limit"
            label="上限"
            width="120"
          />
          <el-table-column
            prop="remaining"
            label="剩余"
            width="120"
          />
          <el-table-column label="使用率">
            <template #default="{ row }">
              <el-progress
                :percentage="row.usage_percent"
                :status="progressStatus(row.usage_percent)"
                :stroke-width="12"
              />
            </template>
          </el-table-column>
          <el-table-column
            label="状态"
            width="140"
          >
            <template #default="{ row }">
              <el-tag
                v-if="row.usage_percent >= 90"
                type="danger"
                size="small"
              >
                即将用尽
              </el-tag><el-tag
                v-else
                type="success"
                size="small"
              >
                充足
              </el-tag>
            </template>
          </el-table-column>
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
  import { useAuthStore } from '@/stores/auth'
  interface QuotaItem {
    limit: number
    used: number
    remaining: number
    usage_percent: number
  }
  interface QuotaWarning {
    kind: string
    level: string
    message: string
  }
  interface QuotaRow extends QuotaItem {
    kind: string
    label: string
  }
  const KIND_LABELS: Record<string, string> = {
    llm_calls: '模型调用',
    knowledge_docs: '知识库文档',
    tasks: '任务',
  }
  const auth = useAuthStore()
  const quotas = reactive<Record<string, QuotaItem>>({})
  const warnings = ref<QuotaWarning[]>([])
  const quotaState = ref<ViewState>('loading')
  /** 顶栏/接口给出的使用空间（单位）名称，替代原来展示的租户 ID */
  const unitsName = computed(() => auth.tenant?.name ?? '当前使用空间')
  const rows = computed<QuotaRow[]>(() =>
    Object.entries(quotas).map(([kind, item]) => ({
      kind,
      label: KIND_LABELS[kind] ?? kind,
      ...item,
    })),
  )
  function progressStatus(percent: number): 'success' | 'warning' | 'exception' | undefined {
    if (percent >= 90) return 'exception'
    if (percent >= 70) return 'warning'
    return undefined
  }
  async function loadQuota() {
    quotaState.value = 'loading'
    try {
      // 顶栏还没有空间名称时补拉一次（后端 403 时静默忽略）
      if (!auth.tenant?.name) {
        try {
          await auth.fetchTenant()
        } catch {
          /* 保持占位名称 */
        }
      }
      const data = await tenantApi.getQuota()
      Object.keys(quotas).forEach(k => delete quotas[k])
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
  .quota {
    padding: 4px;
    max-width: 960px;
  }
  .page-title {
    margin: 0 0 16px;
  }
  .warn {
    margin-bottom: 12px;
  }
  .section {
    margin-bottom: 16px;
  }
</style>
