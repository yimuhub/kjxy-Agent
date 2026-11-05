/** 品牌与门户（白标，v3.67）：公开读 + 管理写 + 平台授权。 */
import { api } from './client'
import type { ApiResponse } from '@/types/api'

function unwrap<T>(promise: Promise<{ data: ApiResponse<T> }>): Promise<T> {
  return promise.then(res => res.data.data)
}

const silent = { skipErrorToast: true } as Record<string, unknown>

/** 公开品牌包：登录页与启动时用，匿名可读 */
export interface BrandingPublic {
  app_name: string
  short_name: string
  login_tagline: string
  footer_text: string
  primary_color: string
  /** 相对 API base 的取图路径；null 表示用文字 logo */
  logo_url: string | null
  /** 缓存戳：用它给 logo 地址加 ?v=，换图立即生效 */
  brand_version: number
}

export interface BrandingAdmin extends BrandingPublic {
  /** 门户对外域名（规范化后的 host，主域名在前） */
  domains: string[]
  raw: {
    app_name: string
    short_name: string
    login_tagline: string
    footer_text: string
    primary_color: string
  }
  defaults: {
    app_name: string
    short_name: string
    login_tagline: string
    footer_text: string
    primary_color: string
  }
  max_domains: number
  logo_max_mb: number
  updated_by: string
  updated_at: string | null
}

export interface BrandingUpdatePayload {
  app_name?: string
  short_name?: string
  login_tagline?: string
  footer_text?: string
  primary_color?: string
  domains?: string[]
}

export interface PlatformGrantTarget {
  user_id: string
  username: string
  display_name: string
  grants: string[]
}

export const brandingApi = {
  /** 匿名：登录页/启动时解析品牌 */
  public(): Promise<BrandingPublic> {
    return unwrap(api.get<ApiResponse<BrandingPublic>>('/branding', silent))
  },
  admin(): Promise<BrandingAdmin> {
    return unwrap(api.get<ApiResponse<BrandingAdmin>>('/branding/settings'))
  },
  update(payload: BrandingUpdatePayload): Promise<BrandingAdmin> {
    return unwrap(api.put<ApiResponse<BrandingAdmin>>('/branding/settings', payload))
  },
  uploadLogo(file: File): Promise<BrandingAdmin> {
    const form = new FormData()
    form.append('file', file)
    return unwrap(api.post<ApiResponse<BrandingAdmin>>('/branding/logo', form))
  },
  clearLogo(): Promise<BrandingAdmin> {
    return unwrap(api.delete<ApiResponse<BrandingAdmin>>('/branding/logo'))
  },
  grants(): Promise<{ items: PlatformGrantTarget[]; grantable: string[] }> {
    return unwrap(
      api.get<ApiResponse<{ items: PlatformGrantTarget[]; grantable: string[] }>>(
        '/platform/grants',
      ),
    )
  },
  setGrants(userId: string, grants: string[]): Promise<{ user_id: string; grants: string[] }> {
    return unwrap(
      api.put<ApiResponse<{ user_id: string; grants: string[] }>>(
        `/platform/grants/${userId}`,
        { grants },
      ),
    )
  },
}
