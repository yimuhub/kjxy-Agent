import type { AxiosResponse } from 'axios'
import { api } from './client'
import type { ApiResponse } from '@/types/api'

/** 语音转写（v3.30）：本机模型 + 在线通道的统一状态与操作。 */

function unwrap<T>(promise: Promise<AxiosResponse<ApiResponse<T>>>): Promise<T> {
  return promise.then(res => res.data.data)
}

export type AsrMode = 'auto' | 'local' | 'online' | 'off'
export type AsrChannel = 'local' | 'online' | 'none'

export interface AsrPlatformInfo {
  os: string
  arch: string
  python: string
  engine: string
  engine_available: boolean
  engine_note: string
}

export interface AsrDownloadState {
  status: 'idle' | 'downloading' | 'verifying' | 'ready' | 'error' | 'cancelled' | 'broken'
  downloaded: number
  total: number
  progress: number
  message: string
  started_at?: string
  finished_at?: string
  installed_bytes?: number
  ready?: boolean
}

export interface AsrModel {
  id: string
  name: string
  description: string
  languages: string[]
  min_ram_mb: number
  license: string
  tier: 'recommended' | 'high_accuracy'
  engine: string
  /** 解码器家族：paraformer（轻量）/ sense-voice（带标点与数字规整） */
  decoder?: string
  size_bytes: number
}

export interface AsrModelStatus extends AsrModel {
  state: AsrDownloadState
}

export interface AsrStatus {
  mode: AsrMode
  settings: { mode: AsrMode; local_model_id: string }
  platform: AsrPlatformInfo
  local: {
    model: AsrModel
    state: AsrDownloadState
    engine_available: boolean
    usable: boolean
  }
  online: {
    available: boolean
    provider?: string
    model?: string
    level?: string
    explicit?: boolean
  }
  effective: AsrChannel
  models: AsrModelStatus[]
  warnings: string[]
}

export interface AsrTestResult {
  ok: boolean
  text: string
  engine: 'local' | 'online'
  model: string
}

export const asrApi = {
  status(): Promise<AsrStatus> {
    return unwrap(api.get<ApiResponse<AsrStatus>>('/asr/status'))
  },
  models(): Promise<AsrModel[]> {
    return unwrap(api.get<ApiResponse<{ models: AsrModel[] }>>('/asr/models')).then(r => r.models)
  },
  startDownload(modelId: string): Promise<AsrDownloadState> {
    return unwrap(api.post<ApiResponse<AsrDownloadState>>(`/asr/models/${modelId}/download`))
  },
  cancelDownload(modelId: string): Promise<AsrDownloadState> {
    return unwrap(api.post<ApiResponse<AsrDownloadState>>(`/asr/models/${modelId}/cancel`))
  },
  remove(modelId: string): Promise<{ removed: boolean; model_id: string }> {
    return unwrap(api.delete<ApiResponse<{ removed: boolean; model_id: string }>>(`/asr/models/${modelId}`))
  },
  test(file: File): Promise<AsrTestResult> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(
      api.post<ApiResponse<AsrTestResult>>('/asr/test', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      }),
    )
  },
}
