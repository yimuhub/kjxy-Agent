<template>
  <el-dialog
    v-model="visible"
    append-to-body
    title="运行记录"
    width="min(1060px, 94vw)"
    top="5vh"
    @open="load"
  >
    <p class="note">
      显示当前账号可见的所有班组手动及定时运行；班组删除后仍可查看历史记录。
    </p>
    <el-form
      inline
      class="filters"
      @submit.prevent="applyFilters"
    >
      <el-form-item label="班组">
        <el-select
          v-model="filters.team_id"
          placeholder="全部班组"
          clearable
          filterable
          @change="applyFilters"
        >
          <el-option
            v-for="team in teamOptions"
            :key="team.id"
            :label="team.name"
            :value="team.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="状态">
        <el-select
          v-model="filters.status"
          placeholder="全部状态"
          clearable
          @change="applyFilters"
        >
          <el-option
            v-for="status in statuses"
            :key="status"
            :label="runStatusLabel(status)"
            :value="status"
          />
        </el-select>
      </el-form-item>
      <el-form-item label="触发方式">
        <el-select
          v-model="filters.trigger"
          placeholder="全部方式"
          clearable
          @change="applyFilters"
        >
          <el-option
            label="手动运行"
            value="manual"
          />
          <el-option
            label="定时运行"
            value="schedule"
          />
        </el-select>
      </el-form-item>
      <el-form-item>
        <el-button
          :loading="loading"
          @click="load"
        >
          刷新
        </el-button>
        <el-button @click="resetFilters">
          重置
        </el-button>
      </el-form-item>
    </el-form>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <el-table
      v-col-resize
      v-loading="loading"
      :data="runs"
      empty-text="没有符合条件的运行记录"
      class="runs-table"
    >
      <el-table-column
        label="班组"
        min-width="145"
      >
        <template #default="{ row }">
          {{ row.team_name || '历史班组' }}
        </template>
      </el-table-column>
      <el-table-column
        label="触发"
        width="86"
      >
        <template #default="{ row }">
          {{ row.trigger === 'schedule' ? '定时' : '手动' }}
        </template>
      </el-table-column>
      <el-table-column
        label="状态"
        width="100"
      >
        <template #default="{ row }">
          <el-tag
            :type="runStatusTag(row.status)"
            size="small"
          >
            {{ runStatusLabel(row.status) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        label="问题 / 结果"
        min-width="220"
      >
        <template #default="{ row }">
          <div
            class="preview"
            :title="row.input_preview"
          >
            {{ row.input_preview || '（无问题摘要）' }}
          </div>
          <div
            class="note preview"
            :title="row.last_error || row.answer_preview"
          >
            {{ row.last_error || row.answer_preview || '暂无结果' }}
          </div>
        </template>
      </el-table-column>
      <el-table-column
        label="格式"
        width="96"
      >
        <template #default="{ row }">
          {{ row.output_format === 'docx' ? 'Word' : row.output_format === 'md' ? 'Markdown' : '文字' }}
        </template>
      </el-table-column>
      <el-table-column
        label="创建时间"
        min-width="170"
      >
        <template #default="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column
        label="操作"
        width="80"
        fixed="right"
      >
        <template #default="{ row }">
          <el-button
            type="primary"
            link
            @click="emit('detail', row.id)"
          >
            详情
          </el-button>
        </template>
      </el-table-column>
    </el-table>
    <el-pagination
      v-model:current-page="page"
      v-model:page-size="pageSize"
      :total="total"
      :page-sizes="[10, 20, 50]"
      layout="total, prev, pager, next, sizes"
      :pager-count="5"
      :disabled="loading"
      class="pagination"
      @current-change="load"
      @size-change="applyFilters"
    />
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onUnmounted, reactive, ref, watch } from 'vue'
import { teamApi, type TeamDefinition, type TeamRunStatus, type TeamRunSummary, type TeamRunTrigger } from '@/api/modules'
import { formatTime } from '@/utils/format'
import { runStatusLabel, runStatusTag } from './presentation'

const visible = defineModel<boolean>({ required: true })
const props = defineProps<{ teams: TeamDefinition[] }>()
const emit = defineEmits<{ detail: [id: string] }>()
const statuses: TeamRunStatus[] = ['queued', 'running', 'completed', 'failed']
const filters = reactive<{ team_id: string; status: TeamRunStatus | ''; trigger: TeamRunTrigger | '' }>({ team_id: '', status: '', trigger: '' })
const runs = ref<TeamRunSummary[]>([])
const snapshots = reactive(new Map<string, string>())
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const loading = ref(false)
const error = ref('')
let requestVersion = 0
const teamOptions = computed(() => {
  const options = new Map(snapshots)
  for (const team of props.teams) options.set(team.id, team.name)
  return Array.from(options, ([id, name]) => ({ id, name }))
})

async function load() {
  if (!visible.value) return
  const version = ++requestVersion
  loading.value = true
  error.value = ''
  try {
    const result = await teamApi.allRuns({
      team_id: filters.team_id || undefined,
      status: filters.status || undefined,
      trigger: filters.trigger || undefined,
      page: page.value,
      page_size: pageSize.value,
    })
    if (version !== requestVersion) return
    runs.value = result.runs
    total.value = result.total
    for (const run of result.runs) {
      if (run.team_id) snapshots.set(run.team_id, run.team_name || '历史班组')
    }
  } catch {
    if (version !== requestVersion) return
    runs.value = []
    total.value = 0
    error.value = '运行记录加载失败，请点击刷新重试。'
  } finally {
    if (version === requestVersion) loading.value = false
  }
}

function applyFilters() {
  page.value = 1
  void load()
}
function resetFilters() {
  Object.assign(filters, { team_id: '', status: '', trigger: '' })
  applyFilters()
}
watch(visible, value => {
  if (!value) {
    requestVersion += 1
    loading.value = false
  }
})
onUnmounted(() => { requestVersion += 1 })
</script>

<style scoped>
.note { color: var(--ds-text-3); font-size: 12px; line-height: 1.6; }
.filters :deep(.el-select) { width: 160px; }
.filters :deep(.el-form-item) { margin-right: 16px; margin-bottom: 12px; }
.runs-table { margin-top: 12px; }
.preview { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pagination { margin-top: 16px; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
@media (max-width: 600px) {
  .filters :deep(.el-form-item) { display: flex; width: 100%; margin-right: 0; }
  .filters :deep(.el-select) { width: 100%; }
  .pagination { justify-content: flex-start; }
}
</style>
