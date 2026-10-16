<template>
  <div class="dashboard">
    <h2 class="page-title">工作台</h2>
    <el-row :gutter="16">
      <!-- 待办任务 -->
      <el-col :span="6">
        <el-card>
          <template #header>待办任务</template>
          <AsyncState :state="pendingState" empty-text="没有待办任务" @retry="loadAll">
            <div class="stat-number">{{ pendingTasks.length }}</div>
            <div class="stat-desc">排队中 / 运行中 / 待审核</div>
          </AsyncState>
        </el-card>
      </el-col>
      <!-- 最近任务 -->
      <el-col :span="6">
        <el-card>
          <template #header>最近任务</template>
          <AsyncState :state="recentState" empty-text="暂无任务" @retry="loadAll">
            <ul class="mini-list">
              <li v-for="t in recentTasks" :key="t.id">
                <el-tag :type="taskStatusMeta(t.status).type" size="small">
                  {{ taskStatusMeta(t.status).label }}
                </el-tag>
                <span class="mini-title">{{ t.task_type }}</span>
              </li>
            </ul>
          </AsyncState>
        </el-card>
      </el-col>
      <!-- 知识库状态 -->
      <el-col :span="6">
        <el-card>
          <template #header>知识库状态</template>
          <AsyncState :state="kbState" empty-text="知识库为空" @retry="loadAll">
            <div class="stat-number">{{ docTotal }}</div>
            <div class="stat-desc">文档总数 · 解析失败 {{ failedDocs }}</div>
          </AsyncState>
        </el-card>
      </el-col>
      <!-- 待审核数 -->
      <el-col :span="6">
        <el-card>
          <template #header>待审核</template>
          <AsyncState :state="reviewState" empty-text="没有待审核内容" @retry="loadAll">
            <div class="stat-number">{{ pendingReview }}</div>
            <div class="stat-desc">待专家审核的候选知识</div>
            <el-button v-if="pendingReview > 0" link type="primary" @click="router.push('/review')">
              前往审核
            </el-button>
          </AsyncState>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="admin-card">
      <template #header>平台概况</template>
      <AsyncState :state="statsState" empty-text="暂无统计数据" @retry="loadAll">
        <el-descriptions :column="3" border>
          <el-descriptions-item label="任务总数">{{ stats.tasks_total ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="文档总数">{{ stats.documents_total ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="反馈总数">{{ stats.feedback_total ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="平均任务耗时">
            {{ stats.avg_task_duration_seconds != null ? `${stats.avg_task_duration_seconds}s` : '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="模型成本累计">
            {{ stats.llm_cost_total ?? '—' }}
          </el-descriptions-item>
        </el-descriptions>
      </AsyncState>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { taskApi, knowledgeApi, adminApi } from '@/api/modules'
import AsyncState from '@/components/AsyncState.vue'
import { classifyError, taskStatusMeta, type ViewState } from '@/utils/status'
import type { AdminStats, Task } from '@/types/api'

const router = useRouter()

const pendingState = ref<ViewState>('loading')
const recentState = ref<ViewState>('loading')
const kbState = ref<ViewState>('loading')
const reviewState = ref<ViewState>('loading')
const statsState = ref<ViewState>('loading')

const pendingTasks = ref<Task[]>([])
const recentTasks = ref<Task[]>([])
const docTotal = ref(0)
const failedDocs = ref(0)
const pendingReview = ref(0)
const stats = reactive<AdminStats>({})

async function loadAll() {
  pendingState.value = recentState.value = kbState.value = reviewState.value = statsState.value = 'loading'

  try {
    const tasks = await taskApi.list({ page: 1, page_size: 5 })
    recentTasks.value = tasks.items
    pendingTasks.value = tasks.items.filter(t => ['queued', 'running', 'awaiting_review'].includes(t.status))
    recentState.value = tasks.items.length ? 'ready' : 'empty'
    pendingState.value = 'ready'
  } catch (e) {
    pendingState.value = recentState.value = classifyError(e)
  }

  try {
    const docs = await knowledgeApi.list({ page: 1, page_size: 1 })
    docTotal.value = docs.meta.total
    failedDocs.value = 0 // 首版仅展示总数；失败数由知识库页明细展示
    kbState.value = docs.meta.total ? 'ready' : 'empty'
  } catch (e) {
    kbState.value = classifyError(e)
  }

  try {
    const cands = await knowledgeApi.candidates({ status: 'pending_review', page: 1, page_size: 1 })
    pendingReview.value = cands.meta.total
    reviewState.value = cands.meta.total ? 'ready' : 'empty'
  } catch (e) {
    reviewState.value = classifyError(e)
  }

  try {
    Object.assign(stats, await adminApi.stats())
    statsState.value = 'ready'
  } catch (e) {
    statsState.value = classifyError(e)
  }
}

onMounted(loadAll)
</script>

<style scoped>
.dashboard {
  padding: 4px;
}
.page-title {
  margin: 0 0 16px;
}
.stat-number {
  font-size: 36px;
  font-weight: 700;
  color: #409eff;
}
.stat-desc {
  color: #909399;
  font-size: 13px;
  margin-top: 4px;
}
.mini-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mini-title {
  margin-left: 8px;
  font-size: 13px;
  color: #606266;
}
.admin-card {
  margin-top: 16px;
}
</style>
