/**
 * 模型调用 / 工具目录 / 供应商 / 模型绑定与能力 / 调用观测 / MCP（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import { api, unwrap, unwrapList } from '../client'
import type {
  ApiResponse,
  LlmStatus,
  ToolDefinition,
} from '@/types/api'

// ---------- 模型 / 工具 ----------

export const llmApi = {
  status(): Promise<LlmStatus> {
    return unwrap(api.get<ApiResponse<LlmStatus>>('/llm/status'))
  },
}

/** 工具目录条目（v3.25）：内置 / 本小院自定义 / MCP 快照三源统一形状 */
export interface ToolCatalogItem {
  code: string
  version: string
  name: string
  description: string
  input_schema: Record<string, unknown>
  output_schema?: Record<string, unknown> | null
  required_permissions: string[]
  risk_level: string
  side_effect: boolean
  timeout_seconds: number
  enabled: boolean
  source: 'builtin' | 'custom' | 'mcp'
  impl_kind: string
  yard_id?: string | null
  readonly: boolean
  implementation?: Record<string, unknown>
}

export interface ToolCatalogOut {
  items: ToolCatalogItem[]
  counts: Record<string, number>
  risk_labels: Record<string, string>
  source_labels: Record<string, string>
}

export interface ToolDetailOut extends ToolCatalogItem {
  definition_markdown?: string
  call_example?: string
  recent_calls?: {
    at: string
    action: string
    result: string
    latency_ms?: number | null
    stage?: string | null
    actor_id?: string | null
  }[]
}

export interface ToolGenerateOut {
  spec: Record<string, unknown>
  source: 'model' | 'fallback'
  model: string
  note: string
}

export const toolApi = {
  list(): Promise<ToolDefinition[]> {
    return unwrapList<ToolDefinition>(api.get('/tools')).then(r => r.items)
  },
  /** 「可用工具」页目录：三类来源合并 + 分组计数 + 中文标签 */
  catalog(): Promise<ToolCatalogOut> {
    return unwrap(api.get<ApiResponse<ToolCatalogOut>>('/tools/catalog'))
  },
  detail(code: string): Promise<ToolDetailOut> {
    return unwrap(api.get<ApiResponse<ToolDetailOut>>(`/tools/${encodeURIComponent(code)}`))
  },
  /** 一句话生成草稿（不落库，预览确认后 create） */
  generate(utterance: string): Promise<ToolGenerateOut> {
    // 一次生成最多 3 轮真实模型调用，30s 默认超时会把慢模型直接掐断
    return unwrap(api.post<ApiResponse<ToolGenerateOut>>('/tools/generate', { utterance }, { timeout: 150_000 }))
  },
  create(payload: Record<string, unknown>, enabled = false): Promise<ToolCatalogItem> {
    return unwrap(api.post<ApiResponse<ToolCatalogItem>>('/tools', { ...payload, enabled }))
  },
  update(code: string, payload: Record<string, unknown>): Promise<ToolCatalogItem> {
    return unwrap(api.put<ApiResponse<ToolCatalogItem>>(`/tools/${encodeURIComponent(code)}`, payload))
  },
  enable(code: string): Promise<ToolCatalogItem> {
    return unwrap(api.post<ApiResponse<ToolCatalogItem>>(`/tools/${encodeURIComponent(code)}/enable`))
  },
  disable(code: string): Promise<ToolCatalogItem> {
    return unwrap(api.post<ApiResponse<ToolCatalogItem>>(`/tools/${encodeURIComponent(code)}/disable`))
  },
  remove(code: string): Promise<void> {
    return api.delete(`/tools/${encodeURIComponent(code)}`).then(() => undefined)
  },
}


// ---------- 模型供应商（架构补强 A 组） ----------

export interface LlmSupplier {
  id: string
  yard_id: string | null
  name: string
  provider_type: string
  base_url: string
  model_id: string
  api_key_masked: string
  models: string | null
  priority: number
  status: number // 1=启用 2=禁用
  is_active: boolean
  // function-calling 能力位：true 才允许该供应商的助手进入工具调用循环（后端恒返回）
  supports_tools?: boolean
  // v3.32 金额额度单价（元/千 token）；null = 未配置
  price_in_per_1k?: number | null
  price_out_per_1k?: number | null
  consecutive_failures: number
  test_time: string | null
  response_time_ms: number | null
  last_error: string | null
  // 后端权限信号：yard_admin 视角下平台全局行=true（只可选用不可改）；缺省按非只读兼容
  readonly?: boolean
  created_at: string
  updated_at: string
}

export interface LlmSupplierCreate {
  name: string
  provider_type: string
  base_url: string
  model_id: string
  api_key: string
  models?: string
  /** 创建时勾选的可用模型；服务端会为每个模型落一行并自动补默认模型 */
  available_models?: string[]
  priority?: number
  scope?: 'yard' | 'global'
  price_in_per_1k?: number | null
  price_out_per_1k?: number | null
}

export interface LlmSupplierUpdate {
  name?: string
  provider_type?: string
  base_url?: string
  model_id?: string
  api_key?: string
  models?: string
  /** 勾选的可用模型；未勾选的已有模型置为停用（不删除） */
  available_models?: string[]
  priority?: number
  // function-calling 能力位开关（PUT 语义：传则覆盖）
  supports_tools?: boolean
  price_in_per_1k?: number | null
  price_out_per_1k?: number | null
}

export interface LlmTestResult {
  ok: boolean
  latency_ms: number
  models: string[]
  note: string
}

export interface LlmProviderTemplate {
  provider_type: string
  label: string
  base_url: string
  model_id: string
  note: string
}

export const llmSupplierApi = {
  /** 供应商列表（Key 已打码） */
  list(): Promise<LlmSupplier[]> {
    return unwrap(api.get<ApiResponse<LlmSupplier[]>>('/llm/providers'))
  },
  create(payload: LlmSupplierCreate): Promise<LlmSupplier> {
    return unwrap(api.post<ApiResponse<LlmSupplier>>('/llm/providers', payload))
  },
  update(id: string, payload: LlmSupplierUpdate): Promise<LlmSupplier> {
    return unwrap(api.put<ApiResponse<LlmSupplier>>(`/llm/providers/${id}`, payload))
  },
  remove(id: string): Promise<void> {
    return api.delete(`/llm/providers/${id}`).then(() => undefined)
  },
  enable(id: string): Promise<LlmSupplier> {
    return unwrap(api.post<ApiResponse<LlmSupplier>>(`/llm/providers/${id}/enable`))
  },
  disable(id: string): Promise<LlmSupplier> {
    return unwrap(api.post<ApiResponse<LlmSupplier>>(`/llm/providers/${id}/disable`))
  },
  /** 未落库候选配置的连通性测试 */
  testCandidate(payload: {
    provider_type: string
    base_url: string
    model_id: string
    api_key: string
  }): Promise<LlmTestResult> {
    return unwrap(api.post<ApiResponse<LlmTestResult>>('/llm/providers/test', payload))
  },
  /** 已存供应商测试（结果写回） */
  test(id: string): Promise<{ supplier: LlmSupplier; result: LlmTestResult }> {
    return unwrap(api.post(`/llm/providers/${id}/test`))
  },
  /** 设为当前生效供应商（小院级） */
  setActive(supplier_id: string): Promise<LlmSupplier> {
    return unwrap(api.post<ApiResponse<LlmSupplier>>('/llm/providers/active', { supplier_id }))
  },
  /** 预置模板 */
  templates(): Promise<LlmProviderTemplate[]> {
    return unwrap(api.get<ApiResponse<LlmProviderTemplate[]>>('/llm/provider-templates'))
  },
  /** 探测上游模型列表（v1.3 §A）：key 只在请求体瞬时使用，不落库 */
  probeModels(payload: {
    provider_type: string
    base_url: string
    api_key?: string
    supplier_id?: string
  }): Promise<{ models: { id: string; label: string }[]; source: 'remote' | 'unsupported' | 'error'; message?: string }> {
    return unwrap(api.post<ApiResponse<{ models: { id: string; label: string }[]; source: 'remote' | 'unsupported' | 'error'; message?: string }>>('/llm/providers/probe-models', payload))
  },
}


// ---------- 模型绑定与能力清单（v3.25 模型中心） ----------

export interface LlmCapability {
  code: string
  label: string
  group: string
  hint: string
}

export type LlmScopeType = 'global' | 'purpose' | 'agent' | 'agent_purpose'

export interface LlmBindingItem {
  id: string
  yard_id: string | null
  scope_type: LlmScopeType
  scope_key: string
  supplier_id: string
  supplier_name: string
  provider_type: string
  model_id: string
  model_name: string | null
  /** v3.37：绑定到具体模型行时返回（供应商下多模型） */
  supplier_model_id?: string | null
  model_key?: string | null
  modality?: string | null
  priority: number
  enabled: boolean
  // v3.26：停用/熔断的供应商要标「不可用」，否则界面会把绑定当成真的在用
  supplier_status?: number | null
  available?: boolean
}

export interface LlmBindingEntry {
  supplier_id: string
  supplier_model_id?: string | null
  model_name?: string | null
  group_name?: string | null
  priority?: number
  enabled?: boolean
}

export interface LlmResolutionHop {
  level: string
  level_label: string
  reason: string
  supplier_id: string | null
  supplier_name: string | null
  model_name: string | null
  usable: boolean
}

/** 模型中心 → 智能体模型：一行 = 一个助手 + 它当前真正会用的模型 */
export interface LlmAgentModelItem {
  code: string
  name: string
  kind: 'platform' | 'yard'
  agent_id: string | null
  status: string
  /** follow=跟随中心，custom=已单独指定，legacy=助手编辑页旧设置 */
  mode: 'follow' | 'custom' | 'legacy'
  bindings: LlmBindingItem[]
  /** v3.37：该助手的专属能力模型（图像/视频） */
  capabilities?: LlmAgentCapabilityItem[]
  legacy: { supplier_id: string; supplier_name: string; model_id: string; model_name: string | null } | null
  resolved: { supplier_id: string; supplier_name: string; model_name: string; level: string; level_label: string } | null
  hops: { level_label: string; supplier_name: string; model_name: string }[]
}

export interface LlmAgentCapabilityItem {
  purpose: 'image_generation' | 'video_generation'
  label: string
  bindings: LlmBindingItem[]
}

export interface LlmVisualModelItem {
  purpose: 'image_generation' | 'video_generation'
  label: string
  bindings: LlmBindingItem[]
  resolved: LlmAgentModelItem['resolved']
}

export interface LlmAgentModelsOut {
  items: LlmAgentModelItem[]
  visual: LlmVisualModelItem[]
  max_fallback: number
}

export interface LlmCapabilitiesOut {
  capabilities: LlmCapability[]
  purpose_codes: string[]
  scope_types: { type: string; label: string; need_key: boolean }[]
  levels: { level: string; label: string }[]
  max_fallback: number
}

export const llmBindingApi = {
  capabilities(): Promise<LlmCapabilitiesOut> {
    return unwrap(api.get<ApiResponse<LlmCapabilitiesOut>>('/llm/capabilities'))
  },
  list(scope?: { scope_type?: string; scope_key?: string }): Promise<{
    items: LlmBindingItem[]
  }> {
    return unwrap(api.get<ApiResponse<{ items: LlmBindingItem[] }>>('/llm/bindings', { params: scope }))
  },
  replace(payload: {
    scope_type: LlmScopeType
    scope_key?: string
    entries: LlmBindingEntry[]
  }): Promise<{ items: LlmBindingItem[] }> {
    return unwrap(api.put<ApiResponse<{ items: LlmBindingItem[] }>>('/llm/bindings', payload))
  },
  remove(id: string): Promise<void> {
    return api.delete(`/llm/bindings/${id}`).then(() => undefined)
  },
  agents(): Promise<LlmAgentModelsOut> {
    return unwrap(api.get<ApiResponse<LlmAgentModelsOut>>('/llm/bindings/agents'))
  },
  resolution(params: { purpose?: string; agent_code?: string }): Promise<{
    purpose: string | null
    purpose_label: string | null
    resolved_supplier_id: string | null
    resolved_model: string | null
    resolved_level: string
    hops: LlmResolutionHop[]
  }> {
    return unwrap(api.get<ApiResponse<never>>('/llm/bindings/resolution', { params }))
  },
  test(payload: { scope_type: string; scope_key?: string }): Promise<{
    ok: boolean
    model?: string
    latency_ms?: number
    note?: string
    reply?: string
  }> {
    return unwrap(api.post<ApiResponse<never>>('/llm/bindings/test', payload, { timeout: 120_000 }))
  },
}


export interface LlmTraceItem {
  id: string
  created_at: string
  provider_id: string | null
  model: string
  purpose: string
  agent_ref: string | null
  run_id: string | null
  status: string
  input_tokens: number | null
  output_tokens: number | null
  latency_ms: number | null
  prompt_digest: string | null
  error_text: string | null
}

export interface LlmUsageRow {
  day: string
  model: string
  calls: number
  tokens: number
  avg_latency_ms: number
  errors: number
}

export interface LlmUsageOverview {
  days: number
  today_date?: string
  today_tokens: number
  today_calls: number
  today_input_tokens: number
  today_output_tokens: number
  total_calls: number
  total_input_tokens: number
  total_output_tokens: number
  total_tokens: number
  by_purpose: Array<{
    purpose: string
    calls: number
    input_tokens: number
    output_tokens: number
    total_tokens: number
  }>
  by_agent: Array<{
    agent_ref: string
    calls: number
    input_tokens: number
    output_tokens: number
    total_tokens: number
  }>
  /** 按日聚合，后端已按 days 补零（最新一天在末尾） */
  by_day: Array<{
    day: string
    calls: number
    input_tokens: number
    output_tokens: number
    total_tokens: number
  }>
  /** 按模型聚合，token 总量降序 */
  by_model: Array<{
    model: string
    calls: number
    input_tokens: number
    output_tokens: number
    total_tokens: number
  }>
}

export const llmTraceApi = {
  traces(params: { purpose?: string; days?: number; limit?: number }): Promise<LlmTraceItem[]> {
    return api
      .get<ApiResponse<{ items: LlmTraceItem[] }>>('/llm/traces', { params })
      .then(r => (r.data as ApiResponse<{ items: LlmTraceItem[] }>).data.items)
  },
  summary(days = 30): Promise<LlmUsageRow[]> {
    return api
      .get<ApiResponse<{ items: LlmUsageRow[] }>>('/llm/usage/summary', { params: { days } })
      .then(r => (r.data as ApiResponse<{ items: LlmUsageRow[] }>).data.items)
  },
  overview(days = 30): Promise<LlmUsageOverview> {
    return api
      .get<ApiResponse<LlmUsageOverview>>('/llm/usage/overview', { params: { days } })
      .then(r => (r.data as ApiResponse<LlmUsageOverview>).data)
  },
}

/* ---------- W5a MCP 工具服务器 ---------- */


export interface McpServerItem {
  id: string
  yard_id: string
  name: string
  /** 掩码形态 https://host/***，明文永不出接口 */
  url_masked: string | null
  /** 鉴权头逐值掩码（同 API Key 口径） */
  headers_masked: Record<string, string>
  transport: string
  enabled: boolean
  consecutive_failures: number
  tool_count: number
  last_test_at: string | null
  last_error: string | null
  created_by: string | null
  created_at: string | null
  updated_at: string | null
}

export interface McpToolItem {
  name: string
  description: string
  cached_at?: string | null
}

export interface McpTestResult {
  ok: boolean
  tools: McpToolItem[]
  tool_count: number
  /** 只取一页：true 表示上游还有更多工具未入快照 */
  has_more: boolean
}

export interface McpServerCreatePayload {
  name: string
  url: string
  headers?: Record<string, string> | null
  transport?: string
  enabled?: boolean
}

export type McpServerPatchPayload = Partial<McpServerCreatePayload>

export const mcpApi = {
  list(): Promise<McpServerItem[]> {
    return unwrap(api.get<ApiResponse<McpServerItem[]>>('/settings/mcp-servers'))
  },
  create(payload: McpServerCreatePayload): Promise<McpServerItem> {
    return unwrap(api.post<ApiResponse<McpServerItem>>('/settings/mcp-servers', payload))
  },
  patch(serverId: string, payload: McpServerPatchPayload): Promise<McpServerItem> {
    return unwrap(api.patch<ApiResponse<McpServerItem>>(`/settings/mcp-servers/${serverId}`, payload))
  },
  remove(serverId: string): Promise<void> {
    return api.delete(`/settings/mcp-servers/${serverId}`).then(() => undefined)
  },
  /** 连接测试（initialize + tools/list 一页）：成功刷新工具快照 */
  test(serverId: string): Promise<McpTestResult> {
    return unwrap(api.post<ApiResponse<McpTestResult>>(`/settings/mcp-servers/${serverId}/test`))
  },
  /** 读取连接测试缓存的工具快照（不发起网络请求） */
  tools(serverId: string): Promise<{ tools: McpToolItem[]; tool_count: number }> {
    return unwrap(api.get<ApiResponse<{ tools: McpToolItem[]; tool_count: number }>>(`/settings/mcp-servers/${serverId}/tools`))
  },
}

/* ---------- 总控问答（W5b） ---------- */
