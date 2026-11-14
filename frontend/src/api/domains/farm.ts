/**
 * 农业台账 / 一句话业务 / 作物诊断 / 告警与工单 / 跨院分享 / Agent 自动执行（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import type { AxiosRequestConfig } from 'axios'
import { api, unwrap, unwrapList } from '../client'
import type { ListResult } from '../client'
import type {
  ApiResponse,
  ListResponse,
} from '@/types/api'

// ---------- 农业台账（v3.8） ----------

export interface FarmPlot {
  plot_id: string
  name: string
  crop: string
  area_mu: number | null
  location: string
  soil_type: string
  irrigation_type: string
  status: 'active' | 'fallow' | 'archived'
  notes: string
  /** 乐观并发版本（v3.19）：更新时回传 expected_revision 以检测冲突 */
  revision: number
  created_at: string
  updated_at: string
}

export interface FarmCycle {
  cycle_id: string
  plot_id: string
  crop: string
  variety: string
  planted_on: string | null
  expected_harvest_on: string | null
  status: 'growing' | 'harvested' | 'failed'
  notes: string
  revision: number
  created_at: string
  updated_at: string
}

export type FarmRecordKind =
  | 'sowing'
  | 'fertilizing'
  | 'irrigation'
  | 'pesticide'
  | 'weeding'
  | 'scouting'
  | 'harvest'
  | 'other'

export interface FarmRecord {
  record_id: string
  plot_id: string
  plot_name: string
  cycle_id: string | null
  kind: FarmRecordKind
  kind_label: string
  occurred_on: string
  title: string
  content: string
  quantity: number | null
  unit: string
  operator_name: string
  metadata_json: Record<string, unknown> | null
  /** manual=人工录入；agent=Agent 自动执行（界面标「Agent 自动」） */
  source?: 'manual' | 'agent' | 'device'
  agent_run_id?: string | null
  revision: number
  created_at: string
  updated_at: string
}

export interface FarmReport {
  title: string
  kind: 'log' | 'weekly' | 'quarterly'
  start: string
  end: string
  records: number
  plots: number
  content_md: string
  stats: { by_kind: Record<string, number>; by_plot: Record<string, number> }
}

export interface FarmRecordQuery {
  plot_id?: string
  kind?: string
  start?: string
  end?: string
  page?: number
  page_size?: number
}

export const farmApi = {
  plots(includeArchived = false): Promise<FarmPlot[]> {
    return unwrap(api.get<ApiResponse<FarmPlot[]>>('/farm/plots', { params: { include_archived: includeArchived } }))
  },
  createPlot(payload: Partial<FarmPlot> & { name: string }): Promise<FarmPlot> {
    return unwrap(api.post<ApiResponse<FarmPlot>>('/farm/plots', payload))
  },
  updatePlot(plotId: string, payload: Partial<FarmPlot>): Promise<FarmPlot> {
    return unwrap(api.patch<ApiResponse<FarmPlot>>(`/farm/plots/${plotId}`, payload))
  },
  deletePlot(plotId: string): Promise<unknown> {
    return unwrap(api.delete(`/farm/plots/${plotId}`))
  },
  cycles(plotId: string): Promise<FarmCycle[]> {
    return unwrap(api.get<ApiResponse<FarmCycle[]>>(`/farm/plots/${plotId}/cycles`))
  },
  createCycle(plotId: string, payload: Partial<FarmCycle> & { crop: string }): Promise<FarmCycle> {
    return unwrap(api.post<ApiResponse<FarmCycle>>(`/farm/plots/${plotId}/cycles`, payload))
  },
  updateCycle(cycleId: string, payload: Partial<FarmCycle>): Promise<FarmCycle> {
    return unwrap(api.patch<ApiResponse<FarmCycle>>(`/farm/cycles/${cycleId}`, payload))
  },
  deleteCycle(cycleId: string): Promise<unknown> {
    return unwrap(api.delete(`/farm/cycles/${cycleId}`))
  },
  records(query: FarmRecordQuery = {}): Promise<ListResult<FarmRecord>> {
    return unwrapList(api.get<ListResponse<FarmRecord>>('/farm/records', { params: query }))
  },
  createRecord(payload: Partial<FarmRecord> & { plot_id: string; kind: string; occurred_on: string }): Promise<FarmRecord> {
    return unwrap(api.post<ApiResponse<FarmRecord>>('/farm/records', payload))
  },
  updateRecord(recordId: string, payload: Partial<FarmRecord>): Promise<FarmRecord> {
    return unwrap(api.patch<ApiResponse<FarmRecord>>(`/farm/records/${recordId}`, payload))
  },
  deleteRecord(recordId: string): Promise<unknown> {
    return unwrap(api.delete(`/farm/records/${recordId}`))
  },
  report(payload: { kind: string; start: string; end: string; plot_id?: string }): Promise<FarmReport> {
    return unwrap(api.post<ApiResponse<FarmReport>>('/farm/reports', payload))
  },
}


// ---------- 一句话业务（v3.22） ----------

export type QuickModule = 'farm' | 'diagnosis' | 'work_order'

export interface QuickActionPreview {
  module: QuickModule
  /** plot.create | record.create | diagnosis.create | work_order.create | alert.convert */
  action: string
  payload: Record<string, unknown>
  summary: string
  missing: string[]
  ready: boolean
  source: 'rule' | 'llm' | string
  fingerprint: string
}

export interface QuickActionConfirmResult {
  module: QuickModule
  action: string
  created: Record<string, unknown>
  summary: string
}

export const quickActionApi = {
  /** 只解析不落库；缺字段通过 missing 返回 */
  preview(payload: {
    module: QuickModule
    text: string
    plot_id?: string
    today?: string
  }): Promise<QuickActionPreview> {
    return unwrap(api.post<ApiResponse<QuickActionPreview>>('/quick-actions/preview', payload))
  },
  /** 确认落库：指纹必须与预览一致，服务端仍走原有权限与校验 */
  confirm(payload: {
    module: QuickModule
    action: string
    payload: Record<string, unknown>
    fingerprint: string
  }): Promise<QuickActionConfirmResult> {
    return unwrap(api.post<ApiResponse<QuickActionConfirmResult>>('/quick-actions/confirm', payload))
  },
}


// ---------- 作物诊断闭环（v3.9） ----------

export type DiagnosisStatus =
  | 'open'
  | 'agent_diagnosed'
  | 'expert_review'
  | 'confirmed'
  | 'closed'

export interface DiagnosisAttachment {
  attachment_id: string
  filename: string
  mime: string
  kind: 'image' | 'document'
  size_bytes: number
  chars: number
  error: string | null
  url: string
  created_at: string
}

export interface FarmDiagnosisSummary {
  diagnosis_id: string
  plot_id: string | null
  plot_name: string
  crop: string
  growth_stage: string
  status: DiagnosisStatus
  source: 'manual' | 'device' | 'agent'
  device_id: string | null
  device_name: string | null
  confidence: number
  needs_expert_review: boolean
  top_candidate: string | null
  top_confidence: number | null
  vision_status: string
  created_at: string
}

export interface FarmDiagnosisCandidate {
  name: string
  confidence: number
  matched_symptoms: string[]
  notes: string
}

export interface FarmDiagnosisEvidence {
  document_id: string
  chunk_id: string
  title: string
  source: string
  page_number: number | null
  quote: string
  score: number | null
}

export interface FarmDiagnosisOutput {
  candidates: FarmDiagnosisCandidate[]
  evidence: FarmDiagnosisEvidence[]
  needs_info: string[]
  risk_notes: string[]
  next_steps: string[]
  needs_expert_review: boolean
  medication_intent: boolean
  confidence: number
  disclaimer: string
}

export interface FarmFollowUp {
  followup_id: string
  diagnosis_id: string
  occurred_on: string
  note: string
  image_refs: string[] | null
  created_by: string | null
  created_at: string
}

export interface FarmDiagnosis
  extends Omit<FarmDiagnosisSummary, 'top_candidate' | 'top_confidence'> {
  variety: string
  region: string
  symptoms: string
  image_refs: string[] | null
  attachments: DiagnosisAttachment[]
  environment: Record<string, unknown> | null
  observed_at: string | null
  output: FarmDiagnosisOutput | null
  medication_guard: {
    requires_expert_confirmation: boolean
    registered_products: {
      pesticide_id: string
      name: string
      active_ingredient: string
      pre_harvest_interval_days: number
    }[]
    note: string
  } | null
  expert_reviewer_id: string | null
  expert_note: string
  final_diagnosis: string
  expert_reviewed_at: string | null
  follow_ups: FarmFollowUp[]
  updated_at: string
}

export interface PesticideProduct {
  pesticide_id: string
  name: string
  active_ingredient: string
  registered_crops: string[] | null
  pre_harvest_interval_days: number
  label_notes: string
  status: 'active' | 'draft' | 'archived'
  source?: string
  created_at: string
  updated_at: string
}

export const diagnosisApi = {
  create(payload: {
    crop: string
    symptoms: string
    plot_id?: string
    variety?: string
    growth_stage?: string
    region?: string
    environment?: Record<string, unknown>
    image_refs?: string[]
    attachment_ids?: string[]
  }): Promise<FarmDiagnosis> {
    return unwrap(api.post<ApiResponse<FarmDiagnosis>>('/farm/diagnoses', payload))
  },
  /** 上传诊断材料：图片与文档/PDF 可多选，先拿附件 id 再创建诊断 */
  uploadAttachments(files: File[]): Promise<DiagnosisAttachment[]> {
    const form = new FormData()
    for (const file of files) form.append('files', file)
    return unwrap(
      api.post<ApiResponse<DiagnosisAttachment[]>>('/farm/diagnoses/attachments', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
  },
  deleteAttachment(attachmentId: string): Promise<void> {
    return api
      .delete<ApiResponse<unknown>>(`/farm/diagnoses/attachments/${attachmentId}`)
      .then(() => undefined)
  },
  /** 带登录态取附件原文件（图片预览 / 文档下载） */
  async fetchAttachmentBlob(attachmentId: string): Promise<Blob> {
    const res = await api.get<Blob>(`/farm/diagnoses/attachments/${attachmentId}/file`, {
      responseType: 'blob',
    })
    return res.data
  },
  /** 引用已对接设备：由设备最新读数/分析自动生成诊断（source=device） */
  createFromDevice(payload: {
    device_id: string
    crop: string
    plot_id?: string
    variety?: string
    growth_stage?: string
    region?: string
    symptoms?: string
    attachment_ids?: string[]
  }): Promise<FarmDiagnosis> {
    return unwrap(api.post<ApiResponse<FarmDiagnosis>>('/farm/diagnoses/from-device', payload))
  },
  list(query: { status?: string; plot_id?: string; page?: number; page_size?: number } = {}): Promise<ListResult<FarmDiagnosisSummary>> {
    return unwrapList(api.get<ListResponse<FarmDiagnosisSummary>>('/farm/diagnoses', { params: query }))
  },
  detail(diagnosisId: string): Promise<FarmDiagnosis> {
    return unwrap(api.get<ApiResponse<FarmDiagnosis>>(`/farm/diagnoses/${diagnosisId}`))
  },
  review(
    diagnosisId: string,
    payload: { expert_note: string; final_diagnosis?: string; status?: 'confirmed' | 'closed' },
  ): Promise<FarmDiagnosis> {
    return unwrap(api.post<ApiResponse<FarmDiagnosis>>(`/farm/diagnoses/${diagnosisId}/review`, payload))
  },
  followUp(diagnosisId: string, payload: { occurred_on: string; note: string; image_refs?: string[] }): Promise<FarmFollowUp> {
    return unwrap(api.post<ApiResponse<FarmFollowUp>>(`/farm/diagnoses/${diagnosisId}/follow-ups`, payload))
  },
  pesticides(): Promise<PesticideProduct[]> {
    return unwrap(api.get<ApiResponse<PesticideProduct[]>>('/farm/pesticides'))
  },
  createPesticide(payload: Partial<PesticideProduct> & { name: string }): Promise<PesticideProduct> {
    return unwrap(api.post<ApiResponse<PesticideProduct>>('/farm/pesticides', payload))
  },
  updatePesticide(pesticideId: string, payload: Partial<PesticideProduct>): Promise<PesticideProduct> {
    return unwrap(api.patch<ApiResponse<PesticideProduct>>(`/farm/pesticides/${pesticideId}`, payload))
  },
  deletePesticide(pesticideId: string): Promise<unknown> {
    return unwrap(api.delete(`/farm/pesticides/${pesticideId}`))
  },
  safeHarvest(pesticideId: string, appliedOn: string): Promise<{ earliest_harvest_on: string; pre_harvest_interval_days: number; note: string }> {
    return unwrap(api.get(`/farm/pesticides/${pesticideId}/safe-harvest`, { params: { applied_on: appliedOn } }))
  },
  consult(
    diagnosisId: string,
    payload: { expert_id?: string; note?: string } = {},
  ): Promise<{ diagnosis: FarmDiagnosis; work_order: WorkOrder }> {
    return unwrap(api.post<ApiResponse<{ diagnosis: FarmDiagnosis; work_order: WorkOrder }>>(`/farm/diagnoses/${diagnosisId}/consult`, payload))
  },
}


// ---------- 告警与工单闭环（v3.10） ----------

export type AlertSeverity = 'low' | 'normal' | 'high' | 'critical'
export type WorkOrderPriority = 'low' | 'normal' | 'high' | 'urgent'
export type WorkOrderStatus = 'open' | 'assigned' | 'in_progress' | 'done' | 'closed' | 'cancelled'

export interface FarmAlert {
  alert_id: string
  title: string
  detail: string
  severity: AlertSeverity
  source: string
  plot_id: string | null
  status: 'new' | 'converted' | 'dismissed'
  work_order_id: string | null
  agent_run_id?: string | null
  created_at: string
}

export interface WorkOrderEvent {
  event_id: string
  kind: string
  note: string
  actor_id: string | null
  from_status: string | null
  to_status: string | null
  created_at: string
}

export interface WorkOrder {
  work_order_id: string
  title: string
  description: string
  priority: WorkOrderPriority
  status: WorkOrderStatus
  source: string
  source_ref: string | null
  plot_id: string | null
  assignee_id: string | null
  assignee_name: string
  due_on: string | null
  revision?: number
  escalation_level: number
  resolution: string
  closed_at: string | null
  agent_run_id?: string | null
  created_at: string
  updated_at: string
  events: WorkOrderEvent[]
}

export interface AssignableMember {
  user_id: string
  display_name: string
  role: string
}

export const workOrderApi = {
  alerts(query: { status?: string; severity?: string; page?: number; page_size?: number } = {}): Promise<ListResult<FarmAlert>> {
    return unwrapList(api.get<ListResponse<FarmAlert>>('/farm/alerts', { params: query }))
  },
  createAlert(payload: { title: string; detail?: string; severity?: AlertSeverity; source?: string; plot_id?: string }): Promise<FarmAlert> {
    return unwrap(api.post<ApiResponse<FarmAlert>>('/farm/alerts', payload))
  },
  convertAlert(alertId: string): Promise<WorkOrder> {
    return unwrap(api.post<ApiResponse<WorkOrder>>(`/farm/alerts/${alertId}/convert`))
  },
  assignable(): Promise<AssignableMember[]> {
    return unwrap(api.get<ApiResponse<AssignableMember[]>>('/farm/work-orders/assignable'))
  },
  list(query: { status?: string; priority?: string; assignee_id?: string; page?: number; page_size?: number } = {}): Promise<ListResult<WorkOrder>> {
    return unwrapList(api.get<ListResponse<WorkOrder>>('/farm/work-orders', { params: query }))
  },
  create(payload: {
    title: string
    description?: string
    priority?: WorkOrderPriority
    plot_id?: string
    assignee_id?: string
    due_on?: string
  }): Promise<WorkOrder> {
    return unwrap(api.post<ApiResponse<WorkOrder>>('/farm/work-orders', payload))
  },
  detail(orderId: string): Promise<WorkOrder> {
    return unwrap(api.get<ApiResponse<WorkOrder>>(`/farm/work-orders/${orderId}`))
  },
  update(
    orderId: string,
    payload: { status?: WorkOrderStatus; priority?: WorkOrderPriority; assignee_id?: string; due_on?: string; description?: string },
  ): Promise<WorkOrder> {
    return unwrap(api.patch<ApiResponse<WorkOrder>>(`/farm/work-orders/${orderId}`, payload))
  },
  comment(orderId: string, note: string): Promise<WorkOrder> {
    return unwrap(api.post<ApiResponse<WorkOrder>>(`/farm/work-orders/${orderId}/comments`, { note }))
  },
  receipt(orderId: string, payload: { resolution: string; close?: boolean }): Promise<WorkOrder> {
    return unwrap(api.post<ApiResponse<WorkOrder>>(`/farm/work-orders/${orderId}/receipt`, payload))
  },
  escalate(orderId: string): Promise<WorkOrder> {
    return unwrap(api.post<ApiResponse<WorkOrder>>(`/farm/work-orders/${orderId}/escalate`))
  },
}


// ---------- 跨院只读分享（v3.12） ----------

export type ShareResourceType = 'farm_report' | 'diagnosis'

export interface ShareLink {
  share_id: string
  resource_type: ShareResourceType
  resource_id: string
  title: string
  expires_at: string
  revoked_at: string | null
  view_count: number
  last_viewed_at: string | null
  created_at: string
}

export interface CreatedShareLink extends ShareLink {
  token: string
  path: string
}

export interface PublicShare {
  title: string
  resource_type: ShareResourceType
  content_md: string
  created_at: string
  expires_at: string
  view_count: number
}

export const shareApi = {
  create(payload: {
    resource_type: ShareResourceType
    expires_days?: number
    report_kind?: 'log' | 'weekly' | 'quarterly'
    start?: string
    end?: string
    plot_id?: string
    diagnosis_id?: string
  }): Promise<CreatedShareLink> {
    return unwrap(api.post<ApiResponse<CreatedShareLink>>('/shares', payload))
  },
  list(includeRevoked = false): Promise<ShareLink[]> {
    return unwrap(api.get<ApiResponse<ShareLink[]>>('/shares', { params: { include_revoked: includeRevoked } }))
  },
  revoke(shareId: string): Promise<ShareLink> {
    return unwrap(api.delete<ApiResponse<ShareLink>>(`/shares/${shareId}`))
  },
  /** 匿名读取分享快照（无需登录；令牌失效统一 404，由页面自身提示，不弹全局 toast） */
  publicView(token: string): Promise<PublicShare> {
    return unwrap(
      api.get<ApiResponse<PublicShare>>(`/public/shares/${token}`, {
        skipErrorToast: true,
      } as AxiosRequestConfig & { skipErrorToast?: boolean }),
    )
  },
}


// ---------- Agent 自动执行（v3.28） ----------

export type FarmAgentActionKind = 'alert' | 'work_order' | 'diagnosis' | 'record' | 'pesticide' | 'plot'
export type FarmAgentActionStatus = 'pending' | 'executed' | 'dismissed' | 'failed'
export type FarmAgentPolicy = 'auto' | 'confirm' | 'off'

export interface FarmAgentAction {
  id: string
  run_id: string
  kind: FarmAgentActionKind
  kind_label: string
  rule_code: string
  rule_label: string
  status: FarmAgentActionStatus
  policy: FarmAgentPolicy
  severity: AlertSeverity | 'info' | 'watch' | 'warning'
  title: string
  detail: string
  payload: Record<string, unknown>
  target_type: string | null
  target_id: string | null
  dedupe_key: string
  error: string | null
  confirmed_by: string | null
  confirmed_at: string | null
  created_at: string
}

export interface FarmAgentFinding {
  code: string
  rule_label: string
  level: 'info' | 'watch' | 'warning' | 'critical'
  level_label: string
  title: string
  detail: string
  suggestion: string
  evidence: Record<string, unknown>[]
  dedupe_key: string
  actions: { kind: FarmAgentActionKind | string; kind_label?: string; title: string }[]
}

export interface FarmAgentRun {
  run_id: string
  trigger: 'manual' | 'schedule'
  status: 'running' | 'ok' | 'insufficient' | 'failed'
  level: 'info' | 'watch' | 'warning' | 'critical'
  level_label: string
  window_from: string | null
  window_to: string | null
  summary: string
  evidence: {
    devices?: number
    device_names?: string[]
    plots?: number
    records_30d?: number
    pesticides?: number
    weather_provider?: string | null
    warnings?: string[]
  }
  findings: FarmAgentFinding[]
  model: string | null
  tokens_used: number
  error: string | null
  started_at: string | null
  finished_at: string | null
  duration_ms: number
  created_at: string
  actions?: FarmAgentAction[]
}

export interface FarmAgentOverview {
  enabled: boolean
  schedule: { preset?: string }
  actions: Record<FarmAgentActionKind, FarmAgentPolicy>
  weather_provider: string
  weather_configured: boolean
  dedupe_hours: number
  last_run: FarmAgentRun | null
  pending_count: number
  pending: FarmAgentAction[]
  today_executed: number
}

/** 天气摘要（v3.70）：桌宠/概览轮播用；未配置供应商时 configured=false */
export interface FarmWeatherBrief {
  configured: boolean
  brief: {
    temp_max: number | null
    temp_min: number | null
    rain_mm: number | null
    wind_max_ms: number | null
    humidity_mean: number | null
    tomorrow_temp_min: number | null
    generated_at: string | null
  } | null
  warning: string | null
}

export const farmAgentApi = {
  overview(): Promise<FarmAgentOverview> {
    return unwrap(api.get<ApiResponse<FarmAgentOverview>>('/farm/automation/overview'))
  },
  weatherBrief(): Promise<FarmWeatherBrief> {
    return unwrap(api.get<ApiResponse<FarmWeatherBrief>>('/farm/weather/brief'))
  },
  runs(page = 1, pageSize = 20): Promise<ListResult<FarmAgentRun>> {
    return unwrapList(
      api.get<ListResponse<FarmAgentRun>>('/farm/automation/runs', {
        params: { page, page_size: pageSize },
      }),
    )
  },
  run(runId: string): Promise<FarmAgentRun> {
    return unwrap(api.get<ApiResponse<FarmAgentRun>>(`/farm/automation/runs/${runId}`))
  },
  runNow(): Promise<FarmAgentRun> {
    return unwrap(api.post<ApiResponse<FarmAgentRun>>('/farm/automation/run', {}))
  },
  confirm(actionId: string): Promise<FarmAgentAction> {
    return unwrap(
      api.post<ApiResponse<FarmAgentAction>>(`/farm/automation/actions/${actionId}/confirm`, {}),
    )
  },
  dismiss(actionId: string): Promise<FarmAgentAction> {
    return unwrap(
      api.post<ApiResponse<FarmAgentAction>>(`/farm/automation/actions/${actionId}/dismiss`, {}),
    )
  },
}
