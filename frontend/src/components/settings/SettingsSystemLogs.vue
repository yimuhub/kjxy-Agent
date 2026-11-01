<template>
  <div class="settings-logs">
    <el-card class="section">
      <template #header>
        <div class="card-header">
          <span>系统日志</span>
          <div class="head-actions">
            <el-switch
              v-model="autoRefresh"
              inline-prompt
              active-text="自动刷新"
              inactive-text="自动刷新"
              size="small"
            />
            <el-button
              size="small"
              :loading="loading"
              @click="load()"
            >
              刷新
            </el-button>
            <el-button
              size="small"
              :loading="downloading"
              @click="download"
            >
              下载
            </el-button>
            <el-button
              size="small"
              :disabled="!items.length"
              @click="copy"
            >
              复制
            </el-button>
          </div>
        </div>
      </template>

      <p class="hint">
        记录所有系统操作与报错（已自动脱敏）。排查指挥官/文档生成问题时，
        可先点「只看错误」，或搜索 <code>commander</code>、<code>doc.generate</code>。
      </p>

      <div class="filters">
        <el-select
          v-model="source"
          size="small"
          class="w-source"
          @change="load()"
        >
          <el-option
            label="应用日志"
            value="app"
          />
          <el-option
            label="桌面引擎日志"
            value="engine"
          />
        </el-select>
        <el-select
          v-model="level"
          size="small"
          class="w-level"
          @change="load()"
        >
          <el-option
            label="全部级别"
            value=""
          />
          <el-option
            label="INFO 及以上"
            value="INFO"
          />
          <el-option
            label="WARNING 及以上"
            value="WARNING"
          />
          <el-option
            label="仅 ERROR"
            value="ERROR"
          />
        </el-select>
        <el-input
          v-model="keyword"
          size="small"
          class="w-keyword"
          clearable
          placeholder="搜索关键词：commander / 错误码 / 文件名"
          @keyup.enter="load()"
        />
        <el-button
          size="small"
          type="primary"
          :loading="loading"
          @click="load()"
        >
          查询
        </el-button>
        <el-button
          size="small"
          link
          @click="onlyErrors"
        >
          只看错误
        </el-button>
        <el-button
          size="small"
          link
          @click="searchCommander"
        >
          指挥官
        </el-button>
      </div>

      <div class="meta">
        <span>{{ sourceLabel }}：{{ meta.path || '—' }}</span>
        <span v-if="meta.exists">
          {{ sizeText(meta.size_bytes) }} · 匹配 {{ meta.matched }} 条 · 更新
          {{ meta.mtime ? meta.mtime.replace('T', ' ').slice(0, 19) : '—' }}
        </span>
        <span v-else>日志文件尚未生成（服务刚启动时可稍等再刷新）</span>
      </div>
      <el-alert
        v-if="meta.truncated"
        type="warning"
        :closable="false"
        show-icon
        title="日志较大，只读取最近 4MB；更早内容请下载文件查看。"
        class="truncate-hint"
      />

      <div
        v-if="error"
        class="log-empty"
      >
        <el-alert
          :title="error"
          type="error"
          :closable="false"
          show-icon
        />
      </div>
      <div
        v-else-if="!items.length && !loading"
        class="log-empty"
      >
        没有匹配的日志。
      </div>

      <el-scrollbar
        v-else
        class="log-scroll"
      >
        <ul class="log-list">
          <li
            v-for="(item, i) in items"
            :key="`${item.ts}-${i}`"
            class="log-item"
            :class="levelClass(item.level)"
          >
            <div class="log-head">
              <span class="log-ts">{{ item.ts }}</span>
              <el-tag
                :type="tagType(item.level)"
                size="small"
                effect="plain"
                class="log-level"
              >
                {{ item.level }}
              </el-tag>
              <span class="log-logger">{{ item.logger || '—' }}</span>
              <span
                v-if="item.request_id"
                class="log-rid"
              >{{ item.request_id }}</span>
            </div>
            <pre class="log-message">{{ item.message }}</pre>
          </li>
        </ul>
      </el-scrollbar>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { systemLogsApi, type SystemLogItem, type SystemLogLevel, type SystemLogMeta, type SystemLogSource } from '@/api/systemLogs'
import { saveBlobOrDownload } from '@/utils/download'

const AUTO_MS = 10_000

const source = ref<SystemLogSource>('app')
const level = ref<SystemLogLevel>('')
const keyword = ref('')
const items = ref<SystemLogItem[]>([])
const meta = ref<SystemLogMeta>({
  source: 'app',
  path: '',
  exists: false,
  size_bytes: 0,
  mtime: null,
  scanned_bytes: 0,
  truncated: false,
  matched: 0,
  sources: [],
})
const loading = ref(false)
const downloading = ref(false)
const error = ref('')
const autoRefresh = ref(true)
let timer: ReturnType<typeof setInterval> | null = null

const sourceLabel = computed(() => (source.value === 'engine' ? '桌面引擎日志' : '应用日志'))

function sizeText(bytes: number): string {
  if (!bytes) return '0 B'
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
  return `${bytes} B`
}

function tagType(itemLevel: string): 'danger' | 'warning' | 'info' | 'success' {
  if (itemLevel === 'ERROR' || itemLevel === 'CRITICAL') return 'danger'
  if (itemLevel === 'WARNING') return 'warning'
  if (itemLevel === 'INFO') return 'success'
  return 'info'
}

function levelClass(itemLevel: string): string {
  if (itemLevel === 'ERROR' || itemLevel === 'CRITICAL') return 'is-error'
  if (itemLevel === 'WARNING') return 'is-warning'
  return ''
}

async function load(silent = false) {
  loading.value = true
  if (!silent) error.value = ''
  try {
    const page = await systemLogsApi.list({
      source: source.value,
      level: level.value || undefined,
      q: keyword.value.trim() || undefined,
      limit: 400,
    })
    items.value = page.items
    meta.value = page.meta
    error.value = ''
  } catch (e) {
    const message = e instanceof Error ? e.message : '读取日志失败'
    if (!silent) error.value = message
    if (!silent && message.includes('403')) ElMessage.error('仅负责人可查看系统日志')
  } finally {
    loading.value = false
  }
}

function onlyErrors() {
  level.value = 'ERROR'
  void load()
}

function searchCommander() {
  keyword.value = 'commander'
  void load()
}

async function copy() {
  const text = items.value
    .map(item => `${item.ts} ${item.level} [${item.logger}] ${item.message}`)
    .join('\n')
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success(`已复制 ${items.value.length} 条日志`)
  } catch {
    ElMessage.error('复制失败，请改用下载')
  }
}

async function download() {
  downloading.value = true
  try {
    const blob = await systemLogsApi.download(source.value)
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
    await saveBlobOrDownload(`${source.value}-logs-${stamp}.log`, blob)
  } catch {
    ElMessage.error('下载日志失败')
  } finally {
    downloading.value = false
  }
}

function startTimer() {
  stopTimer()
  if (autoRefresh.value) {
    timer = setInterval(() => void load(true), AUTO_MS)
  }
}

function stopTimer() {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

watch(autoRefresh, startTimer)

onMounted(async () => {
  await load()
  startTimer()
})

onBeforeUnmount(stopTimer)
</script>

<style scoped>
.settings-logs .section {
  margin-bottom: 16px;
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.hint {
  margin: 0 0 12px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
  line-height: 1.7;
}
.hint code {
  padding: 0 4px;
  border-radius: 4px;
  background: var(--el-fill-color-light);
}
.filters {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.w-source {
  width: 140px;
}
.w-level {
  width: 150px;
}
.w-keyword {
  width: 320px;
  max-width: 100%;
}
.meta {
  display: flex;
  gap: 16px;
  flex-wrap: wrap;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-bottom: 8px;
  word-break: break-all;
}
.truncate-hint {
  margin-bottom: 8px;
}
.log-empty {
  padding: 24px 0;
  text-align: center;
  color: var(--el-text-color-secondary);
}
.log-scroll {
  height: 520px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-blank);
}
.log-list {
  margin: 0;
  padding: 8px 12px;
  list-style: none;
}
.log-item {
  padding: 8px 0;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}
.log-item:last-child {
  border-bottom: none;
}
.log-item.is-error {
  background: color-mix(in srgb, var(--el-color-danger) 6%, transparent);
}
.log-item.is-warning {
  background: color-mix(in srgb, var(--el-color-warning) 6%, transparent);
}
.log-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.log-ts {
  font-variant-numeric: tabular-nums;
}
.log-logger {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 280px;
}
.log-rid {
  font-family: var(--el-font-family-mono, monospace);
  opacity: 0.75;
}
.log-message {
  margin: 6px 0 0;
  font-family: var(--el-font-family-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
