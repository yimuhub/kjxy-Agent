/**
 * 安装向导的共享上下文（v3.82 拆分）。
 *
 * SetupWizard.vue 持有全部状态与校验逻辑，7 个步骤子组件
 * （SetupStep*.vue）经 `inject(setupWizardKey)` 拿到**同名绑定**——
 * 模板表达式因此从单文件拆分到子组件时一行都不用改。
 * 子组件只解构自己用到的成员；新增共享状态先扩这里的接口。
 */
import type { ComputedRef, InjectionKey, Ref } from 'vue'
import type { SetupInitializeResult, SetupSkillGroup, SetupSkillItem } from '@/api/setup'

/** 向导表单（7 步收集的全部字段，提交时一次写入） */
export interface SetupWizardForm {
  deployment: string
  username: string
  displayName: string
  password: string
  password2: string
  email: string
  phone: string
  yardName: string
  province: string
  city: string
  county: string
  profileTypes: string[]
  school: string
  industry: string
  description: string
  searchEnabled: boolean
  agentGenerate: boolean
  dailyLimit: number
  modelMode: 'later' | 'ollama' | 'cloud'
  ollamaUrl: string
  ollamaModel: string
  cloudProvider: string
  cloudUrl: string
  cloudModel: string
  cloudKey: string
  appName: string
  primaryColor: string
  welcome: string
}

/** 口令规则条（第 2 步强度条与规则清单共用） */
export interface SetupPwdRule {
  key: string
  label: string
  ok: boolean
  req: boolean
}

/** 第 6 步确认页的分组回执 */
export interface SetupSummaryGroup {
  title: string
  step: number
  rows: string[][]
}

export const DEPLOY = [
  { key: 'desktop', title: '单机桌面版', desc: '一台电脑自用，离线可用，数据只在本地', tag: '推荐' },
  { key: 'server', title: '院内服务器版', desc: '部署在小院内网，多人同时使用', tag: '局域网' },
  { key: 'cloud', title: '云端多小院版', desc: '公网服务多个小院，含平台监管', tag: '集群' },
] as const

export const PROFILES = [
  { key: 'rice', label: '水稻 / 稻麦', emoji: '🌾' },
  { key: 'aquaculture', label: '水产蟹虾', emoji: '🦀' },
  { key: 'fruit', label: '应时鲜果', emoji: '🍇' },
  { key: 'vegetable', label: '蔬菜瓜果', emoji: '🥬' },
  { key: 'tea', label: '茶叶', emoji: '🍵' },
  { key: 'livestock', label: '畜禽', emoji: '🐑' },
  { key: 'smart_agri', label: '智慧农业 / 农文旅', emoji: '📡' },
  { key: 'general', label: '通用', emoji: '🧰' },
] as const

export const ACCENTS = [
  { value: '#6366F1', label: '靛蓝' },
  { value: '#3F9D68', label: '田园绿' },
  { value: '#E0A83C', label: '丰收金' },
  { value: '#C084FC', label: '智慧紫' },
  { value: '#E2604A', label: '蟹虾朱' },
  { value: '#22D3EE', label: '青碧' },
] as const

export const PROVIDERS = [
  { key: 'deepseek', label: 'DeepSeek', providerType: 'deepseek', url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { key: 'qwen', label: '通义千问', providerType: 'qwen', url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-plus' },
  { key: 'zhipu', label: '智谱 GLM', providerType: 'openai_compatible', url: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-plus' },
  { key: 'kimi', label: 'Kimi (Moonshot)', providerType: 'moonshot', url: 'https://api.moonshot.cn/v1', model: 'moonshot-v1-8k' },
  { key: 'openai', label: 'OpenAI 兼容', providerType: 'openai', url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  // 自定义：任何 OpenAI 兼容端点（含企业内网代理、聚合网关）。
  // 此前下拉里只有 5 家 known 厂商，Agnes / OneAPI / vLLM / Ollama 这类
  // 常见部署根本选不到，用户只能改配置文件绕过向导（审查 D-10）。
  { key: 'custom', label: '自定义（OpenAI 兼容）', providerType: 'openai_compatible', url: '', model: '' },
] as const

export interface SetupWizardContext {
  // ---- 共享状态（Ref 在子组件模板里自动解包）----
  cur: Ref<number>
  form: SetupWizardForm
  errors: Record<string, string>
  selectedSkills: Ref<Set<string>>
  catalog: Ref<SetupSkillItem[]>
  skillGroups: Ref<SetupSkillGroup[]>
  skillProfileLabels: Ref<string[]>
  skillRecommendedCount: Ref<number>
  skillQuery: Ref<string>
  skillGroupFilter: Ref<string>
  skillsLoading: Ref<boolean>
  done: Ref<SetupInitializeResult | null>
  savedPwdAck: Ref<boolean>
  showPwd: Ref<boolean>
  submitting: Ref<boolean>
  submitError: Ref<string>
  createPercent: Ref<number>
  createStage: Ref<string>

  // ---- 静态目录 ----
  DEPLOY: typeof DEPLOY
  PROFILES: typeof PROFILES
  ACCENTS: typeof ACCENTS
  PROVIDERS: typeof PROVIDERS
  provinceNames: readonly string[]

  // ---- 派生值 ----
  cities: ComputedRef<string[]>
  locText: ComputedRef<string>
  localAddress: ComputedRef<string>
  localUrl: ComputedRef<string>
  deployHint: ComputedRef<string>
  pwdRules: ComputedRef<SetupPwdRule[]>
  strengthLevel: ComputedRef<number>
  summary: ComputedRef<SetupSummaryGroup[]>
  visibleSkillGroups: ComputedRef<{ key: string; label: string; items: SetupSkillItem[] }[]>
  sampleQuestion: ComputedRef<string>

  // ---- 行为 ----
  profileLabel(key: string): string
  validateField(key: string): boolean
  onProvinceChange(): void
  toggleProfile(key: string): void
  toggleSkill(code: string): void
  selectAllSkills(all: boolean): void
  generatePassword(): void
  onProviderChange(): void
  submit(): Promise<void>
  askFirstQuestion(): void
  enterWorkspace(path?: string): void
}

export const setupWizardKey: InjectionKey<SetupWizardContext> = Symbol('setup-wizard')
