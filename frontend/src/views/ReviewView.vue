<template>
  <div class="review">
    <h2 class="page-title">
      专家审核
    </h2>
    <el-tabs v-model="activeTab">
      <!-- 候选知识 -->
      <el-tab-pane
        label="待审核知识"
        name="candidates"
      >
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
          </div>
          <AsyncState
            :state="candState"
            empty-text="没有待审核的候选知识"
            @retry="loadCandidates"
          >
            <el-table
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
      </el-tab-pane>

      <!-- Skill -->
      <el-tab-pane
        label="Skill 管理"
        name="skills"
      >
        <el-card>
          <div class="toolbar">
            <el-button
              type="primary"
              @click="createDialog = true"
            >
              新建 Skill 草稿
            </el-button>
          </div>
          <AsyncState
            :state="skillState"
            empty-text="暂无 Skill"
            @retry="loadSkills"
          >
            <el-table
              :data="skills"
              stripe
            >
              <el-table-column
                prop="name"
                label="名称"
              />
              <el-table-column
                prop="code"
                label="编码"
              />
              <el-table-column
                prop="version"
                label="版本"
                width="100"
              />
              <el-table-column
                prop="status"
                label="状态"
                width="130"
              >
                <template #default="{ row }">
                  <el-tag
                    :type="
                      row.status === 'published'
                        ? 'success'
                        : row.status === 'pending_review'
                          ? 'warning'
                          : row.status === 'rejected'
                            ? 'danger'
                            : 'info'
                    "
                  >
                    {{ row.status }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column
                prop="description"
                label="描述"
                show-overflow-tooltip
              />
              <el-table-column
                label="操作"
                width="300"
              >
                <template #default="{ row }">
                  <el-button
                    v-if="row.status === 'draft'"
                    link
                    type="primary"
                    @click="doReviewSkill(row as Skill, 'submit')"
                  >
                    提交审核
                  </el-button>
                  <el-button
                    v-if="row.status === 'pending_review'"
                    link
                    type="success"
                    @click="doReviewSkill(row as Skill, 'approve')"
                  >
                    发布
                  </el-button>
                  <el-button
                    v-if="['draft', 'pending_review'].includes(row.status)"
                    link
                    type="danger"
                    @click="openSkillRejectDialog(row as Skill)"
                  >
                    驳回
                  </el-button>
                  <el-button
                    v-if="row.status === 'published'"
                    link
                    type="warning"
                    @click="doReviewSkill(row as Skill, 'rollback')"
                  >
                    回滚
                  </el-button>
                </template>
              </el-table-column>
            </el-table>
          </AsyncState>
        </el-card>
      </el-tab-pane>
    </el-tabs>

    <!-- 驳回候选知识（单条或批量共用；bulkMode 时 rejectTarget 为空） -->
    <el-dialog
      v-model="rejectDialog"
      :title="bulkMode ? '批量驳回候选知识' : '驳回候选知识'"
      width="480px"
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

    <!-- 驳回 Skill -->
    <el-dialog
      v-model="skillRejectDialog"
      title="驳回 Skill"
      width="480px"
    >
      <el-form label-width="60px">
        <el-form-item label="备注">
          <el-input
            v-model="skillRejectNote"
            type="textarea"
            :rows="3"
            placeholder="请输入驳回原因"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="skillRejectDialog = false">
          取消
        </el-button>
        <el-button
          type="danger"
          :loading="reviewing"
          @click="confirmRejectSkill"
        >
          确认驳回
        </el-button>
      </template>
    </el-dialog>

    <!-- 新建 Skill 草稿 -->
    <el-dialog
      v-model="createDialog"
      title="新建 Skill 草稿"
      width="560px"
    >
      <el-form
        :model="skillForm"
        label-width="80px"
      >
        <el-form-item
          label="名称"
          required
        >
          <el-input v-model="skillForm.name" />
        </el-form-item>
        <el-form-item label="编码">
          <el-input
            v-model="skillForm.code"
            placeholder="如 crop.rice.tillering"
          />
        </el-form-item>
        <el-form-item label="版本">
          <el-input
            v-model="skillForm.version"
            placeholder="如 0.1.0"
          />
        </el-form-item>
        <el-form-item label="描述">
          <el-input
            v-model="skillForm.description"
            type="textarea"
            :rows="3"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="creating"
          @click="doCreateSkill"
        >
          创建
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { knowledgeApi, skillApi } from '@/api/modules'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'
  import type { Candidate, ReviewAction, Skill, SkillReviewAction } from '@/types/api'

  const activeTab = ref('candidates')

  const candidates = ref<Candidate[]>([])
  const candState = ref<ViewState>('loading')
  const reviewing = ref(false)
  const rejectDialog = ref(false)
  const rejectNote = ref('')
  const rejectTarget = ref<Candidate | null>(null)
  /** 批量模式：rejectTarget 为空时驳回对话框作用于勾选集合 */
  const bulkMode = ref(false)
  const selectedCandidates = ref<Candidate[]>([])

  const skills = ref<Skill[]>([])
  const skillState = ref<ViewState>('loading')
  const createDialog = ref(false)
  const creating = ref(false)
  const skillForm = reactive({ name: '', code: '', version: '0.1.0', description: '' })
  const skillRejectDialog = ref(false)
  const skillRejectNote = ref('')
  const skillRejectTarget = ref<Skill | null>(null)

  function formatTime(t?: string): string {
    if (!t) return '—'
    const d = new Date(t)
    return isNaN(d.getTime()) ? t : d.toLocaleString('zh-CN')
  }

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

  async function loadSkills() {
    skillState.value = 'loading'
    try {
      skills.value = await skillApi.list()
      skillState.value = skills.value.length ? 'ready' : 'empty'
    } catch (e) {
      skillState.value = classifyError(e)
    }
  }

  async function doCreateSkill() {
    if (!skillForm.name.trim()) {
      ElMessage.warning('请填写 Skill 名称')
      return
    }
    creating.value = true
    try {
      await skillApi.create({ ...skillForm })
      ElMessage.success('草稿已创建')
      createDialog.value = false
      skillForm.name = ''
      skillForm.code = ''
      skillForm.description = ''
      loadSkills()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      creating.value = false
    }
  }

  async function doReviewSkill(s: Skill, action: SkillReviewAction, note?: string) {
    if (action === 'reject' || action === 'rollback') {
      try {
        await ElMessageBox.confirm(
          `确定${action === 'reject' ? '驳回' : '回滚'} Skill「${s.name || s.id}」吗？`,
          '二次确认',
          { type: 'warning' },
        )
      } catch {
        return
      }
    }
    reviewing.value = true
    try {
      await skillApi.review(s.id, { action, note })
      ElMessage.success('操作成功')
      loadSkills()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      reviewing.value = false
    }
  }

  function openSkillRejectDialog(s: Skill) {
    skillRejectTarget.value = s
    skillRejectNote.value = ''
    skillRejectDialog.value = true
  }

  async function confirmRejectSkill() {
    if (!skillRejectTarget.value) return
    await doReviewSkill(skillRejectTarget.value, 'reject', skillRejectNote.value || undefined)
    skillRejectDialog.value = false
  }

  onMounted(() => {
    loadCandidates()
    loadSkills()
  })
</script>

<style scoped>
  .review {
    padding: 4px;
  }
  .toolbar {
    margin-bottom: 16px;
  }
</style>
