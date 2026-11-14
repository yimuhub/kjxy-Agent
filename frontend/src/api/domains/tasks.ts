/**
 * 任务中心 / 高风险工具审批（v3.82 由 modules.ts / v3.ts 拆分而来，section 原文搬运未改逻辑）。
 */
import { api, toParams, unwrap, unwrapList } from '../client'
import type { ListResult } from '../client'
import type {
  ApiResponse,
  CreateTaskPayload,
  ListResponse,
  Task,
  TaskEvent,
  ToolApproval,
} from '@/types/api'

// ---------- 任务 ----------

export interface TaskListQuery {
  page?: number
  page_size?: number
  status?: string
}

export const taskApi = {
  create(payload: CreateTaskPayload): Promise<Task> {
    const { idempotencyKey, ...body } = payload
    return unwrap(
      api.post<ApiResponse<Task>>('/tasks', body, {
        headers: idempotencyKey ? { 'Idempotency-Key': idempotencyKey } : undefined,
      }),
    )
  },
  list(query: TaskListQuery = {}): Promise<ListResult<Task>> {
    return unwrapList(api.get<ListResponse<Task>>('/tasks', { params: toParams(query) }))
  },
  get(task_id: string): Promise<Task> {
    return unwrap(api.get<ApiResponse<Task>>(`/tasks/${task_id}`))
  },
  events(task_id: string): Promise<TaskEvent[]> {
    return unwrap(api.get<ApiResponse<TaskEvent[]>>(`/tasks/${task_id}/events`))
  },
  cancel(task_id: string): Promise<Task> {
    return unwrap(api.post<ApiResponse<Task>>(`/tasks/${task_id}/cancel`))
  },
  retry(task_id: string): Promise<Task> {
    return unwrap(api.post<ApiResponse<Task>>(`/tasks/${task_id}/retry`))
  },
}


// ---------- 高风险工具审批 ----------

export const approvalApi = {
  list(
    query: { status?: string; page?: number; page_size?: number } = {},
  ): Promise<ListResult<ToolApproval>> {
    return unwrapList(
      api.get<ListResponse<ToolApproval>>('/tool-approvals', { params: toParams(query) }),
    )
  },
  approve(
    approval_id: string,
    note?: string,
  ): Promise<{ approval: ToolApproval; tool_result: unknown }> {
    return unwrap(
      api.post<ApiResponse<{ approval: ToolApproval; tool_result: unknown }>>(
        `/tool-approvals/${approval_id}/approve`,
        { note },
      ),
    )
  },
  reject(approval_id: string, note?: string): Promise<{ approval: ToolApproval }> {
    return unwrap(
      api.post<ApiResponse<{ approval: ToolApproval }>>(`/tool-approvals/${approval_id}/reject`, {
        note,
      }),
    )
  },
}
