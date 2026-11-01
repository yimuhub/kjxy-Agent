"""系统日志 REST：负责人查看/下载运行日志（已脱敏）。

权限：平台管理员或当前小院负责人。日志是进程级共享资源，普通成员/管理员不开放，
避免多小院部署时互相看到别人的请求与报错。
"""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_current_user, get_db_session
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.system import logs as logs_service
from app.modules.yards.authz import is_owner

router = APIRouter(prefix="/system", tags=["system"])


def _require_owner(db: Session, user: CurrentUser) -> None:
    roles = set(user.roles or [])
    if "platform_admin" in roles:
        return
    if user.yard_id and is_owner(db, user.yard_id, user.user_id):
        return
    raise AppError(
        "FORBIDDEN",
        "系统日志仅平台管理员或小院负责人可查看。",
        http_status=403,
    )


@router.get("/logs")
def list_logs(
    request: Request,
    source: str = Query(default="app", pattern="^(app|engine)$"),
    level: str | None = Query(default=None, pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$"),
    q: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=300, ge=20, le=1000),
    offset: int = Query(default=0, ge=0, le=10000),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """按来源/级别/关键词读取日志尾部（新→旧），并返回文件元信息。"""
    _require_owner(db, user)
    data = logs_service.read_logs(
        source=source,
        level=level,
        keyword=q,
        limit=limit,
        offset=offset,
    )
    return ok(data, request)


@router.get("/logs/download")
def download_logs(
    request: Request,
    source: str = Query(default="app", pattern="^(app|engine)$"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db_session),
):
    """下载脱敏后的日志尾部（最多 5MB），文件名带时间戳。"""
    _require_owner(db, user)
    filename, text = logs_service.export_text(source=source)
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in filename)
    disposition = (
        f'attachment; filename="{ascii_name}"; ' f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    return Response(
        content=text.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
