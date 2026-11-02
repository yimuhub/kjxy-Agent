/**
 * 指挥官产物的打开方式（v3.52）。
 *
 * 后端把文档产物写成 `/chat/sessions/{sid}/attachments/{aid}/download` 这类
 * **API 相对路径**（挂在 /api/v1 下、且要带登录态）。直接当 href 用会落进前端路由的
 * 404 页「页面不存在」，所以文档一律走应用内预览/下载，只有真外链才渲染成 <a>。
 */

export interface ArtifactLike {
  url?: string
  href?: string
  title?: string
  name?: string
  filename?: string
  attachment_id?: string
  session_id?: string
}

/** 预览/下载弹窗需要的三要素 */
export interface ArtifactPreviewTarget {
  session_id: string
  attachment_id: string
  filename: string
}

export function artifactTitle(item: ArtifactLike): string {
  return item.title || item.name || item.filename || item.url || '未命名产物'
}

export function isDocumentArtifact(item: ArtifactLike): boolean {
  return Boolean(item.attachment_id && item.session_id)
}

export function artifactPreviewTarget(item: ArtifactLike): ArtifactPreviewTarget | null {
  if (!isDocumentArtifact(item)) return null
  return {
    session_id: String(item.session_id),
    attachment_id: String(item.attachment_id),
    filename: item.filename || artifactTitle(item),
  }
}

/** 只放行绝对 http(s) 地址；相对路径（含 API 路径）与 javascript: 一律不渲染成链接 */
export function externalArtifactLink(item: ArtifactLike): string {
  const raw = (item.url || item.href || '').trim()
  if (!/^https?:\/\//i.test(raw)) return ''
  try {
    return new URL(raw).toString()
  } catch {
    return ''
  }
}
