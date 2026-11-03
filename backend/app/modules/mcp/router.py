"""MCP 工具服务器 HTTP 接口：``/settings/mcp-servers``（yard_admin 专属管理）。

安全口径：
- url/headers 提交即加密入库，响应只回掩码；重填 url（或 headers）即换，同时清零
  失败计数；手动重新启用（enabled=true）也清零——给修复后的服务器一次公平机会；
- 保存/换 URL 时 SSRF 校验（localhost/元数据等直接 400 SSRF_BLOCKED）；
- 跨小院与不存在统一 404；同小院重名 409；审计只记 name/transport/fields，
  绝不记 URL 与鉴权头值；
- 产品口径：MCP 工具进入工具白名单体系后，**助手发布涉及 MCP 工具一律高风险**，
  走平台审核队列（assess_risk 锁定）——前端创建成功提示同口径。
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import YardContext, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.mcp import service
from app.modules.mcp.models import McpServer
from app.modules.mcp.schemas import McpServerCreate, McpServerUpdate

router = APIRouter(prefix="/settings", tags=["mcp"])

_ADMIN_ROLES = {"platform_admin", "yard_admin"}


def _admin(ctx: YardContext = Depends(get_yard_context)) -> YardContext:
    if not set(ctx.roles) & _ADMIN_ROLES:
        raise AppError("FORBIDDEN", "MCP 服务器管理需要小院管理员权限。", http_status=403)
    return ctx


def _audit(ctx: YardContext, db: Session, action: str, resource_id: str, meta: dict) -> None:
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action=action,
        resource_type="mcp_server",
        resource_id=resource_id,
        result="success",
        request_id=ctx.request_id,
        metadata=meta,
    )


@router.get("/mcp-servers", response_model=None)
def list_servers(
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    """本小院 MCP 服务器列表（掩码出参 + 已缓存工具数）。"""
    return ok([service.server_to_out(s) for s in service.list_yard_servers(db, ctx.yard_id)])


@router.post("/mcp-servers", response_model=None)
def create_server(
    payload: McpServerCreate,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    service.validate_transport(payload.transport)
    dup = db.scalars(
        select(McpServer.id).where(McpServer.yard_id == ctx.yard_id, McpServer.name == payload.name)
    ).first()
    if dup:
        raise AppError("DUPLICATE_NAME", "同小院下已存在同名 MCP 服务器。", http_status=409)
    srv = McpServer(
        yard_id=ctx.yard_id,
        name=payload.name,
        url_enc=service.prepare_url_enc(payload.url),
        headers_enc=service.prepare_headers_enc(payload.headers),
        transport=payload.transport,
        enabled=payload.enabled,
        created_by=ctx.user_id,
    )
    db.add(srv)
    db.flush()
    # 审计 metadata 只记 name/transport——URL 与鉴权头值绝不入审计
    _audit(ctx, db, "mcp_server_create", srv.id, {"name": srv.name, "transport": srv.transport})
    db.commit()
    return ok(service.server_to_out(srv))


@router.patch("/mcp-servers/{server_id}", response_model=None)
def update_server(
    server_id: str,
    payload: McpServerUpdate,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    srv = service.get_server_or_404(db, ctx.yard_id, server_id)
    changes: dict[str, Any] = payload.model_dump(exclude_unset=True)
    touched: list[str] = []
    new_url = changes.pop("url", None)
    if new_url:
        # 重填即换：SSRF 校验失败即 400 且旧 URL 保留；换成功 = 重新开始（清零计数）
        srv.url_enc = service.prepare_url_enc(str(new_url))
        srv.consecutive_failures = 0
        touched.append("url")
    if "headers" in changes:
        # headers 提交即整体替换（掩码不可回填，重填即换与 url 同口径）；同样清零计数
        srv.headers_enc = service.prepare_headers_enc(changes.pop("headers"))
        srv.consecutive_failures = 0
        touched.append("headers")
    if "name" in changes and changes["name"] is not None:
        new_name = str(changes["name"])
        if new_name != srv.name:
            dup = db.scalars(
                select(McpServer.id).where(
                    McpServer.yard_id == ctx.yard_id,
                    McpServer.name == new_name,
                    McpServer.id != srv.id,
                )
            ).first()
            if dup:
                raise AppError("DUPLICATE_NAME", "同小院下已存在同名 MCP 服务器。", http_status=409)
        srv.name = new_name
        touched.append("name")
    if "enabled" in changes and changes["enabled"] is not None:
        enabled = bool(changes.pop("enabled"))
        if enabled and not srv.enabled:
            # 手动重新启用 = 重新开始（熔断自动停用后的修复入口）
            srv.consecutive_failures = 0
        srv.enabled = enabled
        touched.append("enabled")
    _audit(ctx, db, "mcp_server_update", srv.id, {"fields": sorted(touched)})
    db.commit()
    return ok(service.server_to_out(srv))


@router.delete("/mcp-servers/{server_id}", response_model=None)
def delete_server(
    server_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    srv = service.get_server_or_404(db, ctx.yard_id, server_id)
    db.delete(srv)
    _audit(ctx, db, "mcp_server_delete", server_id, {"name": srv.name})
    db.commit()
    return ok({"deleted": server_id})


@router.post("/mcp-servers/{server_id}/test", response_model=None)
def test_server(
    server_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    """连接测试：initialize + tools/list 一页；成功刷新工具快照，失败计熔断。"""
    srv = service.get_server_or_404(db, ctx.yard_id, server_id)
    result = service.test_connection(db, srv, request_id=ctx.request_id)
    db.commit()
    return ok(result)


@router.get("/mcp-servers/{server_id}/tools", response_model=None)
def list_server_tools(
    server_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    """读取连接测试缓存的工具快照（不发起网络请求）。"""
    srv = service.get_server_or_404(db, ctx.yard_id, server_id)
    cache = srv.tool_cache_json or {}
    return ok({"tools": service.tools_out(cache), "tool_count": len(cache)})
