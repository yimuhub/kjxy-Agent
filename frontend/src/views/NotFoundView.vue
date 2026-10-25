<template>
  <el-result
    icon="warning"
    title="页面不存在"
    sub-title="链接可能已失效，或页面已被移动到其他位置。"
  >
    <template #extra>
      <el-button
        type="primary"
        @click="router.replace('/')"
      >
        返回概览
      </el-button>
    </template>
  </el-result>
</template>

<script setup lang="ts">
  import { useRouter } from 'vue-router'

  const router = useRouter()
</script>
