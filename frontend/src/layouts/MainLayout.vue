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
        <div class="logo-block">
          阗
        </div>
        <div
          v-show="showLogoWord"
          class="logo-word"
        >
          <span class="logo-name">阗策</span>
          <span class="logo-sub">科技小院</span>
        </div>
        <!-- 每次升级都要看得见版本：右侧标出，取的是 package.json 那一份，不另抄 -->
        <span
          v-show="showLogoWord"
          class="logo-ver"
          :title="`当前版本 ${appVersion}`"
        >{{ appVersion }}</span>
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
            :key="g.id"
            :title="g.label"
          >
            <template
              v-for="(node, nIdx) in g.items"
              :key="node.key"
            >
              <el-sub-menu
                v-if="node.children?.length"
                :index="node.key"
              >
                <template #title>
                  <el-icon aria-hidden="true">
                    <component :is="resolveIcon(node.iconName)" />
                  </el-icon>
                  <span>{{ node.title }}</span>
                </template>
                <el-menu-item
                  v-for="(child, cIdx) in node.children"
                  :key="child.path"
                  :index="child.path ?? child.key"
                  class="nav-item"
                  :aria-current="activePath === child.path ? 'page' : undefined"
                  :draggable="menuStore.sidebarEditMode"
                  @dragstart.stop="onSideDragStartChild(g.id, node.key, cIdx)"
                  @dragover.prevent
                  @drop.stop="onSideDropChild(g.id, node.key, cIdx)"
                >
                  <el-icon aria-hidden="true">
                    <component :is="resolveIcon(child.iconName)" />
                  </el-icon>
                  <template #title>
                    <div class="nav-item-row">
                      <span class="nav-item-text">{{ child.title }}</span>
                      <span
                        v-if="menuStore.sidebarEditMode && !isMini"
                        class="nav-quick-diy"
                        title="提至一级直达"
                        @click.stop.prevent="menuStore.promoteToTopLevel(g.id, node.key, child.key)"
                      >↑一级</span>
                    </div>
                  </template>
                </el-menu-item>
              </el-sub-menu>
              <el-menu-item
                v-else
                :index="node.path ?? node.key"
                class="nav-item"
                :aria-current="activePath === node.path ? 'page' : undefined"
                :draggable="menuStore.sidebarEditMode"
                @dragstart.stop="onSideDragStartNode(g.id, nIdx)"
                @dragover.prevent
                @drop.stop="onSideDropNode(g.id, nIdx)"
              >
                <el-icon aria-hidden="true">
                  <component :is="resolveIcon(node.iconName)" />
                </el-icon>
                <template #title>
                  <div class="nav-item-row">
                    <span class="nav-item-text">{{ node.title }}</span>
                    <span
                      v-if="menuStore.sidebarEditMode && !isMini"
                      class="nav-quick-diy"
                      title="收纳进二级菜单"
                      @click.stop.prevent="menuStore.demoteToSubmenu(g.id, node.key)"
                    >↓二级</span>
                  </div>
                </template>
              </el-menu-item>
            </template>
          </el-menu-item-group>
        </el-menu>
      </nav>

      <!-- 侧栏底部：视图调序与工作台视图配置入口 -->
      <div
        v-if="!isMini"
        class="aside-footer"
      >
        <el-button
          size="small"
          text
          class="diy-foot-btn"
          @click="menuStore.sidebarEditMode = !menuStore.sidebarEditMode"
        >
          <el-icon><Operation /></el-icon>
          <span>{{ menuStore.sidebarEditMode ? '完成原地调整' : '原地拖拽调序' }}</span>
        </el-button>
        <el-button
          size="small"
          text
          type="primary"
          class="diy-foot-btn"
          @click="diyDialogVisible = true"
        >
          工作台视图
        </el-button>
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
          <el-breadcrumb-item>{{ auth.yard?.name ?? '未开通小院' }}</el-breadcrumb-item>
          <el-breadcrumb-item v-if="crumb.group">
            {{ crumb.group }}
          </el-breadcrumb-item>
          <el-breadcrumb-item v-if="crumb.parent">
            {{ crumb.parent }}
          </el-breadcrumb-item>
          <el-breadcrumb-item>{{ crumb.current }}</el-breadcrumb-item>
        </el-breadcrumb>
        <div class="spacer" />

        <!-- 全局顶栏 Token 实时统计胶囊（点击跳转用量看板） -->
        <el-tooltip
          v-if="!showNoYardNotice"
          :content="`今日输入 ${tokenOverview.today_input_tokens} / 输出 ${tokenOverview.today_output_tokens} tokens · 近30天累计 ${tokenOverview.total_tokens} tokens（点击查看明细）`"
          placement="bottom"
        >
          <button
            type="button"
            class="token-pill"
            aria-label="查看 Token 消耗统计"
            @click="router.push('/quota')"
          >
            <span class="tp-dot" />
            <span class="tp-label">Token：</span>
            <strong class="tp-val">今日 {{ formatCompact(tokenOverview.today_tokens) }}</strong>
            <span class="tp-sep">·</span>
            <span class="tp-total">累计 {{ formatCompact(tokenOverview.total_tokens) }}</span>
          </button>
        </el-tooltip>

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
                :icon="User"
                @click="router.push('/profile')"
              >
                个人资料
              </el-dropdown-item>
              <el-dropdown-item
                :icon="Operation"
                @click="diyDialogVisible = true"
              >
                工作台视图
              </el-dropdown-item>
              <el-dropdown-item
                :icon="SwitchButton"
                divided
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
          <GlobalOverview
            v-if="showNoYardNotice && isPlatformAdmin && isOverviewRoute"
          />
          <div
            v-else-if="showNoYardNotice"
            class="no-yard"
          >
            <el-empty>
              <template #description>
                <p v-if="isPlatformAdmin">
                  平台管理员账号未绑定小院，业务页面需要小院上下文。
                </p>
                <p v-else>
                  当前账号还没有开通小院，请联系管理员为你开通。
                </p>
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
          <router-view
            v-else
            v-slot="{ Component }"
          >
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

    <!-- 工作台视图：全员可自由拖拽编排左侧导航 -->
    <el-dialog
      v-model="diyDialogVisible"
      title="工作台视图（自由拖拽编排）"
      width="920px"
      top="6vh"
      append-to-body
    >
      <MenuDiyEditor />
      <template #footer>
        <el-button
          type="primary"
          @click="diyDialogVisible = false"
        >
          完成并应用
        </el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup lang="ts">
  import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
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
    Grid,
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
  import { useMenuStore, type DiyMenuGroup, type DiyMenuNode } from '@/stores/menu'
  import { llmTraceApi } from '@/api/modules'
  import GlobalOverview from '@/views/GlobalOverview.vue'
  import MenuDiyEditor from '@/components/layout/MenuDiyEditor.vue'
  // 版本号只有 package.json 这一份事实来源：九处版本面已由 scripts/check_versions.py 对齐，
  // 这里再抄一个字符串就迟早会对不上（具名导入，打包时只带出 version 一个字段）。
  import { version as appVersionRaw } from '../../package.json'

  interface MenuMatch {
    group: DiyMenuGroup
    parent: DiyMenuNode | null
    item: DiyMenuNode
  }

  interface MenuExposed {
    open: (index: string) => void
  }

  const ICON_MAP: Record<string, Component> = {
    Odometer: markRaw(Odometer),
    ChatDotRound: markRaw(ChatDotRound),
    Reading: markRaw(Reading),
    Files: markRaw(Files),
    Grid: markRaw(Grid),
    List: markRaw(List),
    Connection: markRaw(Connection),
    Document: markRaw(Document),
    Cpu: markRaw(Cpu),
    UserFilled: markRaw(UserFilled),
    Shop: markRaw(Shop),
    Operation: markRaw(Operation),
    Checked: markRaw(Checked),
    Setting: markRaw(Setting),
    User: markRaw(User),
    DataLine: markRaw(DataLine),
    TrendCharts: markRaw(TrendCharts),
    Menu: markRaw(Menu),
  }

  function resolveIcon(name?: string): Component {
    return (name && ICON_MAP[name]) || ICON_MAP.Menu
  }

  const ROLE_LABELS: Record<string, string> = {
    platform_admin: '平台管理员',
    yard_owner: '小院负责人',
    yard_admin: '小院管理员',
    expert: '专家',
    operator: '科研操作员',
    viewer: '普通成员',
  }

  const route = useRoute()
  const router = useRouter()
  const auth = useAuthStore()
  const menuStore = useMenuStore()
  const menuRef = ref<MenuExposed>()
  const diyDialogVisible = ref(false)

  /* ---------- 侧栏原地拖拽调序 ---------- */
  const sideDrag = ref<{
    kind: 'node' | 'child'
    groupId: string
    folderKey?: string
    index: number
  } | null>(null)

  function onSideDragStartNode(groupId: string, index: number) {
    sideDrag.value = { kind: 'node', groupId, index }
  }
  function onSideDropNode(groupId: string, toIdx: number) {
    if (!sideDrag.value || sideDrag.value.kind !== 'node' || sideDrag.value.groupId !== groupId)
      return
    menuStore.moveItemInGroup(groupId, sideDrag.value.index, toIdx)
    sideDrag.value = null
  }
  function onSideDragStartChild(groupId: string, folderKey: string, index: number) {
    sideDrag.value = { kind: 'child', groupId, folderKey, index }
  }
  function onSideDropChild(groupId: string, folderKey: string, toIdx: number) {
    if (
      !sideDrag.value ||
      sideDrag.value.kind !== 'child' ||
      sideDrag.value.groupId !== groupId ||
      sideDrag.value.folderKey !== folderKey
    )
      return
    menuStore.moveChildInFolder(groupId, folderKey, sideDrag.value.index, toIdx)
    sideDrag.value = null
  }

  /* ---------- 顶栏实时 Token 统计 ---------- */
  const tokenOverview = reactive({
    today_tokens: 0,
    today_input_tokens: 0,
    today_output_tokens: 0,
    total_tokens: 0,
  })

  function formatCompact(n: number): string {
    if (!n || n <= 0) return '0'
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
    if (n >= 10_000) return `${(n / 1_000).toFixed(1)}k`
    return n.toLocaleString()
  }

  async function refreshTokenOverview() {
    if (!auth.isLoggedIn() || !auth.yards.length) return
    try {
      const ov = await llmTraceApi.overview(30)
      tokenOverview.today_tokens = ov.today_tokens ?? 0
      tokenOverview.today_input_tokens = ov.today_input_tokens ?? 0
      tokenOverview.today_output_tokens = ov.today_output_tokens ?? 0
      tokenOverview.total_tokens = ov.total_tokens ?? 0
    } catch {
      /* 静默容错 */
    }
  }

  /** 递归过滤：角色不符或缺少权限点的叶子、以及空掉的父节点都不渲染 */
  function filterNode(node: DiyMenuNode): DiyMenuNode | null {
    if (!auth.hasAnyRole(node.roles) || !auth.hasPermission(node.permission)) return null
    if (!node.children?.length) return node.path ? node : null
    const children = node.children.filter(
      child => auth.hasAnyRole(child.roles) && auth.hasPermission(child.permission),
    )
    return children.length ? { ...node, children } : null
  }

  /** 按当前角色过滤菜单（支持用户自由 DIY 后的菜单树） */
  const visibleGroups = computed<DiyMenuGroup[]>(() =>
    menuStore.groups
      .map(g => ({
        id: g.id,
        label: g.label,
        items: g.items.map(filterNode).filter((n): n is DiyMenuNode => n !== null),
      }))
      .filter(g => g.items.length > 0),
  )

  /** 当前路由对应的菜单项（最长前缀匹配），驱动激活态、展开态与面包屑 */
  const matchEntry = computed<MenuMatch | null>(() => {
    let best: MenuMatch | null = null
    for (const g of visibleGroups.value) {
      for (const node of g.items) {
        const leaves: DiyMenuNode[] = node.children?.length ? node.children : [node]
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

  const isPlatformAdmin = computed(() => auth.roles.includes('platform_admin'))

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

  /** 侧栏右上角显示的版本，随每次升级自动跟着 package.json 走 */
  const appVersion = `v${appVersionRaw}`
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
      void refreshTokenOverview()
    },
  )

  watch(
    () => matchEntry.value?.parent?.key,
    key => {
      if (key) nextTick(openActiveSubmenu)
    },
  )

  const showNoYardNotice = computed(
    () => auth.meLoaded && auth.isLoggedIn() && auth.yards.length === 0,
  )
  const isOverviewRoute = computed(() => route.name === 'Dashboard')

  onMounted(async () => {
    syncViewport()
    nextTick(openActiveSubmenu)
    if (!auth.user || !auth.meLoaded || auth.roles.length === 0) {
      try {
        await auth.fetchMe()
      } catch {
        /* 401 由 client 拦截器处理跳转 */
      }
    }
    if (!auth.yard) {
      try {
        await auth.fetchYard()
      } catch {
        /* 无权限时顶栏显示占位即可 */
      }
    }
    // 等权限与角色就绪后再加载工作台视图，避免先渲染出无权限入口
    await menuStore.loadFromServer()
    void refreshTokenOverview()
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
  /* 版本号贴在侧栏右上角：小胶囊，不跟品牌名抢视觉 */
  .logo-ver {
    margin-left: auto;
    align-self: flex-start;
    margin-top: 6px;
    padding: 1px 6px;
    border: 1px solid var(--ds-border);
    border-radius: var(--ds-radius-pill);
    font-size: 10.5px;
    font-variant-numeric: tabular-nums;
    letter-spacing: 0.02em;
    color: var(--ds-text-3);
    background: var(--ds-field);
    white-space: nowrap;
  }

  .nav {
    flex: 1;
    overflow-y: auto;
    overflow-x: hidden;
    padding: 8px 0 12px;
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
  .nav-item-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    width: 100%;
    gap: 6px;
  }
  .nav-item-text {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .nav-quick-diy {
    font-size: 11px;
    padding: 1px 5px;
    border-radius: 4px;
    background: var(--ds-glass-strong);
    border: 1px solid var(--ds-border-strong);
    color: var(--ds-accent-text);
    line-height: 1.4;
    flex: none;
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

  .aside-footer {
    flex: none;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 10px;
    border-top: 1px solid var(--ds-border);
    background: rgba(255, 255, 255, 0.015);
  }
  .diy-foot-btn {
    font-size: 12px;
    padding: 4px 6px;
  }

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

  .token-pill {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: var(--ds-radius-pill);
    border: 1px solid color-mix(in srgb, var(--ds-accent) 40%, var(--ds-border));
    background: var(--ds-glass);
    color: var(--ds-text-2);
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s;
  }
  .token-pill:hover {
    border-color: var(--ds-accent);
    background: var(--ds-glass-strong);
    color: var(--ds-text-1);
  }
  .tp-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--ds-accent);
  }
  .tp-val {
    color: var(--ds-text-1);
    font-weight: 600;
  }
  .tp-sep,
  .tp-total {
    color: var(--ds-text-3);
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
  .no-yard {
    display: flex;
    justify-content: center;
    padding-top: 100px;
  }
  .no-yard p {
    margin: 4px 0;
    color: var(--ds-text-2);
  }

  @media (max-width: 900px) {
    .content {
      padding: 12px;
    }
    .user-name,
    .role-badge,
    .tp-total,
    .tp-sep {
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
