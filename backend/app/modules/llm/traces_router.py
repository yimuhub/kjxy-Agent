"""模型调用观测查询端点（W4）：``/llm/traces`` 与 ``/llm/usage/summary``。

权限口径：租户管理员可读（tenant_admin/admin；platform_admin 直通）；数据只按
登录上下文 tenant_id 过滤——别的租户的行**根本不出现在结果里**（跨租户拒绝由
租户边界天然保证，与 devices 读接口同一口径）。platform_admin 无租户上下文时
沿用既有 TENANT_REQUIRED(400)。

隐私：返回体只有 prompt_digest（sha12|前 200 字摘要），无 prompt 全文字段。
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.llm.models import LLMTrace

router = APIRouter()

_ADMIN_ROLES = {"platform_admin", "admin", "tenant_admin"}

#: purpose 合法取值（tool/embedding 为预留档；supervisor 为 W5b 总控分类调用）
VALID_PURPOSES = ("declarative", "factory", "eval", "tool", "embedding", "supervisor", "platform")


def _admin(ctx: TenantContext = Depends(get_tenant_context)) -> TenantContext:
    if not set(ctx.roles) & _ADMIN_ROLES:
        raise AppError("FORBIDDEN", "模型调用观测需要租户管理员权限。", http_status=403)
    return ctx


def _validate_purpose(purpose: str | None) -> None:
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
    ctx: TenantContext = Depends(_admin),
    db: Session = Depends(get_db_session),
    purpose: str | None = None,
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=50, ge=1, le=200),
):
    """本租户近 N 天调用轨迹（最新在前；limit 上限 200）。"""
    _validate_purpose(purpose)
    stmt = select(LLMTrace).where(
        LLMTrace.tenant_id == ctx.tenant_id,
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
    ctx: TenantContext = Depends(_admin),
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
            LLMTrace.tenant_id == ctx.tenant_id,
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
