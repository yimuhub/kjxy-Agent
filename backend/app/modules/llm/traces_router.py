"""模型调用观测查询端点（W4）：``/llm/traces``、``/llm/usage/summary`` 与 ``/llm/usage/overview``。

权限口径：
- ``/llm/traces`` 与 ``/llm/usage/summary``：保持小院管理员权限及原契约形状；
- ``/llm/usage/overview``：小院全体登录成员可读（顶栏实时 Token 胶囊、对话统计、智能体排行），
  严格按 yard_id 边界隔离。

隐私：返回体只有 prompt_digest（sha12|前 200 字摘要），无 prompt 全文字段。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.llm.capabilities import VALID_PURPOSES
from app.modules.llm.models import LLMTrace

router = APIRouter()

_ADMIN_ROLES = {"platform_admin", "admin", "yard_admin", "yard_owner"}


def _admin(ctx: YardContext = Depends(get_yard_context)) -> YardContext:
    if not set(ctx.roles) & _ADMIN_ROLES:
        raise AppError("FORBIDDEN", "模型调用观测需要小院管理员权限。", http_status=403)
    return ctx


def _validate_purpose(purpose: str | None) -> None:
    """用途码以能力清单（``llm/capabilities.py``）为唯一来源（v3.25）。

    清单与真实写入点同源，杜绝「能查但库里从不写」或「库里在写但查询报 400」的错位。
    """
    if purpose is not None and purpose not in VALID_PURPOSES:
        raise AppError(
            "INVALID_REQUEST",
            f"purpose 必须是 {'|'.join(VALID_PURPOSES)} 之一。",
            http_status=400,
        )


def _cutoff(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _iso(dt: Any) -> str:
    if dt is None:
        return ""
    if isinstance(dt, str):  # SQLite 下 func.date() 返回文本日期
        return dt
    return dt.isoformat()


@router.get("/traces", response_model=None)
def list_traces(
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
    purpose: str | None = None,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
):
    """本小院近 N 天调用轨迹（最新在前；limit 上限 200）。"""
    _validate_purpose(purpose)
    stmt = select(LLMTrace).where(
        LLMTrace.yard_id == ctx.yard_id,
        LLMTrace.created_at >= _cutoff(days),
    )
    if purpose:
        stmt = stmt.where(LLMTrace.purpose == purpose)
    rows = list(db.scalars(stmt.order_by(LLMTrace.created_at.desc()).limit(limit)))
    items = [
        {
            "id": t.id,
            "created_at": t.created_at.isoformat() if t.created_at else "",
            "provider_id": t.provider_id,
            "model": t.model,
            "purpose": t.purpose,
            "agent_ref": t.agent_ref,
            "run_id": t.run_id,
            "status": t.status,
            "input_tokens": t.input_tokens,
            "output_tokens": t.output_tokens,
            "latency_ms": t.latency_ms,
            "prompt_digest": t.prompt_digest,
            "error_text": t.error_text,
        }
        for t in rows
    ]
    return ok({"items": items, "count": len(items)})


@router.get("/usage/summary", response_model=None)
def usage_summary(
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
    purpose: str | None = None,
    days: int = Query(default=30, ge=1, le=180),
):
    """按日×模型聚合：次数 / tokens 和 / 平均延迟 / 错误数（日期为 UTC 口径）。"""
    _validate_purpose(purpose)
    day = func.date(LLMTrace.created_at).label("day")
    calls = func.count().label("calls")
    tokens = (
        func.coalesce(func.sum(LLMTrace.input_tokens), 0)
        + func.coalesce(func.sum(LLMTrace.output_tokens), 0)
    ).label("tokens")
    avg_ms = func.avg(LLMTrace.latency_ms).label("avg_latency_ms")
    errors = func.sum(case((LLMTrace.status == "error", 1), else_=0)).label("errors")
    stmt = (
        select(day, LLMTrace.model, calls, tokens, avg_ms, errors)
        .where(
            LLMTrace.yard_id == ctx.yard_id,
            LLMTrace.created_at >= _cutoff(days),
        )
        .group_by(day, LLMTrace.model)
        .order_by(day.desc(), LLMTrace.model)
    )
    if purpose:
        stmt = stmt.where(LLMTrace.purpose == purpose)
    items = [
        {
            "day": _iso(r.day),
            "model": r.model,
            "calls": int(r.calls),
            "tokens": int(r.tokens),
            "avg_latency_ms": int(round(float(r.avg_latency_ms or 0))),
            "errors": int(r.errors or 0),
        }
        for r in db.execute(stmt)
    ]
    return ok({"days": days, "items": items})


@router.get("/usage/overview", response_model=None)
def usage_overview(
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
    days: int = Query(default=30, ge=1, le=180),
):
    """小院全员可读的 Token 全景统计（今日消耗、总消耗、按场景/按智能体/按模型聚合）。"""
    cutoff = _cutoff(days)
    today_str = datetime.now(UTC).strftime("%Y-%m-%d")

    in_sum = func.coalesce(func.sum(LLMTrace.input_tokens), 0)
    out_sum = func.coalesce(func.sum(LLMTrace.output_tokens), 0)
    call_cnt = func.count()

    totals_row = db.execute(
        select(call_cnt, in_sum, out_sum).where(
            LLMTrace.yard_id == ctx.yard_id,
            LLMTrace.created_at >= cutoff,
        )
    ).first()
    total_calls = int(totals_row[0] or 0) if totals_row else 0
    total_in = int(totals_row[1] or 0) if totals_row else 0
    total_out = int(totals_row[2] or 0) if totals_row else 0

    today_cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_row = db.execute(
        select(call_cnt, in_sum, out_sum).where(
            LLMTrace.yard_id == ctx.yard_id,
            LLMTrace.created_at >= today_cutoff,
        )
    ).first()
    today_calls = int(today_row[0] or 0) if today_row else 0
    today_in = int(today_row[1] or 0) if today_row else 0
    today_out = int(today_row[2] or 0) if today_row else 0

    purpose_rows = db.execute(
        select(LLMTrace.purpose, call_cnt, in_sum, out_sum)
        .where(LLMTrace.yard_id == ctx.yard_id, LLMTrace.created_at >= cutoff)
        .group_by(LLMTrace.purpose)
    ).all()
    by_purpose: list[dict[str, Any]] = [
        {
            "purpose": str(r[0] or "platform"),
            "calls": int(r[1] or 0),
            "input_tokens": int(r[2] or 0),
            "output_tokens": int(r[3] or 0),
            "total_tokens": int(r[2] or 0) + int(r[3] or 0),
        }
        for r in purpose_rows
    ]
    by_purpose.sort(key=lambda x: x["total_tokens"], reverse=True)

    agent_rows = db.execute(
        select(LLMTrace.agent_ref, call_cnt, in_sum, out_sum)
        .where(
            LLMTrace.yard_id == ctx.yard_id,
            LLMTrace.created_at >= cutoff,
            LLMTrace.agent_ref.is_not(None),
        )
        .group_by(LLMTrace.agent_ref)
    ).all()
    by_agent: list[dict[str, Any]] = [
        {
            "agent_ref": str(r[0]),
            "calls": int(r[1] or 0),
            "input_tokens": int(r[2] or 0),
            "output_tokens": int(r[3] or 0),
            "total_tokens": int(r[2] or 0) + int(r[3] or 0),
        }
        for r in agent_rows
        if r[0]
    ]
    by_agent.sort(key=lambda x: x["total_tokens"], reverse=True)

    return ok(
        {
            "days": days,
            "today_date": today_str,
            "today_calls": today_calls,
            "today_input_tokens": today_in,
            "today_output_tokens": today_out,
            "today_tokens": today_in + today_out,
            "total_calls": total_calls,
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_tokens": total_in + total_out,
            "by_purpose": by_purpose,
            "by_agent": by_agent,
        }
    )
