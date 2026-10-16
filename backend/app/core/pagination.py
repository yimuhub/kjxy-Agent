"""分页参数。"""

from fastapi import Query


def pagination_params(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
) -> tuple[int, int]:
    return page, page_size
