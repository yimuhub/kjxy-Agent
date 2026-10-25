<template>
  <div class="invite-page">
    <div class="auth-card">
      <div class="brand">
        <div class="logo-block">阗</div>
        <h1 class="wordmark">阗策 · 科技小院</h1>
        <p class="slogan">接受成员邀请</p>
      </div>

      <div
        v-if="loading"
        v-loading="true"
        class="loading-block"
        element-loading-text="正在校验邀请链接…"
      />

      <el-result
        v-else-if="invalidReason"
        icon="warning"
        title="邀请链接不可用"
        :sub-title="invalidReason"
      >
        <template #extra>
          <el-button
            type="primary"
            @click="router.replace('/login')"
          >
            去登录
          </el-button>
        </template>
      </el-result>

      <template v-else-if="preview">
        <el-alert
          type="success"
          :closable="false"
          show-icon
          class="invite-alert"
        >
          <template #title>
            <strong>{{ preview.tenant_name }}</strong> 邀请你以「{{
              roleLabel(preview.role)
            }}」身份加入
          </template>
          <template #default>
            <span class="invite-meta">
              <template v-if="preview.note">备注：{{ preview.note }} · </template>
              链接有效期至 {{ formatTime(preview.expires_at) }}
            </span>
          </template>
        </el-alert>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          label-position="top"
          @submit.prevent
        >
          <el-form-item
            label="账号"
            prop="username"
          >
            <el-input
              v-model="form.username"
              autocomplete="username"
              placeholder="3-32 位字母、数字或下划线"
              size="large"
            />
            <div class="hint">
              已有账号请直接填写原账号与原密码；新账号将自动创建。
            </div>
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
              placeholder="至少 8 位"
              size="large"
            />
          </el-form-item>
          <el-form-item
            label="确认密码"
            prop="confirm"
          >
            <el-input
              v-model="form.confirm"
              type="password"
              show-password
              autocomplete="new-password"
              placeholder="再次输入密码"
              size="large"
              @keyup.enter="handleAccept"
            />
          </el-form-item>
          <el-form-item label="姓名（可选）">
            <el-input
              v-model="form.display_name"
              placeholder="用于团队内的展示名"
              size="large"
            />
          </el-form-item>
          <el-button
            type="primary"
            size="large"
            class="submit-btn"
            :loading="submitting"
            @click="handleAccept"
          >
            接受邀请并进入
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
          <span class="foot-text">已经是该空间成员？</span>
          <router-link
            class="foot-link"
            to="/login"
          >
            直接登录
          </router-link>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { onMounted, reactive, ref } from 'vue'
  import { useRoute, useRouter } from 'vue-router'
  import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
  import { tenantApi } from '@/api/modules'
  import { extractErrorMessage } from '@/api/client'
  import { useAuthStore } from '@/stores/auth'
  import type { InvitationPreview } from '@/types/api'

  const route = useRoute()
  const router = useRouter()
  const auth = useAuthStore()

  const token = String(route.params.token ?? '')
  const loading = ref(true)
  const submitting = ref(false)
  const invalidReason = ref('')
  const errorMsg = ref('')
  const preview = ref<InvitationPreview | null>(null)
  const formRef = ref<FormInstance>()
  const form = reactive({ username: '', password: '', confirm: '', display_name: '' })

  const ROLE_LABELS: Record<string, string> = {
    tenant_admin: '使用空间管理员',
    expert: '专家',
    operator: '操作员',
    viewer: '访客',
  }
  function roleLabel(role?: string): string {
    return ROLE_LABELS[role ?? ''] ?? role ?? '—'
  }
  function formatTime(t?: string): string {
    if (!t) return '—'
    const d = new Date(t)
    return isNaN(d.getTime()) ? t : d.toLocaleString('zh-CN')
  }

  const rules: FormRules = {
    username: [
      { required: true, message: '请输入账号', trigger: 'blur' },
      {
        pattern: /^[A-Za-z0-9_]{3,32}$/,
        message: '账号需为 3-32 位字母、数字或下划线',
        trigger: 'blur',
      },
    ],
    password: [
      { required: true, message: '请输入密码', trigger: 'blur' },
      { min: 8, message: '密码至少 8 位', trigger: 'blur' },
    ],
    confirm: [
      { required: true, message: '请再次输入密码', trigger: 'blur' },
      {
        validator: (_rule: unknown, value: string, callback: (error?: Error) => void) => {
          if (value !== form.password) callback(new Error('两次输入的密码不一致'))
          else callback()
        },
        trigger: 'blur',
      },
    ],
  }

  async function loadPreview() {
    loading.value = true
    try {
      preview.value = await tenantApi.previewInvitation(token)
    } catch (err: unknown) {
      const { code, message } = extractErrorMessage(err)
      invalidReason.value =
        code === 'INVITATION_INVALID' ? message : `${message}（请向邀请人确认链接是否仍然有效）`
    } finally {
      loading.value = false
    }
  }

  async function handleAccept() {
    if (!preview.value) return
    errorMsg.value = ''
    await formRef.value?.validate()
    submitting.value = true
    try {
      const res = await tenantApi.acceptInvitation(token, {
        username: form.username,
        password: form.password,
        display_name: form.display_name || undefined,
      })
      auth.adoptLogin(res, preview.value.tenant_id)
      ElMessage.success(`已加入「${preview.value.tenant_name}」`)
      router.replace('/')
    } catch (err: unknown) {
      const { code, message } = extractErrorMessage(err)
      if (code === 'INVITATION_INVALID') {
        invalidReason.value = message
        return
      }
      errorMsg.value = message
    } finally {
      submitting.value = false
    }
  }

  onMounted(loadPreview)
</script>

<style scoped>
  .invite-page {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 0;
    box-sizing: border-box;
    overflow-y: auto;
  }
  .auth-card {
    width: min(440px, 92vw);
    padding: 32px;
    background: var(--ds-glass);
    backdrop-filter: blur(18px) saturate(1.2);
    -webkit-backdrop-filter: blur(18px) saturate(1.2);
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-card);
  }
  .brand {
    text-align: center;
    margin-bottom: 22px;
  }
  .logo-block {
    width: 44px;
    height: 44px;
    margin: 0 auto 12px;
    border-radius: 12px;
    background: var(--ds-grad-brand);
    color: #fff;
    font-size: 22px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
  }
  .wordmark {
    margin: 0;
    font-size: 20px;
    font-weight: 600;
    color: var(--ds-text-1);
  }
  .slogan {
    margin: 6px 0 0;
    font-size: 13px;
    color: var(--ds-text-3);
  }
  .loading-block {
    height: 180px;
  }
  .invite-alert {
    margin-bottom: 18px;
  }
  .invite-meta {
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .submit-btn {
    width: 100%;
    margin-top: 4px;
  }
  .hint {
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.5;
    margin-top: 4px;
  }
  .error-alert {
    margin-top: 12px;
  }
  .foot {
    margin-top: 18px;
    padding-top: 14px;
    border-top: 1px solid var(--ds-border);
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
  }
  .foot-text,
  .foot-link {
    font-size: 13px;
  }
  .foot-text {
    color: var(--ds-text-3);
  }
  .foot-link {
    color: var(--ds-accent);
    text-decoration: none;
  }
  .foot-link:hover {
    text-decoration: underline;
  }
</style>
