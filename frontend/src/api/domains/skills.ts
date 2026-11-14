/**
 * 自生长管理 / 技能与插件市场（含治理库 API）/ 技能归属 / 一句话生成技能 / 插件运行清单（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import type { AxiosRequestConfig } from 'axios'
import { api, silent, unwrap } from '../client'
import type {
  ApiResponse,
  Skill,
  SkillReviewAction,
  SkillVisibility,
} from '@/types/api'

// ---------- 自生长管理（第四期） ----------

/** 技能近 30 天调用统计（只统计参与次数，不含对话正文） */
export interface SkillUsageStats {
  days?: number
  calls: number
  sessions: number
  failed?: number
  last_used_at?: string | null
}

/** 单个技能的自动升级开关与最近一次执行结果 */
export interface SkillAutoUpgradeInfo {
  skill_id?: string
  skill_code?: string
  enabled: boolean
  min_calls: number
  cooldown_hours: number
  last_run_at: string | null
  last_status: string | null
  last_error: string | null
  usage: SkillUsageStats
  runs?: SkillUpgradeRunRecord[]
}

/** 一次自动升级尝试（只产待审核草稿，不自动发布） */
export interface SkillUpgradeRunRecord {
  id: string
  skill_code: string
  trigger: string
  status: string
  summary: string | null
  error: string | null
  draft_skill_id: string | null
  evidence?: Record<string, unknown> | null
  created_at: string | null
}

export const evolutionApi = {
  /** Skill 列表 */
  listSkills(): Promise<any[]> {
    return unwrap(api.get<any>('/skills')).then((r: any) => r.data ?? r)
  },
  /** 自动升级总览：按 skill_code 返回开关与近 30 天调用量（一次分组查询） */
  autoUpgradeOverview(): Promise<Record<string, SkillAutoUpgradeInfo>> {
    return unwrap(
      api.get<ApiResponse<Record<string, SkillAutoUpgradeInfo>>>('/skills/auto-upgrade/overview'),
    )
  },
  /** 单个技能的自动升级配置与最近运行记录 */
  getAutoUpgrade(skill_id: string): Promise<SkillAutoUpgradeInfo> {
    return unwrap(api.get<ApiResponse<SkillAutoUpgradeInfo>>(`/skills/${skill_id}/auto-upgrade`))
  },
  /** 开关/门槛/冷却时间；只影响草稿产出，发布仍需人工审核 */
  setAutoUpgrade(
    skill_id: string,
    payload: { enabled?: boolean; min_calls?: number; cooldown_hours?: number },
  ): Promise<SkillAutoUpgradeInfo> {
    return unwrap(api.patch<ApiResponse<SkillAutoUpgradeInfo>>(`/skills/${skill_id}/auto-upgrade`, payload))
  },
  /** 立即尝试一次升级（仍需已开启且有调用证据） */
  runAutoUpgrade(skill_id: string): Promise<SkillUpgradeRunRecord> {
    return unwrap(api.post<ApiResponse<SkillUpgradeRunRecord>>(`/skills/${skill_id}/auto-upgrade/run`, {}))
  },
  /** 设置灰度 */
  setRollout(skill_id: string, payload: Record<string, unknown>): Promise<unknown> {
    return unwrap(api.post(`/skills/${skill_id}/rollout`, payload))
  },
  /** 查询灰度状态 */
  getRollout(skill_id: string): Promise<unknown> {
    return unwrap(api.get(`/skills/${skill_id}/rollout`))
  },
  /** 灰度全量 */
  promoteRollout(skill_id: string): Promise<unknown> {
    return unwrap(api.post(`/skills/${skill_id}/rollout/promote`, {}))
  },
  /** 暂停灰度 */
  haltRollout(skill_id: string): Promise<unknown> {
    return unwrap(api.post(`/skills/${skill_id}/rollout/halt`, {}))
  },
  /** 门禁历史 */
  listEvalRuns(skill_id: string): Promise<any[]> {
    return unwrap(api.get<any>(`/skills/${skill_id}/eval-runs`)).then((r: any) => r.data ?? r)
  },
  /** 插件列表 */
  listPlugins(): Promise<any[]> {
    return unwrap(api.get<any>('/plugins')).then((r: any) => r.data ?? r.plugins ?? r)
  },
  /** 插件详情（含 manifest 正文：运行参数/步骤定义从这里取） */
  getPlugin(plugin_id: string): Promise<Record<string, unknown>> {
    return unwrap(api.get<ApiResponse<Record<string, unknown>>>(`/plugins/${plugin_id}`))
  },
  /** 扫描插件 */
  scanPlugins(): Promise<unknown> {
    return unwrap(api.post('/plugins/scan', {}))
  },
  /** 启用插件 */
  enablePlugin(plugin_id: string): Promise<unknown> {
    return unwrap(api.post(`/plugins/${plugin_id}/enable`, {}))
  },
  /** 禁用插件 */
  disablePlugin(plugin_id: string): Promise<unknown> {
    return unwrap(api.post(`/plugins/${plugin_id}/disable`, {}))
  },
  /** AI 总结技能候选（v1.3 C1）：近 7 天证据不足会诚实拒绝 EVOLUTION_INSUFFICIENT_EVIDENCE */
  suggestSkills(
    payload: { focus_hint?: string } = {},
    config?: AxiosRequestConfig & { skipErrorToast?: boolean },
  ): Promise<Record<string, unknown>> {
    // 设计稿 C1 写的是 /evolution/skills/suggest，但后端 evolution 路由挂在 /api/v1 根下
    // （与 /skills、/feedback 同形状），实际路径为 POST /skills/suggest
    return unwrap(
      api.post<ApiResponse<Record<string, unknown>>>('/skills/suggest', payload, config),
    )
  },
  /** 注册声明式 workflow 插件（v1.3 C2）：后端校验通过 → pending_review 等待批准 */
  registerPlugin(manifest: WorkflowManifest): Promise<Record<string, unknown>> {
    return unwrap(api.post<ApiResponse<Record<string, unknown>>>('/plugins/register', { manifest }))
  },
  /** 插件审核：approve → enabled；reject → rejected */
  reviewPlugin(
    plugin_id: string,
    decision: 'approve' | 'reject',
  ): Promise<Record<string, unknown>> {
    return unwrap(api.post<ApiResponse<Record<string, unknown>>>(`/plugins/${plugin_id}/review`, { decision }))
  },
  /** 运行插件（enabled 才可跑）：顺序执行 steps，返回 outputs/final */
  runPlugin(plugin_id: string, params: Record<string, unknown>): Promise<PluginRunResult> {
    return unwrap(api.post<ApiResponse<PluginRunResult>>(`/plugins/${plugin_id}/run`, { params }))
  },
  /** 从历史工具序列挖掘插件草稿（v1.3 C2）：只产草稿走审核，启用永远经人 */
  suggestPlugins(
    config?: AxiosRequestConfig & { skipErrorToast?: boolean },
  ): Promise<Record<string, unknown>> {
    return unwrap(api.post<ApiResponse<Record<string, unknown>>>('/plugins/suggest', {}, config))
  },
}

// ===== 声明式 Agent 与 Agent 工厂（能力中心，ADR-0005） =====

/** 助手可见性：public=本小院都能用，private=仅创建者 */

export interface WorkflowParamDef {
  key: string
  label: string
  required?: boolean
}

/** workflow 步骤：tool（执行注册表工具）| llm（模型补全，{{变量}} 插值） */
export interface WorkflowStepDef {
  kind: 'tool' | 'llm'
  /** kind=tool：工具注册表中的工具编码（如 sensor.query） */
  tool?: string
  /** kind=tool：工具参数（JSON 对象） */
  args?: Record<string, unknown>
  /** kind=tool：结果以此名写入上下文，供后续步骤 {{名}} 引用 */
  save_as?: string
  /** kind=llm：提示词，支持 {{变量}} 插值 */
  prompt?: string
}

/** 声明式 workflow 插件清单（POST /plugins/register 的 manifest） */
export interface WorkflowManifest {
  name: string
  description?: string
  params: WorkflowParamDef[]
  steps: WorkflowStepDef[]
}

/** 插件运行结果：outputs 为 save_as → 输出映射；final 为末步文本 */
export interface PluginRunResult {
  outputs?: Record<string, unknown>
  final?: string
  [key: string]: unknown
}


// ---------------------------------------------------------------------------
// 插件 / 技能市场
// ---------------------------------------------------------------------------

export interface MarketItem {
  item_key: string
  item_type: 'skill' | 'plugin' | 'agent'
  source: 'builtin' | 'remote' | 'github' | 'local'
  code: string
  name: string
  version: string
  description: string
  source_url?: string | null
  license?: string | null
  author?: string | null
  risk_level: string
  status: string
  verified: boolean
  installed: boolean
  stars?: number
  repo?: string
  tags?: string[]
  attribution?: string
}

export interface MarketItemDetail extends MarketItem {
  definition_json?: Record<string, unknown> | null
  manifest_json?: Record<string, unknown> | null
  metadata_json?: Record<string, unknown> | null
}

export interface MarketSearchResult {
  items: MarketItem[]
  total: number
  /** 联网检索失败时的中文提示；离线回退为内置技能库 */
  warning?: string | null
  offline: boolean
}

export interface MarketItemInstallResult {
  item_key: string
  item_type: 'skill' | 'plugin' | 'agent'
  source: string
  name: string
  installed_as: string
  /** installed=已启用；pending_review=等待专家审核 */
  review_status: string
  skill_id?: string
  skill_status?: string
  plugin_id?: string
  plugin_status?: string
  checksum_ok?: boolean
}

export interface InstalledSkillRow {
  id: string
  code: string
  name: string
  version: string
  status: string
  item_key?: string | null
  source: string
  created_at: string
}

export interface InstalledPluginRow {
  id: string
  plugin_id: string
  code: string
  name: string
  version: string
  type: string
  status: string
  item_key?: string | null
  source: string
  created_at: string
}

export interface InstalledResult {
  skills: InstalledSkillRow[]
  plugins: InstalledPluginRow[]
  total: number
}

export interface SkillImportResult {
  item_key: string
  item_type: 'skill'
  source: 'local'
  code: string
  name: string
  version: string
  status: string
  skill_id: string
  skill_status: string
  verified: boolean
}

export interface PluginImportResult {
  item_key: string
  item_type: 'plugin'
  source: 'local'
  code: string
  name: string
  version: string
  status: string
  plugin_id: string
  plugin_status: string
  checksum_ok: boolean
  files: string[]
}

export interface GithubPreviewResult {
  repo: string
  path: string
  ref: string
  license?: string | null
  stars: number
  description: string
  name: string
  version: string
  source_url: string
  definition_json: Record<string, unknown>
  /** 预览即落库，可直接安装 */
  item_key?: string
}

export const libraryApi = {
  search(payload: {
    query?: string
    item_type?: 'skill' | 'plugin' | 'agent'
    source?: string
    limit?: number
  }): Promise<MarketSearchResult> {
    return unwrap(api.post<ApiResponse<MarketSearchResult>>('/market/items/search', payload))
  },
  detail(itemKey: string): Promise<MarketItemDetail> {
    return unwrap(api.get<ApiResponse<MarketItemDetail>>(`/market/items/${itemKey}`))
  },
  install(itemKey: string): Promise<MarketItemInstallResult> {
    return unwrap(
      api.post<ApiResponse<MarketItemInstallResult>>(`/market/items/${itemKey}/install`, {}),
    )
  },
  installed(): Promise<InstalledResult> {
    return unwrap(api.get<ApiResponse<InstalledResult>>('/market/installed'))
  },
  importSkill(file: File): Promise<SkillImportResult> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(
      api.post<ApiResponse<SkillImportResult>>('/market/skills/import', form, {
        ...silent,
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
  },
  importPlugin(file: File): Promise<PluginImportResult> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(
      api.post<ApiResponse<PluginImportResult>>('/market/plugins/import', form, {
        ...silent,
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
  },
  githubSearch(query: string): Promise<{ items: MarketItem[]; offline: boolean; warning: string | null }> {
    return unwrap(
      api.post<ApiResponse<{ items: MarketItem[]; offline: boolean; warning: string | null }>>(
        '/market/github/search',
        { query },
      ),
    )
  },
  githubPreview(repo: string): Promise<GithubPreviewResult> {
    return unwrap(api.post<ApiResponse<GithubPreviewResult>>('/market/github/preview', { repo }))
  },
}


// ---------------------------------------------------------------------------
// 技能归属（v3.2）：我的 / 小院 / 平台三级视图 + 审核与推广
// ---------------------------------------------------------------------------

/** 技能空间：mine=我创建的，yard=小院共享，platform=平台共享 */
export type SkillScope = 'mine' | 'yard' | 'platform'

export interface SkillListQuery {
  scope?: SkillScope
  /** draft|testing|pending_review|published|deprecated */
  status?: string
  page?: number
  pageSize?: number
}

export interface SkillListResult {
  items: Skill[]
  total: number
  page: number
  pageSize: number
}

// ─────────────────────────────────────────────────────────────────────────────
// 自我总结提升（v3.4）
// ─────────────────────────────────────────────────────────────────────────────

export interface SelfImproveRun {
  id: string
  job_id: string
  trigger: string
  status: 'running' | 'success' | 'failed' | 'skipped'
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
  tokens_used: number
  output: Record<string, unknown> | null
  error: string | null
  actor_id: string | null
}

export interface SelfImproveReport {
  settings: Record<string, unknown>
  job: { status: string; next_run_at: string | null; last_run_at: string | null }
  last_run: SelfImproveRun | null
  budget: { daily_budget_tokens: number; used_today: number; remaining: number }
  can_manage: boolean
}

export interface SelfImproveMemory {
  id: string
  user_id: string | null
  summary: string
  tags: string[]
  window_from: string | null
  window_to: string | null
  created_at: string | null
}

export const selfImproveApi = {
  report(): Promise<SelfImproveReport> {
    return unwrap(api.get<ApiResponse<SelfImproveReport>>('/yards/current/self-improve'))
  },
  run(): Promise<SelfImproveRun> {
    return unwrap(api.post<ApiResponse<SelfImproveRun>>('/yards/current/self-improve/run'))
  },
  runs(limit = 20): Promise<{ items: SelfImproveRun[] }> {
    return unwrap(
      api.get<ApiResponse<{ items: SelfImproveRun[] }>>(
        '/yards/current/self-improve/runs',
        { params: { limit } },
      ),
    )
  },
  memories(userId?: string): Promise<{ items: SelfImproveMemory[] }> {
    const params: Record<string, string> = {}
    if (userId) params.user_id = userId
    return unwrap(
      api.get<ApiResponse<{ items: SelfImproveMemory[] }>>(
        '/yards/current/self-improve/memories',
        { params },
      ),
    )
  },
}

/** 上架前评测报告（v3.17） */
export interface SkillEvalCheck {
  code: string
  severity: 'error' | 'warning'
  passed: boolean
  detail: string
}

export interface SkillEvalCase {
  case_id: string
  category: string
  passed: boolean
  reason: string
  latency_ms?: number
  citations_count?: number
}

export interface SkillEvalDataset {
  test_set_id: string
  version?: string
  dataset_sha256?: string | null
  pass_rate: number
  minimum_pass_rate: number
  gate_passed: boolean
  passed: number
  failed: number
  total: number
  failed_case_ids?: string[]
  category_pass_rates?: Record<string, number>
  metrics?: Record<string, number | string | null>
  cases?: SkillEvalCase[]
}

export interface SkillEvalReport {
  report_version: number
  kind: string
  generated_at: string
  skill_code: string
  version: string
  definition_sha256: string
  mode: 'static' | 'static+dataset' | 'static+dataset_skipped'
  pass: boolean
  score: number
  errors: number
  warnings: number
  checks: SkillEvalCheck[]
  dataset?: SkillEvalDataset | null
  notes?: string[]
}

export interface SkillEvalRunBrief {
  id: string
  kind: string
  test_set_id: string
  pass_rate: number
  passed: number
  failed: number
  total: number
  report_sha256?: string | null
  dataset_sha256?: string | null
  created_at: string
  report?: SkillEvalReport | null
}

export interface SkillAttestation {
  skill_id: string
  skill_code: string
  version: string
  status: string
  visibility: string
  definition_sha256?: string | null
  signature?: string | null
  signature_alg?: string | null
  key_id?: string | null
  signed_by?: string | null
  signed_at?: string | null
  verification: {
    valid: boolean
    signed: boolean
    reason: 'ok' | 'unsigned' | 'definition_mismatch' | 'bad_signature' | 'key_mismatch'
    digest: string
    expected_digest?: string | null
  }
  latest_eval_run?: SkillEvalRunBrief | null
}

export interface SkillSigningKeyInfo {
  alg: string
  key_id: string
  public_key: string
  note?: string
}

export interface SkillEvalRunDetail {
  id: string
  skill_code: string
  skill_version: string
  kind: string
  test_set_id: string
  pass_rate: number
  passed: number
  failed: number
  total: number
  report_sha256?: string | null
  dataset_sha256?: string | null
  created_at: string
  report?: SkillEvalReport | null
}

/**
 * 技能接口（v3.2 唯一入口）。
 * 与 modules.ts 的旧 skillApi 合并：list 支持 scope/分页，review 支持 submit_public，新增 promote。
 */
export const skillApi = {
  /** 按技能空间分页拉取；后端暂未支持 scope 时退化为全量列表（不报错） */
  list(query: SkillListQuery = {}): Promise<SkillListResult> {
    const page = query.page && query.page > 0 ? query.page : 1
    const pageSize = query.pageSize && query.pageSize > 0 ? query.pageSize : 20
    return api
      .get<ApiResponse<Skill[]> & { meta?: { total?: number } }>('/skills', {
        params: {
          scope: query.scope,
          status: query.status || undefined,
          page,
          page_size: pageSize,
        },
        ...silent,
      })
      .then(res => ({
        items: (res.data.data ?? []) as unknown as Skill[],
        total: Number(res.data.meta?.total ?? 0),
        page,
        pageSize,
      }))
  },
  create(payload: Record<string, unknown>): Promise<Skill> {
    return unwrap(api.post<ApiResponse<Skill>>('/skills', payload))
  },
  /** 审核动作：submit|submit_public|approve|reject|rollback */
  review(
    id: string,
    action: SkillReviewAction,
    note?: string,
    config: AxiosRequestConfig & { skipErrorToast?: boolean } = {},
  ): Promise<Skill> {
    return unwrap(
      api.post<ApiResponse<Skill>>(`/skills/${id}/review`, { action, note }, config),
    )
  },
  /** 负责人把私有技能转为小院公共（需 skills:publish_any；后端未就绪时 404） */
  promote(id: string, config: AxiosRequestConfig & { skipErrorToast?: boolean } = {}): Promise<Skill> {
    return unwrap(api.post<ApiResponse<Skill>>(`/skills/${id}/promote`, {}, config))
  },
  /** 摘要/签名/验签结论与最近一份评测报告（v3.17） */
  attestation(
    id: string,
    config: AxiosRequestConfig & { skipErrorToast?: boolean } = {},
  ): Promise<SkillAttestation> {
    return unwrap(api.get<ApiResponse<SkillAttestation>>(`/skills/${id}/attestation`, config))
  },
  /** 单次评测报告全文（v3.17） */
  evalRun(
    id: string,
    runId: string,
    config: AxiosRequestConfig & { skipErrorToast?: boolean } = {},
  ): Promise<SkillEvalRunDetail> {
    return unwrap(
      api.get<ApiResponse<SkillEvalRunDetail>>(`/skills/${id}/eval-runs/${runId}`, config),
    )
  },
  /** 签名公钥（Ed25519），用于离线校验（v3.17） */
  signingKey(config: AxiosRequestConfig & { skipErrorToast?: boolean } = {}): Promise<SkillSigningKeyInfo> {
    return unwrap(api.get<ApiResponse<SkillSigningKeyInfo>>('/skills/signing-key', config))
  },
}


// ---------------------------------------------------------------------------
// 一句话生成技能（v3.2）：检索匹配 → 生成 → 安装
// ---------------------------------------------------------------------------

/** 检索命中的候选（市场/内置/GitHub） */
export interface SkillMatch {
  item_key: string
  name: string
  summary?: string
  source?: string
  license?: string | null
  risk_level?: string
  installed?: boolean
  score?: number
}

export interface SkillAgentRequestResult {
  utterance: string
  /** 后端理解出的检索词 */
  query?: string
  recommend: 'install' | 'generate'
  matches: SkillMatch[]
  note?: string
  offline?: boolean
}

/** 生成结果里的技能摘要（一定是私有草稿） */
export interface GeneratedSkillBrief {
  id: string
  code: string
  name: string
  version: string
  visibility: SkillVisibility
  status: string
}

export interface SkillSourceRef {
  title: string
  url: string
}

export interface SkillAgentGenerateResult {
  skill: GeneratedSkillBrief
  /** 生成的 SkillSpec（含 guardrails.risk_level 等），形状由后端模板决定 */
  spec?: Record<string, unknown> | null
  sources?: SkillSourceRef[]
  requires_review?: boolean
  note?: string
  token_usage?: {
    input_tokens?: number
    output_tokens?: number
    total_tokens?: number
  }
}

/** 安装结果形状随安装器：只约定少量公共字段，其余原样透出 */
export interface SkillAgentInstallResult {
  item_key?: string
  skill_id?: string
  installed_as?: string
  review_status?: string
  skill_status?: string
  needs_review?: boolean
  [key: string]: unknown
}

/** 一句话生成插件结果（v3.3）：插件一律 pending_review，审核通过后才能运行 */
export interface PluginAgentGenerateResult {
  plugin: {
    plugin_id: string
    name: string
    description: string
    status: string
    step_count: number
  }
  manifest: Record<string, unknown>
  sources?: SkillSourceRef[]
  note?: string
  token_usage?: {
    input_tokens?: number
    output_tokens?: number
    total_tokens?: number
  }
}

/** 一句话链路：检索/生成/安装全部静默失败，由调用方在区块内展示 note（不弹全局错误） */
export const skillLabApi = {
  request(utterance: string): Promise<SkillAgentRequestResult> {
    return unwrap(
      api.post<ApiResponse<SkillAgentRequestResult>>(
        '/market/agent/request',
        { utterance, kind: 'skill' },
        silent,
      ),
    )
  },
  /** kind 决定返回形状：skill 返回技能草稿，plugin 返回待审工作流插件 */
  generate<K extends 'skill' | 'plugin' = 'skill'>(
    utterance: string,
    useWeb = true,
    kind: K = 'skill' as K,
  ): Promise<K extends 'plugin' ? PluginAgentGenerateResult : SkillAgentGenerateResult> {
    return unwrap(
      api.post<ApiResponse<SkillAgentGenerateResult | PluginAgentGenerateResult>>(
        '/market/agent/generate',
        { utterance, use_web: useWeb, kind },
        // 生成可能触发联网检索 + 模型调用，放宽超时
        { ...silent, timeout: 180_000 },
      ),
    ) as Promise<K extends 'plugin' ? PluginAgentGenerateResult : SkillAgentGenerateResult>
  },
  install(payload: { item_key: string } | { skill_id: string }): Promise<SkillAgentInstallResult> {
    return unwrap(
      api.post<ApiResponse<SkillAgentInstallResult>>('/market/agent/install', payload, silent),
    )
  },
}
