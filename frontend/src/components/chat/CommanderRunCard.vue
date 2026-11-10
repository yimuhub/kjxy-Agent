<template>
  <div
    class="commander-run"
    :class="`is-${status}`"
  >
    <div
      v-if="degraded"
      class="banner warn"
      role="status"
    >
      {{ parallelLabel }} 个助手并行（已降级为 {{ effectiveParallel || 2 }} 个）
    </div>
    <div
      v-else-if="parallelKnown"
      class="banner"
      role="status"
    >
      {{ effectiveParallel || subtasks.length }} 个助手并行
    </div>

    <div class="head">
      <span class="kind">指挥官执行</span>
      <el-tag
        size="small"
        :type="statusTagType"
        effect="plain"
      >
        {{ statusLabel }}
      </el-tag>
      <span class="meta">
        <span v-if="tokensTotal > 0">⚡ {{ tokensTotal.toLocaleString() }} tokens</span>
        <span v-if="latencyMs != null && latencyMs > 0">· {{ (latencyMs / 1000).toFixed(1) }}s</span>
      </span>
    </div>

    <ul
      v-if="subtasks.length"
      class="subtask-list"
    >
      <li
        v-for="task in subtasks"
        :key="task.id"
        class="subtask"
        :class="taskClass(task)"
      >
        <span class="seq">{{ task.seq ?? '-' }}</span>
        <div class="task-main">
          <div class="task-title">
            {{ task.title || '未命名子任务' }}
          </div>
          <div class="task-meta">
            <span>助手：{{ task.agent_name || task.agent_code || '待指派' }}</span>
            <span v-if="task.depends_on?.length">依赖：{{ task.depends_on.join('、') }}</span>
            <span v-if="task.tokens">Token：{{ task.tokens.toLocaleString() }}</span>
            <span v-if="durationOf(task)">耗时：{{ durationOf(task) }}</span>
            <span v-if="(task.attempts ?? 0) > 1">已尝试 {{ task.attempts }} 轮</span>
            <span
              v-if="task.error_code && task.error_code !== 'COMMANDER_CAPABILITY_GAP'"
              class="err"
            >错误码：{{ task.error_code }}</span>
          </div>
          <p
            v-if="task.remediation?.length"
            class="remediation"
          >
            自主补救：{{ remediationSummary(task) }}
          </p>
          <p
            v-if="task.output_excerpt"
            class="excerpt"
          >
            {{ task.output_excerpt }}
          </p>
        </div>
        <el-tag
          size="small"
          :type="subtaskTagType(task.status)"
          class="task-tag"
        >
          {{ subtaskLabel(task.status) }}
        </el-tag>
      </li>
    </ul>
    <p
      v-else
      class="note"
    >
      正在等待子任务下发…
    </p>

    <div
      v-if="failed.length && !active"
      class="block failed"
    >
      <div class="block-title">
        未完成项（{{ failed.length }}）
      </div>
      <ul>
        <li
          v-for="(item, i) in failed"
          :key="item.id || item.seq || i"
        >
          <span class="failed-title">
            {{ item.seq ? `#${item.seq} ` : '' }}{{ item.title || '子任务' }}
          </span>
          <span class="failed-reason">{{ item.reason || '未给出原因' }}</span>
        </li>
      </ul>
    </div>

    <div
      v-if="answer && !active"
      class="block answer"
    >
      <div class="block-title">
        汇总
      </div>
      <SafeMarkdown :content="answer" />
    </div>

    <div
      v-if="artifacts.length && !active"
      class="block artifacts"
    >
      <div class="block-title">
        产物（{{ artifacts.length }}）
      </div>
      <ul>
        <li
          v-for="(item, i) in artifacts"
          :key="`${artifactTitle(item)}-${i}`"
        >
          <button
            v-if="isDocumentArtifact(item)"
            type="button"
            class="artifact-name artifact-name--preview"
            data-artifact-preview
            @click="previewArtifact(item)"
          >
            {{ artifactTitle(item) }}
          </button>
          <a
            v-else-if="externalArtifactLink(item)"
            class="artifact-name"
            :href="externalArtifactLink(item)"
            target="_blank"
            rel="noopener noreferrer"
          >{{ artifactTitle(item) }}</a>
          <span
            v-else
            class="artifact-name"
          >{{ artifactTitle(item) }}</span>
          <span
            v-if="artifactFormat(item)"
            class="artifact-format"
          >{{ artifactFormat(item) }}</span>
          <el-button
            v-if="item.attachment_id && item.session_id"
            text
            size="small"
            type="primary"
            :loading="downloading === `${item.session_id}:${item.attachment_id}`"
            @click="downloadArtifact(item)"
          >
            下载
          </el-button>
        </li>
      </ul>
    </div>

    <p
      v-if="run?.error"
      class="inline-error"
      role="alert"
    >
      {{ run.error }}
    </p>

    <div
      v-if="active || failed.length"
      class="actions"
    >
      <el-button
        v-if="failed.length && !active"
        size="small"
        type="warning"
        :loading="run?.busy"
        @click="emit('retry', runId)"
      >
        重试失败项
      </el-button>
      <el-button
        v-if="active"
        size="small"
        :loading="run?.busy"
        @click="emit('cancel', runId)"
      >
        取消
      </el-button>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { computed, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import type {
    CommanderArtifact,
    CommanderFailedItem,
    CommanderSubtask,
    CommanderSubtaskStatus,
    CommanderSummaryPayload,
  } from '@/api/commander'
  import { chatApi } from '@/api/chat'
  import SafeMarkdown from '@/components/teams/SafeMarkdown.vue'
  import type { CommanderRunView } from '@/stores/chat'
  import { saveBlobOrDownload } from '@/utils/download'
  import {
    artifactPreviewTarget,
    artifactTitle,
    externalArtifactLink,
    isDocumentArtifact,
    type ArtifactPreviewTarget,
  } from '@/utils/commanderArtifacts'

  /**
   * 指挥官执行卡：运行中逐个子任务显示状态/耗时/Token，
   * 完成后展示汇总、产物、失败项与重试入口。
   * 状态由 store 的 commanderRun 视图驱动（SSE + 1.5s 轮询共同更新）。
   */
  const props = withDefaults(
    defineProps<{
      run: CommanderRunView | null
      summary?: CommanderSummaryPayload | null
    }>(),
    { summary: null },
  )

  const emit = defineEmits<{
    (event: 'retry', runId: string): void
    (event: 'cancel', runId: string): void
    (event: 'preview', card: ArtifactPreviewTarget): void
  }>()

  const downloading = ref('')
  const runId = computed(
    () => props.run?.runId || props.summary?.run_id || props.summary?.id || '',
  )

  const subtasks = computed<CommanderSubtask[]>(
    () => (props.run?.subtasks?.length ? props.run.subtasks : props.summary?.subtasks ?? []),
  )
  const artifacts = computed<CommanderArtifact[]>(
    () => props.summary?.artifacts ?? props.run?.artifacts ?? [],
  )
  const failed = computed<CommanderFailedItem[]>(
    () => props.summary?.failed ?? props.run?.failed ?? [],
  )
  const answer = computed(() => props.summary?.answer ?? props.run?.answer ?? '')
  const tokensTotal = computed(() => props.summary?.tokens_total ?? props.run?.tokensTotal ?? 0)
  const latencyMs = computed(() => {
    const value = props.run?.latencyMs ?? props.summary?.latency_ms ?? 0
    return value > 0 ? value : null
  })
  const degraded = computed(() => props.summary?.degraded ?? props.run?.degraded ?? false)
  const maxParallel = computed(
    () => props.run?.maxParallel || props.summary?.max_parallel || 0,
  )
  const effectiveParallel = computed(
    () => props.run?.effectiveParallel || props.summary?.effective_parallel || 0,
  )
  const parallelLabel = computed(() => maxParallel.value || subtasks.value.length || 3)
  const parallelKnown = computed(
    () => maxParallel.value > 0 || effectiveParallel.value > 0 || subtasks.value.length > 0,
  )

  const status = computed(() => {
    if (props.run?.status) return props.run.status
    if (props.summary?.status) return props.summary.status
    if (props.summary) return failed.value.length ? 'partial' : 'completed'
    return 'preview'
  })

  const active = computed(() => status.value === 'queued' || status.value === 'running')

  const STATUS_LABELS: Record<string, string> = {
    preview: '待执行',
    queued: '排队中',
    running: '运行中',
    completed: '已完成',
    partial: '部分完成',
    failed: '失败',
    cancelled: '已取消',
  }

  const statusLabel = computed(() => STATUS_LABELS[status.value] ?? status.value)

  const statusTagType = computed(() => {
    if (status.value === 'completed') return 'success'
    if (status.value === 'partial') return 'warning'
    if (status.value === 'failed') return 'danger'
    if (status.value === 'cancelled') return 'info'
    if (active.value) return 'primary'
    return 'info'
  })

  const SUBTASK_LABELS: Record<string, string> = {
    pending: '等待',
    running: '进行中',
    ok: '完成',
    error: '失败',
    skipped: '跳过',
  }

  function subtaskLabel(value?: CommanderSubtaskStatus): string {
    return SUBTASK_LABELS[String(value ?? 'pending')] ?? String(value ?? '未知')
  }

  function subtaskTagType(value?: CommanderSubtaskStatus): 'info' | 'primary' | 'success' | 'danger' | 'warning' {
    if (value === 'running') return 'primary'
    if (value === 'ok') return 'success'
    if (value === 'error') return 'danger'
    if (value === 'skipped') return 'warning'
    return 'info'
  }

  function taskClass(task: CommanderSubtask): string {
    return `task-${String(task.status ?? 'pending')}`
  }

  const REMEDY_LABELS: Record<string, string> = {
    retry: '重试原执行者',
    alt_agent: '换助手',
    new_agent: '新建助手',
    self: '指挥官亲自执行',
  }

  /** 补救轨迹压成一行（详细原因在悬停提示里），避免卡片被长文案撑开 */
  function remediationSummary(task: CommanderSubtask): string {
    return (task.remediation ?? [])
      .map(item => REMEDY_LABELS[item.action] ?? item.action)
      .join(' → ')
  }

  /** 子任务耗时：有起止时间才算，运行中不硬猜 */
  function durationOf(task: CommanderSubtask): string {
    if (!task.started_at || !task.finished_at) return ''
    const start = new Date(task.started_at).getTime()
    const end = new Date(task.finished_at).getTime()
    if (!Number.isFinite(start) || !Number.isFinite(end) || end < start) return ''
    const seconds = (end - start) / 1000
    return seconds >= 60 ? `${(seconds / 60).toFixed(1)} 分钟` : `${seconds.toFixed(1)} 秒`
  }

  function artifactFormat(item: CommanderArtifact): string {
    return item.format || item.mime || item.kind || item.type || ''
  }

  function previewArtifact(item: CommanderArtifact): void {
    const target = artifactPreviewTarget(item)
    if (target) emit('preview', target)
  }

  async function downloadArtifact(item: CommanderArtifact): Promise<void> {
    const sessionId = item.session_id || ''
    const attachmentId = item.attachment_id || ''
    if (!sessionId || !attachmentId) return
    const key = `${sessionId}:${attachmentId}`
    if (downloading.value) return
    downloading.value = key
    try {
      const blob = await chatApi.downloadDocument(sessionId, attachmentId)
      await saveBlobOrDownload(item.filename || artifactTitle(item), blob)
    } catch {
      ElMessage.error('下载产物失败，请稍后重试')
    } finally {
      downloading.value = ''
    }
  }
</script>

<style scoped>
  .commander-run {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 12px 14px;
    border: 1px solid var(--ds-border);
    border-radius: 12px;
    background: var(--ds-glass);
  }
  .commander-run.is-failed {
    border-color: color-mix(in srgb, var(--ds-danger) 45%, transparent);
  }
  .commander-run.is-partial {
    border-color: color-mix(in srgb, var(--ds-warning) 45%, transparent);
  }

  .banner {
    padding: 5px 10px;
    border-radius: 8px;
    font-size: 12px;
    color: var(--ds-accent-text);
    background: color-mix(in srgb, var(--ds-accent) 12%, transparent);
    border: 1px solid color-mix(in srgb, var(--ds-accent) 32%, transparent);
  }
  .banner.warn {
    color: var(--ds-warning);
    background: color-mix(in srgb, var(--ds-warning) 12%, transparent);
    border-color: color-mix(in srgb, var(--ds-warning) 38%, transparent);
  }

  .head {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .kind {
    font-size: 13px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .meta {
    margin-left: auto;
    font-size: 11.5px;
    color: var(--ds-text-3);
    font-variant-numeric: tabular-nums;
  }

  .subtask-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .subtask {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 8px 10px;
    border: 1px solid var(--ds-border);
    border-radius: 9px;
    background: var(--ds-field);
  }
  .subtask.task-running {
    border-color: color-mix(in srgb, var(--ds-accent) 45%, transparent);
  }
  .subtask.task-error {
    border-color: color-mix(in srgb, var(--ds-danger) 45%, transparent);
  }
  .seq {
    flex: none;
    width: 22px;
    height: 22px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border-radius: 50%;
    font-size: 11px;
    color: var(--ds-text-2);
    background: var(--ds-glass-strong);
    border: 1px solid var(--ds-border);
    font-variant-numeric: tabular-nums;
  }
  .task-main {
    flex: 1;
    min-width: 0;
  }
  .task-title {
    font-size: 13px;
    color: var(--ds-text-1);
    word-break: break-word;
  }
  .task-meta {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    margin-top: 3px;
    font-size: 11.5px;
    color: var(--ds-text-3);
  }
  .task-meta .err {
    color: var(--ds-danger);
  }
  .remediation {
    margin: 4px 0 0;
    font-size: 11.5px;
    line-height: 1.5;
    color: var(--ds-warning);
    word-break: break-word;
  }
  .excerpt {
    margin: 4px 0 0;
    font-size: 12px;
    line-height: 1.6;
    color: var(--ds-text-2);
    word-break: break-word;
  }
  .task-tag {
    flex: none;
  }

  .block {
    border-top: 1px dashed var(--ds-border);
    padding-top: 8px;
  }
  .block-title {
    font-size: 12px;
    font-weight: 600;
    color: var(--ds-text-2);
    margin-bottom: 4px;
  }
  .block ul {
    margin: 0;
    padding-left: 18px;
    font-size: 12.5px;
    color: var(--ds-text-2);
    line-height: 1.7;
  }
  .failed .failed-title {
    color: var(--ds-text-1);
  }
  .failed .failed-reason {
    margin-left: 8px;
    color: var(--ds-text-3);
  }
  .answer {
    font-size: 13px;
    color: var(--ds-text-1);
    line-height: 1.7;
  }
  .artifacts ul {
    list-style: none;
    padding-left: 0;
  }
  .artifacts li {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
  }
  .artifact-name {
    color: var(--ds-accent-text);
    word-break: break-all;
  }

  .artifacts a.artifact-name {
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  /* 产物名是可点的预览按钮：外观跟链接一致，但不跳路由 */
  .artifact-name--preview {
    padding: 0;
    border: 0;
    background: none;
    font: inherit;
    text-align: left;
    cursor: pointer;
    text-decoration: underline;
    text-underline-offset: 2px;
  }

  .artifact-name--preview:hover {
    opacity: 0.82;
  }
  .artifact-format {
    font-size: 11px;
    color: var(--ds-text-3);
  }

  .inline-error {
    margin: 0;
    font-size: 12px;
    color: var(--ds-danger);
  }
  .note {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .actions {
    display: flex;
    gap: 8px;
  }
</style>
