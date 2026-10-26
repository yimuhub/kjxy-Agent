<template>
  <div class="qa">
    <h2 class="page-title">
      知识问答
    </h2>
    <el-card>
      <el-form
        :model="form"
        label-width="80px"
      >
        <el-form-item label="问题">
          <el-input
            v-model="form.question"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="请输入农业技术问题，例如：江苏南京的水稻最近叶子发黄，是缺肥还是病害？"
          />
          <div class="hint">
            试试说清作物、地区和症状，例如：江苏南京的水稻最近叶子发黄，是缺肥还是病害？
          </div>
        </el-form-item>
        <!-- 检索数量属调参项，折叠进高级选项，默认 5 -->
        <el-collapse class="advanced">
          <el-collapse-item
            title="高级选项"
            name="advanced"
          >
            <el-form-item
              label="检索数量"
              class="mb0"
            >
              <el-input-number
                v-model="form.top_k"
                :min="1"
                :max="20"
              />
              <span class="hint inline">召回的知识段落数量，已开启句级相关性裁剪以减少 Token 消耗</span>
            </el-form-item>
          </el-collapse-item>
        </el-collapse>
        <el-form-item>
          <el-button
            type="primary"
            :loading="asking"
            :disabled="!form.question.trim()"
            @click="ask"
          >
            提问
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <!-- 本地提问历史：最近 20 条，仅存本机浏览器 -->
    <el-card
      v-if="history.length"
      class="history-card"
    >
      <template #header>
        <div class="history-header">
          <span>最近提问（本机）</span>
          <el-button
            link
            type="danger"
            @click="clearHistory"
          >
            清空
          </el-button>
        </div>
      </template>
      <ul class="history-list">
        <li
          v-for="(h, i) in history"
          :key="i"
        >
          <el-link
            type="primary"
            :underline="false"
            class="history-item"
            @click="useHistory(h)"
          >
            {{ h.q }}
          </el-link>
          <span class="history-time">{{ formatTime(h.t) }}</span>
        </li>
      </ul>
    </el-card>

    <el-card
      v-if="asked"
      class="answer-card"
    >
      <template #header>
        <div class="answer-header">
          <div class="answer-header-left">
            <span>回答</span>
            <span
              v-if="result?.kind === 'sync' && result.output.token_usage"
              class="qa-token-badge"
            >
              ⚡ 本次消耗 {{ result.output.token_usage.total_tokens.toLocaleString() }} tokens
              <span class="qa-token-sub">
                （输入 {{ result.output.token_usage.input_tokens }} / 输出 {{ result.output.token_usage.output_tokens }}
                <template v-if="result.output.token_usage.rag_tokens != null">
                  · 知识检索上下文 {{ result.output.token_usage.rag_tokens }} tokens
                </template>
                <template v-if="result.output.token_usage.latency_ms != null">
                  · {{ result.output.token_usage.latency_ms }} ms
                </template>）
              </span>
            </span>
          </div>
          <div>
            <el-button
              size="small"
              @click="expertDialog = true"
            >
              转专家
            </el-button>
            <el-button
              size="small"
              @click="feedbackDialog = true"
            >
              反馈
            </el-button>
          </div>
        </div>
      </template>

      <!-- 回答区统一走四态：加载骨架 / 失败重试 / 无权限 / 结果 -->
      <AsyncState
        :state="answerState"
        :error-text="qaError"
        class="answer-body"
        @retry="ask"
      >
        <template v-if="result?.kind === 'sync'">
          <el-alert
            v-if="result.output.needs_expert_review"
            title="该回答建议专家复核"
            type="warning"
            show-icon
            class="mb"
          />
          <div class="answer-text">
            {{ result.output.answer }}
          </div>
          <el-alert
            v-for="(w, i) in result.output.warnings"
            :key="i"
            :title="w"
            type="info"
            show-icon
            class="mb"
          />
          <h4>引用来源（{{ result.output.citations.length }}）</h4>
          <el-empty
            v-if="!result.output.citations.length"
            description="本次回答未引用知识库片段。若问题偏门，可换个说法：试试说清作物、地区和症状，例如：江苏南京的水稻最近叶子发黄，是缺肥还是病害？"
          />
          <el-collapse v-else>
            <el-collapse-item
              v-for="c in result.output.citations"
              :key="c.chunk_id"
              :name="c.chunk_id"
            >
              <template #title>
                <span class="cite-title">{{ c.title }}</span>
                <el-tag
                  size="small"
                  class="cite-tag"
                >
                  {{ c.source }}
                </el-tag>
                <span
                  v-if="c.page_number != null"
                  class="cite-page"
                >第 {{ c.page_number }} 页</span>
              </template>
              <blockquote class="quote">
                {{ c.quote }}
              </blockquote>
              <!-- chunk_id/document_id 属调试信息，默认收起 -->
              <el-collapse class="debug-collapse">
                <el-collapse-item
                  title="调试信息"
                  name="debug"
                >
                  <div class="cite-meta">
                    chunk_id: {{ c.chunk_id }} · document_id: {{ c.document_id }}
                  </div>
                </el-collapse-item>
              </el-collapse>
            </el-collapse-item>
          </el-collapse>
        </template>
        <el-result
          v-else-if="result?.kind === 'async'"
          icon="info"
          title="已转为异步任务"
          :sub-title="`任务 ID：${result.task_id}，可在任务中心查看进度。`"
        >
          <template #extra>
            <el-button
              type="primary"
              @click="router.push('/tasks')"
            >
              前往任务中心
            </el-button>
          </template>
        </el-result>
      </AsyncState>
    </el-card>

    <!-- 转专家：以反馈形式提交复核请求 -->
    <el-dialog
      v-model="expertDialog"
      title="转专家复核"
      width="480px"
    >
      <p>将当前问题与回答提交给专家复核（以一条反馈记录创建）。</p>
      <template #footer>
        <el-button @click="expertDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="expertLoading"
          @click="submitExpert"
        >
          提交复核
        </el-button>
      </template>
    </el-dialog>

    <!-- 反馈 -->
    <el-dialog
      v-model="feedbackDialog"
      title="回答反馈"
      width="520px"
    >
      <el-form
        :model="feedbackForm"
        label-width="90px"
      >
        <el-form-item label="评分">
          <el-radio-group v-model="feedbackForm.rating">
            <el-radio label="up">
              有帮助
            </el-radio>
            <el-radio label="down">
              没帮助
            </el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="反馈说明">
          <el-input
            v-model="feedbackForm.feedback_text"
            type="textarea"
            :rows="3"
          />
        </el-form-item>
        <el-form-item label="纠正答案">
          <el-input
            v-model="feedbackForm.corrected_answer"
            type="textarea"
            :rows="4"
            placeholder="如回答有误，请给出正确内容（将沉淀为候选知识）"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="feedbackDialog = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="feedbackLoading"
          @click="submitFeedback"
        >
          提交
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, reactive, ref } from 'vue'
  import { useRoute, useRouter } from 'vue-router'
  import { ElMessage } from 'element-plus'
  import { qaApi, feedbackApi } from '@/api/modules'
  import { extractErrorMessage } from '@/api/client'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, friendlyErrorMessage, type ViewState } from '@/utils/status'
  import { formatTime } from '@/utils/format'
  import type { QAAnswerResult } from '@/types/api'

  const route = useRoute()
  const router = useRouter()
  const form = reactive({ question: '', top_k: 5 })
  const asking = ref(false)
  const asked = ref(false)
  const result = ref<QAAnswerResult | null>(null)
  const qaError = ref('')
  const answerState = ref<ViewState>('loading')

  const expertDialog = ref(false)
  const expertLoading = ref(false)
  const feedbackDialog = ref(false)
  const feedbackLoading = ref(false)
  const feedbackForm = reactive({
    rating: 'up' as 'up' | 'down',
    feedback_text: '',
    corrected_answer: '',
  })

  const HISTORY_KEY = 'ky_qa_history'
  const HISTORY_LIMIT = 20
  interface HistoryItem {
    q: string
    t: string
  }
  const history = ref<HistoryItem[]>([])

  function loadHistory() {
    try {
      const raw = localStorage.getItem(HISTORY_KEY)
      const parsed = raw ? JSON.parse(raw) : []
      history.value = Array.isArray(parsed)
        ? parsed.filter(h => typeof h?.q === 'string').slice(0, HISTORY_LIMIT)
        : []
    } catch {
      history.value = []
    }
  }

  function pushHistory(question: string) {
    history.value = [
      { q: question, t: new Date().toISOString() },
      ...history.value.filter(h => h.q !== question),
    ].slice(0, HISTORY_LIMIT)
    try {
      localStorage.setItem(HISTORY_KEY, JSON.stringify(history.value))
    } catch {
      /* ignore */
    }
  }

  function useHistory(h: HistoryItem) {
    form.question = h.q
  }

  function clearHistory() {
    history.value = []
    localStorage.removeItem(HISTORY_KEY)
  }

  async function ask() {
    asked.value = true
    asking.value = true
    answerState.value = 'loading'
    qaError.value = ''
    result.value = null
    try {
      const question = form.question.trim()
      result.value = await qaApi.answer({ question, top_k: form.top_k, mode: 'sync' })
      answerState.value = 'ready'
      pushHistory(question)
    } catch (err) {
      const { code, message } = extractErrorMessage(err)
      qaError.value = friendlyErrorMessage(code, message)
      answerState.value = classifyError(err)
    } finally {
      asking.value = false
    }
  }

  async function submitExpert() {
    expertLoading.value = true
    try {
      await feedbackApi.submit({ feedback_text: `请求专家复核。问题：${form.question.trim()}` })
      ElMessage.success('已提交专家复核')
      expertDialog.value = false
    } catch {
      /* handled */
    } finally {
      expertLoading.value = false
    }
  }

  async function submitFeedback() {
    feedbackLoading.value = true
    try {
      await feedbackApi.submit({
        rating: feedbackForm.rating,
        feedback_text: feedbackForm.feedback_text || undefined,
        corrected_answer: feedbackForm.corrected_answer || undefined,
      })
      ElMessage.success('反馈已提交')
      feedbackDialog.value = false
      feedbackForm.feedback_text = ''
      feedbackForm.corrected_answer = ''
    } catch {
      /* handled */
    } finally {
      feedbackLoading.value = false
    }
  }

  onMounted(() => {
    loadHistory()
    const q = route.query.q
    if (typeof q === 'string' && q.trim()) {
      form.question = q.trim()
    }
  })
</script>

<style scoped>
  .qa {
    padding: 4px;
    max-width: 960px;
  }
  .hint {
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.6;
  }
  .hint.inline {
    margin-left: 8px;
  }
  .advanced {
    margin: 0 0 16px 80px;
    border-top: none;
  }
  .mb0 {
    margin-bottom: 0;
  }
  .history-card {
    margin-top: 16px;
  }
  .history-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  .history-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .history-item {
    font-size: 13px;
    white-space: normal;
    text-align: left;
  }
  .history-time {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .answer-card {
    margin-top: 16px;
  }
  .answer-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }
  .answer-header-left {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }
  .qa-token-badge {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 12px;
    color: #34d399;
    background: rgba(16, 185, 129, 0.14);
    border: 1px solid rgba(16, 185, 129, 0.32);
    font-variant-numeric: tabular-nums;
  }
  .qa-token-sub {
    color: var(--ds-text-3);
    font-size: 11px;
  }
  .answer-body {
    min-height: 120px;
  }
  .answer-text {
    white-space: pre-wrap;
    line-height: 1.8;
    margin-bottom: 16px;
  }
  .mb {
    margin-bottom: 12px;
  }
  .cite-title {
    font-weight: 600;
  }
  .cite-tag {
    margin-left: 8px;
  }
  .cite-page {
    margin-left: 8px;
    color: var(--ds-text-3);
    font-size: 12px;
  }
  .quote {
    border-left: 3px solid var(--ds-accent);
    margin: 8px 0;
    padding: 8px 12px;
    background: var(--ds-glass);
    border-radius: 0 var(--ds-radius-control) var(--ds-radius-control) 0;
    color: var(--ds-text-2);
    white-space: pre-wrap;
  }
  .debug-collapse {
    border-top: none;
  }
  .cite-meta {
    font-size: 12px;
    color: var(--ds-text-3);
    word-break: break-all;
  }
  @media (max-width: 900px) {
    .advanced {
      margin-left: 0;
    }
  }
</style>
