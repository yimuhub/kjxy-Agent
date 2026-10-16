<template>
  <el-container class="layout">
    <el-aside width="220px" class="aside">
      <div class="brand">科技小院</div>
      <el-menu :default-active="route.path" router class="menu">
        <el-menu-item index="/">
          <el-icon><Odometer /></el-icon><span>工作台</span>
        </el-menu-item>
        <el-menu-item index="/qa">
          <el-icon><ChatDotRound /></el-icon><span>知识问答</span>
        </el-menu-item>
        <el-menu-item index="/knowledge">
          <el-icon><Files /></el-icon><span>知识库</span>
        </el-menu-item>
        <el-menu-item index="/tasks">
          <el-icon><List /></el-icon><span>任务中心</span>
        </el-menu-item>
        <el-menu-item index="/review">
          <el-icon><Checked /></el-icon><span>专家审核</span>
        </el-menu-item>
        <el-menu-item index="/agents">
          <el-icon><Cpu /></el-icon><span>专业智能体</span>
        </el-menu-item>
        <el-menu-item index="/policies">
          <el-icon><Document /></el-icon><span>政策服务</span>
        </el-menu-item>
        <el-menu-item index="/evolution">
          <el-icon><Setting /></el-icon><span>自生长管理</span>
        </el-menu-item>
        <el-menu-item index="/quota">
          <el-icon><PieChart /></el-icon><span>配额用量</span>
        </el-menu-item>
        <el-menu-item index="/members">
          <el-icon><User /></el-icon><span>成员管理</span>
        </el-menu-item>
        <el-menu-item index="/settings">
          <el-icon><Setting /></el-icon><span>系统设置</span>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="tenant">{{ auth.tenant?.name ?? '—' }}</span>
        <div class="user">
          <el-icon><UserFilled /></el-icon>
          <span>{{ auth.user?.display_name || auth.user?.username || '用户' }}</span>
          <el-button link type="danger" @click="handleLogout">退出</el-button>
        </div>
      </el-header>
      <el-main class="main">
        <!-- 平台管理员等无租户账号：业务页都需要租户上下文，直接给明确提示而不是让各页报加载失败 -->
        <div v-if="showNoTenantNotice" class="no-tenant">
          <el-empty>
            <template #description>
              <p>当前账号未归属任何租户（平台管理员），无法使用业务功能。</p>
              <p>请退出后使用租户账号（演示：tenant_admin / tenant123）登录体验完整流程。</p>
            </template>
            <el-button type="primary" @click="handleLogout">退出登录</el-button>
          </el-empty>
        </div>
        <router-view v-else />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import {
  ChatDotRound,
  Checked,
  Cpu,
  Document,
  Files,
  List,
  Odometer,
  PieChart,
  Setting,
  User,
  UserFilled,
} from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

/** 已登录但确实无租户（如平台管理员）：不渲染业务页，给明确指引 */
const showNoTenantNotice = computed(
  () => auth.meLoaded && auth.isLoggedIn() && auth.tenants.length === 0,
)

onMounted(async () => {
  if (!auth.user) {
    try {
      await auth.fetchMe()
    } catch {
      /* 401 由 client 拦截器处理跳转 */
    }
  }
  if (!auth.tenant) {
    try {
      await auth.fetchTenant()
    } catch {
      /* 无权限时顶栏显示占位即可 */
    }
  }
})

async function handleLogout() {
  await ElMessageBox.confirm('确定要退出登录吗？', '退出', { type: 'warning' })
  await auth.logout()
  router.replace('/login')
}
</script>

<style scoped>
.layout {
  height: 100%;
}
.aside {
  background: #fff;
  border-right: 1px solid #ebeef5;
}
.brand {
  height: 60px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 18px;
  font-weight: 700;
  color: #409eff;
  border-bottom: 1px solid #ebeef5;
}
.menu {
  border-right: none;
}
.no-tenant {
  display: flex;
  justify-content: center;
  padding-top: 120px;
}
.no-tenant p {
  margin: 4px 0;
  color: #606266;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  background: #fff;
  border-bottom: 1px solid #ebeef5;
}
.tenant {
  font-weight: 600;
}
.user {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #606266;
}
.main {
  background: #f5f7fa;
}
</style>
