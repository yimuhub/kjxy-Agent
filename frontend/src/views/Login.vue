<template>
  <div class="login-page">
    <el-card class="login-card">
      <template #header>
        <div class="card-title">
          科技小院 · 登录
        </div>
      </template>
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="80px"
        @submit.prevent
      >
        <el-form-item
          label="用户名"
          prop="username"
        >
          <el-input
            v-model="form.username"
            autocomplete="username"
            placeholder="请输入用户名"
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
            @keyup.enter="handleLogin"
          />
        </el-form-item>
        <el-form-item
          v-if="tenants.length > 1"
          label="使用空间"
          prop="tenant_id"
        >
          <el-select
            v-model="form.tenant_id"
            placeholder="请选择使用空间"
            style="width: 100%"
          >
            <el-option
              v-for="t in tenants"
              :key="t.id ?? t.name"
              :label="t.name ?? t.code ?? t.id"
              :value="t.id ?? ''"
            />
          </el-select>
          <div class="hint">
            该账号归属多个使用空间，登录时需指定一个。
          </div>
        </el-form-item>
        <el-form-item>
          <el-button
            type="primary"
            :loading="loading"
            style="width: 100%"
            @click="handleLogin"
          >
            登录
          </el-button>
        </el-form-item>
        <el-alert
          v-if="errorMsg"
          :title="errorMsg"
          type="error"
          show-icon
          :closable="false"
          class="error-alert"
        />
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import { reactive, ref } from 'vue'
  import { useRouter } from 'vue-router'
  import type { FormInstance, FormRules } from 'element-plus'
  import { useAuthStore } from '@/stores/auth'
  import { authApi } from '@/api/modules'
  import { extractErrorMessage } from '@/api/client'
  import type { Tenant } from '@/types/api'

  const router = useRouter()
  const auth = useAuthStore()
  const formRef = ref<FormInstance>()
  const loading = ref(false)
  const errorMsg = ref('')
  const tenants = ref<Tenant[]>([])

  const form = reactive({ username: '', password: '', tenant_id: '' })

  const rules: FormRules = {
    username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
    password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
  }

  /** 先做一次预登录，拿到租户列表；若多租户则让用户选择后再正式登录 */
  async function handleLogin() {
    errorMsg.value = ''
    await formRef.value?.validate()
    loading.value = true
    try {
      if (tenants.value.length > 1 && !form.tenant_id) {
        errorMsg.value = '请选择使用空间后继续登录。'
        return
      }
      await auth.login(form.username, form.password, form.tenant_id || undefined)
      router.replace('/')
    } catch (err: unknown) {
      const { code, message } = extractErrorMessage(err)
      if (code === 'TENANT_REQUIRED' || message.includes('租户') || message.includes('使用空间')) {
        // 尝试一次无 tenant 登录以拉取使用空间列表（契约要求多空间用户必传 tenant_id）
        try {
          const res = await authApi.login(form.username, form.password)
          tenants.value = res.tenants ?? []
          if (tenants.value.length > 1) {
            errorMsg.value = '该账号归属多个使用空间，请选择后重新登录。'
            return
          }
        } catch {
          /* 忽略二次错误，展示原始错误 */
        }
      }
      errorMsg.value = message
    } finally {
      loading.value = false
    }
  }
</script>

<style scoped>
  .login-page {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    background: #f5f7fa;
  }
  .login-card {
    width: min(420px, 92vw);
  }
  .card-title {
    text-align: center;
    font-size: 18px;
    font-weight: 600;
  }
  .hint {
    font-size: 12px;
    color: #909399;
    margin-top: 4px;
  }
  .error-alert {
    margin-top: 8px;
  }
</style>
