"""组织架构推导异步任务处理器（v3.58）。"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.org import proposal_service
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


@register_handler(proposal_service.TASK_TYPE)
def run_org_generate(input_data: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    data = input_data or {}
    proposal_id = str(data.get("proposal_id") or "")
    task_id = str(context.get("task_id") or "")
    yard_id = str(context.get("yard_id") or "")
    actor_id = str(context.get("actor_id") or "")
    if not proposal_id or not yard_id or not actor_id:
        raise AppError(
            "INVALID_REQUEST",
            "proposal_id / 小院 / 操作人为组织推导任务的必填输入。",
            http_status=400,
        )
    ctx = YardContext(
        yard_id=yard_id,
        user_id=actor_id,
        roles=[str(r) for r in (data.get("roles") or [])],
        request_id=str(context.get("request_id") or "req_unknown"),
    )
    db = _get_session_factory()()
    try:
        summary = asyncio.run(
            proposal_service.generate(
                db,
                ctx=ctx,
                proposal_id=proposal_id,
                instruction=str(data.get("instruction") or ""),
                on_stage=lambda stage, message, progress: _milestone(
                    db, task_id, stage, message, progress
                ),
            )
        )
        db.commit()
        _milestone(
            db,
            task_id,
            STAGE_COMPLETED,
            f"组织架构建议已生成：{summary.get('units')} 个单元、"
            f"{summary.get('agents')} 个 Agent 建议，等待你确认。",
            progress=1.0,
            detail={"proposal_id": proposal_id},
        )
        return summary
    except Exception as exc:
        detail = exc.message if isinstance(exc, AppError) else str(exc) or repr(exc)
        log.exception("org_generate task %s failed", task_id[:8])
        try:
            db.rollback()
            proposal_service.mark_failed(db, proposal_id, detail)
            db.commit()
            _milestone(db, task_id, STAGE_FAILED, f"组织架构推导失败：{detail}")
        except Exception:  # pragma: no cover - 台账失败不得掩盖原始错误
            db.rollback()
            log.exception("org_generate failure bookkeeping failed (task %s)", task_id[:8])
        if isinstance(exc, AppError):
            raise
        raise AppError(
            "ORG_PROPOSAL_FAILED", f"组织架构推导失败：{detail}", http_status=502
        ) from exc
    finally:
        db.close()


log.info("task handler registered: %s", proposal_service.TASK_TYPE)
