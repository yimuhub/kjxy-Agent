"""本机能力通道接口（v3.60）。

- 读：`runtime:read`；执行：`runtime:use`（每次动作另写审计）；
- 插件必须有本小院授权记录覆盖当前权限集合，否则 409；
- 不提供「直接执行任意命令」的入口，动作只能来自固定目录。
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_db_session,
    get_yard_context,
    require_yard_permission,
)
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.local_runtime import service

router = APIRouter(prefix="/local-runtime", tags=["local-runtime"])

_read_gate = require_yard_permission("runtime:read")
_use_gate = require_yard_permission("runtime:use")


@router.get("/status")
def get_runtime_status(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_read_gate),
    db: Session = Depends(get_db_session),
):
    """当前系统桥状态、可用动作与本小院已安装的本机能力插件。"""
    return ok(service.status(db, ctx), request)


@router.post("/plugins/{plugin_id}/actions/{action}")
def run_runtime_action(
    request: Request,
    plugin_id: str,
    action: str,
    payload: dict | None = Body(default=None),
    ctx: YardContext = Depends(get_yard_context),
    user: CurrentUser = Depends(_use_gate),
    db: Session = Depends(get_db_session),
):
    """执行固定目录内的本机动作；参数按动作白名单校验。"""
    params = (payload or {}).get("params")
    if params is not None and not isinstance(params, dict):
        raise AppError("RUNTIME_PARAMS_INVALID", "params 必须是对象。", http_status=400)
    try:
        result = service.execute(db, ctx, plugin_id=plugin_id, action=action, params=params)
        db.commit()
    except AppError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    return ok(result, request)
