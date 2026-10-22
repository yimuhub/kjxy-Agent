"""SSE 任务事件流：GET /tasks/{task_id}/events/stream。

设计要点（交互参考 agency-orchestrator：事件流 + 每步卡片 + 关页即停；但本平台
执行体是服务端 Celery/线程任务）：
- 客户端断开只是停止“看”，任务继续运行；取消必须显式走
  POST /tasks/{task_id}/cancel，服务端绝不因 SSE 断连终止执行器。
- 权限与 GET /tasks/{task_id}/events 完全一致：同一 get_tenant_context 依赖
  + TaskService.get 可见性检查（跨租户读取视为不存在 → 404 TASK_NOT_FOUND）。
- 响应为 text/event-stream 帧协议：不套统一 {data, request_id} 信封——
  SSE 帧由 event:/data: 行构成，信封会破坏前端的流式解析契约。
- 实现为 DB 追尾轮询（默认 800ms）：TaskEvent.id 是 UUID 无单调性，游标
  采用“created_at,id 升序 + 已见 id 集合”；每轮经会话工厂开短 session
  （与 worker 共用注入点 worker_tasks._get_session_factory），用完即关，
  不在流期间长挂请求级 session。SQLite 开发库下这些均为索引只读查询。
- 帧序列：stream-init（首帧：status + event_count）→ task-event（序列化同
  TaskEventOut）/ task-status（{status, progress?}）→ 任务进入终态
  （succeeded/failed/cancelled）后把剩余事件补发完 → stream-end 收尾；
  存活超过 MAX_STREAM_SECONDS（30 分钟）主动 stream-end(timeout) 防泄漏。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.tasks import state_machine as sm
from app.modules.tasks.models import Task, TaskEvent
from app.modules.tasks.schemas import TaskEventOut
from app.modules.tasks.worker_tasks import _get_session_factory

log = get_logger(__name__)

#: 追尾轮询间隔（秒）
POLL_INTERVAL_SECONDS = 0.8
#: 单条流的最长存活时间（秒）：超时主动 stream-end，防止连接与会话泄漏
MAX_STREAM_SECONDS = 30 * 60.0


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    """单帧 SSE 文本：event: <名称> / data: <JSON> / 空行分隔。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _read_snapshot(tenant_id: str, task_id: str) -> tuple[str | None, list[dict[str, Any]]]:
    """短 session 读一次快照：当前状态 + 全部事件的序列化结果。

    事件按 (created_at,id) 升序；TaskEventOut 序列化在 session 内完成，
    避免对象脱离会话后惰性加载。任务不存在（被删）则 status 为 None。
    """
    db: Session = _get_session_factory()()
    try:
        task = db.get(Task, task_id)
        status = task.status if task is not None else None
        events = list(
            db.scalars(
                select(TaskEvent)
                .where(TaskEvent.tenant_id == tenant_id, TaskEvent.task_id == task_id)
                .order_by(TaskEvent.created_at.asc(), TaskEvent.id.asc())
            )
        )
        return status, [TaskEventOut.model_validate(e).model_dump(mode="json") for e in events]
    finally:
        db.close()


async def _stream_frames(tenant_id: str, task_id: str) -> AsyncIterator[str]:
    """SSE 帧生成器：stream-init 开场，追尾补发 task-event/task-status，终态或超时收尾。"""
    seen: set[str] = set()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + MAX_STREAM_SECONDS
    last_status: str | None = None
    last_progress: float | None = None

    def _collect_new(events: list[dict[str, Any]]) -> list[str]:
        """把本轮快照中未见过的事件转成 task-event 帧（游标为已见 id 集合）。"""
        nonlocal last_progress
        frames: list[str] = []
        for data in events:
            if data["id"] in seen:
                continue
            seen.add(data["id"])
            if data.get("progress") is not None:
                last_progress = data["progress"]
            frames.append(_sse_frame("task-event", data))
        return frames

    try:
        status, events = _read_snapshot(tenant_id, task_id)
        if status is None:
            yield _sse_frame("stream-end", {"reason": "task_not_found"})
            return
        # 首帧：让前端立刻拿到当前状态与事件总数，随后回放历史事件
        yield _sse_frame("stream-init", {"status": status, "event_count": len(events)})
        for frame in _collect_new(events):
            yield frame
        last_status = status

        while True:
            status, events = _read_snapshot(tenant_id, task_id)
            timed_out = loop.time() >= deadline
            if status is None:  # 流存活期间任务被删除
                yield _sse_frame("stream-end", {"reason": "task_not_found"})
                return
            for frame in _collect_new(events):
                yield frame
            if status != last_status:
                payload: dict[str, Any] = {"status": status}
                if last_progress is not None:
                    payload["progress"] = last_progress
                yield _sse_frame("task-status", payload)
                last_status = status
            if status in sm.TERMINAL_STATUSES:
                # 终态后再补拉一次，防止状态提交之后才落库的迟到事件丢帧
                _, tail = _read_snapshot(tenant_id, task_id)
                for frame in _collect_new(tail):
                    yield frame
                yield _sse_frame("stream-end", {"status": status, "reason": "task_terminal"})
                return
            if timed_out:
                yield _sse_frame("stream-end", {"status": status, "reason": "timeout"})
                return
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        # 客户端断开（关页/关抽屉/网络中断）：仅停止推送，服务端任务继续执行
        log.info("SSE 订阅者断开，任务 %s 事件流停止推送（任务本身不受影响）", task_id[:8])
        raise
    except SQLAlchemyError as e:
        # DB 抖动降级为一帧错误收尾：观测流绝不静默空流或 500（任务不受影响）
        log.warning("任务 %s 追尾读快照失败：%s", task_id[:8], e)
        yield _sse_frame("stream-end", {"status": "error", "reason": "snapshot_error"})
    finally:
        log.debug("SSE 事件流已清理：task=%s", task_id[:8])


def build_stream_response(tenant_id: str, task_id: str) -> StreamingResponse:
    """组装 SSE 响应：禁用缓存与反向代理缓冲（Nginx X-Accel-Buffering）。"""
    return StreamingResponse(
        _stream_frames(tenant_id, task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
