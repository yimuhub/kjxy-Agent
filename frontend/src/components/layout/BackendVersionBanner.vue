<template>
  <div
    v-if="visible"
    class="backend-version-banner"
    role="status"
    aria-live="polite"
  >
    <el-alert
      :title="title"
      :description="detail"
      :type="type"
      show-icon
      closable
      @close="dismiss"
    />
  </div>
</template>

<script setup lang="ts">
  import { isAxiosError } from 'axios'
  import { onMounted, ref } from 'vue'
  import { metaApi } from '@/api/v3'
  import { APP_VERSION, versionRelation } from '@/utils/version'

  const STORAGE_KEY = 'kjxy_backend_version_notice'

  const visible = ref(false)
  const title = ref('')
  const detail = ref('')
  const type = ref<'warning' | 'error'>('warning')
  let noticeKey = ''

  function show(nextTitle: string, nextDetail: string, nextType: 'warning' | 'error') {
    title.value = nextTitle
    detail.value = nextDetail
    type.value = nextType
    visible.value = true
  }

  function dismiss() {
    visible.value = false
    if (noticeKey) sessionStorage.setItem(STORAGE_KEY, noticeKey)
  }

  onMounted(async () => {
    try {
      const meta = await metaApi.get()
      const relation = versionRelation(meta.version, APP_VERSION)
      if (relation === 'equal' || relation === 'unknown') return
      noticeKey = `${meta.version}->${APP_VERSION}`
      if (sessionStorage.getItem(STORAGE_KEY) === noticeKey) return
      if (relation === 'backend_behind') {
        show(
          `后端 v${meta.version} 低于前端 v${APP_VERSION}`,
          '请先执行数据库迁移（alembic upgrade head）并重启后端，否则新功能会提示「不支持」。',
          'warning',
        )
      } else {
        show(
          `后端 v${meta.version} 高于前端 v${APP_VERSION}`,
          '页面可能是旧缓存，请强制刷新（⌘/Ctrl+Shift+R）。',
          'warning',
        )
      }
    } catch (err) {
      const status = isAxiosError(err) ? err.response?.status : undefined
      if (status !== 404 && status !== 405) return
      noticeKey = `missing->${APP_VERSION}`
      if (sessionStorage.getItem(STORAGE_KEY) === noticeKey) return
      show('后端未提供版本握手接口', '后端进程可能较旧：请升级并重启后端。', 'warning')
    }
  })
</script>

<style scoped>
.backend-version-banner {
  position: fixed;
  top: 12px;
  left: 50%;
  z-index: 3000;
  width: min(720px, calc(100vw - 32px));
  transform: translateX(-50%);
  box-shadow: 0 8px 24px rgb(0 0 0 / 12%);
  border-radius: 8px;
}
</style>
