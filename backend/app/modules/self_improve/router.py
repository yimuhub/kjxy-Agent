"""自我总结提升路由（v3.4）：设置页运行报告与手动触发。

- GET  /yards/current/self-improve            运行报告（负责人/管理员，settings:manage）
- POST /yards/current/self-improve/run        立即运行一次（settings:manage_protected）
- GET  /yards/current/self-improve/runs       运行历史（settings:manage）
- GET  /yards/current/self-improve/memories   偏好记忆（本人；他人需 insights:sensitive）

设置项的写入复用 /yards/current/settings/self_improve（settings_router，
保护型权限 settings:manage_protected，自动发布需 confirm_auto_publish 二次确认）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.self_improve import service as si_service
from app.modules.self_improve.models import YardJob, YardJobRun
from app.modules.yards.authz import has_permission
from app.modules.yards.settings_service import get_setting

router = APIRouter()


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _yard_id(user: CurrentUser) -> str:
    if not user.yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


def _can_read_all(db: Session, user: CurrentUser, yard_id: str) -> bool:
    """谁能看全部成员的记忆：负责人（全权限）或被授予 insights:sensitive 者。"""
    return has_permission(
        db,
        yard_id=yard_id,
        user_id=user.user_id,
        jwt_roles=user.roles,
        permission="insights:sensitive",
    )


@router.get("/yards/current/self-improve")
def get_self_improve(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("settings:manage")),
    db: Session = Depends(get_db_session),
):
    """运行报告：设置 + 任务状态 + 最近一次运行 + 今日预算。"""
    yard_id = _yard_id(user)
    setting = get_setting(db, yard_id, "self_improve")
    job = db.scalar(
        select(YardJob).where(YardJob.yard_id == yard_id, YardJob.kind == "self_improve")
    )
    last_run = db.scalar(
        select(YardJobRun)
        .where(YardJobRun.yard_id == yard_id)
        .order_by(YardJobRun.started_at.desc())
        .limit(1)
    )
    used_today = si_service._used_budget_today(db, yard_id, si_service._utcnow())
    budget = int(setting.get("daily_budget_tokens") or 0)
    data: dict[str, Any] = {
        "settings": dict(setting),
        "job": {
            "status": job.status if job else "none",
            "next_run_at": job.next_run_at.isoformat() if job and job.next_run_at else None,
            "last_run_at": job.last_run_at.isoformat() if job and job.last_run_at else None,
        },
        "last_run": si_service.run_out(last_run) if last_run else None,
        "budget": {
            "daily_budget_tokens": budget,
            "used_today": used_today,
            "remaining": max(0, budget - used_today),
        },
        "can_manage": has_permission(
            db,
            yard_id=yard_id,
            user_id=user.user_id,
            jwt_roles=user.roles,
            permission="settings:manage_protected",
        ),
    }
    return ok(data, request)


@router.post("/yards/current/self-improve/run")
def run_self_improve(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("settings:manage_protected")),
    db: Session = Depends(get_db_session),
):
    """立即运行一次（同步执行；LLM 不可用时保留失败台账并返回 502）。"""
    yard_id = _yard_id(user)
    setting = get_setting(db, yard_id, "self_improve")
    used = si_service._used_budget_today(db, yard_id, si_service._utcnow())
    budget = int(setting.get("daily_budget_tokens") or 0)
    if used >= budget:
        raise AppError(
            "SELF_IMPROVE_BUDGET_EXHAUSTED",
            "今日自我总结 token 预算已用尽，明天再试或提高预算。",
            http_status=409,
            details={"used_today": used, "budget": budget},
        )
    run = si_service.run_once(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        request_id=_request_id(request),
    )
    if run.status == "failed":
        raise AppError(
            "SELF_IMPROVE_LLM_UNAVAILABLE",
            "模型当前不可用或输出无法解析，本轮未产生任何总结。",
            http_status=502,
            details={"reason": run.error_text},
        )
    return ok(si_service.run_out(run), request)


@router.get("/yards/current/self-improve/runs")
def list_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    user: CurrentUser = Depends(require_yard_permission("settings:manage")),
    db: Session = Depends(get_db_session),
):
    return ok({"items": si_service.list_runs(db, _yard_id(user), limit=limit)}, request)


@router.get("/yards/current/self-improve/memories")
def list_memories(
    request: Request,
    user_id: str | None = Query(default=None),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """偏好记忆：默认可看本人；查看他人需 insights:sensitive（负责人恒有）。"""
    yard_id = _yard_id(user)
    if user_id:
        if user_id != user.user_id and not _can_read_all(db, user, yard_id):
            raise AppError("FORBIDDEN", "查看其他成员的记忆需要负责人授权。", http_status=403)
        items = si_service.list_memories(db, yard_id, user_id=user_id)
    else:
        items = si_service.list_memories(db, yard_id, user_id=user.user_id)
        if _can_read_all(db, user, yard_id):
            items = items + si_service.list_memories(db, yard_id, user_id=None)
    return ok({"items": items}, request)


__all__ = ["router"]
