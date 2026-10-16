<template>
  <div class="qa">
    <h2 class="page-title">知识问答</h2>
    <el-card>
      <el-form :model="form" label-width="80px">
        <el-form-item label="问题">
          <el-input
            v-model="form.question"
            type="textarea"
            :rows="3"
            maxlength="2000"
            show-word-limit
            placeholder="请输入农业技术问题，例如：水稻分蘖期需要重点观察什么？"
          />
        </el-form-item>
        <el-form-item label="检索数量">
          <el-input-number v-model="form.top_k" :min="1" :max="20" />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :loading="asking" :disabled="!form.question.trim()" @click="ask">
            提问
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card v-if="asked" class="answer-card">
      <template #header>
        <div class="answer-header">
          <span>回答</span>
          <div>
            <el-button size="small" @click="expertDialog = true">转专家</el-button>
            <el-button size="small" @click="feedbackDialog = true">反馈</el-button>
          </div>
        </div>
      </template>

      <div v-if="asking" class="answer-body"><el-skeleton :rows="6" animated /></div>
      <div v-else-if="qaError" class="answer-body">
        <el-result icon="error" title="问答失败" :sub-title="qaError">
          <template #extra>
            <el-button type="primary" @click="ask">重试</el-button>
          </template>
        </el-result>
      </div>
      <div v-else-if="result?.kind === 'sync'" class="answer-body">
        <el-alert
          v-if="result.output.needs_expert_review"
          title="该回答建议专家复核"
          type="warning"
          show-icon
          class="mb"
        />
        <div class="answer-text">{{ result.output.answer }}</div>
        <el-alert
          v-for="(w, i) in result.output.warnings"
          :key="i"
          :title="w"
          type="info"
          show-icon
          class="mb"
        />
        <h4>引用来源（{{ result.output.citations.length }}）</h4>
        <el-empty v-if="!result.output.citations.length" description="本次回答未引用知识库片段" />
        <el-collapse v-else>
          <el-collapse-item
            v-for="c in result.output.citations"
            :key="c.chunk_id"
            :name="c.chunk_id"
          >
            <template #title>
              <span class="cite-title">{{ c.title }}</span>
              <el-tag size="small" class="cite-tag">{{ c.source }}</el-tag>
              <span v-if="c.page_number != null" class="cite-page">第 {{ c.page_number }} 页</span>
            </template>
            <blockquote class="quote">{{ c.quote }}</blockquote>
            <div class="cite-meta">chunk_id: {{ c.chunk_id }} · document_id: {{ c.document_id }}</div>
          </el-collapse-item>
        </el-collapse>
      </div>
      <div v-else-if="result?.kind === 'async'" class="answer-body">
        <el-result
          icon="info"
          title="已转为异步任务"
          :sub-title="`任务 ID：${result.task_id}，可在任务中心查看进度。`"
        >
          <template #extra>
            <el-button type="primary" @click="router.push('/tasks')">前往任务中心</el-button>
          </template>
        </el-result>
      </div>
    </el-card>

    <!-- 转专家：以反馈形式提交复核请求 -->
    <el-dialog v-model="expertDialog" title="转专家复核" width="480px">
      <p>将当前问题与回答提交给专家复核（以一条反馈记录创建）。</p>
      <template #footer>
        <el-button @click="expertDialog = false">取消</el-button>
        <el-button type="primary" :loading="expertLoading" @click="submitExpert">提交复核</el-button>
      </template>
    </el-dialog>

    <!-- 反馈 -->
    <el-dialog v-model="feedbackDialog" title="回答反馈" width="520px">
      <el-form :model="feedbackForm" label-width="90px">
        <el-form-item label="评分">
          <el-radio-group v-model="feedbackForm.rating">
            <el-radio label="up">有帮助</el-radio>
            <el-radio label="down">没帮助</el-radio>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="反馈说明">
          <el-input v-model="feedbackForm.feedback_text" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="纠正答案">
          <el-input v-model="feedbackForm.corrected_answer" type="textarea" :rows="4" placeholder="如回答有误，请给出正确内容（将沉淀为候选知识）" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="feedbackDialog = false">取消</el-button>
        <el-button type="primary" :loading="feedbackLoading" @click="submitFeedback">提交</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { qaApi, feedbackApi } from '@/api/modules'
import { extractErrorMessage } from '@/api/client'
import type { QAAnswerResult } from '@/types/api'

const router = useRouter()
const form = reactive({ question: '', top_k: 5 })
const asking = ref(false)
const asked = ref(false)
const result = ref<QAAnswerResult | null>(null)
const qaError = ref('')

const expertDialog = ref(false)
const expertLoading = ref(false)
const feedbackDialog = ref(false)
const feedbackLoading = ref(false)
const feedbackForm = reactive({ rating: 'up' as 'up' | 'down', feedback_text: '', corrected_answer: '' })

async function ask() {
  asked.value = true
  asking.value = true
  qaError.value = ''
  result.value = null
  try {
    result.value = await qaApi.answer({ question: form.question.trim(), top_k: form.top_k, mode: 'sync' })
  } catch (err) {
    qaError.value = extractErrorMessage(err).message
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
    /* 错误 toast 已由 client 统一处理 */
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
    /* 错误 toast 已由 client 统一处理 */
  } finally {
    feedbackLoading.value = false
  }
}
</script>

<style scoped>
.qa {
  padding: 4px;
  max-width: 960px;
}
.page-title {
  margin: 0 0 16px;
}
.answer-card {
  margin-top: 16px;
}
.answer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
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
  color: #909399;
  font-size: 12px;
}
.quote {
  border-left: 3px solid #409eff;
  margin: 8px 0;
  padding: 8px 12px;
  background: #f5f7fa;
  white-space: pre-wrap;
}
.cite-meta {
  font-size: 12px;
  color: #909399;
}
</style>
