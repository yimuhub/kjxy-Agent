<template>
  <div class="setup-shell">
    <div class="setup-grid">
      <aside class="rail">
        <div class="brand">
          <div class="mark">
            小院
          </div>
          <div class="brand-txt">
            <strong>科技小院</strong>
            <span>首次安装 · v{{ version }}</span>
          </div>
        </div>
        <div class="progress">
          <div class="progress-top">
            <span>安装进度</span><span>{{ cur }} / 7</span>
          </div>
          <div class="progress-bar">
            <i :style="{ width: `${(cur / 7) * 100}%` }" />
          </div>
        </div>
        <ol class="steps">
          <li
            v-for="(s, i) in STEPS"
            :key="s.title"
            :class="['step', cur > i + 1 ? 'done' : cur === i + 1 ? 'current' : 'future']"
          >
            <span class="num">{{ cur > i + 1 ? '✓' : i + 1 }}</span>
            <span class="lab">{{ s.title }}<small>{{ s.sub }}</small></span>
          </li>
        </ol>
        <div class="rail-foot">
          <p>不预置任何账号或口令，管理员由你此刻创建；口令只以 bcrypt 哈希保存。</p>
          <p>数据只写入本机目录，服务仅监听 127.0.0.1。</p>
        </div>
      </aside>

      <section class="main">
        <div class="pane-wrap">
          <!-- 7 个步骤 pane 拆分为子组件（v3.82）：共享状态经 provide/inject 下发，
               子组件模板与拆分前逐字一致；v-show 由各子组件根节点自带 -->
          <SetupStepDeployment />
          <SetupStepAccount />
          <SetupStepYard />
          <SetupStepCapabilities />
          <SetupStepBranding />
          <SetupStepConfirm />
          <SetupStepDone />
        </div>

        <footer class="actionbar">
          <button
            v-if="cur > 1 && cur < 7"
            type="button"
            class="btn ghost"
            @click="prev"
          >
            上一步
          </button>
          <span class="grow" />
          <button
            v-if="cur < 7"
            :id="cur === 6 ? 'setup-create' : 'setup-next'"
            type="button"
            class="btn primary"
            :disabled="submitting || (cur === 6 && !savedPwdAck)"
            @click="next"
          >
            {{ cur === 6 ? (submitting ? '正在创建…' : '创建我的小院') : '继续' }}
          </button>
          <template v-else>
            <!-- 首问闭环（审查 D-14）：主按钮是「去问一句」，工作台入口降为次按钮。
                 注意两个按钮必须包在同一个 <template v-else> 里——此前写成两个
                 并列 v-else，模板编译直接报错（vue-tsc 不查结构，vite build 才暴露）。 -->
            <button
              id="setup-ask"
              type="button"
              class="btn primary"
              @click="askFirstQuestion"
            >
              去问一句
            </button>
            <button
              id="setup-enter"
              type="button"
              class="btn ghost"
              @click="enterWorkspace()"
            >
              进入工作台
            </button>
          </template>
        </footer>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 首次安装引导（v3.74；v3.82 拆分）。
 *
 * 与旧版（v3.69）的差别：
 * - 桌面版不再预置演示账号，7 步里由用户自建管理员与小院；
 * - 新增真实的内置技能铺设与云端供应商写入（旧版模型选择只是 UI）；
 * - 口令规则与后端一致（≥10 位、字母+数字、弱口令、不含账号）；
 * - 全量使用 --ds-* 令牌，跟随品牌与明暗主题。
 *
 * v3.82 起本组件只负责：7 步的编排（rail/进度/footer）、共享状态与校验逻辑
 * （经 provide 下发给 SetupStep*.vue 子组件）、提交与路由跳转；
 * 各步骤的表单模板与专属样式在 components/setup/SetupStep*.vue。
 */
import { computed, onMounted, provide, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { setupApi, type SetupInitializeResult, type SetupSkillGroup, type SetupSkillItem, type SetupLlmProvider } from '@/api/setup'
import { useAuthStore } from '@/stores/auth'
import { PROVINCES, PROVINCE_NAMES } from '@/utils/regions'
import { APP_VERSION } from '@/utils/version'
import {
  ACCENTS,
  DEPLOY,
  PROVIDERS,
  PROFILES,
  setupWizardKey,
  type SetupWizardContext,
  type SetupWizardForm,
} from '@/components/setup/wizardContext'
import SetupStepDeployment from '@/components/setup/SetupStepDeployment.vue'
import SetupStepAccount from '@/components/setup/SetupStepAccount.vue'
import SetupStepYard from '@/components/setup/SetupStepYard.vue'
import SetupStepCapabilities from '@/components/setup/SetupStepCapabilities.vue'
import SetupStepBranding from '@/components/setup/SetupStepBranding.vue'
import SetupStepConfirm from '@/components/setup/SetupStepConfirm.vue'
import SetupStepDone from '@/components/setup/SetupStepDone.vue'

const router = useRouter()
const auth = useAuthStore()
const version = APP_VERSION

const STEPS = [
  { title: '欢迎 · 形态', sub: '安装形态与数据落点' },
  { title: '管理员账号', sub: '第一个账号由你创建' },
  { title: '我的小院', sub: '名称、地区与产业画像' },
  { title: '能力与模型', sub: '技能、开关与模型接入' },
  { title: '品牌外观', sub: '名称与主色' },
  { title: '确认创建', sub: '核对并写入本机' },
  { title: '完成', sub: '进入工作台' },
]

const WEAK_USERS = ['admin', 'root', 'system', 'administrator', 'platform_admin', 'test', 'guest', 'user', 'yard_admin']
const WEAK_PWDS = ['admin123', 'yard123', 'demo123', 'password', '12345678', '123456789', 'qwerty', 'abc12345', 'admin1234', '11111111', 'password1', 'kjxy1234', '1234567890', 'admin12345']

const cur = ref(1)
const submitting = ref(false)
const skillsLoading = ref(false)
const catalog = ref<SetupSkillItem[]>([])
const selectedSkills = ref<Set<string>>(new Set())
const skillGroups = ref<SetupSkillGroup[]>([])
const skillProfileLabels = ref<string[]>([])
const skillRecommendedCount = ref(0)
const skillQuery = ref('')
const skillGroupFilter = ref<string>('all')
/** 用户手动改过勾选后，不再用「推荐预选」覆盖他的选择 */
const skillsTouched = ref(false)
const done = ref<SetupInitializeResult | null>(null)
const savedPwdAck = ref(false)
const showPwd = ref(false)
const submitError = ref('')
const createPercent = ref(0)
const createStage = ref('正在写入本机数据库…')
const errors = reactive<Record<string, string>>({})

const form = reactive<SetupWizardForm>({
  deployment: 'desktop',
  username: '',
  displayName: '',
  password: '',
  password2: '',
  email: '',
  phone: '',
  yardName: '',
  province: '',
  city: '',
  county: '',
  profileTypes: ['rice'] as string[],
  school: '',
  industry: '',
  description: '',
  searchEnabled: true,
  agentGenerate: true,
  dailyLimit: 10,
  modelMode: 'later',
  ollamaUrl: 'http://127.0.0.1:11434',
  ollamaModel: 'qwen2.5:7b',
  cloudProvider: 'deepseek',
  cloudUrl: 'https://api.deepseek.com/v1',
  cloudModel: 'deepseek-chat',
  cloudKey: '',
  appName: '科技小院',
  primaryColor: '#6366F1',
  welcome: '把论文写在田野大地上。',
})

const provinceNames = PROVINCE_NAMES
const cities = computed(() => (form.province ? PROVINCES[form.province] ?? [] : []))
const locText = computed(() => [form.province, form.city, form.county].filter(Boolean).join(' · '))

/**
 * 本机实际访问地址。
 *
 * 桌面壳会在 18700-18710 里挑一个空闲端口（client/desktop/src-tauri/src/lib.rs
 * 的 PORT_RANGE），此前向导把 `127.0.0.1:18700` 写死在 3 处，换端口就显示错
 * 地址（审查 D-09）。这里改成读当前页面来源——桌面形态下界面正是由引擎自己
 * 提供的，window.location 就是真值。
 */
const localAddress = computed(() => {
  if (typeof window === 'undefined') return '127.0.0.1:18700'
  const { hostname, port } = window.location
  return port ? `${hostname}:${port}` : hostname
})
const localUrl = computed(() => {
  if (typeof window === 'undefined') return 'http://127.0.0.1:18700'
  return window.location.origin
})
const deployHint = computed(() => {
  if (form.deployment === 'desktop') return '单机桌面版固定只监听 127.0.0.1，不对外开放端口。'
  if (form.deployment === 'server') return '本安装器仍只准备本机数据；局域网多用户请在院内服务器上按部署文档另行安装。'
  return '本安装器仍只准备本机数据；云端多小院请使用服务器部署包，并配置反向代理与备份。'
})

const pwdRules = computed(() => {
  const pw = form.password
  const user = form.username.toLowerCase()
  return [
    { key: 'len', label: '至少 10 位', ok: pw.length >= 10, req: true },
    { key: 'alpha', label: '包含字母', ok: /[A-Za-z]/.test(pw), req: true },
    { key: 'digit', label: '包含数字', ok: /\d/.test(pw), req: true },
    { key: 'weak', label: '不是常见弱口令', ok: !!pw && !WEAK_PWDS.includes(pw.toLowerCase()), req: true },
    { key: 'name', label: '不含账号名', ok: !user || !pw.toLowerCase().includes(user), req: true },
    { key: 'sym', label: '包含符号（建议）', ok: /[^A-Za-z0-9]/.test(pw), req: false },
  ]
})
const strengthLevel = computed(() => {
  if (!form.password) return 0
  const passed = pwdRules.value.filter(r => r.ok).length
  return Math.min(4, Math.max(1, Math.ceil((passed / pwdRules.value.length) * 4)))
})

const summary = computed(() => {
  const deploy = DEPLOY.find(d => d.key === form.deployment)?.title ?? form.deployment
  const model = form.modelMode === 'later'
    ? '稍后配置（不写供应商）'
    : form.modelMode === 'ollama'
      ? `本地模型 · ${form.ollamaModel || '未填'}`
      : `${PROVIDERS.find(p => p.key === form.cloudProvider)?.label ?? ''} · ${form.cloudModel || '未填模型'}`
  return [
    { title: '安装形态', step: 1, rows: [['形态', deploy], ['监听地址', localAddress.value]] },
    { title: '管理员', step: 2, rows: [['登录账号', form.username || '未填写'], ['显示名', form.displayName || '未填写'], ['口令', '••••••••'], ['邮箱 / 手机', [form.email, form.phone].filter(Boolean).join(' · ') || '未填写']] },
    { title: '小院', step: 3, rows: [['名称', form.yardName || '未填写'], ['所在地', locText.value || '未填写'], ['产业画像', form.profileTypes.map(profileLabel).join('、') || '未选择'], ['培养单位', form.school || '未填写']] },
    { title: '能力与模型', step: 4, rows: [['内置技能', `${selectedSkills.value.size} 个`], ['联网搜索', form.searchEnabled ? '开' : '关'], ['智能体生成', `${form.agentGenerate ? '开' : '关'} · 每日 ${form.dailyLimit} 次`], ['模型接入', model]] },
    { title: '外观', step: 5, rows: [['应用名称', form.appName || '科技小院'], ['主色', form.primaryColor.toUpperCase()]] },
  ]
})

function profileLabel(key: string): string {
  return PROFILES.find(p => p.key === key)?.label ?? key
}

function clearError(key: string): void {
  delete errors[key]
}

function setError(key: string, message: string): false {
  errors[key] = message
  return false
}

function validateField(key: string): boolean {
  clearError(key)
  switch (key) {
    case 'username': {
      const v = form.username.trim()
      if (!v) return setError('username', '请填写登录账号')
      if (!/^[A-Za-z0-9_]{3,32}$/.test(v)) return setError('username', '需为 3–32 位字母、数字或下划线')
      if (WEAK_USERS.includes(v.toLowerCase())) return setError('username', '该名称过于常见，容易被猜中，请换一个专有账号')
      return true
    }
    case 'displayName':
      if (!form.displayName.trim()) return setError('displayName', '请填写显示名')
      return true
    case 'password': {
      if (!form.password) return setError('password', '请设置登录口令')
      const bad = pwdRules.value.filter(r => r.req && !r.ok)
      if (bad.length) return setError('password', bad.map(r => r.label).join('、'))
      return true
    }
    case 'password2':
      if (!form.password2) return setError('password2', '请再次输入口令')
      if (form.password2 !== form.password) return setError('password2', '两次输入的口令不一致')
      return true
    case 'email':
      if (form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(form.email)) return setError('email', '邮箱格式不正确')
      return true
    case 'phone':
      if (form.phone && !/^1[3-9]\d{9}$/.test(form.phone)) return setError('phone', '请输入 11 位手机号')
      return true
    case 'yardName':
      if (!form.yardName.trim()) return setError('yardName', '请填写小院名称')
      if (form.yardName.trim().length < 2) return setError('yardName', '小院名称至少 2 个字符')
      return true
    case 'province':
      if (!form.province) return setError('province', '请选择省份')
      return true
    case 'city':
      if (!form.city) return setError('city', '请选择地市')
      return true
    case 'profiles':
      if (!form.profileTypes.length) return setError('profiles', '请至少选择一个产业画像')
      return true
    case 'appName':
      if (!form.appName.trim()) return setError('appName', '请填写应用名称')
      return true
    default:
      return true
  }
}

function validateStep(step: number): boolean {
  if (step === 1) return true
  if (step === 2) {
    const ok = ['username', 'displayName', 'password', 'password2', 'email', 'phone'].map(validateField).every(Boolean)
    if (!ok) ElMessage.warning('请先修正标红的字段')
    return ok
  }
  if (step === 3) {
    const ok = ['yardName', 'province', 'city', 'profiles'].map(validateField).every(Boolean)
    if (!ok) ElMessage.warning('请补全小院信息')
    return ok
  }
  if (step === 4) {
    if (form.modelMode === 'cloud') {
      if (!/^https?:\/\//.test(form.cloudUrl)) return fail('接口地址需以 http:// 或 https:// 开头')
      if (!form.cloudKey) return fail('请填写 API Key，或改选「稍后配置」')
      if (!form.cloudModel) return fail('请填写模型名')
    }
    if (form.modelMode === 'ollama' && (!form.ollamaUrl || !form.ollamaModel)) {
      return fail('请填写 Ollama 地址与模型名，或改选「稍后配置」')
    }
    return true
  }
  if (step === 5) return validateField('appName')
  if (step === 6) {
    if (!savedPwdAck.value) return fail('请先勾选「我已妥善保存管理员口令」')
    return true
  }
  return true
}

function fail(message: string): false {
  ElMessage.warning(message)
  return false
}

function next(): void {
  if (submitting.value) return
  if (cur.value === 6) {
    if (validateStep(6)) void submit()
    return
  }
  if (!validateStep(cur.value)) return
  cur.value = Math.min(7, cur.value + 1)
  // 进入第 4 步时按当前产业画像刷新推荐预选（用户在第 3 步可能改过画像）
  if (cur.value === 4) void loadSkills()
}

function prev(): void {
  if (cur.value > 1) cur.value -= 1
}

function onProvinceChange(): void {
  form.city = ''
  validateField('province')
}

function toggleProfile(key: string): void {
  const i = form.profileTypes.indexOf(key)
  if (i >= 0) form.profileTypes.splice(i, 1)
  else form.profileTypes.push(key)
  validateField('profiles')
}

/** 关键词 + 分类过滤后的技能（本地过滤，35 条目录无需服务端检索） */
const filteredSkills = computed<SetupSkillItem[]>(() => {
  const filter = skillGroupFilter.value
  let items = catalog.value
  if (filter === 'recommended') items = items.filter(s => s.recommended)
  else if (filter !== 'all') items = items.filter(s => s.group === filter)
  const q = skillQuery.value.trim().toLowerCase()
  if (!q) return items
  return items.filter(
    s =>
      s.name.toLowerCase().includes(q) ||
      s.description.toLowerCase().includes(q) ||
      s.pack.toLowerCase().includes(q) ||
      s.keywords.some(k => k.toLowerCase().includes(q))
  )
})

/** 按分类分组的可见技能（分类视图带分组标题，「推荐」视图不重复分组） */
const visibleSkillGroups = computed<{ key: string; label: string; items: SetupSkillItem[] }[]>(() => {
  if (skillGroupFilter.value === 'recommended') {
    return [{ key: 'recommended', label: '', items: filteredSkills.value }]
  }
  const order = skillGroups.value.map(g => g.key)
  const buckets = new Map<string, SetupSkillItem[]>()
  for (const item of filteredSkills.value) {
    const list = buckets.get(item.group) ?? []
    list.push(item)
    buckets.set(item.group, list)
  }
  return order
    .filter(key => (buckets.get(key) ?? []).length)
    .map(key => ({
      key,
      label: skillGroups.value.find(g => g.key === key)?.label ?? key,
      items: buckets.get(key) ?? []
    }))
})

function toggleSkill(code: string): void {
  const nextSet = new Set(selectedSkills.value)
  if (nextSet.has(code)) nextSet.delete(code)
  else nextSet.add(code)
  selectedSkills.value = nextSet
  skillsTouched.value = true
}

function selectAllSkills(all: boolean): void {
  // 全选/清空只作用于**当前可见**的技能（搜索或分类过滤后），
  // 否则用户筛到「农技」再点全选，会把看不见的 PPT 技能也一并勾上。
  const visible = visibleSkillGroups.value.flatMap(g => g.items)
  const nextSet = new Set(selectedSkills.value)
  if (all) visible.forEach(s => nextSet.add(s.code))
  else visible.forEach(s => nextSet.delete(s.code))
  selectedSkills.value = nextSet
  skillsTouched.value = true
}

/**
 * 读取内置技能清单，并按第 3 步勾选的产业画像给出「推荐预选」。
 * 审查 D-13：旧实现一次性返回 35 个技能并默认全选，县级农技小院看到的是
 * 一片「PPT 模板与母版套用」这类不认识的名字。
 */
async function loadSkills(): Promise<void> {
  skillsLoading.value = true
  try {
    const res = await setupApi.skills(form.profileTypes)
    catalog.value = res.items
    skillGroups.value = res.groups
    skillProfileLabels.value = res.profile_labels
    skillRecommendedCount.value = res.recommended_count
    if (!skillsTouched.value) {
      selectedSkills.value = new Set(res.recommended_codes)
    }
  } catch {
    catalog.value = []
    skillGroups.value = []
    skillProfileLabels.value = []
    skillRecommendedCount.value = 0
  } finally {
    skillsLoading.value = false
  }
}

function generatePassword(): void {
  const sets = ['ABCDEFGHJKLMNPQRSTUVWXYZ', 'abcdefghijkmnopqrstuvwxyz', '23456789', '!@#$%^&*-_=+']
  const all = sets.join('')
  const pick = (s: string) => {
    const buf = new Uint32Array(1)
    crypto.getRandomValues(buf)
    return s[buf[0] % s.length]
  }
  let out = sets.map(pick).join('')
  for (let i = 0; i < 12; i += 1) out += pick(all)
  out = out.split('').sort(() => Math.random() - 0.5).join('')
  form.password = out
  form.password2 = out
  showPwd.value = true
  validateField('password')
  validateField('password2')
  ElMessage.success('已生成 16 位强口令并填入两次，请妥善保存')
}

function onProviderChange(): void {
  const p = PROVIDERS.find(x => x.key === form.cloudProvider)
  if (!p) return
  form.cloudUrl = p.url
  form.cloudModel = p.model
}

function buildProvider(): SetupLlmProvider | null {
  if (form.modelMode === 'later') return null
  if (form.modelMode === 'ollama') {
    return {
      provider_type: 'openai_compatible',
      name: 'Ollama 本地',
      base_url: form.ollamaUrl.trim(),
      api_key: 'ollama',
      model_id: form.ollamaModel.trim(),
    }
  }
  const preset = PROVIDERS.find(p => p.key === form.cloudProvider)
  return {
    provider_type: preset?.providerType ?? 'openai_compatible',
    name: preset?.label ?? '云端模型',
    base_url: form.cloudUrl.trim(),
    api_key: form.cloudKey.trim(),
    model_id: form.cloudModel.trim(),
  }
}

async function submit(): Promise<void> {
  if (submitting.value || !validateStep(6)) return
  submitting.value = true
  submitError.value = ''
  createPercent.value = 12
  createStage.value = '正在创建管理员账号…'

  const stageTimer = window.setInterval(() => {
    createPercent.value = Math.min(88, createPercent.value + 7)
    if (createPercent.value > 30) createStage.value = '正在建立小院与铺设内置技能…'
    if (createPercent.value > 60) createStage.value = '正在写入能力开关与品牌…'
  }, 420)

  try {
    const result = await setupApi.initialize({
      username: form.username.trim(),
      password: form.password,
      display_name: form.displayName.trim(),
      email: form.email.trim() || null,
      phone: form.phone.trim() || null,
      yard_name: form.yardName.trim(),
      province: form.province,
      city: form.city,
      county: form.county.trim(),
      profile_types: [...form.profileTypes],
      yard_description: form.description.trim(),
      school: form.school.trim(),
      industry: form.industry.trim(),
      skill_codes: [...selectedSkills.value],
      llm_provider: buildProvider(),
      app_name: form.appName.trim(),
      primary_color: form.primaryColor,
      search_enabled: form.searchEnabled,
      agent_generate_enabled: form.agentGenerate,
      agent_generate_daily_limit: form.dailyLimit,
      deployment: form.deployment,
    })
    createPercent.value = 100
    createStage.value = '写入完成，正在登录…'
    auth.adoptSetupSession(result.access_token, result.refresh_token)
    try {
      await auth.fetchMe()
    } catch {
      /* 进入系统后路由守卫会再拉一次 */
    }
    done.value = result
    // 与 DesktopFirstRun.vue 的 DONE_KEY 对齐：向导已接入模型后不再重复弹引导
    if (result.model_configured) localStorage.setItem('ky_desktop_welcome_done', '1')
    cur.value = 7
    ElMessage.success('创建完成，已登录管理员会话')
  } catch (e) {
    const err = (e as { response?: { data?: { error?: { code?: string; message?: string } } } })?.response?.data?.error
    const code = err?.code ?? ''
    const message = err?.message ?? (e as Error)?.message ?? '创建失败，请稍后重试'
    if (code === 'SETUP_WEAK_USERNAME' || code === 'SETUP_INVALID_USERNAME' || code === 'SETUP_USERNAME_TAKEN') {
      errors.username = message
      cur.value = 2
    } else if (code === 'SETUP_WEAK_PASSWORD') {
      errors.password = message
      cur.value = 2
    } else if (code === 'EMAIL_TAKEN') {
      errors.email = message
      cur.value = 2
    } else if (code === 'PHONE_TAKEN') {
      errors.phone = message
      cur.value = 2
    } else if (code === 'SETUP_YARD_NAME_REQUIRED') {
      errors.yardName = message
      cur.value = 3
    } else if (code === 'SETUP_ALREADY_DONE') {
      ElMessage.warning('系统已完成初始化，请直接登录')
      void router.replace('/login')
    }
    submitError.value = message
    ElMessage.error(message)
  } finally {
    window.clearInterval(stageTimer)
    submitting.value = false
    createPercent.value = 0
  }
}

function enterWorkspace(path = '/'): void {
  void router.replace(path)
}

/**
 * 首问示例问题（审查 D-14）。
 *
 * 装完系统的第一分钟没有闭环：第 7 步只给了「进工作台 / 邀请成员 / 接入模型」
 * 三个出口，**没有一个是「提个问题试试」**。小白装完的第一件事应该是
 * 成功提一次问，所以这里按产业画像给一句可直接发送的示例问题，
 * 跳到 /chat?q=... 后由对话页预填草稿。
 */
const SAMPLE_QUESTIONS: Record<string, string> = {
  rice: '当前水稻田块的灌溉墒情怎么样？该不该补水？',
  aquaculture: '今天的塘口水质和投喂要注意什么？',
  fruit: '葡萄疏果期要注意什么？',
  vegetable: '这茬蔬菜的病虫害怎么预防？',
  tea: '当前茶园的采摘与施肥怎么安排？',
  livestock: '今天猪舍的温湿度和饲喂该怎么调整？',
  smart_agri: '本周小院最该先做的一件事是什么？',
  general: '帮我梳理一下本周小院的工作要点。',
}

const sampleQuestion = computed(
  () => SAMPLE_QUESTIONS[form.profileTypes[0]] ?? SAMPLE_QUESTIONS.general,
)

function askFirstQuestion(): void {
  void router.replace({ path: '/chat', query: { q: sampleQuestion.value } })
}

// ---- 共享上下文下发（子组件经 inject 拿到同名绑定，模板零改动）----
provide(setupWizardKey, {
  cur,
  form,
  errors,
  selectedSkills,
  catalog,
  skillGroups,
  skillProfileLabels,
  skillRecommendedCount,
  skillQuery,
  skillGroupFilter,
  skillsLoading,
  done,
  savedPwdAck,
  showPwd,
  submitting,
  submitError,
  createPercent,
  createStage,
  DEPLOY,
  PROFILES,
  ACCENTS,
  PROVIDERS,
  provinceNames,
  cities,
  locText,
  localAddress,
  localUrl,
  deployHint,
  pwdRules,
  strengthLevel,
  summary,
  visibleSkillGroups,
  sampleQuestion,
  profileLabel,
  validateField,
  onProvinceChange,
  toggleProfile,
  toggleSkill,
  selectAllSkills,
  generatePassword,
  onProviderChange,
  submit,
  askFirstQuestion,
  enterWorkspace,
} satisfies SetupWizardContext)

onMounted(async () => {
  try {
    const st = await setupApi.status()
    if (st.initialized) {
      void router.replace('/login')
      return
    }
  } catch {
    /* 状态取不到时按可初始化处理，提交时会再校验 */
  }
  skillsLoading.value = true
  try {
    await loadSkills()
  } catch {
    catalog.value = []
  } finally {
    skillsLoading.value = false
  }
})
</script>

<style scoped>
.setup-shell {
  min-height: 100dvh;
  display: grid;
  place-items: center;
  padding: 22px;
  background:
    radial-gradient(900px 520px at 12% -10%, color-mix(in srgb, var(--ds-accent) 26%, transparent), transparent 62%),
    radial-gradient(760px 460px at 92% 8%, color-mix(in srgb, var(--ds-accent-2) 16%, transparent), transparent 60%),
    var(--ds-bg-0);
}
.setup-grid {
  width: min(1180px, 100%);
  height: min(860px, calc(100dvh - 44px));
  min-height: 600px;
  display: grid;
  grid-template-columns: 264px minmax(0, 1fr);
  border: 1px solid var(--ds-border);
  border-radius: 16px;
  overflow: hidden;
  background: var(--ds-bg-1);
  box-shadow: var(--ds-shadow);
}
.rail {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 22px 18px 16px;
  border-right: 1px solid var(--ds-border);
  background: linear-gradient(180deg, color-mix(in srgb, var(--ds-bg-2) 55%, transparent), transparent 70%);
}
.brand { display: flex; gap: 11px; align-items: center; }
.mark {
  width: 38px; height: 38px; border-radius: 12px; display: grid; place-items: center;
  color: #fff; font-weight: 700; font-size: 14px;
  background: linear-gradient(145deg, var(--ds-accent), var(--ds-accent-2));
  box-shadow: 0 8px 20px color-mix(in srgb, var(--ds-accent) 34%, transparent);
}
.brand-txt strong { display: block; font-size: 13.5px; }
.brand-txt span { display: block; font-size: 11px; color: var(--ds-text-3); margin-top: 2px; }
.progress { display: flex; flex-direction: column; gap: 6px; }
.progress-top { display: flex; justify-content: space-between; font-size: 11px; color: var(--ds-text-3); }
.progress-bar { height: 4px; border-radius: 999px; background: var(--ds-bg-3); overflow: hidden; }
.progress-bar i { display: block; height: 100%; background: linear-gradient(90deg, var(--ds-accent), var(--ds-accent-2)); transition: width .3s ease; }
.steps { list-style: none; display: flex; flex-direction: column; gap: 2px; }
.step { display: grid; grid-template-columns: 30px 1fr; gap: 10px; align-items: start; padding: 8px 6px; border-radius: 10px; }
.step .num {
  width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center;
  font-size: 11.5px; border: 1px solid var(--ds-border-strong); background: var(--ds-bg-1); color: var(--ds-text-3);
}
.step .lab { font-size: 12.5px; color: var(--ds-text-3); line-height: 1.35; padding-top: 1px; }
.step .lab small { display: block; font-size: 10.5px; opacity: .7; margin-top: 2px; }
.step.done .num { background: var(--ds-success); border-color: transparent; color: #04140e; }
.step.done .lab { color: var(--ds-text-2); }
.step.current { background: color-mix(in srgb, var(--ds-accent) 16%, transparent); }
.step.current .num { background: var(--ds-accent); border-color: transparent; color: #fff; }
.step.current .lab { color: var(--ds-text-1); font-weight: 600; }
.rail-foot { margin-top: auto; border-top: 1px solid var(--ds-border); padding-top: 12px; display: flex; flex-direction: column; gap: 8px; }
.rail-foot p { font-size: 10.8px; color: var(--ds-text-3); line-height: 1.55; }

.main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
.pane-wrap { flex: 1; min-height: 0; overflow: auto; }
/* .pane 命中子组件根节点（scoped 样式对子组件根生效）；pane 内部元素样式在各子组件 */
.pane { padding: 30px 34px 26px; max-width: 820px; }

.actionbar { flex: 0 0 auto; display: flex; align-items: center; gap: 10px; padding: 13px 24px 14px; border-top: 1px solid var(--ds-border); }
.btn { height: 38px; padding: 0 17px; border-radius: 10px; font-size: 13px; font-weight: 550; border: 1px solid var(--ds-border); background: rgba(255, 255, 255, .045); color: var(--ds-text-1); }
.btn:hover { border-color: var(--ds-border-strong); }
.btn.primary { background: var(--ds-accent); border-color: transparent; color: #fff; }
.btn.primary:disabled { opacity: .5; cursor: not-allowed; }
.btn.ghost { background: none; border-color: transparent; color: var(--ds-text-2); }

@media (max-width: 980px) {
  .setup-shell { padding: 0; }
  .setup-grid { height: 100dvh; border-radius: 0; border: 0; grid-template-columns: 1fr; }
  .rail { flex-direction: row; align-items: center; gap: 12px; padding: 10px 12px; border-right: 0; border-bottom: 1px solid var(--ds-border); overflow: auto; }
  .brand-txt span, .progress, .rail-foot, .step .lab small { display: none; }
  .steps { flex-direction: row; gap: 4px; }
  .step { grid-template-columns: auto; padding: 5px 7px; }
  .step .num { width: 24px; height: 24px; font-size: 10.5px; }
  .step .lab { font-size: 11px; white-space: nowrap; }
}
</style>
