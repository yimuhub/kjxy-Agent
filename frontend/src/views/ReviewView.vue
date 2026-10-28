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
            <el-button
              :loading="skillUploading"
              aria-label="上传 SKILL.md 技能文件"
              @click="pickSkillFile"
            >
              上传 SKILL.md
            </el-button>
            <el-button
              :loading="pluginUploading"
              aria-label="导入插件 zip 包"
              @click="pickPluginFile"
            >
              导入插件 zip
            </el-button>
            <el-button
              aria-label="从 GitHub 导入技能"
              @click="openGithubDialog"
            >
              从 GitHub 导入
            </el-button>
            <input
              ref="skillFileInput"
              class="hidden-file"
              type="file"
              accept=".md,.markdown,.txt"
              aria-label="选择 Skill 文件"
              @change="handleSkillUpload"
            >
            <input
              ref="pluginFileInput"
              class="hidden-file"
              type="file"
              accept=".zip"
              aria-label="选择插件 zip 包"
              @change="handlePluginUpload"
            >
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
                label="名称"
                min-width="180"
              >
                <template #default="{ row }">
                  {{ skillName(row as SkillRow) }}
                </template>
              </el-table-column>
              <el-table-column
                label="编码"
                min-width="180"
              >
                <template #default="{ row }">
                  {{ skillCode(row as SkillRow) }}
                </template>
              </el-table-column>
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
                v-if="hasSkillSource"
                label="来源"
                width="120"
              >
                <template #default="{ row }">
                  {{ skillSourceLabel(row as SkillRow) }}
                </template>
              </el-table-column>
              <el-table-column
                v-if="hasSkillLicense"
                label="许可证"
                width="130"
                show-overflow-tooltip
              >
                <template #default="{ row }">
                  {{ skillLicense(row as SkillRow) || '未声明' }}
                </template>
              </el-table-column>
              <el-table-column
                v-if="hasSkillRisk"
                label="风险"
                width="110"
              >
                <template #default="{ row }">
                  <el-tag
                    size="small"
                    :type="riskTagType(skillRisk(row as SkillRow))"
                    effect="plain"
                  >
                    {{ riskLabel(skillRisk(row as SkillRow)) }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column
                label="描述"
                min-width="180"
                show-overflow-tooltip
              >
                <template #default="{ row }">
                  {{ skillDescription(row as SkillRow) }}
                </template>
              </el-table-column>
              <el-table-column
                label="操作"
                width="360"
              >
                <template #default="{ row }">
                  <el-button
                    link
                    type="info"
                    @click="openSkillEval(row as Skill)"
                  >
                    报告
                  </el-button>
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
        <el-form-item
          label="编码"
          required
        >
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

    <!-- 从 GitHub 导入技能：先预览 SKILL.md，再落库待审 -->
    <el-dialog
      v-model="githubDialog"
      title="从 GitHub 导入技能"
      width="640px"
    >
      <el-form label-width="80px">
        <el-form-item label="仓库">
          <el-input
            v-model="githubRepo"
            placeholder="owner/repo 或 https://github.com/owner/repo"
            clearable
            aria-label="GitHub 仓库地址"
            @input="resetGithubPreview"
            @keyup.enter="previewGithubRepo"
          />
        </el-form-item>
      </el-form>

      <el-alert
        v-if="githubError"
        class="github-error"
        type="error"
        :closable="false"
        show-icon
        :title="githubError"
      />

      <div
        v-if="githubPreview"
        class="github-preview"
      >
        <el-descriptions
          :column="1"
          border
          size="small"
        >
          <el-descriptions-item label="名称">
            {{ githubPreview.name || '未命名技能' }}
          </el-descriptions-item>
          <el-descriptions-item label="仓库">
            {{ githubPreview.repo || githubRepo }}
          </el-descriptions-item>
          <el-descriptions-item label="版本">
            {{ githubPreview.version || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="许可证">
            {{ githubPreview.license || '未声明' }}
          </el-descriptions-item>
          <el-descriptions-item label="描述">
            {{ githubPreview.description || '暂无描述' }}
          </el-descriptions-item>
          <el-descriptions-item label="来源链接">
            <a
              v-if="githubPreviewUrl"
              :href="githubPreviewUrl"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ githubPreviewUrl }}
            </a>
            <span v-else>{{ githubPreview.repo || githubRepo }}</span>
          </el-descriptions-item>
        </el-descriptions>
        <div class="github-definition">
          <h4>定义摘要</h4>
          <pre
            v-if="githubPreviewPrompt"
            class="code-block"
          >{{ githubPreviewPrompt }}</pre>
          <p
            v-else
            class="muted"
          >
            暂无定义摘要
          </p>
        </div>
      </div>

      <template #footer>
        <el-button @click="githubDialog = false">
          取消
        </el-button>
        <el-button
          :loading="githubPreviewing"
          @click="previewGithubRepo"
        >
          预览
        </el-button>
        <el-button
          type="primary"
          :disabled="!githubPreview"
          :loading="githubInstalling"
          @click="confirmGithubImport"
        >
          确认导入
        </el-button>
      </template>
    </el-dialog>

    <SkillEvalDialog
      v-model="skillEvalDialog"
      :skill="skillEvalTarget"
    />
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref, watch } from 'vue'
  import { useRoute } from 'vue-router'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { knowledgeApi } from '@/api/modules'
  import { libraryApi, skillApi } from '@/api/v3'
  import { extractErrorMessage } from '@/api/client'
  import AsyncState from '@/components/AsyncState.vue'
  import {
    definitionExcerpt,
    definitionOf,
    importSkillMessage,
    installNeedsReview,
    normalizeRepoInput,
    riskLabel,
    riskTagType,
    type GithubSearchResponse,
    type ImportResultLike,
    type InstallResultLike,
    type LibraryItem,
  } from '@/components/library/libraryMeta'
  import { classifyError, friendlyErrorMessage, type ViewState } from '@/utils/status'
  import type { Candidate, ReviewAction, Skill, SkillReviewAction } from '@/types/api'
  import { formatDateTime as formatTime } from '@/utils/format'
  import SkillEvalDialog from '@/components/library/SkillEvalDialog.vue'

  /** 后端 SkillOut 把业务字段放在 definition 内，且没有 name/code 顶层列。 */
  type SkillRow = Skill & {
    definition?: Record<string, unknown>
    definition_json?: Record<string, unknown>
    created_from?: string
    source?: string
    license?: string
    risk_level?: string
  }

  const route = useRoute()

  function tabFromQuery(): string {
    return route.query.tab === 'skills' ? 'skills' : 'candidates'
  }

  const activeTab = ref(tabFromQuery())

  const candidates = ref<Candidate[]>([])
  const candState = ref<ViewState>('loading')
  const reviewing = ref(false)
  const rejectDialog = ref(false)
  const rejectNote = ref('')
  const rejectTarget = ref<Candidate | null>(null)
  /** 批量模式：rejectTarget 为空时驳回对话框作用于勾选集合 */
  const bulkMode = ref(false)
  const selectedCandidates = ref<Candidate[]>([])

  const skills = ref<SkillRow[]>([])
  const skillState = ref<ViewState>('loading')
  const createDialog = ref(false)
  const creating = ref(false)
  const skillForm = reactive({ name: '', code: '', version: '0.1.0', description: '' })
  const skillRejectDialog = ref(false)
  const skillRejectNote = ref('')
  const skillRejectTarget = ref<Skill | null>(null)
  const skillEvalDialog = ref(false)
  const skillEvalTarget = ref<Skill | null>(null)

  const skillUploading = ref(false)
  const pluginUploading = ref(false)
  const skillFileInput = ref<HTMLInputElement | null>(null)
  const pluginFileInput = ref<HTMLInputElement | null>(null)

  const githubDialog = ref(false)
  const githubRepo = ref('')
  const githubPreviewing = ref(false)
  const githubInstalling = ref(false)
  const githubPreview = ref<LibraryItem | null>(null)
  const githubError = ref('')

  const hasSkillSource = computed(() =>
    skills.value.some(row => Boolean(row.source || row.created_from)),
  )
  const hasSkillLicense = computed(() =>
    skills.value.some(row => {
      const license = row.license || definitionOf(row).license
      return typeof license === 'string' && license !== '' && license !== 'unknown'
    }),
  )
  const hasSkillRisk = computed(() =>
    skills.value.some(row => Boolean(row.risk_level || definitionOf(row).risk_level)),
  )

  const githubPreviewUrl = computed(() => {
    const url = githubPreview.value?.source_url
    return typeof url === 'string' && /^https?:\/\//i.test(url) ? url : ''
  })
  const githubPreviewPrompt = computed(() =>
    definitionExcerpt(definitionOf(githubPreview.value), 600),
  )

  function definitionText(row: SkillRow): Record<string, unknown> {
    return definitionOf(row)
  }

  function skillName(row: SkillRow): string {
    const definition = definitionText(row)
    return (
      (typeof definition.name === 'string' && definition.name) ||
      row.name ||
      row.skill_code ||
      row.code ||
      row.id
    )
  }

  function skillCode(row: SkillRow): string {
    return row.skill_code || row.code || '—'
  }

  function skillDescription(row: SkillRow): string {
    const definition = definitionText(row)
    return (
      (typeof definition.description === 'string' && definition.description) ||
      row.description ||
      '—'
    )
  }

  function skillSourceLabel(row: SkillRow): string {
    const source = row.source || row.created_from
    if (!source) return '—'
    const map: Record<string, string> = {
      market: '市场',
      market_import: '本地导入',
      seed: '内置',
      manual: '手工',
      ai: 'AI 生成',
    }
    return map[source] ?? source
  }

  function skillLicense(row: SkillRow): string {
    const definition = definitionText(row)
    const license = row.license || definition.license
    if (typeof license !== 'string' || !license || license === 'unknown') return '未声明'
    return license
  }

  function skillRisk(row: SkillRow): string {
    const definition = definitionText(row)
    const risk = row.risk_level || definition.risk_level
    return typeof risk === 'string' ? risk : ''
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
      skills.value = (await skillApi.list({ pageSize: 100 })).items as unknown as SkillRow[]
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
    if (!skillForm.code.trim()) {
      ElMessage.warning('请填写 Skill 编码')
      return
    }
    creating.value = true
    try {
      const description = skillForm.description.trim()
      // 后端 SkillCreate 只接受 { skill_code, version, definition }；
      // 名称/描述必须放进 definition，否则列表无法展示且字段会被忽略。
      await skillApi.create({
        skill_code: skillForm.code.trim(),
        version: skillForm.version.trim() || '0.1.0',
        definition: {
          name: skillForm.name.trim(),
          description,
          prompt: description,
          steps: [],
        },
      })
      ElMessage.success('草稿已创建')
      createDialog.value = false
      skillForm.name = ''
      skillForm.code = ''
      skillForm.description = ''
      await loadSkills()
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
          `确定${action === 'reject' ? '驳回' : '回滚'} Skill「${skillName(s as SkillRow)}」吗？`,
          '二次确认',
          { type: 'warning' },
        )
      } catch {
        return
      }
    }
    reviewing.value = true
    try {
      await skillApi.review(s.id, action, note)
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

  function openSkillEval(s: Skill) {
    skillEvalTarget.value = s
    skillEvalDialog.value = true
  }

  async function confirmRejectSkill() {
    if (!skillRejectTarget.value) return
    await doReviewSkill(skillRejectTarget.value, 'reject', skillRejectNote.value || undefined)
    skillRejectDialog.value = false
  }

  // ---------- 本地导入 / GitHub 导入 ----------

  function pickSkillFile() {
    skillFileInput.value?.click()
  }

  function pickPluginFile() {
    pluginFileInput.value?.click()
  }

  async function handleSkillUpload(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    if (!/\.(md|markdown|txt)$/i.test(file.name)) {
      ElMessage.warning('技能文件仅支持 .md/.markdown/.txt')
      return
    }
    if (file.size > 256 * 1024) {
      ElMessage.warning('技能文件不能超过 256KB')
      return
    }
    skillUploading.value = true
    try {
      const result = (await libraryApi.importSkill(file)) as unknown as ImportResultLike
      ElMessage.success(importSkillMessage(result))
      await loadSkills()
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message))
    } finally {
      skillUploading.value = false
    }
  }

  async function handlePluginUpload(event: Event) {
    const input = event.target as HTMLInputElement
    const file = input.files?.[0]
    input.value = ''
    if (!file) return
    if (!/\.zip$/i.test(file.name)) {
      ElMessage.warning('插件包仅支持 .zip')
      return
    }
    if (file.size > 2 * 1024 * 1024) {
      ElMessage.warning('插件包不能超过 2MB')
      return
    }
    pluginUploading.value = true
    try {
      await libraryApi.importPlugin(file)
      ElMessage.success('插件已导入，等待专家审核')
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      ElMessage.error(friendlyErrorMessage(code, message))
    } finally {
      pluginUploading.value = false
    }
  }

  function openGithubDialog() {
    githubDialog.value = true
    githubError.value = ''
    githubPreview.value = null
  }

  function resetGithubPreview() {
    githubPreview.value = null
    githubError.value = ''
  }

  function githubFailureMessage(error: unknown): string {
    if (error instanceof Error && error.message === 'GITHUB_OFFLINE') {
      return '网络不可用，无法从 GitHub 导入，请改用本地导入'
    }
    const { code, message } = extractErrorMessage(error)
    if (code === 'NETWORK_UNAVAILABLE' || code === 'NETWORK_ERROR') {
      return '网络不可用，无法从 GitHub 导入，请改用本地导入'
    }
    return friendlyErrorMessage(code, message)
  }

  async function previewGithubRepo() {
    if (githubPreviewing.value) return
    const repo = normalizeRepoInput(githubRepo.value)
    if (!repo || !repo.includes('/')) {
      githubError.value = '请输入 owner/repo 或完整的 GitHub 仓库地址'
      return
    }
    githubPreviewing.value = true
    githubError.value = ''
    githubPreview.value = null
    try {
      const data = (await libraryApi.githubPreview(repo)) as unknown as LibraryItem
      githubPreview.value = {
        ...data,
        item_key: data.item_key || '',
        item_type: 'skill',
        source: 'github',
        code: data.code || repo,
        name: data.name || repo.split('/')[1] || repo,
        version: data.version || 'latest',
        description: data.description || '',
        risk_level: data.risk_level || 'medium',
        status: data.status || 'available',
        repo: data.repo || repo,
      }
    } catch (e) {
      githubError.value = githubFailureMessage(e)
    } finally {
      githubPreviewing.value = false
    }
  }

  function findGithubItem(items: LibraryItem[], repo: string): LibraryItem | null {
    const target = repo.toLowerCase()
    const repoName = target.split('/')[1] ?? ''
    return (
      items.find(item => {
        if (item.source !== 'github') return false
        const itemRepo = typeof item.repo === 'string' ? item.repo.toLowerCase() : ''
        const sourceUrl =
          typeof item.source_url === 'string' ? item.source_url.toLowerCase() : ''
        const name = (item.name || '').toLowerCase()
        return (
          itemRepo === target ||
          sourceUrl.includes(`github.com/${target}`) ||
          (!!repoName && name === repoName)
        )
      }) ?? null
    )
  }

  async function findGithubItemKey(repo: string): Promise<string | null> {
    const repoName = repo.split('/')[1] ?? ''
    const queries = [repo, repoName].filter((value, index, arr) => value && arr.indexOf(value) === index)
    let offline = false
    for (const query of queries) {
      const res = (await libraryApi.githubSearch(query)) as unknown as GithubSearchResponse
      if (res.offline) offline = true
      const hit = findGithubItem(res.items ?? [], repo)
      if (hit?.item_key) return hit.item_key
    }
    if (offline) throw new Error('GITHUB_OFFLINE')
    return null
  }

  async function confirmGithubImport() {
    if (!githubPreview.value || githubInstalling.value) return
    githubInstalling.value = true
    githubError.value = ''
    try {
      const repo = normalizeRepoInput(githubPreview.value.repo || githubRepo.value)
      let itemKey = githubPreview.value.item_key
      if (!itemKey && repo) {
        itemKey = (await findGithubItemKey(repo)) ?? ''
      }
      if (!itemKey) {
        githubError.value = '暂未找到可安装的 GitHub 技能条目，请确认仓库包含 SKILL.md 后重试'
        return
      }
      const result = (await libraryApi.install(itemKey)) as unknown as InstallResultLike
      ElMessage.success(
        installNeedsReview(result) ? '已安装，等待专家审核' : '已安装并启用',
      )
      githubDialog.value = false
      githubRepo.value = ''
      githubPreview.value = null
      await loadSkills()
    } catch (e) {
      githubError.value = githubFailureMessage(e)
    } finally {
      githubInstalling.value = false
    }
  }

  watch(
    () => route.query.tab,
    tab => {
      if (tab === 'skills' || tab === 'candidates') {
        activeTab.value = tab
        if (tab === 'skills') void loadSkills()
      }
    },
  )

  onMounted(() => {
    void loadCandidates()
    void loadSkills()
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
  .hidden-file {
    display: none;
  }
  .github-error {
    margin-bottom: 12px;
  }
  .github-preview {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }
  .github-definition h4 {
    margin: 0 0 8px;
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .muted {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .code-block {
    margin: 0;
    padding: 12px;
    max-height: 260px;
    overflow: auto;
    text-align: left;
    white-space: pre-wrap;
    word-break: break-word;
    background: var(--ds-field);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-control);
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-2);
  }
</style>
