<template>
  <div class="signup-page">
    <el-card class="signup-card">
      <template #header><div class="card-title">科技小院 · 租户注册</div></template>
      <el-form :model="form" :rules="rules" ref="formRef" label-width="100px" @submit.prevent>
        <el-form-item label="租户名称" prop="tenant_name"><el-input v-model="form.tenant_name" placeholder="如：阳光生态农场" /></el-form-item>
        <el-form-item label="管理员账号" prop="admin_username"><el-input v-model="form.admin_username" autocomplete="username" placeholder="3-32 位字母、数字或下划线" /></el-form-item>
        <el-form-item label="密码" prop="password"><el-input v-model="form.password" type="password" show-password autocomplete="new-password" placeholder="至少 6 位" /></el-form-item>
        <el-form-item label="确认密码" prop="confirm_password"><el-input v-model="form.confirm_password" type="password" show-password autocomplete="new-password" placeholder="请再次输入密码" @keyup.enter="handleSignup" /></el-form-item>
        <el-form-item label="联系方式" prop="contact"><el-input v-model="form.contact" placeholder="手机号或邮箱（可选）" /></el-form-item>
        <el-form-item><el-button type="primary" :loading="loading" style="width: 100%" @click="handleSignup">注册并进入</el-button></el-form-item>
        <el-form-item><el-button style="width: 100%" @click="router.push('/login')">已有账号？去登录</el-button></el-form-item>
        <el-alert v-if="errorMsg" :title="errorMsg" type="error" show-icon :closable="false" class="error-alert" />
      </el-form>
    </el-card>
  </div>
</template>
<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { FormInstance, FormRules } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { tenantApi } from '@/api/modules'
import { extractErrorMessage, REFRESH_TOKEN_KEY, TOKEN_KEY } from '@/api/client'
const router = useRouter()
const auth = useAuthStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const errorMsg = ref('')
const form = reactive({ tenant_name: '', admin_username: '', password: '', confirm_password: '', contact: '' })
const validateConfirm = (_rule: unknown, value: string, callback: (e?: Error) => void) => {
  if (value !== form.password) callback(new Error('两次输入的密码不一致'))
  else callback()
}
const rules: FormRules = {
  tenant_name: [{ required: true, message: '请输入租户名称', trigger: 'blur' }, { min: 2, max: 64, message: '租户名称需为 2-64 个字符', trigger: 'blur' }],
  admin_username: [{ required: true, message: '请输入管理员账号', trigger: 'blur' }, { pattern: /^[A-Za-z0-9_]{3,32}$/, message: '账号需为 3-32 位字母、数字或下划线', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }, { min: 6, message: '密码至少 6 位', trigger: 'blur' }],
  confirm_password: [{ required: true, message: '请再次输入密码', trigger: 'blur' }, { validator: validateConfirm, trigger: 'blur' }],
}
async function handleSignup() {
  errorMsg.value = ''
  await formRef.value?.validate()
  loading.value = true
  try {
    const res = await tenantApi.signup({ tenant_name: form.tenant_name.trim(), admin_username: form.admin_username.trim(), password: form.password, contact: form.contact.trim() || undefined })
    auth.token = res.access_token
    localStorage.setItem(TOKEN_KEY, res.access_token)
    localStorage.removeItem(REFRESH_TOKEN_KEY)
    await auth.fetchMe()
    await auth.fetchTenant()
    router.replace('/')
  } catch (err: unknown) {
    errorMsg.value = extractErrorMessage(err).message
  } finally {
    loading.value = false
  }
}
</script>
<style scoped>
.signup-page { height: 100%; display: flex; align-items: center; justify-content: center; background: #f5f7fa; }
.signup-card { width: 440px; }
.card-title { text-align: center; font-size: 18px; font-weight: 600; }
.error-alert { margin-top: 8px; }
</style>
