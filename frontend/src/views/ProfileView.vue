<template>
  <div class="profile">
    <h2 class="page-title">
      个人资料
    </h2>

    <el-alert
      v-if="mustChangePassword"
      type="warning"
      :closable="false"
      show-icon
      class="must-change-alert"
      title="首次登录必须修改密码"
      description="当前账号使用的是管理员分配的初始密码，请在下方「修改密码」中设置新密码后再使用系统。"
    />

    <el-empty
      v-if="!hasUser"
      description="账号信息加载失败，请刷新页面或重新登录"
    />

    <div
      v-else
      class="profile-grid"
    >
      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>基本资料</span>
            <el-button
              size="small"
              type="primary"
              :loading="savingProfile"
              @click="saveProfile"
            >
              保存资料
            </el-button>
          </div>
        </template>
        <el-form
          label-width="90px"
          class="profile-form"
        >
          <el-form-item label="用户名">
            <el-input
              :model-value="me.username || ''"
              disabled
            />
            <div class="field-note">
              用户名用于登录，不可修改
            </div>
          </el-form-item>
          <el-form-item label="姓名">
            <el-input
              v-model="profileForm.display_name"
              maxlength="64"
              placeholder="请输入姓名"
            />
          </el-form-item>
          <el-form-item label="邮箱">
            <el-input
              v-model="profileForm.email"
              placeholder="用于接收通知与找回账号"
            />
          </el-form-item>
          <el-form-item label="手机号">
            <el-input
              v-model="profileForm.phone"
              placeholder="可选"
            />
          </el-form-item>
        </el-form>
        <p class="foot-note">
          邮箱与手机号在全小院成员目录中展示；无敏感权限的成员看到的是脱敏形式。
        </p>
      </el-card>

      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>修改密码</span>
            <el-button
              size="small"
              type="primary"
              :loading="savingPassword"
              @click="savePassword"
            >
              更新密码
            </el-button>
          </div>
        </template>
        <el-form
          ref="passwordFormRef"
          :model="passwordForm"
          :rules="passwordRules"
          label-width="90px"
          class="profile-form"
        >
          <el-form-item
            label="旧密码"
            prop="old_password"
          >
            <el-input
              v-model="passwordForm.old_password"
              type="password"
              show-password
              autocomplete="current-password"
              placeholder="请输入当前密码"
            />
          </el-form-item>
          <el-form-item
            label="新密码"
            prop="new_password"
          >
            <el-input
              v-model="passwordForm.new_password"
              type="password"
              show-password
              autocomplete="new-password"
              placeholder="至少 6 位，建议包含字母与数字"
            />
          </el-form-item>
          <el-form-item
            label="确认新密码"
            prop="confirm_password"
          >
            <el-input
              v-model="passwordForm.confirm_password"
              type="password"
              show-password
              autocomplete="new-password"
              placeholder="请再次输入新密码"
              @keyup.enter="savePassword"
            />
          </el-form-item>
        </el-form>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="修改密码后，其他设备上的登录状态会立即失效，需要使用新密码重新登录。"
        />
      </el-card>
    </div>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref, watch } from 'vue'
  import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
  import { profileApi } from '@/api/v3'
  import { REFRESH_TOKEN_KEY, TOKEN_KEY } from '@/api/client'
  import { useAuthStore } from '@/stores/auth'

  interface ProfileUser {
    id?: string
    username?: string
    display_name?: string
    email?: string | null
    phone?: string | null
    must_change_password?: boolean
  }

  const auth = useAuthStore()
  const hasUser = computed(() => !!auth.user)
  const me = computed(() => (auth.user ?? {}) as ProfileUser)
  const mustChangePassword = computed(() => me.value.must_change_password === true)

  const profileForm = reactive({ display_name: '', email: '', phone: '' })
  const savingProfile = ref(false)

  const passwordFormRef = ref<FormInstance>()
  const savingPassword = ref(false)
  const passwordForm = reactive({
    old_password: '',
    new_password: '',
    confirm_password: '',
  })

  const passwordRules: FormRules = {
    old_password: [{ required: true, message: '请输入旧密码', trigger: 'blur' }],
    new_password: [
      { required: true, message: '请输入新密码', trigger: 'blur' },
      { min: 6, message: '新密码至少 6 位', trigger: 'blur' },
    ],
    confirm_password: [
      { required: true, message: '请再次输入新密码', trigger: 'blur' },
      {
        validator: (_rule: unknown, value: string, callback: (error?: Error) => void) => {
          if (value !== passwordForm.new_password) callback(new Error('两次输入的新密码不一致'))
          else callback()
        },
        trigger: 'blur',
      },
    ],
  }

  function syncFromUser(): void {
    profileForm.display_name = me.value.display_name ?? ''
    profileForm.email = me.value.email ?? ''
    profileForm.phone = me.value.phone ?? ''
  }

  watch(me, syncFromUser, { immediate: true })

  async function saveProfile(): Promise<void> {
    if (!profileForm.display_name.trim()) {
      ElMessage.warning('姓名不能为空')
      return
    }
    savingProfile.value = true
    try {
      await profileApi.updateMe({
        display_name: profileForm.display_name.trim(),
        email: profileForm.email.trim() || null,
        phone: profileForm.phone.trim() || null,
      })
      await auth.fetchMe()
      ElMessage.success('资料已保存')
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      savingProfile.value = false
    }
  }

  async function savePassword(): Promise<void> {
    await passwordFormRef.value?.validate()
    if (passwordForm.new_password === passwordForm.old_password) {
      ElMessage.warning('新密码不能与旧密码相同')
      return
    }
    savingPassword.value = true
    try {
      const res = await profileApi.changePassword({
        old_password: passwordForm.old_password,
        new_password: passwordForm.new_password,
      })
      // 改密后服务端已轮换令牌：同步本地登录态，避免下一次请求 401
      auth.token = res.access_token
      localStorage.setItem(TOKEN_KEY, res.access_token)
      localStorage.setItem(REFRESH_TOKEN_KEY, res.refresh_token)
      passwordForm.old_password = ''
      passwordForm.new_password = ''
      passwordForm.confirm_password = ''
      passwordFormRef.value?.clearValidate()
      ElMessage.success('密码已更新，其他设备已下线')
      try {
        await auth.fetchMe()
      } catch {
        /* 令牌已更新，me 刷新失败不阻塞页面 */
      }
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      savingPassword.value = false
    }
  }

  onMounted(() => {
    syncFromUser()
  })
</script>

<style scoped>
  .profile {
    padding: 4px;
    max-width: 1080px;
  }
  .must-change-alert {
    margin-bottom: 16px;
  }
  .profile-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px;
  }
  @media (max-width: 960px) {
    .profile-grid {
      grid-template-columns: 1fr;
    }
  }
  .section {
    margin-bottom: 16px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .profile-form {
    max-width: 460px;
  }
  .field-note {
    margin-top: 4px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .foot-note {
    margin: 0;
    font-size: 12px;
    color: var(--ds-text-3);
    line-height: 1.6;
  }
</style>
