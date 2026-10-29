<template>
  <div class="forbidden">
    <el-result
      icon="warning"
      title="没有访问权限"
      sub-title="当前账号未被授予该功能。可联系小院负责人或管理员开通，或返回概览。"
    >
      <template #extra>
        <el-button
          type="primary"
          @click="router.push('/')"
        >
          返回概览
        </el-button>
        <el-button
          v-if="auth.hasPermission('chat:use')"
          @click="router.push('/chat')"
        >
          去对话
        </el-button>
      </template>
    </el-result>
  </div>
</template>

<script setup lang="ts">
  import { useRouter } from 'vue-router'
  import { useAuthStore } from '@/stores/auth'

  const router = useRouter()
  const auth = useAuthStore()
</script>

<style scoped>
  .forbidden {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 60vh;
  }
</style>
