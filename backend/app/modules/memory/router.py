"""记忆 REST：列表、检索、删除与日摘要。

所有接口都按当前小院与用户可见范围过滤；日摘要生成只允许负责人/管理员，
避免普通成员触发全小院聚合写库。
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
    get_yard_context,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.memory import service as memory_service

router = APIRouter(prefix="/commander/memories", tags=["commander-memory"])


class MemorySearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=5, ge=1, le=10)
    token_budget: int = Field(default=800, ge=120, le=4000)


class MemorySummaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    day: str | None = Field(default=None, max_length=10)


def _manager(ctx: YardContext) -> bool:
    return bool(set(ctx.roles or []) & {"yard_owner", "yard_admin", "platform_admin"})


@router.get("")
def list_memories(
    request: Request,
    scope_type: str | None = None,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
    user: CurrentUser = Depends(get_current_user),
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    rows = memory_service.list_memories(
        db,
        ctx,
        scope_type=scope_type,
        kind=kind,
        limit=max(1, min(int(limit or 50), 200)),
        offset=max(0, int(offset or 0)),
    )
    return ok(
        {
            "items": [memory_service.memory_out(row) for row in rows],
            "counts": memory_service.count_memories(db, ctx),
        },
        request,
    )


@router.post("/search")
def search_memories(
    body: MemorySearchRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    hits = memory_service.retrieve(
        db,
        ctx,
        body.query,
        top_k=body.top_k,
        token_budget=body.token_budget,
    )
    return ok({"items": [hit.to_dict() for hit in hits]}, request)


@router.delete("/{memory_id}")
def delete_memory(
    memory_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    memory_service.delete_memory(db, ctx, memory_id)
    db.commit()
    return ok({"deleted": True}, request)


@router.post("/summary")
def summarize(
    body: MemorySummaryRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    if not _manager(ctx):
        raise AppError("FORBIDDEN", "仅小院负责人或管理员可生成日复盘。", http_status=403)
    if body.day:
        try:
            day = date.fromisoformat(body.day)
        except ValueError as exc:
            raise AppError("INVALID_REQUEST", "day 必须是 YYYY-MM-DD。", http_status=400) from exc
    else:
        day = datetime.now(UTC).date()
    row = memory_service.summarize_day(db, yard_id=ctx.yard_id, day=day, created_by=ctx.user_id)
    db.commit()
    if row is None:
        return ok({"created": False, "reason": "当天没有指挥官运行"}, request)
    return ok({"created": True, "memory": memory_service.memory_out(row)}, request)


__all__ = ["router"]
