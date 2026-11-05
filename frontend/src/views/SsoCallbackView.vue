<template>
  <div class="sso-callback-page">
    <div class="auth-card">
      <div class="brand">
        <div class="logo-block">阗</div>
        <h1 class="wordmark">阗策 · 科技小院</h1>
        <p class="slogan">正在完成单点登录…</p>
      </div>

      <div
        v-if="state === 'loading'"
        v-loading="true"
        class="loading-block"
        element-loading-text="正在校验单位账号…"
      />

      <el-result
        v-else
        icon="warning"
        title="单点登录未完成"
        :sub-title="message"
      >
        <template #extra>
          <el-button
            type="primary"
            @click="router.replace('/login')"
          >
            返回登录
          </el-button>
        </template>
      </el-result>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, ref } from 'vue'
  import { useRoute, useRouter } from 'vue-router'
  import { useAuthStore } from '@/stores/auth'
  import { extractErrorMessage } from '@/api/client'
  import { friendlyErrorMessage } from '@/utils/status'
  import { ssoApi } from '@/api/v3'
  import { defaultHomePath } from '@/utils/home'

  const route = useRoute()
  const router = useRouter()
  const auth = useAuthStore()
  const state = ref<'loading' | 'error'>('loading')
  const message = ref('')

  function fail(text: string) {
    state.value = 'error'
    message.value = text
  }

  onMounted(async () => {
    const code = typeof route.query.code === 'string' ? route.query.code : ''
    const idpError = typeof route.query.error === 'string' ? route.query.error : ''
    if (!code) {
      fail(
        idpError
          ? `身份提供方返回错误：${idpError}`
          : '缺少登录凭据，请返回登录页重新发起单点登录。',
      )
      return
    }
    try {
      const res = await ssoApi.exchange(code)
      auth.adoptLogin(res, res.yard_id)
      // 枢纽上下文来自 /auth/me，落地页判断前先取一次
      try {
        await auth.fetchMe()
      } catch {
        /* 兜底按角色落地 */
      }
      // 清掉地址栏里的一次性 code，避免刷新时重放（后端校验也只能成功一次）
      await router.replace(defaultHomePath(auth.roles, auth.hubs))
    } catch (err: unknown) {
      const { code: errCode, message: errMessage } = extractErrorMessage(err)
      fail(friendlyErrorMessage(errCode, errMessage))
    }
  })
</script>

<style scoped>
  .sso-callback-page {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 0;
    box-sizing: border-box;
  }
  .auth-card {
    width: min(420px, 92vw);
    padding: 36px 32px 26px;
    background: var(--ds-glass);
    backdrop-filter: blur(18px) saturate(1.2);
    -webkit-backdrop-filter: blur(18px) saturate(1.2);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
  }
  .brand {
    text-align: center;
    margin-bottom: 20px;
  }
  .logo-block {
    width: 44px;
    height: 44px;
    margin: 0 auto 14px;
    border-radius: 12px;
    background: var(--ds-grad-brand);
    color: #fff;
    font-size: 22px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 6px 20px rgba(99, 102, 241, 0.4);
  }
  .wordmark {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    letter-spacing: -0.01em;
    color: var(--ds-text-1);
  }
  .slogan {
    margin: 6px 0 0;
    font-size: 13px;
    color: var(--ds-text-3);
  }
  .loading-block {
    height: 120px;
  }
</style>
