<template>
  <el-container class="layout">
    <el-aside
      class="aside"
      :width="collapsed ? '64px' : '232px'"
    >
      <div
        class="logo"
        :class="{ 'logo--mini': collapsed }"
      >
        <div class="logo-block">阗</div>
        <div
          v-show="!collapsed"
          class="logo-word"
        >
          <span class="logo-name">阗策</span>
          <span class="logo-sub">科技小院</span>
        </div>
      </div>
      <div class="nav">
        <template
          v-for="g in visibleGroups"
          :key="g.label"
        >
          <div
            v-if="!collapsed && g.items.length"
            class="nav-group"
          >
            {{ g.label }}
          </div>
          <el-menu
            v-if="g.items.length"
            :default-active="activePath"
            router
            :collapse="collapsed"
            class="menu"
          >
            <el-menu-item
              v-for="m in g.items"
              :key="m.path"
              :index="m.path"
              class="nav-item"
            >
              <el-icon><component :is="m.icon" /></el-icon>
              <template #title>
                {{ m.title }}
              </template>
            </el-menu-item>
          </el-menu>
        </template>
      </div>
    </el-aside>

    <el-container class="body">
      <el-header
        class="header"
        height="56px"
      >
        <el-button
          class="collapse-btn"
          text
          @click="collapsed = !collapsed"
        >
          <el-icon :size="18">
            <Fold v-if="!collapsed" />
            <Expand v-else />
          </el-icon>
        </el-button>
        <el-breadcrumb
          separator="/"
          class="crumb"
        >
          <el-breadcrumb-item>{{ auth.tenant?.name ?? '未开通空间' }}</el-breadcrumb-item>
          <el-breadcrumb-item v-if="crumb.group">{{ crumb.group }}</el-breadcrumb-item>
          <el-breadcrumb-item>{{ crumb.current }}</el-breadcrumb-item>
        </el-breadcrumb>
        <div class="spacer" />
        <el-dropdown
          trigger="click"
          popper-class="user-menu-popper"
        >
          <div class="user-pill">
            <el-avatar
              :size="26"
              class="user-avatar"
            >
              {{ userInitial }}
            </el-avatar>
            <span class="user-name">{{ displayName }}</span>
            <el-tag
              class="role-badge"
              size="small"
              effect="plain"
              round
            >
              {{ roleLabel }}
            </el-tag>
            <el-icon class="user-caret"><ArrowDown /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item
                :icon="SwitchButton"
                @click="handleLogout"
              >
                退出登录
              </el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>

      <el-main class="main">
        <div class="content">
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
          <router-view v-else v-slot="{ Component }">
            <transition
              name="ds-fade"
              mode="out-in"
            >
              <component :is="Component" />
            </transition>
          </router-view>
        </div>
      </el-main>
    </el-container>
  </el-container>
</template>

<script setup lang="ts">
  import { computed, markRaw, onBeforeUnmount, onMounted, ref } from 'vue'
  import { useRoute, useRouter } from 'vue-router'
  import { ElMessageBox } from 'element-plus'
  import {
    ArrowDown,
    Checked,
    ChatDotRound,
    Connection,
    Cpu,
    DataLine,
    Document,
    Expand,
    Files,
    Fold,
    List,
    Odometer,
    Operation,
    Reading,
    Setting,
    Shop,
    SwitchButton,
    TrendCharts,
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
  interface MenuGroup {
    label: string
    items: MenuEntry[]
  }

  const ADMIN_ROLES = ['tenant_admin', 'platform_admin']
  const REVIEW_ROLES = ['tenant_admin', 'platform_admin', 'expert']

  /** 设计系统 v2 §5：四大分组，path 全部沿用 router 现有路径（/chat 由对话页并行接入） */
  const MENU_GROUPS: MenuGroup[] = [
    {
      label: '工作台',
      items: [
        { path: '/', title: '概览', icon: markRaw(Odometer) },
        { path: '/chat', title: '对话', icon: markRaw(ChatDotRound) },
        { path: '/qa', title: '问答', icon: markRaw(Reading) },
      ],
    },
    {
      label: '能力',
      items: [
        { path: '/agents', title: '专业助手', icon: markRaw(Cpu) },
        { path: '/teams', title: '协作班组', icon: markRaw(UserFilled) },
        { path: '/market', title: 'Agent 市场', icon: markRaw(Shop), roles: ADMIN_ROLES },
        { path: '/agents/factory', title: '智能工厂', icon: markRaw(Operation), roles: ADMIN_ROLES },
      ],
    },
    {
      label: '数据',
      items: [
        { path: '/knowledge', title: '知识库', icon: markRaw(Files) },
        { path: '/devices', title: '设备接入', icon: markRaw(Connection) },
        { path: '/tasks', title: '任务中心', icon: markRaw(List) },
        { path: '/policies', title: '政策服务', icon: markRaw(Document) },
      ],
    },
    {
      label: '管理',
      items: [
        { path: '/review', title: '专家审核', icon: markRaw(Checked), roles: REVIEW_ROLES },
        { path: '/members', title: '成员管理', icon: markRaw(User), roles: ADMIN_ROLES },
        { path: '/quota', title: '用量额度', icon: markRaw(DataLine), roles: ADMIN_ROLES },
        { path: '/settings', title: '系统设置', icon: markRaw(Setting), roles: ADMIN_ROLES },
        { path: '/evolution', title: '自生长管理', icon: markRaw(TrendCharts), roles: ADMIN_ROLES },
      ],
    },
  ]

  const ROLE_LABELS: Record<string, string> = {
    platform_admin: '平台管理员',
    tenant_admin: '空间管理员',
    expert: '专家',
    operator: '运营',
    viewer: '成员',
  }

  const route = useRoute()
  const router = useRouter()
  const auth = useAuthStore()

  /** 按当前角色过滤菜单（权限逻辑与原实现完全一致） */
  const visibleGroups = computed<MenuGroup[]>(() =>
    MENU_GROUPS.map(g => ({
      label: g.label,
      items: g.items.filter(m => auth.hasAnyRole(m.roles)),
    })).filter(g => g.items.length > 0),
  )

  /** 当前路由对应的菜单项（最长前缀匹配），驱动激活态与面包屑 */
  const matchEntry = computed<{ group: MenuGroup; item: MenuEntry } | null>(() => {
    let best: { group: MenuGroup; item: MenuEntry } | null = null
    for (const g of visibleGroups.value) {
      for (const m of g.items) {
        if (
          route.path === m.path ||
          (m.path !== '/' && route.path.startsWith(`${m.path}/`))
        ) {
          if (!best || m.path.length > best.item.path.length) best = { group: g, item: m }
        }
      }
    }
    return best
  })

  const activePath = computed(() => matchEntry.value?.item.path ?? route.path)

  const crumb = computed(() => {
    const m = matchEntry.value
    if (m) return { group: m.group.label, current: m.item.title }
    return {
      group: '',
      current: (route.meta.title as string | undefined) ?? '',
    }
  })

  const displayName = computed(
    () => auth.user?.display_name || auth.user?.username || '用户',
  )
  const userInitial = computed(() => displayName.value.slice(0, 1))
  const roleLabel = computed(() => {
    const hit = auth.roles.find(r => ROLE_LABELS[r])
    return hit ? ROLE_LABELS[hit] : '成员'
  })

  /** 平台管理员：无空间但给"全局总览"（租户列表/调用量/任务状态），而非空指引 */
  const isPlatformAdmin = computed(() => auth.roles.includes('platform_admin'))

  /** 窄屏（≤900px）侧栏收起为图标条；也可手动折叠 */
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

  /* ---------- 侧栏：232px 玻璃 / 折叠 64px ---------- */
  .aside {
    display: flex;
    flex-direction: column;
    background: rgba(255, 255, 255, 0.025);
    backdrop-filter: blur(18px) saturate(1.2);
    -webkit-backdrop-filter: blur(18px) saturate(1.2);
    border-right: 1px solid var(--ds-border);
    transition: width 0.2s;
    overflow: hidden;
  }
  .logo {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 64px;
    padding: 0 16px;
    border-bottom: 1px solid var(--ds-border);
    flex: none;
  }
  .logo--mini {
    justify-content: center;
    padding: 0;
  }
  .logo-block {
    width: 32px;
    height: 32px;
    flex: none;
    border-radius: var(--ds-radius-control);
    background: var(--ds-grad-brand);
    color: #fff;
    font-size: 16px;
    font-weight: 700;
    display: flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 4px 14px rgba(99, 102, 241, 0.35);
  }
  .logo-word {
    display: flex;
    flex-direction: column;
    line-height: 1.2;
    overflow: hidden;
    white-space: nowrap;
  }
  .logo-name {
    font-size: 15px;
    font-weight: 700;
    letter-spacing: 0.02em;
    color: var(--ds-text-1);
  }
  .logo-sub {
    font-size: 11px;
    letter-spacing: 0.14em;
    color: var(--ds-text-3);
  }

  /* ---------- 分组导航 ---------- */
  .nav {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 8px 0 16px;
  }
  .nav-group {
    margin: 14px 16px 6px;
    font-size: 12px;
    letter-spacing: 0.08em;
    color: var(--ds-text-3);
    user-select: none;
  }
  .menu {
    --el-menu-hover-bg-color: var(--ds-glass);
    --el-menu-item-height: 38px;
    background: transparent;
    border-right: none;
  }
  .menu:not(.el-menu--collapse) {
    padding: 0 8px;
  }
  .nav-item {
    position: relative;
    margin: 2px 0;
    border-radius: var(--ds-radius-control);
    color: var(--ds-text-2);
  }
  .nav-item:hover {
    color: var(--ds-text-1);
  }
  .nav-item.is-active {
    background: var(--ds-glass-strong);
    color: var(--ds-text-1);
  }
  .nav-item.is-active::before {
    content: '';
    position: absolute;
    left: 0;
    top: 22%;
    bottom: 22%;
    width: 2px;
    border-radius: var(--ds-radius-pill);
    background: var(--ds-grad-brand);
  }
  .nav-item.is-active .el-icon {
    color: var(--ds-accent);
  }

  /* ---------- 顶栏 56px：面包屑 + 用户胶囊 ---------- */
  .body {
    background: transparent;
    min-width: 0;
  }
  .header {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 0 16px;
    background: rgba(255, 255, 255, 0.02);
    backdrop-filter: blur(18px) saturate(1.2);
    -webkit-backdrop-filter: blur(18px) saturate(1.2);
    border-bottom: 1px solid var(--ds-border);
  }
  .collapse-btn {
    color: var(--ds-text-2);
    padding: 6px;
  }
  .collapse-btn:hover {
    color: var(--ds-text-1);
  }
  .crumb {
    min-width: 0;
    flex: none;
  }
  .spacer {
    flex: 1;
  }
  .user-pill {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 4px 10px;
    border-radius: var(--ds-radius-pill);
    border: 1px solid var(--ds-border);
    background: var(--ds-glass);
    color: var(--ds-text-1);
    cursor: pointer;
    outline: none;
    transition: border-color 0.2s, background 0.2s;
  }
  .user-pill:hover {
    border-color: var(--ds-border-strong);
    background: var(--ds-glass-strong);
  }
  .user-avatar {
    background: var(--ds-accent);
    color: #fff;
    font-size: 13px;
    font-weight: 600;
    flex: none;
  }
  .user-name {
    font-size: 13px;
    max-width: 140px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .role-badge {
    flex: none;
  }
  .user-caret {
    color: var(--ds-text-3);
    font-size: 12px;
  }

  /* ---------- 内容区：1440 居中 + 120ms fade ---------- */
  .main {
    background: transparent;
    padding: 0;
  }
  .content {
    max-width: 1440px;
    margin: 0 auto;
    padding: 24px;
    min-height: 100%;
    box-sizing: border-box;
  }
  .no-tenant {
    display: flex;
    justify-content: center;
    padding-top: 100px;
  }
  .no-tenant p {
    margin: 4px 0;
    color: var(--ds-text-2);
  }

  @media (max-width: 900px) {
    .content {
      padding: 12px;
    }
    .user-name,
    .role-badge {
      display: none;
    }
    .crumb {
      overflow: hidden;
      white-space: nowrap;
    }
  }
</style>
