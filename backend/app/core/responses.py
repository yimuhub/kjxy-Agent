"""统一成功响应封装。"""

from typing import Any

from fastapi import Request


def _request_id(request: Request | None) -> str:
    if request is None:
        return "req_unknown"
    return getattr(request.state, "request_id", "req_unknown")


def ok(data: Any, request: Request | None = None) -> dict:
    return {"data": data, "request_id": _request_id(request)}


def paged(
    items: list, page: int, page_size: int, total: int, request: Request | None = None
) -> dict:
    return {
        "data": items,
        "meta": {"page": page, "page_size": page_size, "total": total},
        "request_id": _request_id(request),
    }
