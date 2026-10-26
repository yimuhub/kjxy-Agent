<template>
  <div class="settings-registration">
    <AsyncState
      :state="state"
      empty-text="暂无设置项"
      @retry="loadSettings"
    >
      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>注册与加入</span>
            <el-button
              size="small"
              type="primary"
              :loading="regSaving"
              @click="saveRegistration"
            >
              保存注册设置
            </el-button>
          </div>
        </template>
        <el-form
          label-width="140px"
          class="settings-form"
        >
          <el-form-item label="注册模式">
            <el-radio-group v-model="registration.mode">
              <el-radio value="closed">
                关闭注册
              </el-radio>
              <el-radio value="approval">
                申请审核
              </el-radio>
              <el-radio value="open">
                开放注册
              </el-radio>
            </el-radio-group>
            <div class="note">
              {{ modeHint }}
            </div>
          </el-form-item>
          <el-form-item label="默认角色">
            <el-select
              v-model="registration.default_role"
              style="width: 240px"
            >
              <el-option
                v-for="option in defaultRoleOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
                :disabled="option.value === 'yard_admin' && !isOwner"
              />
            </el-select>
            <div class="note">
              {{ isOwner ? '新加入成员默认获得的角色' : '只有小院负责人可以把管理员设为默认角色' }}
            </div>
          </el-form-item>
          <el-form-item label="必填联系方式">
            <el-checkbox v-model="registration.require_email">
              注册时必须填写邮箱
            </el-checkbox>
            <el-checkbox v-model="registration.require_phone">
              注册时必须填写手机号
            </el-checkbox>
          </el-form-item>
          <el-form-item label="允许新建小院">
            <el-switch v-model="registration.allow_new_yard" />
            <span class="note">关闭后，注册页只允许「加入已有小院」</span>
          </el-form-item>
        </el-form>
        <el-alert
          type="info"
          :closable="false"
          show-icon
          title="注册审核在「成员管理 → 注册审核」中处理；通过后申请人自动成为本小院成员。"
        />
      </el-card>

      <el-card class="section">
        <template #header>
          <div class="card-header">
            <span>账号安全策略</span>
            <el-button
              size="small"
              type="primary"
              :loading="secSaving"
              @click="saveSecurity"
            >
              保存安全策略
            </el-button>
          </div>
        </template>
        <el-form
          label-width="140px"
          class="settings-form"
        >
          <el-form-item label="密码最小长度">
            <el-input-number
              v-model="security.password_min_length"
              :min="6"
              :max="64"
            />
            <span class="note">6-64 位</span>
          </el-form-item>
          <el-form-item label="最大登录失败次数">
            <el-input-number
              v-model="security.max_login_attempts"
              :min="3"
              :max="100"
            />
            <span class="note">3-100 次，超出后临时锁定账号</span>
          </el-form-item>
          <el-form-item label="锁定时长（秒）">
            <el-input-number
              v-model="security.lockout_seconds"
              :min="30"
              :max="86400"
              :step="30"
            />
            <span class="note">30-86400 秒</span>
          </el-form-item>
        </el-form>
        <p class="foot-note">
          安全策略仅影响本小院成员的登录校验；已在线的会话不会立即失效。
        </p>
      </el-card>
    </AsyncState>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, reactive, ref } from 'vue'
  import { ElMessage } from 'element-plus'
  import { governanceApi } from '@/api/v3'
  import AsyncState from '@/components/AsyncState.vue'
  import { classifyError, type ViewState } from '@/utils/status'

  const props = defineProps<{ isOwner: boolean }>()

  const state = ref<ViewState>('loading')
  const regSaving = ref(false)
  const secSaving = ref(false)

  const registration = reactive({
    mode: 'approval' as 'closed' | 'approval' | 'open',
    default_role: 'viewer',
    require_email: false,
    require_phone: false,
    allow_new_yard: true,
  })

  const security = reactive({
    password_min_length: 8,
    max_login_attempts: 10,
    lockout_seconds: 300,
  })

  const defaultRoleOptions = [
    { value: 'viewer', label: '普通成员' },
    { value: 'operator', label: '科研操作员' },
    { value: 'expert', label: '专家' },
    { value: 'yard_admin', label: '小院管理员' },
  ]

  const modeHint = computed(() => {
    if (registration.mode === 'closed') return '任何人无法通过注册页加入本小院，仅管理员可手动添加'
    if (registration.mode === 'open') return '注册后直接成为成员并可按默认角色登录'
    return '注册后进入待审核队列，由管理员在成员管理中通过或拒绝'
  })

  async function loadSettings(): Promise<void> {
    state.value = 'loading'
    try {
      const data = await governanceApi.settings()
      Object.assign(registration, data.registration)
      Object.assign(security, data.security)
      state.value = 'ready'
    } catch (e) {
      state.value = classifyError(e)
    }
  }

  async function saveRegistration(): Promise<void> {
    regSaving.value = true
    try {
      const payload: Record<string, unknown> = {
        mode: registration.mode,
        require_email: registration.require_email,
        require_phone: registration.require_phone,
        allow_new_yard: registration.allow_new_yard,
      }
      // 管理员不能设置管理员为默认角色：保持原值时不提交，避免被后端按保护项拒绝
      if (props.isOwner || registration.default_role !== 'yard_admin') {
        payload.default_role = registration.default_role
      }
      await governanceApi.updateSetting('registration', payload)
      ElMessage.success('注册设置已保存')
      await loadSettings()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      regSaving.value = false
    }
  }

  async function saveSecurity(): Promise<void> {
    secSaving.value = true
    try {
      await governanceApi.updateSetting('security', {
        password_min_length: security.password_min_length,
        max_login_attempts: security.max_login_attempts,
        lockout_seconds: security.lockout_seconds,
      })
      ElMessage.success('安全策略已保存')
      await loadSettings()
    } catch {
      /* 错误 toast 已由 client 统一处理 */
    } finally {
      secSaving.value = false
    }
  }

  onMounted(() => {
    void loadSettings()
  })
</script>

<style scoped>
  .section {
    margin-bottom: 16px;
  }
  .card-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .settings-form {
    max-width: 720px;
  }
  .note {
    margin-left: 8px;
    font-size: 12px;
    color: var(--ds-text-3);
  }
  .foot-note {
    margin: 0;
    color: var(--ds-text-3);
    font-size: 12px;
  }
</style>
