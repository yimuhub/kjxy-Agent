/**
 * 设备中心 / 手机配对（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import { api, toParams, unwrap } from '../client'
import type {
  ApiResponse,
} from '@/types/api'

// ---------- 设备中心（物联网接入，A3） ----------

/** 指标字典条目 */
export interface DeviceMetric {
  code: string
  name: string
  unit: string
}

/** 通用 value/name 字典项（设备类型、接入方式、阈值方向） */
export interface DeviceDictItem {
  value: string
  name: string
}

export interface DeviceCapabilities {
  metrics: DeviceMetric[]
  device_types: DeviceDictItem[]
  source_types: DeviceDictItem[]
  ops?: DeviceDictItem[]
}

/** 连接器字段映射条目（厂商字段 → 统一指标） */
export interface DeviceFieldMapEntry {
  metric: string
  unit?: string | null
}

/** 连接器配置（写入形态：headers 为明文，入库加密） */
export interface DeviceConnectorIn {
  url: string
  method: 'GET' | 'POST'
  headers?: Record<string, string> | null
  body_template?: Record<string, unknown> | null
  interval_seconds: number
  json_path: string
  field_map: Record<string, DeviceFieldMapEntry>
  timeout_seconds: number
  time_field?: string | null
}

/** 连接器配置（读出形态：headers 一律掩码） */
export interface DeviceConnectorOut extends Omit<DeviceConnectorIn, 'headers'> {
  headers_masked: Record<string, string>
}

/** 每指标最新一条读数（仅 GET /devices/{id} 详情返回；列表接口没有） */
export interface DeviceLatestItem {
  metric: string
  metric_name: string
  value: number | null
  text: string | null
  unit: string | null
  collected_at: string
}

export interface Device {
  id: string
  yard_id: string
  name: string
  device_type: string
  source_type: string
  model_hint: string | null
  longitude: number | null
  latitude: number | null
  location_note: string | null
  enabled: boolean
  created_by: string | null
  created_at: string
  updated_at: string
  has_ingest_token: boolean
  connector: DeviceConnectorOut | null
  last_polled_at: string | null
  consecutive_failures: number
  last_error: string | null
  /** 仅详情接口返回 */
  latest?: DeviceLatestItem[]
  /** 最近一次分析摘要（v3.23；列表与详情都返回，未分析过为 null） */
  latest_analysis?: DeviceAnalysisBrief | null
}

export interface DeviceCreatePayload {
  name: string
  device_type: string
  source_type: 'webhook' | 'connector'
  model_hint?: string
  location_note?: string
  longitude?: number | null
  latitude?: number | null
  connector?: DeviceConnectorIn | null
}

export interface DevicePatchPayload {
  name?: string
  device_type?: string
  model_hint?: string | null
  longitude?: number | null
  latitude?: number | null
  location_note?: string | null
  enabled?: boolean
  connector?: DeviceConnectorIn | null
}

/** 读数组（GET readings → data[:] 裸数组，时间升序），raw_json 体积大不拉回 */
export interface DeviceReading {
  id: string
  metric: string
  value: number | null
  text: string | null
  unit: string | null
  collected_at: string
}

export interface DeviceAlertRule {
  id: string
  yard_id: string
  device_id: string | null
  metric: string
  op: 'lt' | 'gt'
  threshold: number
  enabled: boolean
  notify_note: string | null
  created_at: string
  updated_at: string
}

export interface DeviceAlertRuleCreate {
  metric: string
  op: 'lt' | 'gt'
  threshold: number
  device_id?: string | null
  enabled?: boolean
  notify_note?: string | null
}

export interface DeviceAlert {
  id: string
  yard_id: string
  rule_id: string | null
  device_id: string
  device_name: string | null
  metric: string
  metric_name: string | null
  value: number | null
  triggered_at: string
  notified_text: string | null
  /** 一键工单：tasks 任务 id；null=尚未开工单（workorder_no 为展示编号，当前同值） */
  workorder_id: string | null
  workorder_no: string | null
}

export interface AlertWorkorder {
  workorder_id: string
  workorder_no: string
  status: string
  created: boolean
  link: string
  message: string
}

/* ---------- 设备分析（v3.23） ---------- */

export type DeviceAnalysisLevel = 'info' | 'watch' | 'warning' | 'critical'

export interface DeviceAnalysisFinding {
  code: string
  level: DeviceAnalysisLevel
  metric: string
  metric_name: string
  title: string
  detail: string
  evidence: { at: string; value: number; unit: string }[]
  suggestion: string
  rule: string
}

export interface DeviceAnalysisQuality {
  score: number
  coverage: number
  points: number
  hours: number
  covered_hours: number
  battery: number | null
  notes: string[]
  stuck: { metric: string; value: number; points: number }[]
  out_of_range: { metric: string; value: number; at: string }[]
}

export interface DeviceAnalysisBrief {
  analysis_id: string
  status: 'ok' | 'insufficient' | 'failed'
  level: DeviceAnalysisLevel
  level_label: string
  summary: string
  findings_count: number
  created_at: string
}

export interface DeviceAnalysis extends DeviceAnalysisBrief {
  yard_id: string
  device_id: string
  device_name: string
  window_from: string
  window_to: string
  granularity: string
  findings: DeviceAnalysisFinding[]
  metrics: Record<string, { metric_name: string; unit: string; points: number; mean: number; min: number; max: number; change: number }>
  quality: DeviceAnalysisQuality
  model: string | null
  tokens_used: number
  trigger: 'manual' | 'schedule'
  /** 仅 POST 返回 */
  created?: boolean
}

export interface DeviceInsights {
  items: {
    analysis_id: string
    device_id: string
    device_name: string
    level: DeviceAnalysisLevel
    level_label: string
    summary: string
    findings_count: number
    top_finding: string
    created_at: string
  }[]
  counts: Partial<Record<DeviceAnalysisLevel, number>>
}

export const deviceApi = {
  /** 能力字典：指标 18 项 + 设备类型 + 接入方式 + 阈值方向 */
  capabilities(): Promise<DeviceCapabilities> {
    return unwrap(api.get<ApiResponse<DeviceCapabilities>>('/devices/capabilities'))
  },
  /** 设备列表（无 latest；最新值需逐台调 detail 补齐） */
  list(): Promise<Device[]> {
    return unwrap(api.get<ApiResponse<Device[]>>('/devices'))
  },
  /** 设备详情（含 latest + 连接器掩码配置） */
  detail(device_id: string): Promise<Device> {
    return unwrap(api.get<ApiResponse<Device>>(`/devices/${device_id}`))
  },
  /** 新建：webhook 时 data.device_token 一次性明文，仅本次响应出现 */
  create(payload: DeviceCreatePayload): Promise<Device & { device_token?: string }> {
    return unwrap(api.post<ApiResponse<Device & { device_token?: string }>>('/devices', payload))
  },
  patch(device_id: string, payload: DevicePatchPayload): Promise<Device> {
    return unwrap(api.patch<ApiResponse<Device>>(`/devices/${device_id}`, payload))
  },
  remove(device_id: string): Promise<void> {
    return api.delete(`/devices/${device_id}`).then(() => undefined)
  },
  /** 重新生成设备令牌：data.device_token 同样只展示这一次 */
  rotateToken(device_id: string): Promise<{ device_token: string }> {
    return unwrap(api.post<ApiResponse<{ device_token: string }>>(`/devices/${device_id}/rotate-token`))
  },
  /** 读数（时间升序裸数组；limit 上限 1000） */
  readings(
    device_id: string,
    opts: { metric?: string; start?: string; end?: string; limit?: number } = {},
  ): Promise<DeviceReading[]> {
    return unwrap(
      api.get<ApiResponse<DeviceReading[]>>(`/devices/${device_id}/readings`, {
        params: toParams(opts),
      }),
    )
  },
  rules(): Promise<DeviceAlertRule[]> {
    return unwrap(api.get<ApiResponse<DeviceAlertRule[]>>('/devices/alert-rules'))
  },
  createRule(payload: DeviceAlertRuleCreate): Promise<DeviceAlertRule> {
    return unwrap(api.post<ApiResponse<DeviceAlertRule>>('/devices/alert-rules', payload))
  },
  patchRule(rule_id: string, payload: Partial<DeviceAlertRuleCreate>): Promise<DeviceAlertRule> {
    return unwrap(api.patch<ApiResponse<DeviceAlertRule>>(`/devices/alert-rules/${rule_id}`, payload))
  },
  removeRule(rule_id: string): Promise<void> {
    return api.delete(`/devices/alert-rules/${rule_id}`).then(() => undefined)
  },
  alerts(limit = 50): Promise<DeviceAlert[]> {
    return unwrap(
      api.get<ApiResponse<DeviceAlert[]>>('/devices/alerts', { params: { limit } }),
    )
  },
  /** 告警一键开工单（全员可用；幂等——已开过的返回既有工单号 created=false） */
  createAlertWorkorder(alert_id: string): Promise<AlertWorkorder> {
    return unwrap(
      api.post<ApiResponse<AlertWorkorder>>(`/devices/alerts/${alert_id}/create-workorder`),
    )
  },
  /** 设备分析历史（时间倒序） */
  analyses(device_id: string, limit = 20): Promise<DeviceAnalysis[]> {
    return unwrap(
      api.get<ApiResponse<DeviceAnalysis[]>>(`/devices/${device_id}/analyses`, {
        params: { limit },
      }),
    )
  },
  /** 立即分析（管理员）：同一天重复运行幂等返回既有结果 created=false */
  runAnalysis(device_id: string): Promise<DeviceAnalysis> {
    return unwrap(
      api.post<ApiResponse<DeviceAnalysis>>(`/devices/${device_id}/analyses`, {}),
    )
  },
  /** 概览卡：只回需要人处理的设备发现 */
  insights(limit = 10): Promise<DeviceInsights> {
    return unwrap(api.get<ApiResponse<DeviceInsights>>('/devices/insights', { params: { limit } }))
  },
}

/* ---------- W4 模型调用观测 ---------- */


// ---------- 手机配对（Wave B：扫码接入局域网） ----------

export interface PairDeviceOut {
  id: string
  name: string
  created_at: string
  expires_at: string
  last_seen_at: string | null
  revoked_at: string | null
}

export const pairingApi = {
  createCode(): Promise<{ pair_url: string; expires_in: number }> {
    return unwrap(api.post<ApiResponse<{ pair_url: string; expires_in: number }>>('/pairing/codes'))
  },
  devices(): Promise<PairDeviceOut[]> {
    return unwrap(api.get<ApiResponse<PairDeviceOut[]>>('/pairing/devices'))
  },
  revoke(deviceId: string): Promise<PairDeviceOut> {
    return unwrap(api.post<ApiResponse<PairDeviceOut>>(`/pairing/devices/${deviceId}/revoke`))
  },
}
