<template>
  <div class="kb">
    <div class="page-head">
      <div>
        <h2 class="page-title">
          知识库
        </h2>
        <p class="page-sub">
          已集成 Microsoft MarkItDown 文档转 Markdown 引擎 + 标题树/表格感知分块器，自动清洗页眉页脚噪点并降低检索 Token 消耗。
        </p>
      </div>
      <div class="page-actions">
        <el-button
          link
          type="primary"
          @click="loadDocs"
        >
          刷新
        </el-button>
      </div>
    </div>

    <!-- 上传 -->
    <el-card class="section">
      <template #header>
        <div class="card-head-row">
          <span>上传文档（MarkItDown 快速结构化入库）</span>
          <el-tag
            size="small"
            type="success"
            effect="plain"
          >
            自动转 Markdown · 表格表头保留 · 检索上下文裁剪省 Token
          </el-tag>
        </div>
      </template>
      <el-form
        :model="uploadForm"
        label-width="80px"
        inline
      >
        <el-form-item label="标题">
          <el-input
            v-model="uploadForm.title"
            placeholder="可选，默认使用文件名"
          />
        </el-form-item>
        <el-form-item label="文件">
          <el-upload
            ref="uploadRef"
            :auto-upload="false"
            :limit="1"
            accept=".pdf,.docx,.xlsx,.pptx,.txt,.md,.csv,.html,.htm"
            :on-change="handleFileChange"
            :on-remove="() => (uploadFile = null)"
          >
            <el-button type="primary">
              选择文件
            </el-button>
          </el-upload>
        </el-form-item>
        <el-form-item>
          <el-button
            type="success"
            :loading="uploading"
            :disabled="!uploadFile"
            @click="doUpload"
          >
            上传并转化为结构化知识
          </el-button>
        </el-form-item>
      </el-form>
      <p class="upload-hint">
        支持 PDF / DOCX / XLSX / PPTX / CSV / HTML / TXT / MD 格式（单个文件 ≤ 50MB），上传后自动转化为层级 Markdown 并统计分块 Token。
      </p>
    </el-card>

    <!-- 检索测试：仅管理员可见（普通成员用知识问答即可） -->
    <el-card
      v-if="auth.isYardAdmin()"
      class="section"
    >
      <template #header>
        检索测试（含句级相关性裁剪后 Token 预览）
      </template>
      <el-form inline>
        <el-form-item>
          <el-input
            v-model="searchQuery"
            placeholder="输入检索关键词"
            style="width: 360px"
            @keyup.enter="doSearch"
          />
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="searching"
            :disabled="!searchQuery.trim()"
            @click="doSearch"
          >
            检索
          </el-button>
        </el-form-item>
      </el-form>
      <AsyncState
        :state="searchState"
        empty-text="输入关键词后点击检索"
        @retry="doSearch"
      >
        <el-table
          v-col-resize
          :data="hits"
          stripe
          max-height="420"
        >
          <el-table-column
            prop="title"
            label="文档"
            min-width="220"
          />
          <el-table-column
            prop="source"
            label="来源"
            width="140"
          />
          <el-table-column
            prop="score"
            label="得分"
            width="90"
          >
            <template #default="{ row }">
              {{ row.score.toFixed(3) }}
            </template>
          </el-table-column>
          <el-table-column
            prop="trust_level"
            label="可信度"
            width="90"
          />
          <el-table-column
            prop="page_number"
            label="页码"
            width="70"
          />
          <el-table-column
            label="片段 Token"
            width="105"
          >
            <template #default="{ row }">
              <span class="token-pill">⚡ {{ row.token_count ?? estimateQuoteTokens(row.quote) }}</span>
            </template>
          </el-table-column>
          <el-table-column
            prop="quote"
            label="裁剪后片段"
            show-overflow-tooltip
          />
        </el-table>
      </AsyncState>
    </el-card>

    <!-- 文档列表 -->
    <el-card class="section">
      <template #header>
        文档列表
      </template>
      <AsyncState
        :state="docState"
        empty-text="知识库暂无文档"
        @retry="loadDocs"
      >
        <el-table
          v-col-resize
          :data="docs"
          stripe
          max-height="560"
        >
          <el-table-column
            prop="title"
            label="标题"
            min-width="220"
          >
            <template #default="{ row }">
              {{ row.title || row.filename || row.id }}
            </template>
          </el-table-column>
          <el-table-column
            prop="filename"
            label="文件名"
            min-width="150"
          />
          <el-table-column
            label="解析状态"
            width="110"
          >
            <template #default="{ row }">
              <el-tag :type="docStatusMeta(row.status).type">
                {{ docStatusMeta(row.status).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            prop="chunk_count"
            label="结构化分块"
            width="105"
          >
            <template #default="{ row }">
              {{ row.chunk_count ?? '—' }} 段
            </template>
          </el-table-column>
          <el-table-column
            label="总 Token 数"
            width="125"
          >
            <template #default="{ row }">
              <span
                v-if="row.token_count != null"
                class="token-pill"
              >⚡ {{ row.token_count.toLocaleString() }}</span>
              <span v-else>—</span>
            </template>
          </el-table-column>
          <el-table-column
            label="失败原因"
            show-overflow-tooltip
          >
            <template #default="{ row }">
              {{ row.error_message || '—' }}
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="230"
          >
            <template #default="{ row }">
              <el-button
                v-if="row.status === 'indexed' || row.status === 'ready' || (row.chunk_count ?? 0) > 0"
                link
                type="primary"
                @click="openChunksDrawer(row as KnowledgeDocument)"
              >
                预览 Markdown 分块
              </el-button>
              <el-button
                v-if="row.status === 'failed'"
                link
                type="primary"
                :loading="reparseId === row.id"
                @click="doReparse(row as KnowledgeDocument)"
              >
                重新解析
              </el-button>
              <el-button
                link
                type="danger"
                @click="confirmRemove(row as KnowledgeDocument)"
              >
                删除
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="total"
          layout="prev, pager, next, total"
          class="pager"
          @change="loadDocs"
        />
      </AsyncState>
    </el-card>

    <!-- MarkItDown 结构化分块预览 -->
    <el-dialog
      v-model="chunkDrawerVisible"
      append-to-body
      :title="`Markdown 结构化分块预览：${activeDoc?.title || activeDoc?.filename || ''}`"
      width="680px"
      align-center
    >
      <div
        v-loading="chunksLoading"
        class="chunk-drawer-body"
      >
        <div
          v-if="activeDoc"
          class="chunk-summary"
        >
          <span>共 <b>{{ chunkItems.length }}</b> 个语义分块</span>
          <span>合计 <b>⚡ {{ totalPreviewTokens.toLocaleString() }} tokens</b></span>
          <span>解析引擎：<b>MarkItDown + 标题树/表格感知切分</b></span>
        </div>

        <el-alert
          v-if="chunksError"
          type="error"
          show-icon
          :closable="false"
          :title="chunksError"
          class="chunk-error"
        />

        <el-empty
          v-if="!chunksLoading && !chunksError && !chunkItems.length"
          description="该文档暂无分块记录"
        />

        <div
          v-for="ch in chunkItems"
          :key="ch.id"
          class="chunk-card"
        >
          <div class="chunk-head">
            <span class="chunk-idx">#{{ ch.chunk_index + 1 }}</span>
            <span
              v-if="ch.title_path"
              class="chunk-path"
            >📑 {{ ch.title_path }}</span>
            <span
              v-if="ch.page_number != null"
              class="chunk-page"
            >第 {{ ch.page_number }} 页</span>
            <span class="chunk-tok">⚡ {{ ch.token_count }} tokens</span>
          </div>
          <pre class="chunk-content">{{ ch.content }}</pre>
        </div>
      </div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage, ElMessageBox, type UploadFile } from 'element-plus'
  import { knowledgeApi } from '@/api/modules'
  import { extractErrorMessage } from '@/api/client'
  import AsyncState from '@/components/AsyncState.vue'
  import {
    classifyError,
    docStatusMeta,
    friendlyErrorMessage,
    type ViewState,
  } from '@/utils/status'
  import { useAuthStore } from '@/stores/auth'
  import type { ChunkHit, KnowledgeChunkPreview, KnowledgeDocument } from '@/types/api'

  const auth = useAuthStore()

  const MAX_UPLOAD_BYTES = 50 * 1024 * 1024

  const docs = ref<KnowledgeDocument[]>([])
  const page = ref(1)
  const pageSize = ref(20)
  const total = ref(0)
  const docState = ref<ViewState>('loading')
  const reparseId = ref('')

  const uploadForm = reactive({ title: '' })
  const uploadFile = ref<File | null>(null)
  const uploading = ref(false)
  const uploadRef = ref()

  const searchQuery = ref('')
  const hits = ref<ChunkHit[]>([])
  const searching = ref(false)
  const searchState = ref<ViewState>('empty')

  const chunkDrawerVisible = ref(false)
  const activeDoc = ref<KnowledgeDocument | null>(null)
  const chunkItems = ref<KnowledgeChunkPreview[]>([])
  const chunksLoading = ref(false)
  const chunksError = ref('')

  const totalPreviewTokens = computed(() =>
    chunkItems.value.reduce((sum, c) => sum + (c.token_count ?? 0), 0),
  )

  function estimateQuoteTokens(text?: string): number {
    if (!text) return 0
    return Math.max(1, Math.ceil(text.length * 0.6))
  }

  async function openChunksDrawer(doc: KnowledgeDocument) {
    activeDoc.value = doc
    chunkItems.value = []
    chunksError.value = ''
    chunkDrawerVisible.value = true
    chunksLoading.value = true
    try {
      chunkItems.value = await knowledgeApi.chunks(doc.id)
    } catch (e) {
      const { code, message } = extractErrorMessage(e)
      chunksError.value = friendlyErrorMessage(code, message)
    } finally {
      chunksLoading.value = false
    }
  }

  async function loadDocs() {
    docState.value = 'loading'
    try {
      const res = await knowledgeApi.list({ page: page.value, page_size: pageSize.value })
      docs.value = res.items
      total.value = res.meta.total
      docState.value = res.items.length ? 'ready' : 'empty'
    } catch (e) {
      docState.value = classifyError(e)
    }
  }

  function handleFileChange(file: UploadFile) {
    const raw = file.raw ?? null
    if (raw && raw.size > MAX_UPLOAD_BYTES) {
      ElMessage.warning('文件超过 50MB 限制，请拆分或压缩后再上传')
      uploadRef.value?.clearFiles()
      uploadFile.value = null
      return
    }
    uploadFile.value = raw
  }

  async function doUpload() {
    if (!uploadFile.value) return
    uploading.value = true
    try {
      await knowledgeApi.upload(uploadFile.value, uploadForm.title || undefined)
      ElMessage.success('上传成功，已通过 MarkItDown 引擎转化为结构化知识分块')
      uploadRef.value?.clearFiles()
      uploadFile.value = null
      uploadForm.title = ''
      loadDocs()
    } catch (err) {
      const { code, message } = extractErrorMessage(err)
      ElMessage.warning(friendlyErrorMessage(code, message))
    } finally {
      uploading.value = false
    }
  }

  async function doReparse(doc: KnowledgeDocument) {
    if (reparseId.value) return
    reparseId.value = doc.id
    try {
      await knowledgeApi.reparse(doc.id)
      ElMessage.success('已重新提交解析，可在任务中心查看进度')
      loadDocs()
    } catch {
      /* toast handled */
    } finally {
      reparseId.value = ''
    }
  }

  async function doSearch() {
    if (!searchQuery.value.trim()) return
    searching.value = true
    searchState.value = 'loading'
    try {
      hits.value = await knowledgeApi.search(searchQuery.value.trim(), 5)
      searchState.value = hits.value.length ? 'ready' : 'empty'
    } catch (e) {
      searchState.value = classifyError(e)
    } finally {
      searching.value = false
    }
  }

  async function confirmRemove(doc: KnowledgeDocument) {
    try {
      await ElMessageBox.confirm(
        `确定删除文档「${doc.title || doc.filename || doc.id}」吗？`,
        '删除确认',
        {
          type: 'warning',
          confirmButtonText: '删除',
          cancelButtonText: '取消',
        },
      )
    } catch {
      return
    }
    try {
      await knowledgeApi.remove(doc.id)
      ElMessage.success('已删除')
      loadDocs()
    } catch {
      /* toast handled */
    }
  }

  onMounted(loadDocs)
</script>

<style scoped>
  .kb {
    padding: 4px;
    max-width: 1140px;
  }
  .page-head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 16px;
  }
  .page-head .page-title {
    margin-bottom: 4px;
  }
  .page-sub {
    margin: 0;
    font-size: 12.5px;
    color: var(--ds-text-3);
  }
  .card-head-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px;
  }
  .section {
    margin-bottom: 16px;
  }
  .upload-hint {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .token-pill {
    display: inline-block;
    padding: 1px 8px;
    border-radius: 999px;
    font-size: 12px;
    color: var(--ds-success);
    background: rgba(16, 185, 129, 0.14);
    border: 1px solid rgba(16, 185, 129, 0.3);
    font-variant-numeric: tabular-nums;
  }
  .pager {
    margin-top: 16px;
    justify-content: flex-end;
  }
  .chunk-drawer-body {
    display: flex;
    flex-direction: column;
    gap: 12px;
    max-height: min(62vh, 640px);
    overflow-y: auto;
  }
  .chunk-summary {
    display: flex;
    flex-wrap: wrap;
    gap: 16px;
    padding: 10px 14px;
    border-radius: 10px;
    background: var(--ds-glass);
    border: 1px solid var(--ds-border);
    font-size: 12.5px;
    color: var(--ds-text-2);
  }
  .chunk-error {
    margin: 0;
  }
  .chunk-card {
    border: 1px solid var(--ds-border);
    border-radius: 10px;
    background: var(--ds-glass);
    overflow: hidden;
  }
  .chunk-head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--ds-border);
    font-size: 12px;
  }
  .chunk-idx {
    font-weight: 700;
    color: var(--ds-accent-text);
  }
  .chunk-path {
    color: var(--ds-text-1);
    font-weight: 600;
  }
  .chunk-page {
    color: var(--ds-text-3);
  }
  .chunk-tok {
    margin-left: auto;
    color: var(--ds-success);
    font-variant-numeric: tabular-nums;
  }
  .chunk-content {
    margin: 0;
    padding: 10px 12px;
    font-size: 12.5px;
    line-height: 1.65;
    white-space: pre-wrap;
    word-break: break-word;
    color: var(--ds-text-1);
    font-family: var(--ds-font-mono);
  }
</style>
