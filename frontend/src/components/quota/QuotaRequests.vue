<template>
  <div class="qr">
    <div class="qr-head">
      <el-radio-group
        v-model="status"
        size="small"
        @change="load"
      >
        <el-radio-button value="">
          全部
        </el-radio-button>
        <el-radio-button value="pending">
          待审批
        </el-radio-button>
        <el-radio-button value="approved">
          已通过
        </el-radio-button>
        <el-radio-button value="rejected">
          已驳回
        </el-radio-button>
      </el-radio-group>
      <span class="hint">审批通过后自动发放临时额度，到期失效，不动原策略。</span>
    </div>

    <el-table
      v-loading="loading"
      :data="rows"
      size="small"
      empty-text="暂无提额申请"
    >
      <el-table-column
        label="申请时间"
        width="170"
      >
        <template #default="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column
        label="对象"
        min-width="150"
      >
        <template #default="{ row }">
          {{ row.scope_type_label }}<span v-if="row.scope_key">「{{ row.scope_key }}」</span>
        </template>
      </el-table-column>
      <el-table-column
        label="额度"
        width="190"
      >
        <template #default="{ row }">
          {{ formatValue(row.metric, row.current_limit) }} → <b>{{ formatValue(row.metric, row.requested_limit) }}</b>
          <div class="hint">
            {{ row.metric_label }} · {{ row.window_label }}
          </div>
        </template>
      </el-table-column>
      <el-table-column
        prop="reason"
        label="理由"
        min-width="180"
        show-overflow-tooltip
      />
      <el-table-column
        label="状态"
        width="100"
      >
        <template #default="{ row }">
          <el-tag
            :type="statusType(row.status)"
            size="small"
          >
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        v-if="canApprove"
        label="操作"
        width="150"
      >
        <template #default="{ row }">
          <template v-if="row.status === 'pending'">
            <el-button
              link
              size="small"
              type="primary"
              @click="openApprove(row as QuotaRequestItem)"
            >
              通过
            </el-button>
            <el-button
              link
              size="small"
              type="danger"
              @click="reject(row as QuotaRequestItem)"
            >
              驳回
            </el-button>
          </template>
          <span
            v-else
            class="hint"
          >{{ row.decision_note || '—' }}</span>
        </template>
      </el-table-column>
    </el-table>

    <el-dialog
      v-model="approveVisible"
      title="批准提额"
      width="480px"
      append-to-body
    >
      <el-form label-width="96px">
        <el-form-item label="申请额度">
          <el-input-number
            v-model="approveLimit"
            :min="1"
            :step="1000"
            style="width: 200px"
          />
          <span class="hint">{{ unitHint }}</span>
        </el-form-item>
        <el-form-item label="有效期">
          <span>{{ approveTarget?.valid_days ?? 7 }} 天后自动失效</span>
        </el-form-item>
        <el-form-item label="审批意见">
          <el-input
            v-model="approveNote"
            type="textarea"
            :rows="2"
            maxlength="500"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="approveVisible = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="deciding"
          @click="confirmApprove"
        >
          批准
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { quotaApi, type QuotaMetric, type QuotaRequestItem } from '@/api/quota'
  import { formatTime } from '@/utils/format'

  defineProps<{ canApprove: boolean }>()

  const rows = ref<QuotaRequestItem[]>([])
  const loading = ref(false)
  const status = ref('')
  const approveVisible = ref(false)
  const approveTarget = ref<QuotaRequestItem | null>(null)
  const approveLimit = ref(0)
  const approveNote = ref('')
  const deciding = ref(false)

  const unitHint = computed(() => {
    const metric = approveTarget.value?.metric
    return metric === 'tokens' ? 'Token' : metric === 'calls' ? '次' : '元'
  })

  function formatValue(metric: QuotaMetric, value: number): string {
    if (metric === 'cost') return `¥${Number(value).toFixed(2)}`
    if (metric === 'tokens') {
      if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(2)}M`
      if (value >= 1000) return `${(value / 1000).toFixed(1)}K`
    }
    return String(Math.round(value))
  }

  function statusLabel(value: string): string {
    return (
      { pending: '待审批', approved: '已通过', rejected: '已驳回', cancelled: '已取消', expired: '已过期' }[
        value
      ] ?? value
    )
  }

  function statusType(value: string) {
    if (value === 'approved') return 'success'
    if (value === 'pending') return 'warning'
    if (value === 'rejected') return 'danger'
    return 'info'
  }

  async function load() {
    loading.value = true
    try {
      const data = await quotaApi.listRequests(status.value || undefined)
      rows.value = data.items
    } catch (e) {
      ElMessage.error('申请列表加载失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      loading.value = false
    }
  }

  function openApprove(row: QuotaRequestItem) {
    approveTarget.value = row
    approveLimit.value = row.requested_limit
    approveNote.value = ''
    approveVisible.value = true
  }

  async function confirmApprove() {
    if (!approveTarget.value) return
    deciding.value = true
    try {
      const res = await quotaApi.approve(approveTarget.value.id, {
        note: approveNote.value,
        approved_limit: approveLimit.value,
      })
      ElMessage.success(`已批准，临时额度 +${formatValue(res.request.metric, res.grant.amount)}`)
      approveVisible.value = false
      await load()
    } catch (e) {
      ElMessage.error('审批失败：' + (e instanceof Error ? e.message : String(e)))
    } finally {
      deciding.value = false
    }
  }

  async function reject(row: QuotaRequestItem) {
    try {
      const { value } = await ElMessageBox.prompt('填写驳回理由（可选）', '驳回申请', {
        confirmButtonText: '驳回',
        cancelButtonText: '取消',
        inputType: 'textarea',
      })
      await quotaApi.reject(row.id, value || '')
      ElMessage.success('已驳回')
      await load()
    } catch {
      /* 取消 */
    }
  }

  onMounted(load)
  defineExpose({ reload: load })
</script>

<style scoped>
  .qr {
    display: flex;
    flex-direction: column;
    gap: 12px;
  }
  .qr-head {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }
  .hint {
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
</style>
