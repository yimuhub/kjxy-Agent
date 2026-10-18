<template>
  <el-container class="layout">
    <el-aside
      :width="collapsed ? '64px' : '220px'"
      class="aside"
    >
      <div class="brand">
        {{ collapsed ? '科' : '科技小院' }}
      </div>
      <el-menu
        :default-active="route.path"
        router
        class="menu"
        :collapse="collapsed"
      >
        <el-menu-item
          v-for="m in visibleMenus"
          :key="m.path"
          :index="m.path"
        >
          <el-icon><component :is="m.icon" /></el-icon>
          <template #title>
            {{ m.title }}
          </template>
        </el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">
        <span class="tenant">{{ auth.tenant?.name ?? '—' }}</span>
        <div class="user">
          <el-icon><UserFilled /></el-icon>
          <span>{{ auth.user?.display_name || auth.user?.username || '用户' }}</span>
          <el-button
            link
            type="danger"
            @click="handleLogout"
          >
            退出
          </el-button>
        </div>
      </el-header>
      <el-main class="main">
        <!-- 尚无使用空间的账号：业务页都需要空间上下文。
             平台管理员给"全局总览"（租户列表/调用量/任务状态，只读边界）；其他账号给明确指引。 -->
        <GlobalOverview
          v-if="showNoTenantNotice && isPlatformAdmin"
        />
        <div
          v-else-if="showNoTenantNotice"
          class="no-tenant"
        >
          <el-empty>
            <template #description>
              <p>当前账号还没有开通使用空间，请联系管理员为你开通。</p>
            </template>
            <el-button
              type="primary"
              @click="handleLogout"
            >
              退出登录
            </el-button>
          </el-empty>
        </div>
        <router-view v-else />
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
  import { computed, markRaw, onBeforeUnmount, onMounted, ref } from 'vue'
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
  import type { Component } from 'vue'
  import { useAuthStore } from '@/stores/auth'
  import GlobalOverview from '@/views/GlobalOverview.vue'

  /** 侧栏菜单配置：roles 为空表示全员可见（与 router meta.roles 保持一致） */
  interface MenuEntry {
    path: string
    title: string
    icon: Component
    roles?: string[]
  }

  const ADMIN_ROLES = ['tenant_admin', 'platform_admin']
  const MENUS: MenuEntry[] = [
    { path: '/', title: '工作台', icon: markRaw(Odometer) },
    { path: '/qa', title: '知识问答', icon: markRaw(ChatDotRound) },
    { path: '/knowledge', title: '知识库', icon: markRaw(Files) },
    { path: '/tasks', title: '任务中心', icon: markRaw(List) },
    {
      path: '/review',
      title: '专家审核',
      icon: markRaw(Checked),
      roles: ['tenant_admin', 'platform_admin', 'expert'],
    },
    { path: '/agents', title: '专业助手', icon: markRaw(Cpu) },
    { path: '/agents/factory', title: 'Agent 工厂', icon: markRaw(Cpu), roles: ADMIN_ROLES },
    { path: '/policies', title: '政策服务', icon: markRaw(Document) },
    { path: '/evolution', title: '自生长管理', icon: markRaw(Setting), roles: ADMIN_ROLES },
    { path: '/quota', title: '用量额度', icon: markRaw(PieChart), roles: ADMIN_ROLES },
    { path: '/members', title: '成员管理', icon: markRaw(User), roles: ADMIN_ROLES },
    { path: '/settings', title: '系统设置', icon: markRaw(Setting), roles: ADMIN_ROLES },
  ]

  const route = useRoute()
  const router = useRouter()
  const auth = useAuthStore()

  /** 按当前角色过滤菜单 */
  const visibleMenus = computed(() => MENUS.filter(m => auth.hasAnyRole(m.roles)))

  /** 平台管理员：无空间但给"全局总览"（租户列表/调用量/任务状态），而非空指引 */
  const isPlatformAdmin = computed(() => auth.roles.includes('platform_admin'))

  /** 窄屏（≤900px）侧栏收起为图标条 */
  const collapsed = ref(false)
  const mql = window.matchMedia('(max-width: 900px)')
  function syncCollapsed() {
    collapsed.value = mql.matches
  }
  mql.addEventListener('change', syncCollapsed)
  onBeforeUnmount(() => mql.removeEventListener('change', syncCollapsed))

  /** 已登录但确实没有使用空间的账号：不渲染业务页，给明确指引 */
  const showNoTenantNotice = computed(
    () => auth.meLoaded && auth.isLoggedIn() && auth.tenants.length === 0,
  )

  onMounted(async () => {
    syncCollapsed()
    // 角色与用户信息可能还没加载（直达 URL / 刷新），补齐一次
    if (!auth.user || !auth.meLoaded || auth.roles.length === 0) {
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
    transition: width 0.2s;
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
  @media (max-width: 900px) {
    /* 窄屏收紧主内容留白 */
    .main {
      padding: 12px;
    }
    .tenant {
      max-width: 40vw;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }
</style>
