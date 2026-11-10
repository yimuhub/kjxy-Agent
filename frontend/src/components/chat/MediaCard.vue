<template>
  <div
    class="media-card"
    :class="card.kind"
    role="group"
    :aria-label="card.kind === 'video' ? '视频生成结果' : '图片生成结果'"
  >
    <div class="head">
      <span class="kind">{{ card.kind === 'video' ? '视频' : '图片' }}</span>
      <el-tag
        size="small"
        :type="statusTone"
        effect="plain"
      >
        {{ statusText }}
      </el-tag>
    </div>

    <p
      v-if="card.prompt"
      class="prompt"
    >
      {{ card.prompt }}
    </p>

    <div
      v-if="assets.length"
      class="grid"
      :class="`n${Math.min(assets.length, 4)}`"
    >
      <figure
        v-for="(asset, index) in assets"
        :key="asset.id"
        class="item"
      >
        <template v-if="showable(asset)">
          <el-image
            v-if="asset.kind === 'image'"
            class="visual"
            :src="imageSrc(asset)"
            :preview-src-list="previewList"
            :initial-index="previewIndex(asset)"
            fit="cover"
            preview-teleported
            hide-on-click-modal
            :alt="asset.filename"
          />
          <video
            v-else
            class="visual"
            :src="asset.url"
            controls
            playsinline
            preload="metadata"
          />
        </template>
        <div
          v-else
          class="visual placeholder"
          :class="asset.status"
        >
          <el-icon
            v-if="asset.status === 'running'"
            class="is-loading"
          >
            <Loading />
          </el-icon>
          <el-icon v-else>
            <WarningFilled v-if="asset.status === 'failed'" />
            <Picture v-else />
          </el-icon>
          <span>{{ assetHint(asset) }}</span>
        </div>

        <figcaption class="meta">
          <span
            class="name"
            :title="asset.filename"
          >{{ asset.filename }}</span>
          <span
            v-if="asset.status === 'ready'"
            class="detail"
          >{{ assetDetail(asset) }}</span>
          <el-button
            v-if="asset.status === 'ready' && asset.url"
            size="small"
            text
            type="primary"
            :loading="downloadingId === asset.id"
            :aria-label="`下载 ${asset.filename}`"
            @click="download(asset, index)"
          >
            下载
          </el-button>
        </figcaption>
      </figure>
    </div>

    <p
      v-if="card.notice"
      class="note"
    >
      {{ card.notice }}
    </p>
    <p
      v-if="card.hint && card.status === 'running'"
      class="note hint"
    >
      {{ card.hint }}
    </p>
    <p
      v-if="card.kind === 'video' && card.retention_days && hasReady"
      class="note"
    >
      成品保存在本机，{{ card.retention_days }} 天后自动清理；需要长期留存请先下载。
    </p>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
  import { ElMessage } from 'element-plus'
  import { Loading, Picture, WarningFilled } from '@element-plus/icons-vue'
  import type { ChatMediaAsset, ChatMediaCard } from '@/api/chat'
  import { mediaApi } from '@/api/media'
  import { safeImageUrl } from '@/components/teams/presentation'
  import { saveBlobOrDownload } from '@/utils/download'

  const props = defineProps<{ card: ChatMediaCard }>()

  const POLL_INTERVAL_MS = 4000
  const POLL_MAX_MS = 10 * 60 * 1000
  const POLL_MAX_FAILURES = 5

  /** 本地副本：后端轮询完成后刷新卡片，不依赖整段会话重载 */
  const assets = ref<ChatMediaAsset[]>(props.card.assets.map(asset => ({ ...asset })))
  const downloadingId = ref('')

  watch(
    () => props.card.assets,
    next => {
      assets.value = (next ?? []).map(asset => ({ ...asset }))
      syncPolling()
    },
    { deep: true },
  )

  const statusText = computed(() => {
    const status = cardStatus(assets.value)
    return { ready: '已生成', running: '生成中', failed: '生成失败', awaiting_confirmation: '待确认' }[
      status
    ]
  })

  const statusTone = computed<'success' | 'warning' | 'danger' | 'info'>(() => {
    const status = cardStatus(assets.value)
    if (status === 'ready') return 'success'
    if (status === 'failed') return 'danger'
    return 'warning'
  })

  const hasReady = computed(() => assets.value.some(asset => asset.status === 'ready' && asset.url))

  /** 预览列表只收可展示且通过白名单的图片，索引与列表保持一致 */
  const readyImages = computed(() =>
    assets.value.filter(asset => asset.status === 'ready' && safeImageUrl(asset.url)),
  )
  const previewList = computed(() => readyImages.value.map(imageSrc))

  function previewIndex(asset: ChatMediaAsset): number {
    return Math.max(0, readyImages.value.findIndex(item => item.id === asset.id))
  }

  function cardStatus(items: ChatMediaAsset[]): ChatMediaCard['status'] {
    if (items.some(item => item.status === 'running')) return 'running'
    if (items.some(item => item.status === 'ready')) return 'ready'
    if (items.some(item => item.status === 'awaiting_confirmation')) return 'awaiting_confirmation'
    return 'failed'
  }

  function imageSrc(asset: ChatMediaAsset): string {
    return safeImageUrl(asset.url) ?? ''
  }

  /** 图片必须过白名单，视频只需有地址（签名或上游临时链接） */
  function showable(asset: ChatMediaAsset): boolean {
    if (asset.status !== 'ready' || !asset.url) return false
    return asset.kind === 'video' || Boolean(imageSrc(asset))
  }

  function assetHint(asset: ChatMediaAsset): string {
    if (asset.status === 'running') return '正在生成，完成后自动出现在这里'
    if (asset.status === 'awaiting_confirmation') return '等待确认后开始生成'
    if (asset.status === 'ready') return asset.error_message || '成品链接不可用，请重新生成'
    return asset.error_message || '生成失败，可换个描述再试'
  }

  function formatBytes(bytes: number): string {
    if (!Number.isFinite(bytes) || bytes <= 0) return ''
    if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  }

  function assetDetail(asset: ChatMediaAsset): string {
    const parts: string[] = []
    if (asset.kind === 'video' && asset.duration_seconds) {
      parts.push(`${Math.round(asset.duration_seconds)} 秒`)
    } else if (asset.width && asset.height) {
      parts.push(`${asset.width}×${asset.height}`)
    }
    const size = formatBytes(asset.bytes)
    if (size) parts.push(size)
    return parts.join(' · ')
  }

  let timer: ReturnType<typeof setInterval> | undefined
  let inFlight = false
  let failures = 0
  const startedAt = Date.now()
  let stopped = false

  function needsPolling(): boolean {
    return assets.value.some(
      asset => asset.status === 'running' || asset.status === 'awaiting_confirmation',
    )
  }

  function stopPolling(): void {
    if (timer) clearInterval(timer)
    timer = undefined
  }

  /** 只在卡片仍处于未完成态时轮询；完成、失败或超时立即停 */
  function syncPolling(): void {
    if (stopped) return
    if (!needsPolling()) {
      stopPolling()
      return
    }
    if (timer) return
    timer = setInterval(tick, POLL_INTERVAL_MS)
  }

  async function tick(): Promise<void> {
    if (inFlight || stopped) return
    if (Date.now() - startedAt > POLL_MAX_MS) {
      stopPolling()
      ElMessage.warning('生成状态长时间未更新，请稍后刷新页面查看')
      return
    }
    inFlight = true
    try {
      const pending = assets.value.filter(
        asset => asset.status === 'running' || asset.status === 'awaiting_confirmation',
      )
      const results = await Promise.all(
        pending.map(asset =>
          mediaApi
            .asset(asset.id)
            .then(next => ({ id: asset.id, next }))
            .catch(() => null),
        ),
      )
      let responded = false
      for (const result of results) {
        if (!result) continue
        responded = true
        const index = assets.value.findIndex(item => item.id === result.id)
        if (index >= 0) assets.value[index] = { ...result.next }
      }
      failures = responded ? 0 : failures + 1
      if (failures >= POLL_MAX_FAILURES) {
        stopPolling()
        return
      }
    } finally {
      inFlight = false
      syncPolling()
    }
  }

  async function download(asset: ChatMediaAsset, index: number): Promise<void> {
    if (downloadingId.value || !asset.url) return
    downloadingId.value = asset.id
    try {
      const blob = await mediaApi.download(asset.url)
      if (!blob.size) throw new Error('empty')
      await saveBlobOrDownload(asset.filename || `media-${index + 1}`, blob)
    } catch {
      ElMessage.error('下载失败，请稍后重试')
    } finally {
      downloadingId.value = ''
    }
  }

  onMounted(syncPolling)
  onUnmounted(() => {
    stopped = true
    stopPolling()
  })
</script>

<style scoped>
  .media-card {
    margin-top: 10px;
    padding: 12px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-md, 10px);
    background: var(--ds-surface-2, #fff);
  }
  .head {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 8px;
  }
  .kind {
    font-size: 12px;
    font-weight: 600;
    color: var(--ds-text-2);
    letter-spacing: 0.08em;
  }
  .prompt {
    margin: 0 0 10px;
    font-size: 13px;
    color: var(--ds-text-1);
    line-height: 1.6;
    word-break: break-word;
  }
  .grid {
    display: grid;
    gap: 10px;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .grid.n1 {
    grid-template-columns: minmax(0, 1fr);
  }
  .grid.n3,
  .grid.n4 {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .item {
    margin: 0;
    min-width: 0;
  }
  .visual {
    display: block;
    width: 100%;
    border-radius: 8px;
    background: var(--ds-bg-2);
    overflow: hidden;
  }
  .media-card.image .grid.n1 .visual {
    max-height: 420px;
  }
  .media-card.video .visual {
    aspect-ratio: 16 / 9;
    object-fit: contain;
    background: #000;
  }
  .placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 150px;
    padding: 16px;
    font-size: 12px;
    color: var(--ds-text-2);
    text-align: center;
    line-height: 1.5;
  }
  .placeholder.failed {
    color: var(--el-color-danger);
    background: var(--el-color-danger-light-9);
  }
  .placeholder .el-icon {
    font-size: 22px;
  }
  .meta {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: 6px;
    font-size: 12px;
    color: var(--ds-text-2);
    min-width: 0;
  }
  .meta .name {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .meta .detail {
    flex: none;
    color: var(--ds-text-3, var(--ds-text-2));
  }
  .note {
    margin: 8px 0 0;
    font-size: 12px;
    color: var(--ds-text-2);
    line-height: 1.6;
  }
  .note.hint {
    color: var(--el-color-warning);
  }
</style>
