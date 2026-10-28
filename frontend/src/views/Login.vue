<template>
  <div class="login-page">
    <div class="auth-card">
      <div class="brand">
        <div class="logo-block">阗</div>
        <h1 class="wordmark">阗策 · 科技小院</h1>
        <p class="slogan">多智能体自生长开发平台</p>
      </div>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent="handleLogin"
      >
        <el-form-item
          label="用户名"
          prop="username"
        >
          <el-input
            v-model="form.username"
            autocomplete="username"
            placeholder="请输入用户名"
            size="large"
          />
        </el-form-item>
        <el-form-item
          label="密码"
          prop="password"
        >
          <el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="current-password"
            placeholder="请输入密码"
            size="large"
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-form-item
          v-if="yards.length > 1"
          label="小院"
          prop="yard_id"
        >
          <el-select
            v-model="form.yard_id"
            placeholder="请选择小院"
            size="large"
            style="width: 100%"
          >
            <el-option
              v-for="t in yards"
              :key="t.yard_id"
              :label="t.yard_name"
              :value="t.yard_id"
            />
          </el-select>
          <div class="hint">
            该账号归属多个小院，登录时需指定一个。
          </div>
        </el-form-item>
        <el-button
          type="primary"
          size="large"
          native-type="submit"
          class="submit-btn"
          :loading="loading"
          @click="handleLogin"
        >
          登录
        </el-button>
        <el-alert
          v-if="errorMsg"
          :title="errorMsg"
          type="error"
          show-icon
          :closable="false"
          class="error-alert"
        />
      </el-form>
      <div class="foot">
        <router-link
          class="foot-link"
          to="/signup"
        >
          开通新的小院
        </router-link>
        <span
          v-if="showDemoHint"
          class="demo-hint"
        >演示环境账号见 README</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { reactive, ref } from 'vue'
  import { useRoute, useRouter } from 'vue-router'
  import type { FormInstance, FormRules } from 'element-plus'
  import { useAuthStore } from '@/stores/auth'
  import { defaultHomePath } from '@/utils/home'
  import { extractErrorDetails, extractErrorMessage } from '@/api/client'
  import { friendlyErrorMessage } from '@/utils/status'
  import type { YardBrief } from '@/types/api'

  const router = useRouter()
  const route = useRoute()
  const auth = useAuthStore()
  const formRef = ref<FormInstance>()
  const loading = ref(false)
  const errorMsg = ref('')
  const yards = ref<YardBrief[]>([])

  const form = reactive({ username: '', password: '', yard_id: '' })
  /** 生产构建不展示演示账号提示，避免误导真实用户 */
  const showDemoHint = !import.meta.env.PROD

  const rules: FormRules = {
    username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
    password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  }

  /** 多小院账号先无 yard 登录拿到小院列表，用户选择后再正式登录 */
  async function handleLogin() {
    errorMsg.value = ''
    await formRef.value?.validate()
    if (yards.value.length > 1 && !form.yard_id) {
      errorMsg.value = '请选择小院后继续登录。'
      return
    }
    loading.value = true
    try {
      await auth.login(form.username, form.password, form.yard_id || undefined)
      const redirect =
        typeof route.query.redirect === 'string' ? route.query.redirect : defaultHomePath(auth.roles)
      router.replace(redirect.startsWith('/') ? redirect : defaultHomePath(auth.roles))
    } catch (err: unknown) {
      const { code, message } = extractErrorMessage(err)
      if (code === 'YARD_REQUIRED') {
        const details = extractErrorDetails(err) as { yards?: YardBrief[] } | undefined
        yards.value = details?.yards ?? []
        errorMsg.value = yards.value.length
          ? '该账号归属多个小院，请选择后重新登录。'
          : message
        return
      }
      errorMsg.value = friendlyErrorMessage(code, message)
    } finally {
      loading.value = false
    }
  }
</script>

<style scoped>
  /* 设计系统 v2 §8：全屏居中玻璃卡（420px），背景光斑由全局 body::before 提供 */
  .login-page {
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
    transition: border-color 0.2s, background 0.2s;
  }
  .auth-card:hover {
    border-color: var(--ds-border-strong);
  }
  .brand {
    text-align: center;
    margin-bottom: 26px;
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
  .submit-btn {
    width: 100%;
    margin-top: 4px;
  }
  .hint {
    font-size: 12px;
    color: var(--ds-text-3);
    margin-top: 4px;
    line-height: 1.5;
  }
  .error-alert {
    margin-top: 12px;
  }
  .foot {
    margin-top: 20px;
    padding-top: 16px;
    border-top: 1px solid var(--ds-border);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }
  .foot-link {
    font-size: 13px;
    color: var(--ds-accent);
    text-decoration: none;
  }
  .foot-link:hover {
    text-decoration: underline;
  }
  .demo-hint {
    font-size: 12px;
    color: var(--ds-text-3);
  }
</style>
