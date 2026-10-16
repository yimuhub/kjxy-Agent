import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import MainLayout from '@/layouts/MainLayout.vue'
import Login from '@/views/Login.vue'
import Dashboard from '@/views/Dashboard.vue'
import QaView from '@/views/QaView.vue'
import KnowledgeView from '@/views/KnowledgeView.vue'
import TasksView from '@/views/TasksView.vue'
import ReviewView from '@/views/ReviewView.vue'
import MembersView from '@/views/MembersView.vue'
import SettingsView from '@/views/SettingsView.vue'
import AgentsView from '@/views/AgentsView.vue'
import PolicyView from '@/views/PolicyView.vue'
import EvolutionView from '@/views/EvolutionView.vue'
import QuotaView from '@/views/QuotaView.vue'
import SignupView from '@/views/SignupView.vue'

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
    meta: { public: true, title: '租户注册' },
  },
  {
    path: '/',

    component: MainLayout,
    children: [
      { path: '', name: 'Dashboard', component: Dashboard, meta: { title: '工作台' } },
      { path: 'qa', name: 'QA', component: QaView, meta: { title: '知识问答' } },
      { path: 'knowledge', name: 'Knowledge', component: KnowledgeView, meta: { title: '知识库' } },
      { path: 'tasks', name: 'Tasks', component: TasksView, meta: { title: '任务中心' } },
      { path: 'review', name: 'Review', component: ReviewView, meta: { title: '专家审核' } },
      { path: 'agents', name: 'Agents', component: AgentsView, meta: { title: '专业智能体' } },
      { path: 'policies', name: 'Policies', component: PolicyView, meta: { title: '政策服务' } },
      { path: 'evolution', name: 'Evolution', component: EvolutionView, meta: { title: '自生长管理' } },
      { path: 'quota', name: 'Quota', component: QuotaView, meta: { title: '配额用量' } },
      { path: 'members', name: 'Members', component: MembersView, meta: { title: '成员管理' } },
      { path: 'settings', name: 'Settings', component: SettingsView, meta: { title: '系统设置' } },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.beforeEach((to, _from, next) => {
  const auth = useAuthStore()
  if (to.path === '/login' || to.path === '/signup') {
    if (auth.isLoggedIn()) return next('/')
    return next()
  }
  if (!auth.isLoggedIn()) return next('/login')
  next()
})

export default router
