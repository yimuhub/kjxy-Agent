"""工作日志/月报 REST API（/journal，角色工作台 P0）。

- POST /journal/draft      生成（或取回）当日/当月草稿（logs:write；幂等）
- GET  /journal            本院列表（本人默认；logs:review 可看全院）
- PUT  /journal/{id}       编辑草稿（仅作者，draft 态）
- POST /journal/{id}/submit        提交（仅作者）
- POST /journal/{id}/review        通过/驳回（logs:review）
- GET  /journal/stats            本人月度考核进度
- GET  /journal/monthly/export   月报导出 Word（docs 渲染管线）
- POST /journal/training/notify    培训提醒推送（owner/admin，push 事件）
- GET  /journal/aggregate        跨小院监管聚合（platform_admin，P1）
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote as urlquote

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
    get_yard_context,
    require_yard_permission,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.journal import service
from app.modules.push.models import EVENT_LABELS

router = APIRouter(prefix="/journal", tags=["journal"])

_LOGS_WRITE = require_yard_permission("logs:write")
_LOGS_REVIEW = require_yard_permission("logs:review")


class JournalDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(default="daily")
    date: str = Field(default="", max_length=10, description="YYYY-MM-DD，缺省今天")


class JournalSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content_md: str = Field(min_length=1, max_length=20000)
    title: str | None = Field(default=None, max_length=200)


class JournalReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(pattern="^(approve|reject)$")
    note: str | None = Field(default=None, max_length=500)


class TrainingNotifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=2, max_length=120)
    time_text: str = Field(min_length=1, max_length=60)
    place: str = Field(default="", max_length=120)


def _parse_day(raw: str) -> date:
    if not raw:
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(raw)
    except ValueError as e:
        raise AppError("INVALID_REQUEST", "日期格式应为 YYYY-MM-DD。", http_status=400) from e


@router.post("/draft")
def create_draft(
    payload: JournalDraftRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_LOGS_WRITE),
    db: Session = Depends(get_db_session),
):
    """生成/取回日志草稿（幂等：同 kind+period 已有则原样返回）。"""
    row = service.build_draft(db, ctx, payload.kind, _parse_day(payload.date))
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    name = service.author_name_map(db, [row.author_user_id]).get(row.author_user_id)
    return ok(service.journal_out(row, author_name=name), request)


@router.get("")
def list_journal(
    request: Request,
    month: str = "",
    scope: str = "mine",
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """本院日志列表：scope=mine（默认，本人）| yard（logs:review 看全院）。"""
    mine_only = scope != "yard"
    if not mine_only:
        # 全院视图需要审核权：复用 require_yard_permission 的判定链（DB 角色+grants）
        from app.modules.yards.authz import has_permission

        if not user.yard_id or not has_permission(
            db,
            yard_id=user.yard_id,
            user_id=user.user_id,
            jwt_roles=user.roles,
            permission="logs:review",
        ):
            raise AppError(
                "FORBIDDEN", "缺少 logs:review 权限，无法查看全院日志。", http_status=403
            )
    rows = service.list_journal(db, ctx, month=month or None, mine_only=mine_only)
    names = service.author_name_map(db, [r.author_user_id for r in rows])
    return ok(
        {
            "items": [
                service.journal_out(r, author_name=names.get(r.author_user_id)) for r in rows
            ]
        },
        request,
    )


@router.put("/{journal_id}")
def save_journal(
    journal_id: str,
    payload: JournalSaveRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_LOGS_WRITE),
    db: Session = Depends(get_db_session),
):
    row = service.save_content(db, ctx, journal_id, payload.content_md, payload.title)
    db.commit()
    name = service.author_name_map(db, [row.author_user_id]).get(row.author_user_id)
    return ok(service.journal_out(row, author_name=name), request)


@router.post("/{journal_id}/submit")
def submit_journal(
    journal_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_LOGS_WRITE),
    db: Session = Depends(get_db_session),
):
    row = service.submit(db, ctx, journal_id)
    db.commit()
    name = service.author_name_map(db, [row.author_user_id]).get(row.author_user_id)
    return ok(service.journal_out(row, author_name=name), request)


@router.post("/{journal_id}/review")
def review_journal(
    journal_id: str,
    payload: JournalReviewRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_LOGS_REVIEW),
    db: Session = Depends(get_db_session),
):
    row = service.review(db, ctx, journal_id, payload.action, payload.note)
    db.commit()
    name = service.author_name_map(db, [row.author_user_id]).get(row.author_user_id)
    return ok(service.journal_out(row, author_name=name), request)


@router.get("/stats")
def journal_stats(
    request: Request,
    month: str = "",
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    month = month or datetime.now(UTC).strftime("%Y-%m")
    return ok(service.stats(db, ctx, month), request)


@router.get("/monthly/export")
def export_monthly(
    request: Request,
    month: str = "",
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_LOGS_WRITE),
    db: Session = Depends(get_db_session),
):
    """本人月报导出 Word（按当月日志聚合；无日志 404）。"""
    month = month or datetime.now(UTC).strftime("%Y-%m")
    raw = service.export_monthly_docx_raw(db, ctx, month)
    if raw is None:
        raise AppError("JOURNAL_NOT_FOUND", "本月还没有日志，先生成日报再导出。", http_status=404)
    from app.modules.docs.model import parse_model
    from app.modules.docs.render_docx import to_docx

    data = to_docx(parse_model(raw))
    filename = f"工作月报-{month}.docx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{urlquote(filename, safe='')}"
        },
    )


@router.post("/training/notify")
def training_notify(
    payload: TrainingNotifyRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(require_yard_permission("members:manage_limited")),
    db: Session = Depends(get_db_session),
):
    """培训提醒：向本院订阅 training_reminder 的通道推送（负责人/管理员发起）。"""
    from app.modules.push.models import EVENT_TRAINING_REMINDER
    from app.modules.push.service import dispatch

    dispatch(
        db,
        yard_id=ctx.yard_id,
        event_type=EVENT_TRAINING_REMINDER,
        payload={
            "title": payload.title,
            "时间": payload.time_text,
            "地点": payload.place or "待定",
        },
        request_id=ctx.request_id,
    )
    db.commit()
    return ok({"sent": EVENT_LABELS.get(EVENT_TRAINING_REMINDER, EVENT_TRAINING_REMINDER)}, request)


@router.get("/aggregate")
def aggregate(
    request: Request,
    month: str = "",
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """跨小院监管聚合（P1，platform_admin）：日志量/活跃作者/断更预警。"""
    if "platform_admin" not in (user.roles or []):
        raise AppError("FORBIDDEN", "仅平台管理员可查看跨小院监管看板。", http_status=403)
    month = month or datetime.now(UTC).strftime("%Y-%m")
    data: dict[str, Any] = service.aggregate_overview(db, month=month)
    return ok(data, request)


__all__ = ["router"]
