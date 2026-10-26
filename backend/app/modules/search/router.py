"""联网搜索 HTTP 路由：状态探测与连通性测试。

挂载到 /api/v1 由主程序统一接入（本模块只暴露 ``router``）。
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_db_session,
    get_yard_context,
    require_yard_permission,
)
from app.core.responses import ok
from app.modules.search import service

router = APIRouter(prefix="/search", tags=["search"])

DEFAULT_TEST_QUERY = "科技小院"


class SearchTestRequest(BaseModel):
    query: str | None = Field(default=None, max_length=500)
    count: int = Field(default=3, ge=1, le=8)


@router.get("/status")
async def get_network_status(
    request: Request,
    db: Session = Depends(get_db_session),
    ctx: YardContext = Depends(get_yard_context),
) -> dict[str, Any]:
    """联网状态 + 当前供应商 + 今日配额（登录即可）。"""
    status = await service.network_status(db, ctx.yard_id)
    supplier, _chain = service.select_providers(db, ctx.yard_id)
    data = {
        **status,
        "supplier": supplier,
        "quota": service.quota_snapshot(db, ctx.yard_id),
    }
    return ok(data, request)


@router.post("/test")
async def test_search(
    request: Request,
    payload: SearchTestRequest | None = None,
    db: Session = Depends(get_db_session),
    ctx: YardContext = Depends(get_yard_context),
    _user: CurrentUser = Depends(require_yard_permission("search:manage")),
) -> dict[str, Any]:
    """真实搜一条，返回供应商 / 结果数 / 耗时；失败原样抛 AppError。"""
    query = ((payload.query if payload else None) or DEFAULT_TEST_QUERY).strip()
    count = payload.count if payload else 3
    started = time.perf_counter()
    result = await service.search_web(db, ctx.yard_id, query, count=count)
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    return ok(
        {
            "query": result["query"],
            "provider": result["provider"],
            "result_count": len(result["results"]),
            "latency_ms": elapsed_ms,
            "cached": result["cached"],
            "fetched_at": result["fetched_at"],
            "results": result["results"],
        },
        request,
    )
