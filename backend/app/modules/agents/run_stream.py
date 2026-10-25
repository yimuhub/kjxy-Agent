"""Agent 运行步骤 SSE：GET /agents/declarative/runs/{run_id}/events/stream。

与 tasks/stream.py 同一机制（DB 追尾轮询 + 短 session + 帧协议），差别只在
数据源：这里追尾的是 ``agent_run_steps``（声明式/工厂 run 的 checkpoint 表），
而不是 TaskEvent——声明式同步运行不建 tasks 行（记账口径），run_id 即步骤表
分组键，因此复用「机制与帧协议」而非复用 tasks/stream 的具体查询。

运行模式说明（取舍）：
- worker/celery/inline 线程执行：步骤每步 commit，本流可在运行期间实时推送；
- 纯同步 API 执行：run_id 在响应返回前客户端不可知（除非预置 task_id），
  实时观看需另开连接按预置 run_id 追尾；响应体内本就带完整结果，
  steps GET 端点覆盖事后查看。

帧序列：steps-init（status + step_count）→ step-event（步骤序列化，游标
为「id:status:updated_at」集合，续跑覆盖行会再发一次）→ 终态或超时后
steps-end 收尾。终态判据：final ok 步（completed）或 error 的 llm 步（failed）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.agents import run_steps as rs
from app.modules.tasks.worker_tasks import _get_session_factory

log = get_logger(__name__)

#: 追尾轮询间隔（秒）
POLL_INTERVAL_SECONDS = 0.8
#: 单条流最长存活（秒）：超时主动收尾防泄漏
MAX_STREAM_SECONDS = 30 * 60.0


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _status_of(rows: list[dict[str, Any]]) -> str:
    """由步骤行动集推导运行状态：succeeded / failed（error final 或 llm error）/ running。"""
    for r in rows:
        if r["step_type"] == rs.STEP_FINAL:
            return "succeeded" if r["status"] == rs.STEP_OK else "failed"
    if any(r["step_type"] == rs.STEP_LLM and r["status"] != rs.STEP_OK for r in rows):
        return "failed"
    return "running"


def _read_snapshot(tenant_id: str, run_id: str) -> list[dict[str, Any]]:
    """短 session 读一次快照（租户边界内步骤行序列化，step_index 升序）。"""
    db: Session = _get_session_factory()()
    try:
        return [rs.step_out(row) for row in rs.list_steps(db, tenant_id=tenant_id, run_id=run_id)]
    finally:
        db.close()


async def _stream_frames(tenant_id: str, run_id: str) -> AsyncIterator[str]:
    seen: set[str] = set()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + MAX_STREAM_SECONDS
    last_emit = loop.time()

    def _key(row: dict[str, Any]) -> str:
        # 续跑会原位覆盖失败行（id 不变、status/updated_at 变）→ 游标必须含状态与时序
        return f"{row['id']}:{row['status']}:{row['updated_at']}"

    def _collect_new(rows: list[dict[str, Any]]) -> list[str]:
        frames: list[str] = []
        for row in rows:
            key = _key(row)
            if key in seen:
                continue
            seen.add(key)
            frames.append(
                _sse_frame(
                    "step-event",
                    {
                        "step_index": row["step_index"],
                        "step_type": row["step_type"],
                        "status": row["status"],
                        "tool_code": row["tool_code"],
                        "duration_ms": row["duration_ms"],
                    },
                )
            )
        return frames

    try:
        rows = _read_snapshot(tenant_id, run_id)
        status = _status_of(rows)
        if not rows and status == "running":
            # 尚无任何步骤：可能是 run 未开始，也可能是 run_id 不存在/跨租户。
            # SSE 语义下不 404：发一帧提示后按追尾窗口等待（与 tasks/stream 风格一致）。
            yield _sse_frame("steps-note", {"note": "no_steps_yet"})
        yield _sse_frame("steps-init", {"status": status, "step_count": len(rows)})
        for frame in _collect_new(rows):
            yield frame
        if status != "running":
            yield _sse_frame("steps-end", {"status": status, "reason": "run_terminal"})
            return

        while True:
            rows = _read_snapshot(tenant_id, run_id)
            status = _status_of(rows)
            emitted = False
            for frame in _collect_new(rows):
                emitted = True
                yield frame
            if status != "running":
                yield _sse_frame("steps-end", {"status": status, "reason": "run_terminal"})
                return
            if loop.time() >= deadline:
                yield _sse_frame("steps-end", {"status": "running", "reason": "timeout"})
                return
            if emitted:
                last_emit = loop.time()
            elif loop.time() - last_emit >= 15.0:
                yield ": keepalive\n\n"
                last_emit = loop.time()
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        # 客户端断开只是停止"看"：run 本身由服务端继续执行（与 tasks/stream 一致）
        log.info("SSE 订阅者断开，run %s 步骤流停止推送（运行本身不受影响）", run_id[:8])
        raise
    except SQLAlchemyError as e:
        # DB 抖动降级为一帧错误收尾：观测流绝不静默空流或 500（运行不受影响）
        log.warning("run %s 追尾读快照失败：%s", run_id[:8], e)
        yield _sse_frame("steps-end", {"status": "error", "reason": "snapshot_error"})


def build_stream_response(tenant_id: str, run_id: str) -> StreamingResponse:
    return StreamingResponse(
        _stream_frames(tenant_id, run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
