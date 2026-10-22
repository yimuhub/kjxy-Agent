<template>
  <div class="dashboard">
    <h2 class="page-title">
      工作台
    </h2>

    <!-- 新手引导：三步上手 + 可以点击的示例问题 -->
    <el-card class="guide-card">
      <template #header>
        新手引导
      </template>
      <el-steps
        :active="3"
        align-center
        class="guide-steps"
      >
        <el-step
          title="上传资料"
          description="把技术手册、病例记录上传到知识库"
        />
        <el-step
          title="提问"
          description="在知识问答里用一句话描述作物、地区和症状"
        />
        <el-step
          title="反馈纠错"
          description="回答不准就点反馈，纠正内容会沉淀为知识"
        />
      </el-steps>
      <div class="guide-examples">
        <span class="guide-examples-label">试试这些示例问题：</span>
        <el-button
          v-for="q in exampleQuestions"
          :key="q"
          size="small"
          round
          @click="goAskExample(q)"
        >
          {{ q }}
        </el-button>
      </div>
    </el-card>

    <el-row :gutter="16">
      <!-- 待办任务 -->
      <el-col
        :span="6"
        :xs="12"
        :sm="12"
      >
        <el-card>
          <template #header>
            待办任务
          </template>
          <AsyncState
            :state="pendingState"
            empty-text="没有待办任务"
            @retry="loadAll"
          >
            <div class="stat-number">
              {{ pendingTasks.length }}
            </div>
            <div class="stat-desc">
              排队中 / 运行中 / 待审核
            </div>
          </AsyncState>
        </el-card>
      </el-col>
      <!-- 最近任务 -->
      <el-col
        :span="6"
        :xs="12"
        :sm="12"
      >
        <el-card>
          <template #header>
            最近任务
          </template>
          <AsyncState
            :state="recentState"
            empty-text="暂无任务"
            @retry="loadAll"
          >
            <ul class="mini-list">
              <li
                v-for="t in recentTasks"
                :key="t.id"
              >
                <el-tag
                  :type="taskStatusMeta(t.status).type"
                  size="small"
                >
                  {{ taskStatusMeta(t.status).label }}
                </el-tag>
                <span class="mini-title">{{ t.task_type }}</span>
              </li>
            </ul>
          </AsyncState>
        </el-card>
      </el-col>
      <!-- 知识库状态 -->
      <el-col
        :span="6"
        :xs="12"
        :sm="12"
      >
        <el-card>
          <template #header>
            知识库状态
          </template>
          <AsyncState
            :state="kbState"
            empty-text="知识库为空"
            @retry="loadAll"
          >
            <div class="stat-number">
              {{ docTotal }}
            </div>
            <div class="stat-desc">
              文档总数 · 解析失败 {{ failedDocs }}
            </div>
          </AsyncState>
        </el-card>
      </el-col>
      <!-- 待审核数 -->
      <el-col
        :span="6"
        :xs="12"
        :sm="12"
      >
        <el-card>
          <template #header>
            待审核
          </template>
          <AsyncState
            :state="reviewState"
            empty-text="没有待审核内容"
            @retry="loadAll"
          >
            <div class="stat-number">
              {{ pendingReview }}
            </div>
            <div class="stat-desc">
              待专家审核的候选知识
            </div>
            <el-button
              v-if="pendingReview > 0"
              link
              type="primary"
              @click="router.push('/review')"
            >
              前往审核
            </el-button>
          </AsyncState>
        </el-card>
      </el-col>
    </el-row>

    <el-card class="admin-card">
      <template #header>
        平台概况
      </template>
      <AsyncState
        :state="statsState"
        empty-text="暂无统计数据"
        @retry="loadAll"
      >
        <el-descriptions
          :column="3"
          border
        >
          <el-descriptions-item label="任务总数">
            {{
              stats.tasks?.total ?? '—'
            }}
          </el-descriptions-item>
          <el-descriptions-item label="文档总数">
            {{
              Object.values(stats.knowledge?.documents_by_status ?? {}).reduce(
                (a: number, b: number) => a + b,
                0,
              )
            }}
          </el-descriptions-item>
          <el-descriptions-item label="成员总数">
            {{ stats.members?.total ?? '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="模型调用">
            {{ stats.llm?.total_calls ?? '—' }} 次 · 输入 {{ stats.llm?.total_input_tokens ?? 0 }}
            / 输出 {{ stats.llm?.total_output_tokens ?? 0 }} tokens
          </el-descriptions-item>
          <el-descriptions-item label="模型成本累计">
            {{ stats.llm?.total_estimated_cost ?? '—' }}
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

  /** 新手引导示例问题：点击后跳到问答页预填（不自动发送） */
  const EXAMPLE_QUESTIONS = [
    '江苏南京的水稻最近叶子发黄，是缺肥还是病害？',
    '小麦赤霉病在扬花期怎么预防？',
    '柑橘园测土配方施肥报告怎么看？',
  ]
  const exampleQuestions = EXAMPLE_QUESTIONS

  function goAskExample(q: string) {
    router.push({ path: '/qa', query: { q } })
  }

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
    pendingState.value =
      recentState.value =
      kbState.value =
      reviewState.value =
      statsState.value =
        'loading'

    try {
      const tasks = await taskApi.list({ page: 1, page_size: 5 })
      recentTasks.value = tasks.items
      pendingTasks.value = tasks.items.filter(t =>
        ['queued', 'running', 'awaiting_review'].includes(t.status),
      )
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
      const cands = await knowledgeApi.candidates({
        status: 'pending_review',
        page: 1,
        page_size: 1,
      })
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
  .guide-card {
    margin-bottom: 16px;
  }
  .guide-steps {
    margin-bottom: 16px;
  }
  .guide-examples {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
  }
  .guide-examples-label {
    font-size: 13px;
    color: var(--ds-text-2);
  }
  .stat-number {
    font-size: 36px;
    font-weight: 700;
    color: var(--ds-accent);
    font-variant-numeric: tabular-nums;
  }
  .stat-desc {
    color: var(--ds-text-3);
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
    color: var(--ds-text-2);
  }
  .admin-card {
    margin-top: 16px;
  }
</style>
