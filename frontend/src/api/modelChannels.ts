/**
 * 模型中心扩展接口：供应商多模型、官方 API 中转站、官方模型通道。
 *
 * 与 `modules.ts` 分离，避免把仍在并行开发中的接口混进主模块。
 * 所有字段均按「后端可能尚未返回」处理：类型全部可选，调用方必须兜底。
 */
import { isAxiosError, type AxiosRequestConfig, type AxiosResponse } from 'axios'
import { api } from './client'

/** 解包成功信封 {data, request_id} -> data；未包信封时原样返回 */
function unwrap<T>(promise: Promise<AxiosResponse<unknown>>): Promise<T> {
  return promise.then(res => {
    const body: unknown = res.data
    if (body && typeof body === 'object' && 'data' in body) {
      return (body as { data: T }).data
    }
    return body as T
  })
}

function asItems<T>(res: { items?: T[] } | T[] | null | undefined): T[] {
  if (Array.isArray(res)) return res
  return Array.isArray(res?.items) ? res.items : []
}

function asObject<T>(res: T | null | undefined): T {
  return (res ?? {}) as T
}

/** 官方通道未开通（503）与静默探测不弹全局错误 toast */
const silentConfig: AxiosRequestConfig & { skipErrorToast?: boolean } = { skipErrorToast: true }

// ---------------------------------------------------------------------------
// 模态 / 协议元数据（界面彩色标签与筛选共用）
// ---------------------------------------------------------------------------

export type TagType = 'primary' | 'success' | 'info' | 'warning' | 'danger'

export const MODALITY_META: Record<string, { label: string; type: TagType }> = {
  text: { label: '文本', type: 'primary' },
  image: { label: '图像', type: 'success' },
  video: { label: '视频', type: 'warning' },
  multimodal: { label: '多模态', type: 'danger' },
  embedding: { label: '嵌入', type: 'info' },
  rerank: { label: '重排', type: 'warning' },
  asr: { label: 'ASR', type: 'success' },
  tts: { label: 'TTS', type: 'primary' },
}

export const MODALITY_OPTIONS = Object.entries(MODALITY_META).map(([value, meta]) => ({
  value,
  label: meta.label,
}))

export function modalityKey(value?: string | null): string {
  return (value ?? '').trim().toLowerCase()
}

export function modalityMeta(value?: string | null): { label: string; type: TagType } {
  const key = modalityKey(value)
  const found = MODALITY_META[key]
  if (found) return found
  return { label: value?.trim() || '未标注', type: 'info' }
}

export const PROTOCOL_OPTIONS = [
  { value: 'openai_chat', label: 'OpenAI Chat' },
  { value: 'openai_responses', label: 'OpenAI Responses' },
  { value: 'anthropic_messages', label: 'Anthropic Messages' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'openai_embedding', label: 'OpenAI Embedding' },
  { value: 'openai_rerank', label: '重排（Rerank）' },
  { value: 'openai_asr', label: '语音识别（ASR）' },
  { value: 'openai_tts', label: '语音合成（TTS）' },
]

export function protocolLabel(value?: string | null): string {
  const key = (value ?? '').trim()
  if (!key) return '—'
  return PROTOCOL_OPTIONS.find(item => item.value === key)?.label ?? key
}

// ---------------------------------------------------------------------------
// 供应商模型
// ---------------------------------------------------------------------------

export interface ModelPricing {
  unit?: string | null
  input?: number | null
  output?: number | null
  currency?: string | null
}

/** 供应商下的单个模型；后端字段可能缺失，全部按可选处理 */
export interface SupplierModel {
  id?: string | null
  supplier_id?: string | null
  model_key?: string | null
  upstream_model_id?: string | null
  display_name?: string | null
  modality?: string | null
  protocol?: string | null
  input_modalities?: string[] | null
  output_modalities?: string[] | null
  capabilities?: Record<string, unknown> | null
  context_window?: number | null
  max_output_tokens?: number | null
  endpoint_path?: string | null
  pricing?: ModelPricing | null
  default_params?: Record<string, unknown> | null
  status?: number | string | null
  source?: string | null
  test_ok?: boolean | null
  latency_ms?: number | null
  last_error?: string | null
  last_test_at?: string | null
}

/** 新增 / 导入时的单模型载荷（对应契约里的数组元素） */
export interface SupplierModelInput {
  model_key: string
  upstream_model_id?: string
  display_name?: string
  modality?: string
  protocol?: string
  capabilities?: Record<string, unknown>
  context_window?: number | null
  max_output_tokens?: number | null
  pricing?: ModelPricing
}

export interface ModelImportPayload {
  schema_version: number
  models: SupplierModelInput[]
  /** 冲突是否覆盖：后端支持时生效，不支持则忽略（默认跳过） */
  overwrite?: boolean
}

export interface ProviderInline {
  name: string
  provider_type: string
  base_url: string
  api_key?: string
}

export interface ProviderImportPayload extends ModelImportPayload {
  supplier: ProviderInline
}

export type ImportAction = 'create' | 'update' | 'skip' | 'conflict' | 'error'

export interface ImportPreviewItem {
  action?: ImportAction | string
  model_key?: string
  error?: string | null
}

export interface ImportPreviewSummary {
  create?: number
  update?: number
  skip?: number
  conflict?: number
  error?: number
  [key: string]: number | undefined
}

export interface ImportPreviewResult {
  items?: ImportPreviewItem[]
  summary?: ImportPreviewSummary
}

export interface ImportCommitError {
  model_key?: string
  error?: string
}

export interface ImportCommitResult {
  supplier_id?: string
  created?: number
  updated?: number
  skipped?: number
  errors?: (string | ImportCommitError)[]
}

export type ModelImportResult = ImportPreviewResult & ImportCommitResult

export interface ModelTestResult {
  ok?: boolean
  latency_ms?: number | null
  message?: string | null
  detail?: string | null
}

export interface BatchModelTestResult extends ModelTestResult {
  model_id?: string
  model_key?: string
}

export interface ProbeModel {
  id: string
  label?: string
  guessed_modality?: string | null
}

export interface ProbeModelsResult {
  models?: ProbeModel[]
  source?: string
  message?: string
}

export interface SupplierModelUpdate {
  display_name?: string
  modality?: string
  protocol?: string
  capabilities?: Record<string, unknown>
  context_window?: number | null
  pricing?: ModelPricing
  status?: number
}

function dryRunConfig(dryRun: boolean): AxiosRequestConfig {
  return { params: { dry_run: dryRun ? 1 : 0 } }
}

export const supplierModelApi = {
  list(supplierId: string): Promise<SupplierModel[]> {
    return unwrap<{ items?: SupplierModel[] }>(
      api.get(`/llm/providers/${encodeURIComponent(supplierId)}/models`),
    ).then(asItems)
  },

  create(supplierId: string, payload: SupplierModelInput): Promise<SupplierModel> {
    return unwrap<SupplierModel>(
      api.post(`/llm/providers/${encodeURIComponent(supplierId)}/models`, payload),
    ).then(asObject)
  },

  update(
    supplierId: string,
    modelId: string,
    payload: SupplierModelUpdate,
  ): Promise<SupplierModel> {
    return unwrap<SupplierModel>(
      api.patch(
        `/llm/providers/${encodeURIComponent(supplierId)}/models/${encodeURIComponent(modelId)}`,
        payload,
      ),
    ).then(asObject)
  },

  remove(supplierId: string, modelId: string): Promise<void> {
    return api
      .delete(
        `/llm/providers/${encodeURIComponent(supplierId)}/models/${encodeURIComponent(modelId)}`,
      )
      .then(() => undefined)
  },

  /** 供应商内批量导入（dry_run=1 预览，dry_run=0 落库） */
  importModels(
    supplierId: string,
    payload: ModelImportPayload,
    dryRun: boolean,
  ): Promise<ModelImportResult> {
    return unwrap<ModelImportResult>(
      api.post(
        `/llm/providers/${encodeURIComponent(supplierId)}/models/import`,
        payload,
        dryRunConfig(dryRun),
      ),
    ).then(asObject)
  },

  /** 新建供应商并同时导入模型 */
  importProvider(payload: ProviderImportPayload, dryRun: boolean): Promise<ModelImportResult> {
    return unwrap<ModelImportResult>(
      api.post('/llm/providers/import', payload, dryRunConfig(dryRun)),
    ).then(asObject)
  },

  /** 从 URL（仅 https）拉取模型目录并导入 */
  importFromUrl(
    supplierId: string,
    url: string,
    dryRun: boolean,
    payload: { overwrite?: boolean } = {},
  ): Promise<ModelImportResult> {
    return unwrap<ModelImportResult>(
      api.post(
        `/llm/providers/${encodeURIComponent(supplierId)}/models/import-url`,
        { url, ...payload },
        dryRunConfig(dryRun),
      ),
    ).then(asObject)
  },

  test(supplierId: string, modelId: string): Promise<ModelTestResult> {
    return unwrap<ModelTestResult>(
      api.post(
        `/llm/providers/${encodeURIComponent(supplierId)}/models/${encodeURIComponent(modelId)}/test`,
      ),
    ).then(asObject)
  },

  batchTest(supplierId: string, modelIds: string[]): Promise<BatchModelTestResult[]> {
    return unwrap<{ items?: BatchModelTestResult[] }>(
      api.post(`/llm/providers/${encodeURIComponent(supplierId)}/models/batch-test`, {
        model_ids: modelIds,
      }),
    ).then(asItems)
  },

  /** 探测上游模型列表；guessed_modality 为本轮新增的模态猜测 */
  probe(payload: {
    provider_type?: string
    base_url?: string
    api_key?: string
    supplier_id?: string
  }): Promise<ProbeModelsResult> {
    return unwrap<ProbeModelsResult>(api.post('/llm/providers/probe-models', payload)).then(
      asObject,
    )
  },
}

// ---------------------------------------------------------------------------
// 官方 API 中转站
// ---------------------------------------------------------------------------

export type RelayChannelKind = 'official' | 'private'

export interface RelayChannel {
  id?: string
  name?: string
  kind?: string
  base_url?: string
  api_key_masked?: string | null
  public_key?: string | null
  catalog_path?: string | null
  status?: number | string | null
  model_count?: number | null
  last_sync_at?: string | null
  last_error?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface RelayChannelInput {
  name: string
  kind: RelayChannelKind
  base_url: string
  /** 编辑时留空表示不修改 */
  api_key?: string
  public_key?: string
  catalog_path?: string
}

export interface RelaySyncItem {
  model_key?: string
  action?: string
}

export interface RelaySyncResult {
  supplier_id?: string
  created?: number
  updated?: number
  removed?: number
  items?: RelaySyncItem[]
  source?: string
  message?: string
}

export const relayChannelApi = {
  list(): Promise<RelayChannel[]> {
    return unwrap<{ items?: RelayChannel[] }>(api.get('/llm/relay/channels')).then(asItems)
  },

  create(payload: RelayChannelInput): Promise<RelayChannel> {
    return unwrap<RelayChannel>(api.post('/llm/relay/channels', payload)).then(asObject)
  },

  update(id: string, payload: RelayChannelInput): Promise<RelayChannel> {
    return unwrap<RelayChannel>(
      api.patch(`/llm/relay/channels/${encodeURIComponent(id)}`, payload),
    ).then(asObject)
  },

  remove(id: string): Promise<void> {
    return api.delete(`/llm/relay/channels/${encodeURIComponent(id)}`).then(() => undefined)
  },

  sync(id: string): Promise<RelaySyncResult> {
    return unwrap<RelaySyncResult>(
      api.post(`/llm/relay/channels/${encodeURIComponent(id)}/sync`),
    ).then(asObject)
  },

  models(id: string, quiet = false): Promise<SupplierModel[]> {
    return unwrap<{ items?: SupplierModel[] }>(
      api.get(
        `/llm/relay/channels/${encodeURIComponent(id)}/models`,
        quiet ? silentConfig : undefined,
      ),
    ).then(asItems)
  },
}

// ---------------------------------------------------------------------------
// 官方模型通道
// ---------------------------------------------------------------------------

export interface OfficialStatus {
  enabled?: boolean
  message?: string
}

export interface OfficialModel {
  model_key?: string
  display_name?: string
  modality?: string
  capabilities?: Record<string, unknown> | null
  context_window?: number | null
  pricing?: ModelPricing | null
  trial_available?: boolean
  price?: number | null
  currency?: string | null
}

export interface OfficialPlan {
  code?: string
  model_key?: string
  name?: string
  billing_unit?: string
  price?: number | null
  currency?: string | null
  trial_tokens?: number | null
  trial_days?: number | null
}

export interface OfficialEntitlement {
  id?: string
  model_key?: string
  source?: string
  status?: string
  quota_total?: number | null
  quota_used?: number | null
  starts_at?: string | null
  expires_at?: string | null
}

export interface OfficialOrder {
  id?: string
  status?: string
  amount?: number
  currency?: string
  payment_url?: string | null
}

/** 官方通道未开通时后端统一 503 OFFICIAL_CHANNEL_DISABLED */
export function isOfficialDisabled(err: unknown): boolean {
  if (!isAxiosError(err)) return false
  if (err.response?.status === 503) return true
  const body = err.response?.data as { error?: { code?: string } } | undefined
  return body?.error?.code === 'OFFICIAL_CHANNEL_DISABLED'
}

export const officialApi = {
  status(): Promise<OfficialStatus> {
    return unwrap<OfficialStatus>(api.get('/llm/official/status', silentConfig)).then(asObject)
  },

  models(): Promise<OfficialModel[]> {
    return unwrap<{ items?: OfficialModel[] }>(
      api.get('/llm/official/models', silentConfig),
    ).then(asItems)
  },

  plans(): Promise<OfficialPlan[]> {
    return unwrap<{ items?: OfficialPlan[] }>(
      api.get('/llm/official/plans', silentConfig),
    ).then(asItems)
  },

  entitlements(): Promise<OfficialEntitlement[]> {
    return unwrap<{ items?: OfficialEntitlement[] }>(
      api.get('/llm/official/entitlements', silentConfig),
    ).then(asItems)
  },

  startTrial(modelKey: string, planCode: string): Promise<unknown> {
    return api
      .post('/llm/official/trials', { model_key: modelKey, plan_code: planCode }, silentConfig)
      .then(res => res.data)
  },

  createOrder(modelKey: string, planCode: string): Promise<OfficialOrder> {
    return unwrap<OfficialOrder>(
      api.post(
        '/llm/official/orders',
        { model_key: modelKey, plan_code: planCode },
        silentConfig,
      ),
    ).then(asObject)
  },
}
