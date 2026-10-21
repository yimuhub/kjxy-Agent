"""班组运行 SSE：GET /agents/team-runs/{run_id}/stream。

与 run_stream（发表讨论步骤流）同一机制：DB 追尾轮询 + 短 session +
「init / event / end」帧协议，差别只在数据源：这里追尾 ``team_runs`` +
``team_run_members``（班组接力台账），事件粒度 = **成员步**。

事件形状（取舍说明）：
- ``team-init``：{status, team_id, member_count}
- ``member-event``：{member_index, role_name, status(pending|running|ok|error),
  agent_run_id, error_text} —— 成员状态每变一次发一帧（游标
  「id:status:updated_at」与 W1 相同，续跑覆盖会再发一帧）。
- ``team-end``：{status, reason(run_terminal|timeout|not_found)}
内部步骤二跳：``member-event.agent_run_id`` 即该成员的声明式 run_id，
前端可再订 GET /agents/declarative/runs/{agent_run_id}/events/stream 看内部
llm/tool 步（无工具成员走单轮旧路径无步骤行，答案直接看 member.output）。
游标只做成员级：班组流自身保持轻量，粒度切换的代价写在成员事件里。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.agents.run_stream import MAX_STREAM_SECONDS, POLL_INTERVAL_SECONDS
from app.modules.agents.teams.models import TeamRun, TeamRunMember
from app.modules.tasks.worker_tasks import _get_session_factory

log = get_logger(__name__)


def _sse_frame(event: str, data: dict[str, Any]) -> str:
    """与 run_stream/tasks stream 相同的 SSE 帧格式。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _read_snapshot(
    tenant_id: str, run_id: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """短 session 读一次快照：run 状态 + Member 台账序列化（index 升序）。"""
    db: Session = _get_session_factory()()
    try:
        run = db.scalar(select(TeamRun).where(TeamRun.id == run_id, TeamRun.tenant_id == tenant_id))
        if run is None:
            return None, []
        rows = list(
            db.scalars(
                select(TeamRunMember)
                .where(TeamRunMember.team_run_id == run.id)
                .order_by(TeamRunMember.index.asc(), TeamRunMember.id.asc())
            )
        )
        run_d = {
            "id": run.id,
            "team_id": run.team_id,
            "status": run.status,
            "member_count": len(run.members_json or []),
        }
        members = [
            {
                "id": m.id,
                "member_index": int(m.index),
                "role_name": m.role_name,
                "status": m.status,
                "agent_run_id": m.task_id,
                "error_text": m.error_text,
                "updated_at": m.updated_at.isoformat() if m.updated_at else None,
            }
            for m in rows
        ]
        return run_d, members
    finally:
        db.close()


async def _stream_frames(tenant_id: str, run_id: str) -> AsyncIterator[str]:
    seen: set[str] = set()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + MAX_STREAM_SECONDS

    def _key(row: dict[str, Any]) -> str:
        # 状态每变一次发一帧：游标必须含状态与时序（同 W1 口径）
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
                    "member-event",
                    {
                        "member_index": row["member_index"],
                        "role_name": row["role_name"],
                        "status": row["status"],
                        "agent_run_id": row["agent_run_id"],
                        "error_text": row["error_text"],
                    },
                )
            )
        return frames

    try:
        run_d, rows = _read_snapshot(tenant_id, run_id)
        if run_d is None:
            # SSE 语义不 404：一帧说明后直接收流（run_id 应由 run/retry 响应给出）
            yield _sse_frame("team-end", {"status": "not_found", "reason": "run_not_found"})
            return
        yield _sse_frame(
            "team-init",
            {
                "status": run_d["status"],
                "team_id": run_d["team_id"],
                "member_count": run_d["member_count"],
            },
        )
        for frame in _collect_new(rows):
            yield frame
        if run_d["status"] != "running":
            yield _sse_frame("team-end", {"status": run_d["status"], "reason": "run_terminal"})
            return

        while True:
            run_d, rows = _read_snapshot(tenant_id, run_id)
            if run_d is None:
                yield _sse_frame("team-end", {"status": "not_found", "reason": "run_not_found"})
                return
            for frame in _collect_new(rows):
                yield frame
            if run_d["status"] != "running":
                yield _sse_frame("team-end", {"status": run_d["status"], "reason": "run_terminal"})
                return
            if loop.time() >= deadline:
                yield _sse_frame("team-end", {"status": "running", "reason": "timeout"})
                return
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        # 断开只是停止"看"：班组 run 由服务端继续执行（与 tasks/stream 一致）
        log.info("SSE 订阅者断开，team run %s 成员流停止推送（运行不受影响）", run_id[:8])
        raise


def build_stream_response(tenant_id: str, run_id: str) -> StreamingResponse:
    return StreamingResponse(
        _stream_frames(tenant_id, run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
