<template>
  <div class="review">
    <h2 class="page-title">
      专家审核
    </h2>
    <el-card>
      <div class="toolbar">
        <el-button
          type="success"
          :disabled="!selectedCandidates.length"
          :loading="reviewing"
          @click="doBatchReview('approve')"
        >
          批量通过（{{ selectedCandidates.length }}）
        </el-button>
        <el-button
          type="danger"
          :disabled="!selectedCandidates.length"
          @click="openBulkRejectDialog"
        >
          批量驳回（{{ selectedCandidates.length }}）
        </el-button>
        <span class="toolbar-note">
          技能的新建、审核与自动升级已并入
          <el-button
            link
            type="primary"
            @click="router.push('/evolution')"
          >
            自生长管理
          </el-button>
        </span>
      </div>
      <AsyncState
        :state="candState"
        empty-text="没有待审核的候选知识"
        @retry="loadCandidates"
      >
        <el-table
          v-col-resize
          :data="candidates"
          stripe
          @selection-change="handleSelectionChange"
        >
          <el-table-column
            type="selection"
            width="44"
          />
          <el-table-column
            prop="title"
            label="标题"
          />
          <el-table-column
            prop="source"
            label="来源"
            width="140"
          />
          <el-table-column
            prop="content"
            label="内容"
            show-overflow-tooltip
          />
          <el-table-column
            prop="created_at"
            label="创建时间"
            width="180"
          >
            <template #default="{ row }">
              {{ formatTime(row.created_at) }}
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="180"
          >
            <template #default="{ row }">
              <el-button
                link
                type="success"
                @click="doReviewCandidate(row as Candidate, 'approve')"
              >
                通过
              </el-button>
              <el-button
                link
                type="danger"
                @click="openRejectDialog(row as Candidate)"
              >
                驳回
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </AsyncState>
    </el-card>

    <!-- 驳回候选知识（单条或批量共用；bulkMode 时 rejectTarget 为空） -->
    <el-dialog
      v-model="rejectDialog"
      append-to-body
      :title="bulkMode ? '批量驳回候选知识' : '驳回候选知识'"
      width="480px"
      align-center
    >
      <el-form label-width="60px">
        <el-form-item label="备注">
          <el-input
            v-model="rejectNote"
            type="textarea"
            :rows="3"
            placeholder="请输入驳回原因"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rejectDialog = false">
          取消
        </el-button>
        <el-button
          type="danger"
          :loading="reviewing"
          @click="confirmRejectCandidate"
        >
          确认驳回
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { knowledgeApi } from '@/api/modules'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'
  import type { Candidate, ReviewAction } from '@/types/api'
  import { formatDateTime as formatTime } from '@/utils/format'

  const router = useRouter()

  const candidates = ref<Candidate[]>([])
  const candState = ref<ViewState>('loading')
  const reviewing = ref(false)
  const rejectDialog = ref(false)
  const rejectNote = ref('')
  const rejectTarget = ref<Candidate | null>(null)
  /** 批量模式：rejectTarget 为空时驳回对话框作用于勾选集合 */
  const bulkMode = ref(false)
  const selectedCandidates = ref<Candidate[]>([])

  async function loadCandidates() {
    candState.value = 'loading'
    try {
      const res = await knowledgeApi.candidates({
        status: 'pending_review',
        page: 1,
        page_size: 20,
      })
      candidates.value = res.items
      candState.value = res.items.length ? 'ready' : 'empty'
    } catch (e) {
      candState.value = classifyError(e)
    }
  }

  async function doReviewCandidate(c: Candidate, action: ReviewAction, note?: string) {
    if (action === 'reject') {
      try {
        await ElMessageBox.confirm(`确定驳回候选知识「${c.title || c.id}」吗？`, '驳回确认', {
          type: 'warning',
        })
      } catch {
        return
      }
    }
    reviewing.value = true
    try {
      await knowledgeApi.reviewCandidate(c.id, { action, note })
      ElMessage.success(action === 'approve' ? '已通过' : '已驳回')
      loadCandidates()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      reviewing.value = false
    }
  }

  function openRejectDialog(c: Candidate) {
    bulkMode.value = false
    rejectTarget.value = c
    rejectNote.value = ''
    rejectDialog.value = true
  }

  async function confirmRejectCandidate() {
    if (bulkMode.value) {
      await doBatchReview('reject', rejectNote.value || undefined)
      rejectDialog.value = false
      return
    }
    if (!rejectTarget.value) return
    await doReviewCandidate(rejectTarget.value, 'reject', rejectNote.value || undefined)
    rejectDialog.value = false
  }

  // ---------- 批量审核（POST /knowledge/candidates/batch-review） ----------

  function handleSelectionChange(rows: Candidate[]) {
    selectedCandidates.value = rows
  }

  function openBulkRejectDialog() {
    if (!selectedCandidates.value.length) return
    bulkMode.value = true
    rejectTarget.value = null
    rejectNote.value = ''
    rejectDialog.value = true
  }

  /** 汇总批量结果并按失败数分级提示 */
  function summarizeBatch(
    result: {
      approved: string[]
      rejected: string[]
      failed: Array<{ id: string; error_code: string }>
    },
    action: 'approve' | 'reject',
  ) {
    const parts: string[] = []
    if (action === 'approve') parts.push(`通过 ${result.approved.length} 条`)
    else parts.push(`驳回 ${result.rejected.length} 条`)
    if (result.failed?.length) parts.push(`失败 ${result.failed.length} 条`)
    const text = `批量处理完成：${parts.join('，')}`
    if (result.failed?.length) ElMessage.warning(text)
    else ElMessage.success(text)
  }

  async function doBatchReview(action: 'approve' | 'reject', note?: string) {
    const ids = selectedCandidates.value.map(c => c.id)
    if (!ids.length) return
    if (action === 'approve') {
      try {
        await ElMessageBox.confirm(`确定批量通过选中的 ${ids.length} 条候选知识吗？`, '批量通过', {
          type: 'warning',
        })
      } catch {
        return
      }
    }
    reviewing.value = true
    try {
      const result = await knowledgeApi.batchReview(ids, action, note)
      summarizeBatch(result, action)
      rejectDialog.value = false
      loadCandidates()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      reviewing.value = false
    }
  }

  onMounted(() => {
    void loadCandidates()
  })
</script>

<style scoped>
  .review {
    padding: 4px;
  }
  .toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 16px;
  }
  .toolbar-note {
    margin-left: auto;
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>
