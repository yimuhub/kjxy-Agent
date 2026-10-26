"""MCP 服务器服务：CRUD + 掩码/加密 + 连接测试（工具快照）+ 熔断复用。

安全口径（复刻 push 通道 + devices 连接器）：
- URL/鉴权头 Fernet 加密入库（llm/supplier_crypto），接口只回掩码，明文/密文
  都绝不出接口；重填 url 即换（无独立轮换端点）且清零失败计数；
- 保存/换 URL 时 ``check_egress_url`` 第一重校验；每次外呼前 client 内复检；
- 跨小院与不存在统一 404；同小院重名 409；审计只记 name/transport/fields，
  绝不记 URL 与头值。
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.net_guard import check_egress_url
from app.modules.audit.service import log_audit
from app.modules.mcp import tool_bridge
from app.modules.mcp.client import McpStreamableHttpClient
from app.modules.mcp.models import VALID_TRANSPORTS, McpServer

log = get_logger(__name__)

#: 工具描述在接口/快照展示层的截断长度
DESCRIPTION_CLIP = 80


# ---------------------------------------------------------------------------
# 加密 / 掩码
# ---------------------------------------------------------------------------


def mask_url(url: str) -> str:
    """掩码形态：``scheme://host/***``——key 常藏 path/query，一律不出接口。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return "****"
    return f"{parsed.scheme}://{parsed.netloc}/***"


def prepare_url_enc(url: str) -> str:
    """保存/换 URL 入口：SSRF 校验（第一重）→ Fernet 加密，返回入库密文。"""
    from app.modules.llm.supplier_crypto import encrypt_api_key

    check_egress_url(url, context="mcp_server")
    return encrypt_api_key(url)


def prepare_headers_enc(headers: dict[str, str] | None) -> dict[str, str] | None:
    """鉴权头逐值 Fernet 加密（空字典归一为 None：不存空壳）。"""
    from app.modules.llm.supplier_crypto import encrypt_api_key

    if not headers:
        return None
    return {str(k): encrypt_api_key(str(v)) for k, v in headers.items()}


def server_to_out(srv: McpServer) -> dict[str, Any]:
    """出参：URL 与头值只有掩码（解密失败也不回显任何密文/明文片段）。"""
    from app.modules.llm.supplier_crypto import decrypt_api_key, mask_api_key

    url_masked: str | None = None
    if srv.url_enc:
        try:
            url_masked = mask_url(decrypt_api_key(srv.url_enc))
        except AppError:
            url_masked = "****（无法解密，请重新录入 URL）"
    headers_masked: dict[str, str] = {}
    for name, cipher in (srv.headers_enc or {}).items():
        try:
            headers_masked[str(name)] = mask_api_key(decrypt_api_key(str(cipher)))
        except AppError:
            headers_masked[str(name)] = "****（无法解密，请重新录入）"
    return {
        "id": srv.id,
        "yard_id": srv.yard_id,
        "name": srv.name,
        "url_masked": url_masked,
        "headers_masked": headers_masked,
        "transport": srv.transport,
        "enabled": srv.enabled,
        "consecutive_failures": srv.consecutive_failures,
        "tool_count": len(srv.tool_cache_json or {}),
        "last_test_at": srv.last_test_at.isoformat() if srv.last_test_at else None,
        "last_error": srv.last_error,
        "created_by": srv.created_by,
        "created_at": srv.created_at.isoformat() if srv.created_at else None,
        "updated_at": srv.updated_at.isoformat() if srv.updated_at else None,
    }


# ---------------------------------------------------------------------------
# 校验 / 定位
# ---------------------------------------------------------------------------


def validate_transport(transport: str) -> None:
    if transport not in VALID_TRANSPORTS:
        raise AppError(
            "INVALID_REQUEST",
            f"transport 必须是 {'|'.join(VALID_TRANSPORTS)} 之一（当前仅支持 "
            "streamable-HTTP 传输）。",
            http_status=400,
        )


def get_server_or_404(db: Session, yard_id: str, server_id: str) -> McpServer:
    """跨小院与不存在统一 404（不泄露其他小院服务器是否存在）。"""
    srv = db.get(McpServer, server_id)
    if srv is None or srv.yard_id != yard_id:
        raise AppError("NOT_FOUND", "MCP 服务器不存在。", http_status=404)
    return srv


# ---------------------------------------------------------------------------
# 工具快照形状（缓存 JSON → 接口出参；描述截断到 80 字）
# ---------------------------------------------------------------------------


def tools_out(cache: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not cache:
        return []
    return [
        {
            "name": str(name),
            "description": str(entry.get("description") or "")[:DESCRIPTION_CLIP],
            "cached_at": entry.get("cached_at"),
        }
        for name, entry in sorted(cache.items())
        if isinstance(entry, dict)
    ]


# ---------------------------------------------------------------------------
# 连接测试：initialize → tools/list → 快照落库（或失败计数）
# ---------------------------------------------------------------------------


def test_connection(db: Session, srv: McpServer, *, request_id: str) -> dict[str, Any]:
    """连一次目标服务器并刷新工具缓存。

    成功：``tool_cache_json`` 全量替换 + ``last_test_at`` + 失败计数清零 +
    ``last_error`` 清空，返回 {ok, tools, tool_count, has_more}；
    失败：记 last_error + 熔断计数（**已 commit**——错误响应也要把计数持久化），
    原始 AppError 原样上抛（SSRF 保持 400，连接故障 MCP_ERROR 502）。

    只取一页 tools/list：``nextCursor`` 非空时以 ``has_more`` 如实回报。
    """
    try:
        url, headers = tool_bridge.decrypt_config(srv)
    except AppError as e:
        tool_bridge.record_connection_failure(
            db, srv, f"配置解密失败：{e.message}", request_id=request_id
        )
        db.commit()
        raise
    context = f"mcp_server:{srv.id[:8]}"
    try:
        client = McpStreamableHttpClient(url, headers, context=context)
        client.initialize()
        tools, next_cursor = client.list_tools()
    except AppError as e:
        if e.code == "MCP_ERROR":
            tool_bridge.record_connection_failure(db, srv, e.message, request_id=request_id)
            db.commit()
        raise
    now = datetime.now(UTC)
    cache: dict[str, Any] = {}
    for tool in tools:
        name = str(tool.get("name"))
        schema = tool.get("inputSchema")
        cache[name] = {
            "description": str(tool.get("description") or ""),
            "input_schema": schema if isinstance(schema, dict) else {},
            "cached_at": now.isoformat(),
        }
    srv.tool_cache_json = cache
    srv.last_test_at = now
    tool_bridge.record_connection_success(db, srv)
    log_audit(
        db,
        yard_id=srv.yard_id,
        actor_id=None,
        action="mcp_server_test",
        resource_type="mcp_server",
        resource_id=srv.id,
        result="success",
        request_id=request_id,
        metadata={"name": srv.name, "tool_count": len(cache), "has_more": bool(next_cursor)},
    )
    return {
        "ok": True,
        "tools": tools_out(cache),
        "tool_count": len(cache),
        "has_more": bool(next_cursor),
    }


def list_yard_servers(db: Session, yard_id: str) -> list[McpServer]:
    return list(
        db.scalars(
            select(McpServer)
            .where(McpServer.yard_id == yard_id)
            .order_by(McpServer.created_at.desc())
        )
    )
