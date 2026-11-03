import { api } from './client'
import type { ApiResponse } from '@/types/api'

/** 工作区插件（v3.63）：受控项目、差异预览、应用与回滚。 */
export interface WorkspaceProject {
  id: string
  name: string
  root_path: string
  description?: string
  allowed_paths: string[]
  status: string
  git?: boolean
  git_head?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export interface WorkspaceChange {
  id: string
  project_id: string
  plugin_id: string
  path: string
  status: 'preview' | 'applied' | 'rolled_back' | string
  before_existed: boolean
  before_sha?: string | null
  after_sha: string
  diff: string
  diff_truncated: boolean
  added_lines: number
  removed_lines: number
  git_head_before?: string | null
  git_head_after?: string | null
  created_at?: string | null
  applied_at?: string | null
  rolled_back_at?: string | null
  error?: string
}

export interface WorkspaceFile {
  path: string
  exists: boolean
  content: string
  size_bytes: number
  sha256: string
}

export interface WorkspacePlugin {
  plugin_id: string
  name: string
  version: string
  enabled: boolean
  authorized: boolean
  permissions: string[]
}

export interface WorkspaceStatus {
  allowed_roots: string[]
  max_file_bytes: number
  preview_ttl_minutes: number
  git_available: boolean
  plugins: WorkspacePlugin[]
}

export interface WorkspaceProjectInput {
  name: string
  root_path: string
  description?: string
  allowed_paths?: string[]
}

export interface WorkspacePreviewInput {
  plugin_id: string
  path: string
  content: string
}

function unwrap<T>(request: Promise<{ data: ApiResponse<T> | T }>): Promise<T> {
  return request.then(res => {
    const body = res.data as unknown
    if (body && typeof body === 'object' && 'data' in body) {
      return (body as ApiResponse<T>).data
    }
    return body as T
  })
}

function toList<T>(raw: { items?: T[] } | T[] | null | undefined): T[] {
  if (Array.isArray(raw)) return raw
  return Array.isArray(raw?.items) ? raw.items : []
}

export const workspaceApi = {
  status(): Promise<WorkspaceStatus> {
    return unwrap<WorkspaceStatus>(
      api.get<ApiResponse<WorkspaceStatus>>('/workspace/status'),
    )
  },

  projects(): Promise<WorkspaceProject[]> {
    return unwrap<{ items?: WorkspaceProject[] } | WorkspaceProject[]>(
      api.get<ApiResponse<{ items?: WorkspaceProject[] } | WorkspaceProject[]>>(
        '/workspace/projects',
      ),
    ).then(res => toList<WorkspaceProject>(res))
  },

  createProject(payload: WorkspaceProjectInput): Promise<WorkspaceProject> {
    return unwrap<WorkspaceProject>(
      api.post<ApiResponse<WorkspaceProject>>('/workspace/projects', payload),
    )
  },

  archiveProject(id: string): Promise<WorkspaceProject> {
    return unwrap<WorkspaceProject>(
      api.delete<ApiResponse<WorkspaceProject>>(`/workspace/projects/${id}`),
    )
  },

  readFile(projectId: string, path: string): Promise<WorkspaceFile> {
    return unwrap<WorkspaceFile>(
      api.get<ApiResponse<WorkspaceFile>>(`/workspace/projects/${projectId}/file`, {
        params: { path },
      }),
    )
  },

  changes(projectId: string): Promise<WorkspaceChange[]> {
    return unwrap<{ items?: WorkspaceChange[] } | WorkspaceChange[]>(
      api.get<ApiResponse<{ items?: WorkspaceChange[] } | WorkspaceChange[]>>(
        `/workspace/projects/${projectId}/changes`,
      ),
    ).then(res => toList<WorkspaceChange>(res))
  },

  preview(projectId: string, payload: WorkspacePreviewInput): Promise<WorkspaceChange> {
    return unwrap<WorkspaceChange>(
      api.post<ApiResponse<WorkspaceChange>>(
        `/workspace/projects/${projectId}/changes/preview`,
        payload,
      ),
    )
  },

  apply(changeId: string): Promise<WorkspaceChange> {
    return unwrap<WorkspaceChange>(
      api.post<ApiResponse<WorkspaceChange>>(`/workspace/changes/${changeId}/apply`),
    )
  },

  rollback(changeId: string, force = false): Promise<WorkspaceChange> {
    return unwrap<WorkspaceChange>(
      api.post<ApiResponse<WorkspaceChange>>(`/workspace/changes/${changeId}/rollback`, {
        force,
      }),
    )
  },
}
