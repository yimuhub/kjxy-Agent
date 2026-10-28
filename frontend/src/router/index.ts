import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import MainLayout from '@/layouts/MainLayout.vue'
import Login from '@/views/Login.vue'

/** 管理类页面：仅小院管理员与平台管理员 */
const ADMIN_ROLES = ['yard_owner', 'yard_admin', 'platform_admin']

/** 业务页按路由懒加载，首屏只下载登录页与主框架 */
const Dashboard = () => import('@/views/Dashboard.vue')
const ChatView = () => import('@/views/ChatView.vue')
const QaView = () => import('@/views/QaView.vue')
const KnowledgeView = () => import('@/views/KnowledgeView.vue')
const TasksView = () => import('@/views/TasksView.vue')
const ReviewView = () => import('@/views/ReviewView.vue')
const MembersView = () => import('@/views/MembersView.vue')
const SettingsView = () => import('@/views/SettingsView.vue')
const AgentsView = () => import('@/views/AgentsView.vue')
const CustomAgentsView = () => import('@/views/CustomAgentsView.vue')
const TeamsView = () => import('@/views/TeamsView.vue')
const FarmView = () => import('@/views/FarmView.vue')
const DiagnosisView = () => import('@/views/DiagnosisView.vue')
const WorkOrderView = () => import('@/views/WorkOrderView.vue')
const AgentFactoryView = () => import('@/views/AgentFactoryView.vue')
const DevicesView = () => import('@/views/DevicesView.vue')
const MarketView = () => import('@/views/MarketView.vue')
const BotsView = () => import('@/views/BotsView.vue')
const PolicyView = () => import('@/views/PolicyView.vue')
const EvolutionView = () => import('@/views/EvolutionView.vue')
const QuotaView = () => import('@/views/QuotaView.vue')
const SignupView = () => import('@/views/SignupView.vue')
const InviteAcceptView = () => import('@/views/InviteAcceptView.vue')
const LibraryView = () => import('@/views/LibraryView.vue')
const OwnerView = () => import('@/views/OwnerView.vue')
const ProfileView = () => import('@/views/ProfileView.vue')
const NotFoundView = () => import('@/views/NotFoundView.vue')

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
    meta: { public: true, title: '小院注册' },
  },
  {
    path: '/invite/:token',
    name: 'InviteAccept',
    component: InviteAcceptView,
    meta: { public: true, title: '接受邀请' },
  },
  {
    path: '/',
    component: MainLayout,
    children: [
      { path: '', name: 'Dashboard', component: Dashboard, meta: { title: '概览' } },
      // 对话模式：全员可用（§10 契约：全部登录可用，成员级）
      { path: 'chat', name: 'Chat', component: ChatView, meta: { title: '对话' } },
      { path: 'qa', name: 'QA', component: QaView, meta: { title: '知识问答' } },
      { path: 'knowledge', name: 'Knowledge', component: KnowledgeView, meta: { title: '知识库' } },
      { path: 'farm', name: 'Farm', component: FarmView, meta: { title: '农业台账' } },
      { path: 'diagnosis', name: 'Diagnosis', component: DiagnosisView, meta: { title: '作物诊断' } },
      { path: 'work-orders', name: 'WorkOrders', component: WorkOrderView, meta: { title: '告警与工单' } },
      { path: 'tasks', name: 'Tasks', component: TasksView, meta: { title: '任务中心' } },
      {
        path: 'review',
        name: 'Review',
        component: ReviewView,
        meta: { title: '专家审核', roles: ['yard_owner', 'yard_admin', 'platform_admin', 'expert'] },
      },
      // 能力中心 - 我的智能体（用户自建 Agent 独立板块：卡片/列表双视图 + 单聊/多Agent群聊 + 多模型与1对N多技能绑定）
      {
        path: 'agents/custom',
        name: 'CustomAgents',
        component: CustomAgentsView,
        meta: { title: '我的智能体' },
      },
      // 专业助手全员可见
      { path: 'agents', name: 'Agents', component: AgentsView, meta: { title: '专业助手' } },
      // 协作班组全员可跑，管理类按钮页内按角色收敛
      { path: 'teams', name: 'Teams', component: TeamsView, meta: { title: '协作班组' } },
      {
        path: 'agents/factory',
        name: 'AgentFactory',
        component: AgentFactoryView,
        meta: { title: 'Agent 工厂', roles: ['yard_owner', 'yard_admin', 'platform_admin'] },
      },
      {
        path: 'market',
        name: 'Market',
        component: MarketView,
        meta: { title: 'Agent 市场', roles: ADMIN_ROLES },
      },
      {
        path: 'library',
        name: 'Library',
        component: LibraryView,
        meta: {
          title: '插件与技能库',
          roles: ['yard_owner', 'yard_admin', 'platform_admin', 'expert'],
        },
      },
      { path: 'devices', name: 'Devices', component: DevicesView, meta: { title: '设备中心' } },
      { path: 'policies', name: 'Policies', component: PolicyView, meta: { title: '政策服务' } },
      {
        path: 'evolution',
        name: 'Evolution',
        component: EvolutionView,
        meta: { title: '技能与插件', roles: ADMIN_ROLES },
      },
      {
        path: 'quota',
        name: 'Quota',
        component: QuotaView,
        meta: { title: '用量额度' },
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
      {
        path: 'owner',
        name: 'Owner',
        component: OwnerView,
        meta: { title: '负责人驾驶舱', roles: ['yard_owner', 'platform_admin'] },
      },
      {
        path: 'profile',
        name: 'Profile',
        component: ProfileView,
        meta: { title: '个人资料' },
      },
      {
        path: 'bots',
        name: 'Bots',
        component: BotsView,
        meta: { title: 'IM 机器人', roles: ADMIN_ROLES },
      },
      // 兜底：未知路径显示站内 404，避免白屏
      { path: ':pathMatch(.*)*', name: 'NotFound', component: NotFoundView, meta: { title: '页面不存在' } },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior: (_to, _from, saved) => saved ?? { top: 0 },
})

const APP_NAME = '阗策 · 科技小院'

router.afterEach(to => {
  const title = to.meta.title as string | undefined
  document.title = title ? `${title} · ${APP_NAME}` : APP_NAME
})

router.beforeEach(async to => {
  const auth = useAuthStore()
  if (to.meta.public) {
    if ((to.path === '/login' || to.path === '/signup') && auth.isLoggedIn()) {
      const redirect = typeof to.query.redirect === 'string' ? to.query.redirect : '/'
      return redirect.startsWith('/') ? redirect : '/'
    }
    return true
  }
  if (!auth.isLoggedIn()) {
    const query = to.fullPath !== '/' ? { redirect: to.fullPath } : undefined
    return { path: '/login', query }
  }
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
