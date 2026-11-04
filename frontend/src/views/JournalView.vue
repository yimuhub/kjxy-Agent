<template>
  <div class="journal-page">
    <div class="page-head">
      <div>
        <h3>工作日志</h3>
        <p class="sub">平台活动自动汇总成草稿，核对补充后提交，月报按日志聚合导出 Word。</p>
      </div>
      <div class="head-actions">
        <el-button
          type="primary"
          :loading="drafting"
          @click="makeDraft('daily')"
        >
          生成今日草稿
        </el-button>
        <el-button
          :loading="drafting"
          @click="makeDraft('monthly')"
        >
          生成本月月报草稿
        </el-button>
        <el-button
          :loading="exporting"
          @click="exportMonthly"
        >
          导出月报 Word
        </el-button>
      </div>
    </div>

    <!-- 考核进度 -->
    <div class="stats-row">
      <el-card class="stat">
        <div class="stat-num">{{ stats?.daily_total ?? '—' }}</div>
        <div class="stat-label">本月日志篇数</div>
      </el-card>
      <el-card class="stat">
        <div class="stat-num">{{ stats?.daily_approved ?? '—' }}</div>
        <div class="stat-label">已审核通过</div>
      </el-card>
      <el-card class="stat">
        <div class="stat-num">{{ stats?.active_days ?? '—' }}</div>
        <div class="stat-label">活跃天数</div>
      </el-card>
      <el-card class="stat">
        <div class="stat-num">{{ stats?.monthly_total ?? '—' }}</div>
        <div class="stat-label">月报草稿</div>
      </el-card>
      <el-card class="stat stat-wide">
        <el-progress
          :percentage="Math.round((stats?.annual_progress ?? 0) * 100)"
          :stroke-width="10"
          :status="(stats?.on_track ?? true) ? 'success' : 'warning'"
        />
        <div class="stat-label">
          年度达标牵引：{{ stats?.year_total ?? 0 }}/{{ stats?.annual_target ?? 100 }} 篇 ·
          周均 {{ stats?.weekly_pace ?? 0 }} 篇 ·
          预计 {{ stats?.projected_year_total ?? 0 }} 篇（{{ (stats?.on_track ?? true) ? '进度正常' : '进度偏慢，建议补填' }}）
        </div>
      </el-card>
    </div>

    <!-- 待办提示 -->
    <el-alert
      v-if="submittedCount > 0"
      :title="`${submittedCount} 篇日志已提交，等待负责人/专家审核`"
      type="info"
      :closable="false"
      show-icon
      class="hint"
    />

    <!-- 列表 -->
    <el-table
      :data="rows"
      v-loading="loading"
      empty-text="还没有日志，点上方「生成今日草稿」开始"
    >
      <el-table-column
        prop="period_key"
        label="周期"
        width="130"
      />
      <el-table-column
        prop="title"
        label="标题"
        min-width="180"
        show-overflow-tooltip
      />
      <el-table-column
        label="状态"
        width="110"
      >
        <template #default="{ row }">
          <el-tag
            size="small"
            :type="statusTag(row.status)"
            effect="plain"
          >
            {{ statusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        v-if="canReview"
        prop="author_user_id"
        label="作者"
        width="140"
        show-overflow-tooltip
      />
      <el-table-column
        label="操作"
        width="230"
      >
        <template #default="{ row }">
          <el-button
            link
            type="primary"
            @click="openEdit(row as JournalRow)"
          >
            查看/编辑
          </el-button>
          <el-button
            v-if="row.status === 'draft' && row.author_user_id === myId"
            link
            type="success"
            @click="submitRow(row as JournalRow)"
          >
            提交
          </el-button>
          <template v-if="canReview && row.status === 'submitted'">
            <el-button
              link
              type="success"
              @click="reviewRow(row as JournalRow, 'approve')"
            >
              通过
            </el-button>
            <el-button
              link
              type="danger"
              @click="reviewRow(row as JournalRow, 'reject')"
            >
              驳回
            </el-button>
          </template>
        </template>
      </el-table-column>
    </el-table>

    <!-- 编辑/查看弹窗 -->
    <el-dialog
      v-model="editVisible"
      :title="editing?.title || '日志'"
      width="640px"
      top="6vh"
    >
      <el-input
        v-if="editable"
        v-model="editContent"
        type="textarea"
        :rows="14"
        maxlength="20000"
        show-word-limit
      />
      <pre
        v-else
        class="readonly-md"
      >{{ editContent }}</pre>
      <p
        v-if="editing?.review_note && editing?.status === 'draft'"
        class="review-note"
      >
        驳回意见：{{ editing.review_note }}
      </p>
      <template #footer>
        <el-button @click="editVisible = false">关闭</el-button>
        <el-button
          v-if="editable"
          type="primary"
          :loading="saving"
          @click="saveEdit"
        >
          保存
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref } from 'vue'
  import { ElMessage, ElMessageBox } from 'element-plus'
  import { journalApi, type JournalRow, type JournalStats } from '@/api/journal'
  import { useAuthStore } from '@/stores/auth'

  /**
   * 研究生/技术员的工作台（P0）：日志草稿自动聚合 → 核对提交 → 审核 → 月报导出。
   * 审核者（logs:review）在本页直接看全院列表并通过/驳回。
   */
  const auth = useAuthStore()
  const rows = ref<JournalRow[]>([])
  const stats = ref<JournalStats | null>(null)
  const loading = ref(false)
  const drafting = ref(false)
  const saving = ref(false)
  const exporting = ref(false)
  const editVisible = ref(false)
  const editing = ref<JournalRow | null>(null)
  const editContent = ref('')
  const myId = auth.user?.user_id ?? auth.user?.id ?? ''

  const canReview = computed(() => auth.hasPermission?.('logs:review') ?? true)
  const editable = computed(
    () => editing.value?.status === 'draft' && editing.value.author_user_id === myId,
  )
  const submittedCount = computed(() => rows.value.filter(r => r.status === 'submitted').length)
  const monthNow = () => new Date().toISOString().slice(0, 7)

  async function load(): Promise<void> {
    loading.value = true
    try {
      const scope = canReview.value ? 'yard' : 'mine'
      const [list, stat] = await Promise.all([journalApi.list(monthNow(), scope), journalApi.stats(monthNow())])
      rows.value = list.items
      stats.value = stat
    } catch {
      /* 错误 toast 由 client 统一处理 */
    } finally {
      loading.value = false
    }
  }

  async function makeDraft(kind: 'daily' | 'monthly'): Promise<void> {
    drafting.value = true
    try {
      const row = await journalApi.draft(kind)
      ElMessage.success(kind === 'daily' ? '今日草稿已就绪，请核对补充后提交' : '月报草稿已生成')
      await load()
      openEdit(row)
    } catch {
      /* toast 已统一处理 */
    } finally {
      drafting.value = false
    }
  }

  function openEdit(row: JournalRow): void {
    editing.value = row
    editContent.value = row.content_md
    editVisible.value = true
  }

  async function saveEdit(): Promise<void> {
    if (!editing.value) return
    saving.value = true
    try {
      const updated = await journalApi.save(editing.value.id, editContent.value)
      ElMessage.success('已保存')
      editVisible.value = false
      await load()
      editing.value = updated
    } catch {
      /* toast 已统一处理 */
    } finally {
      saving.value = false
    }
  }

  async function submitRow(row: JournalRow): Promise<void> {
    try {
      await journalApi.submit(row.id)
      ElMessage.success('已提交，等待审核')
      await load()
    } catch {
      /* toast 已统一处理 */
    }
  }

  async function reviewRow(row: JournalRow, action: 'approve' | 'reject'): Promise<void> {
    let note: string | undefined
    if (action === 'reject') {
      const { value } = await ElMessageBox.prompt('请填写驳回意见（作者会看到）', '驳回', {
        inputPlaceholder: '例如：补充培训人数',
      })
      note = value
    }
    try {
      await journalApi.review(row.id, action, note)
      ElMessage.success(action === 'approve' ? '已通过' : '已驳回')
      await load()
    } catch {
      /* toast 已统一处理 */
    }
  }

  async function exportMonthly(): Promise<void> {
    exporting.value = true
    try {
      await journalApi.exportMonthly(monthNow())
      ElMessage.success('月报已导出为 Word')
    } catch {
      /* toast 已统一处理 */
    } finally {
      exporting.value = false
    }
  }

  function statusLabel(s: string): string {
    return { draft: '草稿', submitted: '待审核', approved: '已通过' }[s] ?? s
  }
  type TagType = 'info' | 'warning' | 'success' | 'danger' | 'primary'
  function statusTag(s: string): TagType {
    return ({ draft: 'info', submitted: 'warning', approved: 'success' } as Record<string, TagType>)[s] ?? 'info'
  }

  onMounted(() => {
    void load()
  })
</script>

<style scoped>
  .journal-page {
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .page-head {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
  }
  .page-head h3 {
    margin: 0 0 4px;
  }
  .sub {
    margin: 0;
    font-size: 12.5px;
    color: var(--ds-text-3);
  }
  .head-actions {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .stats-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 12px;
  }
  .stat-wide {
    grid-column: 1 / -1;
    text-align: left;
  }
  .stat {
    text-align: center;
  }
  .stat-num {
    font-size: 26px;
    font-weight: 700;
    color: var(--ds-text-1);
  }
  .stat-label {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .hint {
    margin: 0;
  }
  .readonly-md {
    margin: 0;
    white-space: pre-wrap;
    font-family: var(--ds-font-mono, monospace);
    font-size: 12.5px;
    line-height: 1.7;
    color: var(--ds-text-2);
    max-height: 55vh;
    overflow: auto;
  }
  .review-note {
    margin: 8px 0 0;
    font-size: 12.5px;
    color: var(--ds-danger);
  }
</style>
