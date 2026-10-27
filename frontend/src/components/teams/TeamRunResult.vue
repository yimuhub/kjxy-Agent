<template>
  <section class="run-result">
    <el-alert
      v-if="run.output_format && run.output_format !== 'text'"
      type="info"
      :closable="false"
      show-icon
      :title="exportable ? `文档结果已就绪：${run.output_format === 'docx' ? 'Word' : 'Markdown'}，可在下方下载。` : '本次选择文档结果，运行完成并生成答案后可下载。'"
    />
    <el-card
      v-if="run.answer"
      class="result-card"
      shadow="never"
    >
      <template #header>
        <div class="answer-head">
          <span>班组最终结论</span>
          <div
            v-if="exportable"
            class="downloads"
          >
            <el-button
              size="small"
              :loading="downloading === 'md'"
              :disabled="!!downloading"
              @click="download('md')"
            >
              下载 Markdown
            </el-button>
            <el-button
              size="small"
              :loading="downloading === 'docx'"
              :disabled="!!downloading"
              @click="download('docx')"
            >
              下载 Word
            </el-button>
          </div>
        </div>
      </template>
      <SafeMarkdown :content="run.answer" />
    </el-card>
    <el-alert
      v-else-if="run.status === 'completed'"
      title="运行已完成，但未返回答案。可刷新详情确认。"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="downloadError"
      :title="downloadError"
      type="error"
      :closable="false"
      show-icon
    />
    <template v-if="run.research || run.status === 'completed'">
      <h3>知识库引用</h3>
      <div
        v-for="(source, index) in run.research?.knowledge ?? []"
        :key="`${source.chunk_id}-${index}`"
        class="source"
      >
        <strong>{{ index + 1 }}. {{ source.title || '未命名文档' }}</strong>
        <p class="source-meta">
          {{ source.source || '知识库' }}<span v-if="source.page_number != null"> · 第 {{ source.page_number }} 页</span>
          <span v-if="source.trust_level"> · 可信等级：{{ source.trust_level }}</span>
          <span v-if="source.score != null"> · 匹配分：{{ source.score }}</span>
        </p>
        <p class="quote">
          {{ source.quote }}
        </p>
      </div>
      <el-alert
        v-if="!run.research?.knowledge?.length"
        title="本次未返回知识库引用，答案缺少知识库证据，请注意核验。"
        type="warning"
        :closable="false"
      />
      <h3>联网引用</h3>
      <div
        v-for="(source, index) in run.research?.web ?? []"
        :key="`${source.url}-${index}`"
        class="source"
      >
        <a
          v-if="safeHttpUrl(source.url)"
          :href="safeHttpUrl(source.url)"
          target="_blank"
          rel="noopener noreferrer"
        >{{ index + 1 }}. {{ source.title || source.url }}</a>
        <strong v-else>{{ index + 1 }}. {{ source.title || '无效来源链接' }}（链接不可访问）</strong>
        <p class="quote">
          {{ source.snippet }}
        </p>
      </div>
      <el-alert
        v-if="!run.research?.web?.length"
        title="本次未返回联网引用，可能无结果或联网服务离线；请结合下方警告核验。"
        type="warning"
        :closable="false"
      />
      <div
        v-if="run.research?.warnings?.length"
        class="warnings"
      >
        <h3>检索与运行警告</h3>
        <el-alert
          v-for="(warning, index) in run.research.warnings"
          :key="index"
          :title="warning"
          type="warning"
          :closable="false"
          show-icon
        />
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { teamApi, type TeamRun } from '@/api/modules'
import SafeMarkdown from './SafeMarkdown.vue'
import { canExportRun, safeHttpUrl, saveDocumentBlob, teamDocumentFilename } from './presentation'

const props = defineProps<{ run: TeamRun }>()
const downloading = ref<'md' | 'docx' | ''>('')
const downloadError = ref('')
const exportable = computed(() => canExportRun(props.run))
async function download(format: 'md' | 'docx') {
  if (!exportable.value || downloading.value) return
  const run = props.run
  downloading.value = format
  downloadError.value = ''
  try {
    const blob = await teamApi.document(run.id, format)
    saveDocumentBlob(blob, teamDocumentFilename(run.team_name, run.id, format))
  } catch {
    downloadError.value = '文档下载失败，请检查连接后重试。'
  } finally {
    downloading.value = ''
  }
}
</script>

<style scoped>
.run-result { margin-top: 12px; }
.result-card, .run-result :deep(.el-alert) { margin-top: 10px; }
.answer-head, .downloads { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.answer-head { justify-content: space-between; font-weight: 600; }
h3 { font-size: 14px; margin: 20px 0 10px; }
.source { border: 1px solid var(--ds-border); border-radius: var(--ds-radius-control); padding: 10px 12px; margin-top: 8px; overflow-wrap: anywhere; }
.source a { color: var(--el-color-primary); }
.source-meta { color: var(--ds-text-3); font-size: 12px; margin: 6px 0; }
.quote { color: var(--ds-text-2); white-space: pre-wrap; font-size: 13px; margin: 6px 0 0; }
.downloads :deep(.el-button + .el-button) { margin-left: 0; }
</style>
