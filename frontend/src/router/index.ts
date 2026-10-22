import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import MainLayout from '@/layouts/MainLayout.vue'
import Login from '@/views/Login.vue'
import Dashboard from '@/views/Dashboard.vue'
import ChatView from '@/views/ChatView.vue'
import QaView from '@/views/QaView.vue'
import KnowledgeView from '@/views/KnowledgeView.vue'
import TasksView from '@/views/TasksView.vue'
import ReviewView from '@/views/ReviewView.vue'
import MembersView from '@/views/MembersView.vue'
import SettingsView from '@/views/SettingsView.vue'
import AgentsView from '@/views/AgentsView.vue'
import TeamsView from '@/views/TeamsView.vue'
import AgentFactoryView from '@/views/AgentFactoryView.vue'
import DevicesView from '@/views/DevicesView.vue'
import MarketView from '@/views/MarketView.vue'
import PolicyView from '@/views/PolicyView.vue'
import EvolutionView from '@/views/EvolutionView.vue'
import QuotaView from '@/views/QuotaView.vue'
import SignupView from '@/views/SignupView.vue'

/** 管理类页面：仅使用空间管理员与平台管理员 */
const ADMIN_ROLES = ['tenant_admin', 'platform_admin']

// meta.roles 声明允许访问的角色；缺省表示所有登录用户可见
const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: Login,
    meta: { public: true, title: '登录' },
  },
  {
    path: '/signup',
    name: 'Signup',
    component: SignupView,
    meta: { public: true, title: '使用空间注册' },
  },
  {
    path: '/',

    component: MainLayout,
    children: [
      { path: '', name: 'Dashboard', component: Dashboard, meta: { title: '工作台' } },
      // 对话模式：全员可用（§10 契约：全部登录可用，成员级）
      { path: 'chat', name: 'Chat', component: ChatView, meta: { title: '对话' } },
      { path: 'qa', name: 'QA', component: QaView, meta: { title: '知识问答' } },
      { path: 'knowledge', name: 'Knowledge', component: KnowledgeView, meta: { title: '知识库' } },
      { path: 'tasks', name: 'Tasks', component: TasksView, meta: { title: '任务中心' } },
      {
        path: 'review',
        name: 'Review',
        component: ReviewView,
        meta: { title: '专家审核', roles: ['tenant_admin', 'platform_admin', 'expert'] },
      },
      // 专业助手全员可见
      { path: 'agents', name: 'Agents', component: AgentsView, meta: { title: '专业助手' } },
      // 协作班组全员可跑，管理类按钮页内按角色收敛
      { path: 'teams', name: 'Teams', component: TeamsView, meta: { title: '协作班组' } },
      {
        path: 'agents/factory',
        name: 'AgentFactory',
        component: AgentFactoryView,
        meta: { title: 'Agent 工厂', roles: ['tenant_admin', 'platform_admin'] },
      },
      {
        path: 'market',
        name: 'Market',
        component: MarketView,
        meta: { title: 'Agent 市场', roles: ADMIN_ROLES },
      },
      { path: 'devices', name: 'Devices', component: DevicesView, meta: { title: '设备中心' } },
      { path: 'policies', name: 'Policies', component: PolicyView, meta: { title: '政策服务' } },
      {
        path: 'evolution',
        name: 'Evolution',
        component: EvolutionView,
        meta: { title: '自生长管理', roles: ADMIN_ROLES },
      },
      {
        path: 'quota',
        name: 'Quota',
        component: QuotaView,
        meta: { title: '用量额度', roles: ADMIN_ROLES },
      },
      {
        path: 'members',
        name: 'Members',
        component: MembersView,
        meta: { title: '成员管理', roles: ADMIN_ROLES },
      },
      {
        path: 'settings',
        name: 'Settings',
        component: SettingsView,
        meta: { title: '系统设置', roles: ADMIN_ROLES },
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach(async to => {
  const auth = useAuthStore()
  if (to.path === '/login' || to.path === '/signup') {
    return auth.isLoggedIn() ? '/' : true
  }
  if (!auth.isLoggedIn()) return '/login'
  // 直达管理页 URL 时角色可能还没加载，先补一次 /auth/me
  if (!auth.meLoaded) {
    try {
      await auth.fetchMe()
    } catch {
      /* 401 由 client 拦截器统一处理 */
    }
  }
  const required = to.meta.roles as string[] | undefined
  if (required && !auth.hasAnyRole(required)) {
    ElMessage.warning('你没有权限访问该页面')
    return '/'
  }
  return true
})

export default router
