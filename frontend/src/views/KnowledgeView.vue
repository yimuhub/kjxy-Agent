<template>
  <div class="kb">
    <div class="page-head">
      <h2 class="page-title">
        知识库
      </h2>
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
        上传文档
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
            accept=".pdf,.docx,.txt,.md,.csv"
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
            上传并解析
          </el-button>
        </el-form-item>
      </el-form>
      <p class="upload-hint">
        支持 PDF / DOCX / TXT / MD / CSV 格式，单个文件不超过 50MB。
      </p>
    </el-card>

    <!-- 检索测试：仅管理员可见（普通成员用知识问答即可） -->
    <el-card
      v-if="auth.isSpaceAdmin()"
      class="section"
    >
      <template #header>
        检索测试
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
          :data="hits"
          stripe
        >
          <el-table-column
            prop="title"
            label="文档"
          />
          <el-table-column
            prop="source"
            label="来源"
            width="140"
          />
          <el-table-column
            prop="score"
            label="得分"
            width="100"
          >
            <template #default="{ row }">
              {{ row.score.toFixed(3) }}
            </template>
          </el-table-column>
          <el-table-column
            prop="trust_level"
            label="可信度"
            width="100"
          />
          <el-table-column
            prop="page_number"
            label="页码"
            width="80"
          />
          <el-table-column
            prop="quote"
            label="片段"
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
          :data="docs"
          stripe
        >
          <el-table-column
            prop="title"
            label="标题"
          >
            <template #default="{ row }">
              {{ row.title || row.filename || row.id }}
            </template>
          </el-table-column>
          <el-table-column
            prop="filename"
            label="文件名"
          />
          <el-table-column
            label="解析状态"
            width="120"
          >
            <template #default="{ row }">
              <el-tag :type="docStatusMeta(row.status).type">
                {{ docStatusMeta(row.status).label }}
              </el-tag>
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
            prop="chunk_count"
            label="可检索段落数"
            width="120"
          >
            <template #default="{ row }">
              {{ row.chunk_count ?? '—' }}
            </template>
          </el-table-column>
          <el-table-column
            label="操作"
            width="160"
          >
            <template #default="{ row }">
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
  </div>
</template>

<script setup lang="ts">
  import { onMounted, reactive, ref } from 'vue'
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
  import type { ChunkHit, KnowledgeDocument } from '@/types/api'

  const auth = useAuthStore()

  /** 单个上传文件大小上限（与后端一致：50MB） */
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
      ElMessage.success('上传成功，已创建解析任务')
      uploadRef.value?.clearFiles()
      uploadFile.value = null
      uploadForm.title = ''
      loadDocs()
    } catch (err) {
      // 413/415 等由 client 统一 toast，这里仅补充中文说明
      const { code, message } = extractErrorMessage(err)
      ElMessage.warning(friendlyErrorMessage(code, message))
    } finally {
      uploading.value = false
    }
  }

  /** 重新解析：仅解析失败的文档可调，成功后重新拉列表 */
  async function doReparse(doc: KnowledgeDocument) {
    if (reparseId.value) return
    reparseId.value = doc.id
    try {
      await knowledgeApi.reparse(doc.id)
      ElMessage.success('已重新提交解析，可在任务中心查看进度')
      loadDocs()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
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
      /* 错误 toast 已由 client 统一处理 */
    }
  }

  onMounted(loadDocs)
</script>

<style scoped>
  .kb {
    padding: 4px;
    max-width: 1100px;
  }
  .page-head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    margin-bottom: 16px;
  }
  .page-head .page-title {
    margin-bottom: 0;
  }
  .section {
    margin-bottom: 16px;
  }
  .upload-hint {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .pager {
    margin-top: 16px;
    justify-content: flex-end;
  }
</style>
