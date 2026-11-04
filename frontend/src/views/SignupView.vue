<template>
  <div class="signup-page">
    <div class="auth-card">
      <div class="brand">
        <div class="logo-block">
          阗
        </div>
        <h1 class="wordmark">
          {{ activeTab === 'create' ? '开通小院' : '加入小院' }}
        </h1>
        <p class="slogan">
          阗策 · 科技小院注册
        </p>
      </div>

      <el-tabs
        v-model="activeTab"
        class="signup-tabs"
        stretch
        @tab-change="onTabChange"
      >
        <el-tab-pane
          label="创建小院"
          name="create"
        >
          <el-form
            ref="formRef"
            :model="form"
            :rules="rules"
            label-position="top"
            @submit.prevent
          >
            <el-form-item
              label="小院名称"
              prop="yard_name"
            >
              <el-input
                v-model="form.yard_name"
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
        </el-tab-pane>

        <el-tab-pane
          label="加入小院"
          name="join"
          lazy
        >
          <el-result
            v-if="joinResult"
            :icon="joinResult.status === 'active' ? 'success' : 'info'"
            :title="joinResult.status === 'active' ? '注册成功' : '已提交，等待小院管理员审核'"
            :sub-title="joinResult.sub_title"
          >
            <template #extra>
              <el-button
                type="primary"
                @click="goLogin"
              >
                {{ joinResult.status === 'active' ? '去登录' : '返回登录' }}
              </el-button>
              <el-button @click="resetJoinResult">
                再次申请
              </el-button>
            </template>
          </el-result>

          <el-form
            v-else
            ref="joinFormRef"
            :model="joinForm"
            :rules="joinRules"
            label-position="top"
            @submit.prevent
          >
            <el-form-item
              label="小院 ID"
              prop="yard_id"
            >
              <el-input
                v-model="joinForm.yard_id"
                placeholder="向小院管理员索取小院 ID"
                @blur="lookupYard"
              />
              <div
                v-if="yardLookup.checked"
                class="yard-lookup"
                :class="yardLookup.found ? 'ok' : 'bad'"
              >
                {{ yardLookup.found ? `✓ 小院名称：${yardLookup.name}` : '✗ 未找到该小院，请核对 ID' }}
              </div>
            </el-form-item>
            <el-form-item
              label="用户名"
              prop="username"
            >
              <el-input
                v-model="joinForm.username"
                autocomplete="username"
                placeholder="加入账号，3-32 位字母、数字或下划线"
              />
            </el-form-item>
            <el-form-item label="姓名">
              <el-input
                v-model="joinForm.display_name"
                maxlength="64"
                placeholder="可选"
              />
            </el-form-item>
            <el-form-item
              label="邮箱"
              prop="email"
            >
              <el-input
                v-model="joinForm.email"
                placeholder="部分小院要求必填"
              />
            </el-form-item>
            <el-form-item
              label="手机号"
              prop="phone"
            >
              <el-input
                v-model="joinForm.phone"
                placeholder="部分小院要求必填"
              />
            </el-form-item>
            <el-form-item
              label="密码"
              prop="password"
            >
              <el-input
                v-model="joinForm.password"
                type="password"
                show-password
                autocomplete="new-password"
                placeholder="加入密码，至少 6 位"
              />
            </el-form-item>
            <el-form-item
              label="确认密码"
              prop="confirm_password"
            >
              <el-input
                v-model="joinForm.confirm_password"
                type="password"
                show-password
                autocomplete="new-password"
                placeholder="请再次确认加入密码"
                @keyup.enter="handleJoin"
              />
            </el-form-item>
            <el-form-item label="备注">
              <el-input
                v-model="joinForm.note"
                type="textarea"
                :rows="2"
                maxlength="300"
                show-word-limit
                placeholder="可填写所属部门、岗位等信息，便于管理员审核"
              />
            </el-form-item>
            <el-button
              type="primary"
              size="large"
              class="submit-btn"
              :loading="joinSubmitting"
              @click="handleJoin"
            >
              提交加入申请
            </el-button>
            <el-alert
              v-if="joinErrorMsg"
              :title="joinErrorMsg"
              type="error"
              show-icon
              :closable="false"
              class="error-alert"
            />
          </el-form>
        </el-tab-pane>
      </el-tabs>

      <div class="foot">
        <span class="demo-hint">
          {{ activeTab === 'create' ? '注册后自动以小院管理员身份登录' : '提交后等待小院管理员审核' }}
        </span>
        <a
          class="foot-link"
          href="javascript:void(0)"
          @click="goLogin"
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
  import { yardApi } from '@/api/modules'
  import { profileApi } from '@/api/v3'
  import { api, extractErrorMessage, REFRESH_TOKEN_KEY, TOKEN_KEY } from '@/api/client'
  import type { ApiResponse } from '@/types/api'
  import { friendlyErrorMessage } from '@/utils/status'

  const router = useRouter()
  const auth = useAuthStore()

  const activeTab = ref<'create' | 'join'>('create')

  // ---------- 创建小院（原有流程保持不变） ----------
  const formRef = ref<FormInstance>()
  const loading = ref(false)
  const errorMsg = ref('')
  const form = reactive({
    yard_name: '',
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
    yard_name: [
      { required: true, message: '请输入小院名称', trigger: 'blur' },
      { min: 2, max: 64, message: '小院名称需为 2-64 个字符', trigger: 'blur' },
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

  async function handleSignup(): Promise<void> {
    errorMsg.value = ''
    await formRef.value?.validate()
    loading.value = true
    try {
      const res = await yardApi.signup({
        yard_name: form.yard_name.trim(),
        admin_username: form.admin_username.trim(),
        password: form.password,
        contact: form.contact.trim() || undefined,
      })
      auth.token = res.access_token
      localStorage.setItem(TOKEN_KEY, res.access_token)
      localStorage.removeItem(REFRESH_TOKEN_KEY)
      await auth.fetchMe()
      await auth.fetchYard()
      router.replace('/')
    } catch (err: unknown) {
      const { code, message } = extractErrorMessage(err)
      errorMsg.value = friendlyErrorMessage(code, message)
    } finally {
      loading.value = false
    }
  }

  // ---------- 加入小院（注册申请） ----------
  const joinFormRef = ref<FormInstance>()
  const joinSubmitting = ref(false)
  const joinErrorMsg = ref('')
  const joinResult = ref<{ status: string; sub_title: string } | null>(null)

  const yardLookup = ref<{ checked: boolean; found: boolean; name: string | null }>({
  checked: false,
  found: false,
  name: null,
})

async function lookupYard(): Promise<void> {
  const id = (joinForm.yard_id || '').trim()
  yardLookup.value = { checked: false, found: false, name: null }
  if (!id) return
  try {
    const res = await api.post<ApiResponse<{ found: boolean; name: string | null }>>(
      '/yards/lookup',
      { yard_id: id },
    )
    yardLookup.value = { checked: true, found: res.data.data.found, name: res.data.data.name }
  } catch {
    yardLookup.value = { checked: true, found: false, name: null }
  }
}

const joinForm = reactive({
    yard_id: '',
    username: '',
    display_name: '',
    email: '',
    phone: '',
    password: '',
    confirm_password: '',
    note: '',
  })

  const validateJoinConfirm = (_rule: unknown, value: string, callback: (e?: Error) => void) => {
    if (value !== joinForm.password) callback(new Error('两次输入的密码不一致'))
    else callback()
  }

  const joinRules: FormRules = {
    yard_id: [{ required: true, message: '请输入小院 ID', trigger: 'blur' }],
    username: [
      { required: true, message: '请输入用户名', trigger: 'blur' },
      {
        pattern: /^[A-Za-z0-9_]{3,32}$/,
        message: '用户名需为 3-32 位字母、数字或下划线',
        trigger: 'blur',
      },
    ],
    password: [
      { required: true, message: '请输入密码', trigger: 'blur' },
      { min: 6, message: '密码至少 6 位', trigger: 'blur' },
    ],
    confirm_password: [
      { required: true, message: '请再次输入密码', trigger: 'blur' },
      { validator: validateJoinConfirm, trigger: 'blur' },
    ],
  }

  function onTabChange(): void {
    errorMsg.value = ''
    joinErrorMsg.value = ''
  }

  async function handleJoin(): Promise<void> {
    joinErrorMsg.value = ''
    await joinFormRef.value?.validate()
    joinSubmitting.value = true
    try {
      const res = await profileApi.register({
        yard_id: joinForm.yard_id.trim(),
        username: joinForm.username.trim(),
        password: joinForm.password,
        display_name: joinForm.display_name.trim() || undefined,
        email: joinForm.email.trim() || undefined,
        phone: joinForm.phone.trim() || undefined,
        note: joinForm.note.trim() || undefined,
      })
      const isActive = res.status === 'active'
      joinResult.value = {
        status: res.status,
        sub_title: isActive
          ? res.message || '注册成功，可直接使用该账号登录本小院。'
          : res.message || '申请已提交，等待小院管理员审核通过后即可登录。',
      }
      joinForm.password = ''
      joinForm.confirm_password = ''
    } catch (err: unknown) {
      const { code, message } = extractErrorMessage(err)
      joinErrorMsg.value = friendlyErrorMessage(code, message)
    } finally {
      joinSubmitting.value = false
    }
  }

  function resetJoinResult(): void {
    joinResult.value = null
    joinErrorMsg.value = ''
  }

  function goLogin(): void {
    router.push('/login')
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
    width: min(480px, 92vw);
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
    margin-bottom: 18px;
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
  .signup-tabs {
    margin-top: 4px;
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

<style scoped>
.yard-lookup {
  font-size: 12px;
  margin-top: 4px;
}
.yard-lookup.ok {
  color: var(--el-color-success);
}
.yard-lookup.bad {
  color: var(--el-color-danger);
}
</style>
