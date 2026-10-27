"""一句话生成智能体的异步任务处理器（v3.5）。

请求侧（agent_generate_router）只建 ``agent_generate`` 任务并立即返回，
真正的检索/设计/匹配/落库在这里执行；每个阶段写一条 TaskEvent 里程碑，
前端用现成的 ``GET /tasks/{task_id}/events/stream`` 追进度。

失败口径与工厂一致：先把错误如实写成 failed 里程碑，再把异常抛给 worker
（worker 侧记 task failed）；已提交的草稿不会被回滚成“半成品”以外的东西——
生成过程只在最后一步落库，落库前的任何失败都不会留下草稿。
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.agents import agent_builder
from app.modules.tasks.handlers import register_handler
from app.modules.tasks.models import Task
from app.modules.tasks.service import TaskService
from app.modules.tasks.worker_tasks import _get_session_factory

log = get_logger(__name__)

STAGE_COMPLETED = "completed"
STAGE_FAILED = "failed"


def _milestone(
    db: Session,
    task_id: str,
    stage: str,
    message: str,
    progress: float | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """写毫秒级进度：task_events 行 + 立即 commit，保证订阅者实时可见。"""
    task = db.get(Task, task_id)
    if task is not None:
        TaskService._add_event(
            db,
            task,
            "milestone",
            message,
            progress=progress,
            payload={"milestone": stage, **(detail or {})},
        )
    db.commit()


def _result_summary(result: dict[str, Any]) -> dict[str, Any]:
    """任务结果：只回前端需要渲染的字段（完整规格已在草稿 blueprint 里）。"""
    agent = result.get("agent") or {}
    return {
        "agent_id": agent.get("id"),
        "agent_code": agent.get("agent_code"),
        "name": agent.get("name"),
        "status": agent.get("status"),
        "risk_level": agent.get("risk_level"),
        "visibility": agent.get("visibility"),
        "matched": result.get("matched") or [],
        "skill_requirements": result.get("skill_requirements") or [],
        "sources": result.get("sources") or [],
        "offline": bool(result.get("offline")),
        "note": result.get("note") or "",
        "token_usage": result.get("token_usage") or {},
    }


@register_handler(agent_builder.TASK_TYPE)
def run_agent_generate(input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """执行一次一句话生成：检索 → 定框架 → 匹配技能 → 落草稿（不发布）。"""
    data = input_data or {}
    utterance = str(data.get("utterance") or "").strip()
    task_id = str(context.get("task_id") or "")
    yard_id = str(context.get("yard_id") or "")
    actor_id = str(context.get("actor_id") or "")
    if not utterance or not yard_id or not actor_id:
        raise AppError(
            "INVALID_REQUEST", "utterance / 小院 / 操作人为生成任务的必填输入。", http_status=400
        )

    ctx = YardContext(
        yard_id=yard_id,
        user_id=actor_id,
        roles=[str(r) for r in (data.get("roles") or [])],
        request_id=str(context.get("request_id") or "req_unknown"),
    )
    visibility = str(data.get("visibility") or "private")
    db = _get_session_factory()()
    try:
        result = asyncio.run(
            agent_builder.generate_agent(
                db,
                ctx=ctx,
                utterance=utterance,
                visibility=visibility,
                use_web=bool(data.get("use_web", True)),
                supplier_id=data.get("supplier_id") or None,
                on_stage=lambda stage, message, progress: _milestone(
                    db, task_id, stage, message, progress
                ),
            )
        )
        db.commit()
        summary = _result_summary(result)
        _milestone(
            db,
            task_id,
            STAGE_COMPLETED,
            f"草稿已生成：{summary.get('name')}（{len(summary['matched'])} 项技能已绑定，"
            f"{len(summary['skill_requirements'])} 项待补）。",
            progress=1.0,
            detail={"agent_id": summary.get("agent_id")},
        )
        return summary
    except Exception as exc:
        detail = exc.message if isinstance(exc, AppError) else str(exc) or repr(exc)
        log.exception("agent_generate task %s failed", task_id[:8])
        try:
            db.rollback()
            _milestone(db, task_id, STAGE_FAILED, f"生成失败：{detail}")
        except Exception:  # pragma: no cover - 台账写入失败不得掩盖原始错误
            db.rollback()
            log.exception("agent_generate failure bookkeeping failed (task %s)", task_id[:8])
        if isinstance(exc, AppError):
            raise
        raise AppError(
            "AGENT_GENERATE_FAILED", f"智能体生成失败：{detail}", http_status=502
        ) from exc
    finally:
        db.close()


log.info("task handler registered: %s", agent_builder.TASK_TYPE)
