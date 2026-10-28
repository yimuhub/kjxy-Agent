<template>
  <el-config-provider :locale="zhCn">
    <BackendVersionBanner />
    <router-view />
  </el-config-provider>
</template>

<script setup lang="ts">
  import zhCn from 'element-plus/es/locale/lang/zh-cn'
  import BackendVersionBanner from '@/components/layout/BackendVersionBanner.vue'
</script>

<style>
  html,
  body,
  #app {
    margin: 0;
    height: 100%;
  }
  /* 窄屏：主内容留白收紧、表格容器允许横向滚动，避免宽表把页面撑破 */
  @media (max-width: 900px) {
    .el-main {
      padding: 12px;
    }
    .el-table {
      overflow-x: auto;
    }
    .el-card__body {
      overflow-x: auto;
    }
  }
</style>
