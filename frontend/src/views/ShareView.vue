<template>
  <div class="share-page">
    <main class="share-card">
      <div
        v-if="loading"
        class="state"
      >
        <el-skeleton
          :rows="6"
          animated
        />
      </div>

      <div
        v-else-if="error"
        class="state"
      >
        <h1>链接不可用</h1>
        <p>{{ error }}</p>
        <p class="muted">
          分享可能已过期或被分享者撤销；如有疑问，请联系发送链接的人。
        </p>
      </div>

      <template v-else-if="share">
        <header class="share-head">
          <h1>{{ share.title }}</h1>
          <div class="meta">
            <span>只读快照</span>
            <span>生成于 {{ formatTime(share.created_at) }}</span>
            <span>有效期至 {{ formatDate(share.expires_at) }}</span>
            <span>访问 {{ share.view_count }} 次</span>
          </div>
        </header>
        <SafeMarkdown
          class="body"
          :content="share.content_md"
        />
        <footer class="share-foot">
          <span>由 科技小院 · 多智能体平台 生成</span>
          <el-button
            size="small"
            text
            @click="printPage"
          >
            打印 / 存为 PDF
          </el-button>
        </footer>
      </template>
    </main>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, ref } from 'vue'
  import { useRoute } from 'vue-router'
  import { shareApi, type PublicShare } from '@/api/modules'
  import SafeMarkdown from '@/components/teams/SafeMarkdown.vue'

  const route = useRoute()
  const loading = ref(true)
  const error = ref('')
  const share = ref<PublicShare | null>(null)

  function formatTime(value?: string | null): string {
    if (!value) return '—'
    return value.replace('T', ' ').slice(0, 16)
  }

  function formatDate(value?: string | null): string {
    if (!value) return '—'
    return value.slice(0, 10)
  }

  function printPage() {
    window.print()
  }

  onMounted(async () => {
    const token = String(route.params.token || '')
    try {
      share.value = await shareApi.publicView(token)
    } catch (err: unknown) {
      const status = (err as { response?: { status?: number } })?.response?.status
      error.value =
        status === 429 ? '访问过于频繁，请稍后再试。' : '分享不存在或已失效。'
    } finally {
      loading.value = false
    }
  })
</script>

<style scoped>
  .share-page {
    min-height: 100vh;
    padding: 32px 16px 48px;
    background: var(--el-bg-color-page, #0b1020);
  }
  .share-card {
    max-width: 820px;
    margin: 0 auto;
    padding: 32px 28px;
    border-radius: 14px;
    background: var(--el-bg-color, #151a2b);
    border: 1px solid var(--el-border-color, #2a3147);
    box-shadow: 0 18px 50px rgb(0 0 0 / 28%);
  }
  .state {
    padding: 24px 0;
  }
  .state h1 {
    margin: 0 0 8px;
    font-size: 20px;
  }
  .muted {
    color: var(--el-text-color-secondary);
    font-size: 13px;
  }
  .share-head h1 {
    margin: 0 0 10px;
    font-size: 22px;
    line-height: 1.4;
  }
  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }
  .body {
    margin-top: 22px;
  }
  .share-foot {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: 12px;
    margin-top: 28px;
    padding-top: 16px;
    border-top: 1px dashed var(--el-border-color, #2a3147);
    color: var(--el-text-color-secondary);
    font-size: 12px;
  }
  @media print {
    .share-page {
      background: #fff;
      padding: 0;
    }
    .share-card {
      border: none;
      box-shadow: none;
      padding: 0;
    }
    .share-foot {
      display: none;
    }
  }
</style>
