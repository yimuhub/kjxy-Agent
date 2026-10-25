<template>
  <div class="signup-page">
    <div class="auth-card">
      <div class="brand">
        <div class="logo-block">阗</div>
        <h1 class="wordmark">开通使用空间</h1>
        <p class="slogan">阗策 · 科技小院多租户注册</p>
      </div>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-position="top"
        @submit.prevent
      >
        <el-form-item
          label="空间名称"
          prop="tenant_name"
        >
          <el-input
            v-model="form.tenant_name"
            placeholder="如：阳光生态农场"
          />
        </el-form-item>
        <el-form-item
          label="管理员账号"
          prop="admin_username"
        >
          <el-input
            v-model="form.admin_username"
            autocomplete="username"
            placeholder="3-32 位字母、数字或下划线"
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
            autocomplete="new-password"
            placeholder="至少 6 位"
          />
        </el-form-item>
        <el-form-item
          label="确认密码"
          prop="confirm_password"
        >
          <el-input
            v-model="form.confirm_password"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="请再次输入密码"
            @keyup.enter="handleSignup"
          />
        </el-form-item>
        <el-form-item
          label="联系方式"
          prop="contact"
        >
          <el-input
            v-model="form.contact"
            placeholder="手机号或邮箱（可选）"
          />
        </el-form-item>
        <el-button
          type="primary"
          size="large"
          class="submit-btn"
          :loading="loading"
          @click="handleSignup"
        >
          注册并进入
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
        <span class="demo-hint">注册后自动以空间管理员身份登录</span>
        <a
          class="foot-link"
          href="javascript:void(0)"
          @click="router.push('/login')"
        >已有账号？去登录</a>
      </div>
    </div>
  </div>
</template>
<script setup lang="ts">
  import { reactive, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import type { FormInstance, FormRules } from 'element-plus'
  import { useAuthStore } from '@/stores/auth'
  import { tenantApi } from '@/api/modules'
  import { extractErrorMessage, REFRESH_TOKEN_KEY, TOKEN_KEY } from '@/api/client'
  import { friendlyErrorMessage } from '@/utils/status'
  const router = useRouter()
  const auth = useAuthStore()
  const formRef = ref<FormInstance>()
  const loading = ref(false)
  const errorMsg = ref('')
  const form = reactive({
    tenant_name: '',
    admin_username: '',
    password: '',
    confirm_password: '',
    contact: '',
  })
  const validateConfirm = (_rule: unknown, value: string, callback: (e?: Error) => void) => {
    if (value !== form.password) callback(new Error('两次输入的密码不一致'))
    else callback()
  }
  const rules: FormRules = {
    tenant_name: [
      { required: true, message: '请输入使用空间名称', trigger: 'blur' },
      { min: 2, max: 64, message: '使用空间名称需为 2-64 个字符', trigger: 'blur' },
    ],
    admin_username: [
      { required: true, message: '请输入管理员账号', trigger: 'blur' },
      {
        pattern: /^[A-Za-z0-9_]{3,32}$/,
        message: '账号需为 3-32 位字母、数字或下划线',
        trigger: 'blur',
      },
    ],
    password: [
      { required: true, message: '请输入密码', trigger: 'blur' },
      { min: 6, message: '密码至少 6 位', trigger: 'blur' },
    ],
    confirm_password: [
      { required: true, message: '请再次输入密码', trigger: 'blur' },
      { validator: validateConfirm, trigger: 'blur' },
    ],
  }
  async function handleSignup() {
    errorMsg.value = ''
    await formRef.value?.validate()
    loading.value = true
    try {
      const res = await tenantApi.signup({
        tenant_name: form.tenant_name.trim(),
        admin_username: form.admin_username.trim(),
        password: form.password,
        contact: form.contact.trim() || undefined,
      })
      auth.token = res.access_token
      localStorage.setItem(TOKEN_KEY, res.access_token)
      localStorage.removeItem(REFRESH_TOKEN_KEY)
      await auth.fetchMe()
      await auth.fetchTenant()
      router.replace('/')
    } catch (err: unknown) {
      const { code, message } = extractErrorMessage(err)
      errorMsg.value = friendlyErrorMessage(code, message)
    } finally {
      loading.value = false
    }
  }
</script>
<style scoped>
  /* 设计系统 v2 §8：全屏居中玻璃卡，背景光斑由全局 body::before 提供 */
  .signup-page {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 0;
    box-sizing: border-box;
  }
  .auth-card {
    width: min(460px, 92vw);
    padding: 34px 32px 22px;
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
    margin-bottom: 22px;
  }
  .logo-block {
    width: 40px;
    height: 40px;
    margin: 0 auto 12px;
    border-radius: 12px;
    background: var(--ds-grad-brand);
    color: #fff;
    font-size: 20px;
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
    margin-top: 2px;
  }
  .error-alert {
    margin-top: 12px;
  }
  .foot {
    margin-top: 14px;
    padding-top: 14px;
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
