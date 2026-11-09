import { api } from './client'
import type { ApiResponse } from '@/types/api'
import type { ChatMediaAsset } from './chat'

/** 对话内媒体（v3.73）：单个资产状态轮询与当日额度。 */

export interface MediaQuota {
  kind: 'image' | 'video'
  limit: number
  used: number
  remaining: number | null
  seconds_used: number
  seconds_limit: number | null
  reset_at: string
}

export interface MediaUsage {
  image: MediaQuota
  video: MediaQuota
}

export const mediaApi = {
  async asset(assetId: string): Promise<ChatMediaAsset> {
    const res = await api.get<ApiResponse<ChatMediaAsset>>(`/media/assets/${assetId}`, {
      silent: true,
    } as never)
    return res.data.data
  },

  async usage(): Promise<MediaUsage> {
    const res = await api.get<ApiResponse<MediaUsage>>('/media/usage')
    return res.data.data
  },

  /** 签名地址转 blob：桌面壳与浏览器共用 saveBlobOrDownload */
  async download(url: string): Promise<Blob> {
    const base = '/api/v1'
    const path = url.startsWith(base) ? url.slice(base.length) : url
    const res = await api.get<Blob>(path, {
      responseType: 'blob',
      silent: true,
    } as never)
    return res.data
  },
}
