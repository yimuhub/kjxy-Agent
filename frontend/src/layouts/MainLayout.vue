<template>
  <el-container class="layout">
    <a
      class="skip-link"
      href="#main-content"
    >
      跳到主要内容
    </a>

    <div
      v-if="isMobile && drawerOpen"
      class="drawer-backdrop"
      aria-hidden="true"
      @click="drawerOpen = false"
    />

    <el-aside
      class="aside"
      :class="{ 'aside--drawer': isMobile, 'aside--open': isMobile && drawerOpen }"
      :width="asideWidth"
    >
      <div
        class="logo"
        :class="{ 'logo--mini': isMini }"
      >
        <div class="logo-block">阗</div>
        <div
          v-show="showLogoWord"
          class="logo-word"
        >
          <span class="logo-name">阗策</span>
          <span class="logo-sub">科技小院</span>
        </div>
      </div>
      <nav
        id="main-nav"
        class="nav"
        aria-label="主导航"
      >
        <el-menu
          ref="menuRef"
          :default-active="activePath"
          :default-openeds="openedSubmenus"
          router
          :collapse="menuCollapse"
          class="menu"
        >
          <el-menu-item-group
            v-for="g in visibleGroups"
            :key="g.label"
            :title="g.label"
          >
            <template
              v-for="node in g.items"
              :key="node.key"
            >
              <el-sub-menu
                v-if="node.children?.length"
                :index="node.key"
              >
                <template #title>
                  <el-icon aria-hidden="true">
                    <component :is="node.icon" />
                  </el-icon>
                  <span>{{ node.title }}</span>
                </template>
                <el-menu-item
                  v-for="child in node.children"
                  :key="child.path"
                  :index="child.path ?? child.key"
                  class="nav-item"
                  :aria-current="activePath === child.path ? 'page' : undefined"
                >
                  <el-icon aria-hidden="true">
                    <component :is="child.icon" />
                  </el-icon>
                  <template #title>
                    {{ child.title }}
                  </template>
                </el-menu-item>
              </el-sub-menu>
              <el-menu-item
                v-else
                :index="node.path ?? node.key"
                class="nav-item"
                :aria-current="activePath === node.path ? 'page' : undefined"
              >
                <el-icon aria-hidden="true">
                  <component :is="node.icon" />
                </el-icon>
                <template #title>
                  {{ node.title }}
                </template>
              </el-menu-item>
            </template>
          </el-menu-item-group>
        </el-menu>
      </nav>
    </el-aside>

    <el-container class="body">
      <el-header
        class="header"
        height="56px"
      >
        <el-button
          class="collapse-btn"
          text
          :aria-label="navToggleLabel"
          aria-controls="main-nav"
          :aria-expanded="isMobile ? drawerOpen : undefined"
          :title="navToggleLabel"
          @click="toggleNav"
        >
          <el-icon
            :size="18"
            aria-hidden="true"
          >
            <Menu v-if="isMobile" />
            <Fold v-else-if="!collapsed" />
            <Expand v-else />
          </el-icon>
        </el-button>
        <el-breadcrumb
          separator="/"
          class="crumb"
        >
          <el-breadcrumb-item>{{ auth.tenant?.name ?? '未开通空间' }}</el-breadcrumb-item>
          <el-breadcrumb-item v-if="crumb.group">{{ crumb.group }}</el-breadcrumb-item>
          <el-breadcrumb-item v-if="crumb.parent">{{ crumb.parent }}</el-breadcrumb-item>
          <el-breadcrumb-item>{{ crumb.current }}</el-breadcrumb-item>
        </el-breadcrumb>
        <div class="spacer" />
        <el-dropdown
          trigger="click"
          popper-class="user-menu-popper"
        >
          <button
            type="button"
            class="user-pill"
            aria-label="账号菜单"
          >
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
            <el-icon
              class="user-caret"
              aria-hidden="true"
            >
              <ArrowDown />
            </el-icon>
          </button>
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

      <el-main
        id="main-content"
        class="main"
        tabindex="-1"
      >
        <div class="content">
          <!-- 尚无使用空间的账号：业务页都需要空间上下文。
               平台管理员仅在概览展示全局总览；其余路由给明确指引，避免 URL 与内容不一致。 -->
          <GlobalOverview
            v-if="showNoTenantNotice && isPlatformAdmin && isOverviewRoute"
          />
          <div
            v-else-if="showNoTenantNotice"
            class="no-tenant"
          >
            <el-empty>
              <template #description>
                <p v-if="isPlatformAdmin">
                  平台管理员账号未绑定使用空间，业务页面需要空间上下文。
                </p>
                <p v-else>当前账号还没有开通使用空间，请联系管理员为你开通。</p>
              </template>
              <el-button
                v-if="isPlatformAdmin"
                type="primary"
                @click="router.push('/')"
              >
                返回全局总览
              </el-button>
              <el-button
                v-else
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
  import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
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
    Menu,
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

  /** 菜单节点：带 children 的为二级菜单（不可直接导航） */
  interface MenuNode {
    key: string
    title: string
    icon: Component
    path?: string
    roles?: string[]
    children?: MenuNode[]
  }
  interface MenuGroup {
    label: string
    items: MenuNode[]
  }

  interface MenuMatch {
    group: MenuGroup
    parent: MenuNode | null
    item: MenuNode
  }

  interface MenuExposed {
    open: (index: string) => void
  }

  const ADMIN_ROLES = ['tenant_admin', 'platform_admin']
  const REVIEW_ROLES = ['tenant_admin', 'platform_admin', 'expert']

  /**
   * 设计系统 v2 §5：四大分组、9 个一级入口。
   * 低频管理项收进二级菜单，避免侧栏 17 项平铺带来的认知负担。
   */
  const MENU_GROUPS: MenuGroup[] = [
    {
      label: '工作台',
      items: [
        { key: 'dashboard', path: '/', title: '概览', icon: markRaw(Odometer) },
        { key: 'chat', path: '/chat', title: '对话', icon: markRaw(ChatDotRound) },
        { key: 'qa', path: '/qa', title: '问答', icon: markRaw(Reading) },
      ],
    },
    {
      label: '知识数据',
      items: [
        { key: 'knowledge', path: '/knowledge', title: '知识库', icon: markRaw(Files) },
        { key: 'tasks', path: '/tasks', title: '任务中心', icon: markRaw(List) },
        {
          key: 'data',
          title: '设备与政策',
          icon: markRaw(Connection),
          children: [
            { key: 'devices', path: '/devices', title: '设备接入', icon: markRaw(Connection) },
            { key: 'policies', path: '/policies', title: '政策服务', icon: markRaw(Document) },
          ],
        },
      ],
    },
    {
      label: '能力协作',
      items: [
        {
          key: 'capability',
          title: '能力中心',
          icon: markRaw(Cpu),
          children: [
            { key: 'agents', path: '/agents', title: '专业助手', icon: markRaw(Cpu) },
            { key: 'teams', path: '/teams', title: '协作班组', icon: markRaw(UserFilled) },
            { key: 'market', path: '/market', title: 'Agent 市场', icon: markRaw(Shop), roles: ADMIN_ROLES },
            { key: 'factory', path: '/agents/factory', title: '智能工厂', icon: markRaw(Operation), roles: ADMIN_ROLES },
          ],
        },
      ],
    },
    {
      label: '管理',
      items: [
        { key: 'review', path: '/review', title: '专家审核', icon: markRaw(Checked), roles: REVIEW_ROLES },
        {
          key: 'admin',
          title: '空间管理',
          icon: markRaw(Setting),
          roles: ADMIN_ROLES,
          children: [
            { key: 'members', path: '/members', title: '成员管理', icon: markRaw(User) },
            { key: 'quota', path: '/quota', title: '用量额度', icon: markRaw(DataLine) },
            { key: 'settings', path: '/settings', title: '系统设置', icon: markRaw(Setting) },
            { key: 'bots', path: '/bots', title: 'IM 机器人', icon: markRaw(ChatDotRound) },
            { key: 'evolution', path: '/evolution', title: '自生长管理', icon: markRaw(TrendCharts) },
          ],
        },
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
  const menuRef = ref<MenuExposed>()

  /** 递归过滤：无权限的叶子与空掉的父节点都不渲染 */
  function filterNode(node: MenuNode): MenuNode | null {
    if (!auth.hasAnyRole(node.roles)) return null
    if (!node.children?.length) return node.path ? node : null
    const children = node.children
      .map(filterNode)
      .filter((n): n is MenuNode => n !== null)
    return children.length ? { ...node, children } : null
  }

  /** 按当前角色过滤菜单（权限逻辑与原实现完全一致） */
  const visibleGroups = computed<MenuGroup[]>(() =>
    MENU_GROUPS.map(g => ({
      label: g.label,
      items: g.items.map(filterNode).filter((n): n is MenuNode => n !== null),
    })).filter(g => g.items.length > 0),
  )

  /** 当前路由对应的菜单项（最长前缀匹配），驱动激活态、展开态与面包屑 */
  const matchEntry = computed<MenuMatch | null>(() => {
    let best: MenuMatch | null = null
    for (const g of visibleGroups.value) {
      for (const node of g.items) {
        const leaves = node.children?.length ? node.children : [node]
        for (const leaf of leaves) {
          if (!leaf.path) continue
          const hit =
            route.path === leaf.path ||
            (leaf.path !== '/' && route.path.startsWith(`${leaf.path}/`))
          if (hit && (!best || leaf.path.length > best.item.path!.length)) {
            best = { group: g, parent: node.children?.length ? node : null, item: leaf }
          }
        }
      }
    }
    return best
  })

  const activePath = computed(() => matchEntry.value?.item.path ?? route.path)

  const openedSubmenus = computed(() => {
    const parent = matchEntry.value?.parent
    return parent ? [parent.key] : []
  })

  const crumb = computed(() => {
    const m = matchEntry.value
    if (m) {
      return { group: m.group.label, parent: m.parent?.title ?? '', current: m.item.title }
    }
    return {
      group: '',
      parent: '',
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

  /** 窄屏（≤768px）侧栏改为抽屉；平板（≤900px）收成图标条 */
  const collapsed = ref(false)
  const isMobile = ref(false)
  const drawerOpen = ref(false)
  const mqlMobile = window.matchMedia('(max-width: 768px)')
  const mqlCompact = window.matchMedia('(max-width: 900px)')

  function syncViewport() {
    isMobile.value = mqlMobile.matches
    collapsed.value = !isMobile.value && mqlCompact.matches
    if (!isMobile.value) drawerOpen.value = false
  }

  const menuCollapse = computed(() => collapsed.value && !isMobile.value)
  const isMini = computed(() => menuCollapse.value)
  const showLogoWord = computed(() => !isMini.value)
  const asideWidth = computed(() =>
    isMobile.value ? '232px' : collapsed.value ? '64px' : '232px',
  )
  const navToggleLabel = computed(() => {
    if (isMobile.value) return drawerOpen.value ? '关闭导航菜单' : '打开导航菜单'
    return collapsed.value ? '展开侧栏' : '收起侧栏'
  })

  function toggleNav() {
    if (isMobile.value) drawerOpen.value = !drawerOpen.value
    else collapsed.value = !collapsed.value
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape' && drawerOpen.value) drawerOpen.value = false
  }

  function openActiveSubmenu() {
    const key = matchEntry.value?.parent?.key
    if (key) menuRef.value?.open(key)
  }

  mqlMobile.addEventListener('change', syncViewport)
  mqlCompact.addEventListener('change', syncViewport)
  window.addEventListener('keydown', onKeydown)

  watch(drawerOpen, open => {
    document.body.style.overflow = open ? 'hidden' : ''
  })

  watch(
    () => route.path,
    () => {
      drawerOpen.value = false
      nextTick(openActiveSubmenu)
    },
  )

  watch(
    () => matchEntry.value?.parent?.key,
    key => {
      if (key) nextTick(openActiveSubmenu)
    },
  )

  /** 已登录但确实没有使用空间的账号：不渲染业务页，给明确指引 */
  const showNoTenantNotice = computed(
    () => auth.meLoaded && auth.isLoggedIn() && auth.tenants.length === 0,
  )
  const isOverviewRoute = computed(() => route.name === 'Dashboard')

  onMounted(async () => {
    syncViewport()
    nextTick(openActiveSubmenu)
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
    nextTick(openActiveSubmenu)
  })

  onBeforeUnmount(() => {
    mqlMobile.removeEventListener('change', syncViewport)
    mqlCompact.removeEventListener('change', syncViewport)
    window.removeEventListener('keydown', onKeydown)
    document.body.style.overflow = ''
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

  /* ---------- 键盘可达性：跳过导航 ---------- */
  .skip-link {
    position: absolute;
    left: 12px;
    top: -56px;
    z-index: 2000;
    padding: 8px 14px;
    border: 1px solid var(--ds-accent);
    border-radius: var(--ds-radius-control);
    background: var(--ds-bg-2);
    color: var(--ds-text-1);
    font-size: 13px;
    text-decoration: none;
    transition: top 0.15s ease;
  }
  .skip-link:focus {
    top: 12px;
  }

  /* ---------- 侧栏：232px 玻璃 / 折叠 64px / 移动端抽屉 ---------- */
  .aside {
    display: flex;
    flex-direction: column;
    background: rgba(255, 255, 255, 0.025);
    backdrop-filter: blur(18px) saturate(1.2);
    -webkit-backdrop-filter: blur(18px) saturate(1.2);
    border-right: 1px solid var(--ds-border);
    transition: width 0.2s, transform 0.2s;
    overflow: hidden;
  }
  .aside--drawer {
    position: fixed;
    inset: 0 auto 0 0;
    z-index: 1001;
    height: 100%;
    background: rgba(17, 21, 31, 0.98);
    transform: translateX(-100%);
    box-shadow: var(--ds-shadow);
  }
  .aside--drawer.aside--open {
    transform: translateX(0);
  }
  .drawer-backdrop {
    position: fixed;
    inset: 0;
    z-index: 1000;
    background: rgba(5, 7, 10, 0.62);
    backdrop-filter: blur(2px);
    -webkit-backdrop-filter: blur(2px);
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
  .menu {
    --el-menu-hover-bg-color: var(--ds-glass);
    --el-menu-item-height: 38px;
    --el-menu-sub-item-height: 34px;
    background: transparent;
    border-right: none;
  }
  .menu:not(.el-menu--collapse) {
    padding: 0 8px;
  }
  .menu :deep(.el-menu-item-group__title) {
    padding: 14px 10px 6px;
    font-size: 12px;
    letter-spacing: 0.08em;
    color: var(--ds-text-3);
    user-select: none;
  }
  .menu.el-menu--collapse :deep(.el-menu-item-group__title) {
    display: none;
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
  .menu :deep(.el-sub-menu__title) {
    margin: 2px 0;
    border-radius: var(--ds-radius-control);
    color: var(--ds-text-2);
  }
  .menu :deep(.el-sub-menu__title:hover) {
    color: var(--ds-text-1);
    background: var(--ds-glass);
  }
  .menu :deep(.el-sub-menu.is-active > .el-sub-menu__title) {
    color: var(--ds-text-1);
  }
  .menu :deep(.el-sub-menu.is-active > .el-sub-menu__title .el-icon) {
    color: var(--ds-accent);
  }
  .menu :deep(.el-sub-menu .el-menu-item) {
    min-width: 0;
    padding-left: 44px;
  }
  .menu :deep(.el-menu-item:focus-visible),
  .menu :deep(.el-sub-menu__title:focus-visible) {
    outline: 2px solid var(--ds-accent-text);
    outline-offset: -2px;
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
    font: inherit;
    appearance: none;
    cursor: pointer;
    outline: none;
    transition: border-color 0.2s, background 0.2s;
  }
  .user-pill:hover {
    border-color: var(--ds-border-strong);
    background: var(--ds-glass-strong);
  }
  .user-pill:focus-visible {
    outline: 2px solid var(--ds-accent-text);
    outline-offset: 2px;
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
    outline: none;
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

  @media (prefers-reduced-motion: reduce) {
    .aside,
    .skip-link {
      transition: none;
    }
  }
</style>
