<template>
  <div class="workspace-page">
    <div class="page-header">
      <div>
        <h2 class="page-title">
          工作区
        </h2>
        <p class="sub">
          登记本机项目目录，预览差异后再落盘；每次修改可回滚，敏感文件与项目根之外的路径一律拒绝。
        </p>
      </div>
      <div class="page-actions">
        <el-button
          :loading="loading"
          @click="reloadAll"
        >
          刷新
        </el-button>
        <el-button
          v-if="canManage"
          type="primary"
          aria-label="新建工作区"
          @click="openCreate"
        >
          新建工作区
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="status && !authorizedPlugins.length"
      class="plugin-alert"
      type="warning"
      show-icon
      :closable="false"
    >
      <template #title>
        还没有已授权的工作区插件
      </template>
      <div class="alert-body">
        <span>修改文件必须由已安装、已授权的工作区插件发起。</span>
        <el-button
          size="small"
          type="primary"
          plain
          @click="goLibrary"
        >
          去插件中心安装
        </el-button>
      </div>
    </el-alert>

    <el-alert
      v-else-if="statusFailed"
      class="plugin-alert"
      type="error"
      show-icon
      :closable="false"
    >
      <template #title>
        工作区状态加载失败
      </template>
      <div class="alert-body">
        <span>插件授权与目录限制暂时不可用，请稍后重试。</span>
        <el-button
          size="small"
          type="primary"
          plain
          @click="reloadAll"
        >
          重新加载
        </el-button>
      </div>
    </el-alert>

    <div class="workspace-grid">
      <el-card
        class="projects-card"
        shadow="never"
      >
        <template #header>
          <div class="card-head">
            <span>项目</span>
            <span class="note">{{ projects.length }} 个</span>
          </div>
        </template>
        <el-empty
          v-if="!projects.length"
          :image-size="70"
          description="还没有登记项目目录"
        />
        <ul
          v-else
          class="project-list"
        >
          <li
            v-for="project in projects"
            :key="project.id"
            class="project-item"
            :class="{ active: project.id === selectedId }"
            @click="selectProject(project)"
          >
            <div class="project-name">
              {{ project.name }}
              <el-tag
                v-if="project.git"
                size="small"
                effect="plain"
              >
                git
              </el-tag>
            </div>
            <div
              class="project-path"
              :title="project.root_path"
            >
              {{ project.root_path }}
            </div>
          </li>
        </ul>
        <div
          v-if="status"
          class="roots-tip"
        >
          <div>允许根：{{ status.allowed_roots.join('、') }}</div>
          <div>
            单文件上限 {{ Math.round(status.max_file_bytes / 1024) }}KB ·
            预览 {{ status.preview_ttl_minutes }} 分钟内有效 ·
            {{ status.git_available ? 'git 可用' : '未检测到 git' }}
          </div>
        </div>
      </el-card>

      <el-card
        class="editor-card"
        shadow="never"
      >
        <template #header>
          <div class="card-head">
            <span>{{ selected?.name || '选择左侧项目' }}</span>
            <div
              v-if="selected"
              class="card-head-actions"
            >
              <el-tag
                size="small"
                effect="plain"
                :type="selected.git ? 'success' : 'info'"
              >
                {{ selected.git ? 'git 仓库' : '普通目录' }}
              </el-tag>
              <el-button
                v-if="canManage"
                size="small"
                text
                type="danger"
                @click="archiveCurrent"
              >
                归档项目
              </el-button>
            </div>
          </div>
        </template>

        <el-empty
          v-if="!selected"
          :image-size="90"
          description="先选择或新建一个工作区"
        />

        <div
          v-else
          class="editor-body"
        >
          <div class="editor-toolbar">
            <el-input
              v-model="pathInput"
              class="path-input"
              placeholder="相对路径，如 src/App.vue"
              aria-label="工作区文件相对路径"
              @keyup.enter="loadFile"
            />
            <el-button
              :loading="fileLoading"
              @click="loadFile"
            >
              读取文件
            </el-button>
            <el-select
              v-model="pluginId"
              class="plugin-select"
              placeholder="选择已授权插件"
              aria-label="选择发起修改的插件"
            >
              <el-option
                v-for="plugin in authorizedPlugins"
                :key="plugin.plugin_id"
                :label="`${plugin.name}（${plugin.version}）`"
                :value="plugin.plugin_id"
              />
            </el-select>
          </div>

          <el-input
            v-model="content"
            type="textarea"
            :rows="16"
            spellcheck="false"
            placeholder="先读取文件，或直接输入要写入的新内容"
            aria-label="工作区文件内容"
          />

          <div class="editor-actions">
            <span class="editor-hint">
              {{ file.exists ? `当前 ${file.size_bytes} 字节` : '新文件' }}
            </span>
            <el-button
              type="primary"
              :loading="previewing"
              :disabled="!canWrite"
              @click="previewChange"
            >
              预览差异
            </el-button>
          </div>

          <div class="changes-head">
            <span>变更记录</span>
            <span class="note">最近 {{ changes.length }} 条</span>
          </div>
          <el-table
            v-col-resize
            :data="changes"
            border
            stripe
            empty-text="还没有变更记录"
            row-key="id"
          >
            <el-table-column
              prop="created_at"
              label="时间"
              min-width="150"
              resizable
            >
              <template #default="{ row }">
                {{ formatDateTime(row.created_at) }}
              </template>
            </el-table-column>
            <el-table-column
              prop="path"
              label="文件"
              min-width="180"
              resizable
              show-overflow-tooltip
            />
            <el-table-column
              label="增删"
              width="100"
              resizable
            >
              <template #default="{ row }">
                <span class="plus">+{{ row.added_lines }}</span>
                <span class="minus">-{{ row.removed_lines }}</span>
              </template>
            </el-table-column>
            <el-table-column
              prop="status"
              label="状态"
              width="110"
              resizable
            >
              <template #default="{ row }">
                <el-tag
                  size="small"
                  effect="plain"
                  :type="statusTagType(row.status)"
                >
                  {{ statusLabel(row.status) }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column
              label="操作"
              width="130"
              resizable
            >
              <template #default="{ row }">
                <el-button
                  v-if="row.status === 'applied' && canWrite"
                  size="small"
                  type="warning"
                  plain
                  @click="rollback(row as WorkspaceChange)"
                >
                  回滚
                </el-button>
                <el-button
                  v-else-if="row.status === 'preview' && canWrite"
                  size="small"
                  type="success"
                  plain
                  @click="applyExisting(row as WorkspaceChange)"
                >
                  应用
                </el-button>
                <span
                  v-else
                  class="muted"
                >—</span>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-card>
    </div>

    <el-dialog
      v-model="createOpen"
      title="新建工作区"
      width="520px"
      append-to-body
    >
      <el-form
        label-width="92px"
        @submit.prevent
      >
        <el-form-item label="名称">
          <el-input
            v-model="form.name"
            maxlength="80"
            placeholder="如 kjxy-Agent 前端"
          />
        </el-form-item>
        <el-form-item label="项目根">
          <div class="root-browser">
            <div class="root-current">
              <span
                class="root-path"
                :title="form.root_path"
              >{{ form.root_path || '尚未选择，请点选下方目录' }}</span>
              <el-button
                size="small"
                :disabled="!browse.parent"
                @click="browseTo(browse.parent || '')"
              >
                上一级
              </el-button>
            </div>
            <div
              v-loading="browseLoading"
              class="root-dirs"
            >
              <el-button
                v-for="d in browse.directories"
                :key="d.path"
                class="root-dir-item"
                size="small"
                @click="enterDir(d.path)"
              >
                📁 {{ d.name }}
              </el-button>
              <span
                v-if="!browse.directories.length && !browseLoading"
                class="root-empty"
              >此目录下没有子目录，可直接点「使用当前目录」</span>
            </div>
            <el-button
              size="small"
              type="primary"
              plain
              :disabled="!form.root_path"
              @click="useCurrentDir"
            >
              使用当前目录
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="说明">
          <el-input
            v-model="form.description"
            maxlength="2000"
            placeholder="可选"
          />
        </el-form-item>
        <el-form-item label="允许路径">
          <el-input
            v-model="form.allowed_paths"
            placeholder="可选，逗号分隔，如 src/**, docs/*.md；留空表示全部"
          />
        </el-form-item>
      </el-form>
      <div class="roots-tip dialog-tip">
        项目根必须位于：{{ status?.allowed_roots.join('、') || '用户主目录' }}
      </div>
      <template #footer>
        <el-button @click="createOpen = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="creating"
          @click="createProject"
        >
          创建
        </el-button>
      </template>
    </el-dialog>

    <el-dialog
      v-model="previewOpen"
      title="差异预览"
      width="760px"
      append-to-body
      @closed="pendingChange = null"
    >
      <template v-if="pendingChange">
        <div class="preview-meta">
          <el-tag
            size="small"
            effect="plain"
          >
            {{ pendingChange.path }}
          </el-tag>
          <span class="plus">+{{ pendingChange.added_lines }}</span>
          <span class="minus">-{{ pendingChange.removed_lines }}</span>
          <span
            v-if="pendingChange.diff_truncated"
            class="muted"
          >差异过长已截断</span>
        </div>
        <pre class="diff-view"><code
          v-for="(line, index) in diffLines"
          :key="index"
          :class="lineClass(line)"
        >{{ line }}&#10;</code></pre>
      </template>
      <template #footer>
        <el-button @click="previewOpen = false">
          取消
        </el-button>
        <el-button
          type="primary"
          :loading="applying"
          @click="applyPending"
        >
          应用修改
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { AxiosError } from 'axios'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'
import { workspaceApi, type BrowseResult } from '@/api/workspace'
import type {
  WorkspaceChange,
  WorkspaceFile,
  WorkspaceProject,
  WorkspaceStatus,
} from '@/api/workspace'

const router = useRouter()
const auth = useAuthStore()
const canManage = computed(() => auth.hasPermission('workspace:manage'))
const canWrite = computed(() => auth.hasPermission('workspace:write'))

const loading = ref(false)
const status = ref<WorkspaceStatus | null>(null)
const statusFailed = ref(false)
const projects = ref<WorkspaceProject[]>([])
const selectedId = ref('')
const selected = computed(() => projects.value.find(row => row.id === selectedId.value) || null)
const authorizedPlugins = computed(
  () => status.value?.plugins.filter(row => row.enabled && row.authorized) || [],
)
const pluginId = ref('')

const file = ref<WorkspaceFile>({
  path: '',
  exists: false,
  content: '',
  size_bytes: 0,
  sha256: '',
})
const pathInput = ref('')
const content = ref('')
const fileLoading = ref(false)
const changes = ref<WorkspaceChange[]>([])

const createOpen = ref(false)
const creating = ref(false)
const form = ref({
  name: '',
  root_path: '',
  description: '',
  allowed_paths: '',
})

const previewOpen = ref(false)
const previewing = ref(false)
const applying = ref(false)
const pendingChange = ref<WorkspaceChange | null>(null)

const diffLines = computed(() => (pendingChange.value?.diff || '').replace(/\n$/, '').split('\n'))

function lineClass(line: string): string {
  if (line.startsWith('+++') || line.startsWith('---')) return 'diff-meta'
  if (line.startsWith('+')) return 'diff-add'
  if (line.startsWith('-')) return 'diff-del'
  if (line.startsWith('@@')) return 'diff-hunk'
  return ''
}

function statusLabel(value: string): string {
  const labels: Record<string, string> = { preview: '待应用', applied: '已应用', rolled_back: '已回滚' }
  return labels[value] || value
}

function statusTagType(value: string): 'success' | 'warning' | 'info' {
  if (value === 'applied') return 'success'
  if (value === 'preview') return 'warning'
  return 'info'
}

function goLibrary() {
  router.push('/library')
}

function apiErrorCode(err: unknown): string {
  if (err instanceof AxiosError) {
    const body = err.response?.data as { error?: { code?: string } } | undefined
    return body?.error?.code || ''
  }
  return ''
}

async function loadStatus() {
  try {
    status.value = await workspaceApi.status()
    statusFailed.value = false
    if (!pluginId.value || !authorizedPlugins.value.some(row => row.plugin_id === pluginId.value)) {
      pluginId.value = authorizedPlugins.value[0]?.plugin_id || ''
    }
  } catch {
    status.value = null
    statusFailed.value = true
  }
}

async function loadProjects() {
  projects.value = await workspaceApi.projects()
  if (!selectedId.value || !projects.value.some(row => row.id === selectedId.value)) {
    selectedId.value = projects.value[0]?.id || ''
  }
}

async function reloadAll() {
  loading.value = true
  try {
    await Promise.allSettled([loadStatus(), loadProjects()])
    const current = projects.value.find(row => row.id === selectedId.value)
    if (current) await selectProject(current)
  } finally {
    loading.value = false
  }
}

async function selectProject(project: WorkspaceProject) {
  selectedId.value = project.id
  pathInput.value = ''
  content.value = ''
  file.value = { path: '', exists: false, content: '', size_bytes: 0, sha256: '' }
  await loadChanges()
}

async function loadChanges() {
  if (!selectedId.value) {
    changes.value = []
    return
  }
  try {
    changes.value = await workspaceApi.changes(selectedId.value)
  } catch {
    changes.value = []
  }
}

async function loadFile() {
  if (!selectedId.value || !pathInput.value.trim()) {
    ElMessage.info('请先填写相对路径')
    return
  }
  fileLoading.value = true
  try {
    const result = await workspaceApi.readFile(selectedId.value, pathInput.value.trim())
    file.value = result
    content.value = result.content
    ElMessage.success(result.exists ? '已读取文件' : '文件尚不存在，将按新文件处理')
  } finally {
    fileLoading.value = false
  }
}

async function previewChange() {
  if (!selectedId.value) return
  if (!pluginId.value) {
    ElMessage.warning('请选择已授权的工作区插件')
    return
  }
  if (!pathInput.value.trim()) {
    ElMessage.info('请先填写相对路径')
    return
  }
  previewing.value = true
  try {
    const change = await workspaceApi.preview(selectedId.value, {
      plugin_id: pluginId.value,
      path: pathInput.value.trim(),
      content: content.value,
    })
    pendingChange.value = change
    previewOpen.value = true
    await loadChanges()
  } finally {
    previewing.value = false
  }
}

async function applyPending() {
  if (!pendingChange.value || applying.value) return
  applying.value = true
  const target = pendingChange.value.path
  try {
    await workspaceApi.apply(pendingChange.value.id)
    ElMessage.success('修改已写入')
    previewOpen.value = false
    await loadChanges()
    if (file.value.path === target) await loadFile()
  } finally {
    applying.value = false
  }
}

async function applyExisting(row: WorkspaceChange) {
  if (applying.value) return
  applying.value = true
  try {
    await workspaceApi.apply(row.id)
    ElMessage.success('修改已写入')
    await loadChanges()
    if (file.value.path === row.path) await loadFile()
  } finally {
    applying.value = false
  }
}

async function rollback(row: WorkspaceChange) {
  try {
    await ElMessageBox.confirm(
      `确认把 ${row.path} 回滚到本次修改之前？`,
      '回滚确认',
      { type: 'warning', confirmButtonText: '回滚', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  try {
    await workspaceApi.rollback(row.id)
    ElMessage.success('已回滚')
  } catch (err) {
    // 只有「文件已被改动」才需要二次确认强制回滚；其他错误（过期、权限、网络）不能硬来
    if (apiErrorCode(err) !== 'WORKSPACE_FILE_CHANGED') return
    try {
      await ElMessageBox.confirm(
        '文件在应用后又被修改，强制回滚会丢弃这些改动，继续吗？',
        '文件已变化',
        { type: 'warning', confirmButtonText: '强制回滚', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
    await workspaceApi.rollback(row.id, true)
    ElMessage.success('已强制回滚')
  }
  await loadChanges()
  if (file.value.path === row.path) await loadFile()
}

function openCreate() {
  form.value = { name: '', root_path: '', description: '', allowed_paths: '' }
  createOpen.value = true
  void browseTo('')  // 从允许根开始浏览
}

/* ---------- 项目根目录浏览（傻瓜化选择，v3.65） ---------- */
const browse = ref<BrowseResult>({ current: '', parent: null, allowed_roots: [], directories: [] })
const browseLoading = ref(false)

async function browseTo(base: string): Promise<void> {
  browseLoading.value = true
  try {
    browse.value = await workspaceApi.browse(base)
    if (browse.value.current) form.value.root_path = browse.value.current
  } catch {
    /* toast 已统一处理 */
  } finally {
    browseLoading.value = false
  }
}

function enterDir(path: string): void {
  void browseTo(path)
}

function useCurrentDir(): void {
  if (browse.value.current) form.value.root_path = browse.value.current
}

async function createProject() {
  if (!form.value.name.trim() || !form.value.root_path.trim()) {
    ElMessage.warning('名称和项目根都不能为空')
    return
  }
  creating.value = true
  try {
    const allowed = form.value.allowed_paths
      .split(',')
      .map(item => item.trim())
      .filter(Boolean)
    const project = await workspaceApi.createProject({
      name: form.value.name.trim(),
      root_path: form.value.root_path.trim(),
      description: form.value.description.trim() || undefined,
      allowed_paths: allowed.length ? allowed : undefined,
    })
    createOpen.value = false
    ElMessage.success('工作区已创建')
    await loadProjects()
    const created = projects.value.find(row => row.id === project.id)
    if (created) await selectProject(created)
  } finally {
    creating.value = false
  }
}

async function archiveCurrent() {
  if (!selected.value) return
  try {
    await ElMessageBox.confirm(
      `归档后不再出现在列表中，确认归档「${selected.value.name}」？`,
      '归档确认',
      { type: 'warning', confirmButtonText: '归档', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  await workspaceApi.archiveProject(selected.value.id)
  ElMessage.success('已归档')
  selectedId.value = ''
  await loadProjects()
  const next = projects.value.find(row => row.id === selectedId.value)
  if (next) await selectProject(next)
}

onMounted(reloadAll)
</script>

<style scoped>
.workspace-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.plugin-alert {
  border-radius: var(--el-border-radius-base);
}

.alert-body {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.workspace-grid {
  display: grid;
  grid-template-columns: 300px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-weight: 600;
}

.card-head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.note {
  font-size: 12px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}

.project-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.project-item {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease;
}

.project-item:hover {
  border-color: var(--el-color-primary-light-5);
}

.project-item.active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.project-name {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  margin-bottom: 4px;
}

.project-path {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.roots-tip {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px dashed var(--el-border-color-lighter);
  font-size: 12px;
  color: var(--el-text-color-secondary);
  line-height: 1.7;
  word-break: break-all;
}

.dialog-tip {
  margin-top: 0;
}

.editor-body {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.editor-toolbar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.path-input {
  flex: 1 1 260px;
}

.plugin-select {
  width: 220px;
}

.editor-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.editor-hint {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.changes-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 6px;
  font-weight: 600;
}

.plus {
  color: var(--el-color-success);
  margin-right: 8px;
}

.minus {
  color: var(--el-color-danger);
}

.muted {
  color: var(--el-text-color-secondary);
}

.preview-meta {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
  flex-wrap: wrap;
}

.diff-view {
  max-height: 52vh;
  overflow: auto;
  margin: 0;
  padding: 12px;
  border-radius: 8px;
  background: var(--el-fill-color-lighter);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.6;
}

.diff-view code {
  display: block;
  white-space: pre;
}

.diff-add {
  color: var(--el-color-success);
  background: var(--el-color-success-light-9);
}

.diff-del {
  color: var(--el-color-danger);
  background: var(--el-color-danger-light-9);
}

.diff-hunk {
  color: var(--el-color-primary);
}

.diff-meta {
  color: var(--el-text-color-secondary);
}

@media (max-width: 960px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }
}
</style>
