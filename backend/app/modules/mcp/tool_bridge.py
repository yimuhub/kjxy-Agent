"""租户动态 MCP 工具 → 平台工具注册表的桥（W5a）。

wire_name 约定：``mcp__<server_id 前 8 位>__<MCP 工具名>``——该串**同时就是**
``allowed_tools`` 里写的工具 code 与发给模型的 OpenAI function 名（``_wire_name``
对无点号的 mcp code 是恒等映射）。动态工具**不进全局 BUILTIN/内存注册表**：
按租户从 ``mcp_servers.tool_cache_json`` 快照作用域内解析（宁缺勿幻——连接测试
没刷进缓存的工具，模型看不到、也调不到）。

双层风险纪律（与既有分级吻合，测试锁定）：
1. **发布层**：``assess_risk`` 对 ``mcp__`` 前缀一律 high → 助手发布进平台审核队列；
2. **运行层**：桥出的 ToolDef.risk_level=external_side_effect → 照走 registry
   全通道风险闸（未批自动建 pending 单并回填 TOOL_RISK_BLOCKED），与
   workorder.dispatch 在循环内的既有行为完全一致（宁严勿松）。

熔断口径：连接级失败（网络/协议/HTTP/出网拦截/密文不可解）计一次
``consecutive_failures``，≥ ``MCP_MAX_FAILURES`` 自动 ``enabled=false`` + 审计；
工具级 ``isError``（服务器活着、只是工具报错）→ AppError 回填语义但**不计**熔断；
任何一次连接成功即清零计数。
"""

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.mcp.client import McpStreamableHttpClient
from app.modules.mcp.models import McpServer
from app.modules.tools.registry import ToolDef

log = get_logger(__name__)

#: wire_name 前缀（与 declarative.assess_risk 的高风险直通判定共用字面量口径）
MCP_WIRE_PREFIX = "mcp__"
#: 连接级连续失败自动停用阈值（对齐 devices/push 连接器纪律）
MCP_MAX_FAILURES = 5
#: wire_name 中 server 短 id 长度（uuid 前 8 位；同租户碰撞概率可忽略，命中多个取最早）
SERVER_ID_PREFIX_LEN = 8


def build_wire_name(server_id: str, tool_name: str) -> str:
    """(server_id, 工具名) → 规范 wire_name（缓存快照与 allowed_tools 同口径）。"""
    return f"{MCP_WIRE_PREFIX}{server_id[:SERVER_ID_PREFIX_LEN]}__{tool_name}"


def parse_wire_name(code: str) -> tuple[str, str] | None:
    """wire_name → (server 短 id, MCP 工具名)；非法/非 mcp 前缀返回 None。"""
    if not code.startswith(MCP_WIRE_PREFIX):
        return None
    rest = code[len(MCP_WIRE_PREFIX) :]
    server8, sep, tool = rest.partition("__")
    if not sep or not server8 or not tool:
        return None
    return server8, tool


# ---------------------------------------------------------------------------
# 服务器解析与配置解密（作用域：本租户 + enabled + 未熔断）
# ---------------------------------------------------------------------------


def resolve_server(
    db: Session, tenant_id: str, server_id_prefix: str, tool_name: str
) -> tuple[McpServer, dict[str, Any]] | None:
    """按短 id + 工具缓存命中定位服务器；仅 enabled 且未熔断的可见（纯读）。"""
    rows = list(
        db.scalars(
            select(McpServer)
            .where(
                McpServer.tenant_id == tenant_id,
                McpServer.enabled.is_(True),
                McpServer.consecutive_failures < MCP_MAX_FAILURES,
            )
            .order_by(McpServer.created_at.asc())
        )
    )
    for srv in rows:
        if srv.id[:SERVER_ID_PREFIX_LEN] != server_id_prefix:
            continue
        entry = (srv.tool_cache_json or {}).get(tool_name)
        if isinstance(entry, dict):
            return srv, entry
    return None


def decrypt_config(srv: McpServer) -> tuple[str, dict[str, str]]:
    """解密 (url, headers 明文头)。密文不可解按连接级故障口径抛 AppError。"""
    from app.modules.llm.supplier_crypto import decrypt_api_key

    if not srv.url_enc:
        raise AppError("MCP_ERROR", "MCP 服务器缺少 URL 配置。", http_status=502)
    url = decrypt_api_key(srv.url_enc)
    headers: dict[str, str] = {}
    for name, cipher in (srv.headers_enc or {}).items():
        headers[str(name)] = decrypt_api_key(str(cipher))
    return url, headers


# ---------------------------------------------------------------------------
# 熔断计数（test 端点与工具执行共用）
# ---------------------------------------------------------------------------


def record_connection_failure(db: Session, srv: McpServer, reason: str, *, request_id: str) -> None:
    """连接级失败 +1；≥阈值自动停用 + 审计（调用方负责 commit）。"""
    srv.consecutive_failures += 1
    srv.last_error = reason[:300]
    log.warning(
        "mcp server %s connection failed (%s/%s): %s",
        srv.id[:8],
        srv.consecutive_failures,
        MCP_MAX_FAILURES,
        reason[:200],
    )
    log_audit(
        db,
        tenant_id=srv.tenant_id,
        actor_id=None,
        action="mcp_server_failed",
        resource_type="mcp_server",
        resource_id=srv.id,
        result="failed",
        request_id=request_id,
        metadata={"failures": srv.consecutive_failures, "reason": reason[:200]},
    )
    if srv.consecutive_failures >= MCP_MAX_FAILURES:
        srv.enabled = False
        log.warning(
            "mcp server %s 连续 %s 次连接失败，已自动停用", srv.id[:8], srv.consecutive_failures
        )
        log_audit(
            db,
            tenant_id=srv.tenant_id,
            actor_id=None,
            action="mcp_server_disabled",
            resource_type="mcp_server",
            resource_id=srv.id,
            result="success",
            request_id=request_id,
            metadata={
                "name": srv.name,
                "reason": (
                    f"连续 {MCP_MAX_FAILURES} 次连接失败，已自动停用，"
                    "请检查 URL 与网络后重新启用"
                ),
            },
        )


def record_connection_success(db: Session, srv: McpServer) -> None:
    """任意连接成功：清零失败计数并清 last_error。"""
    srv.consecutive_failures = 0
    srv.last_error = None


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def remote_call(
    url: str,
    headers: dict[str, str],
    tool_name: str,
    arguments: dict[str, Any],
    *,
    context: str,
) -> dict[str, Any]:
    """纯网络一轮（**不碰 DB**）：握手 → tools/call；供线程池调用。"""
    client = McpStreamableHttpClient(url, headers, context=context)
    client.initialize()
    return client.call_tool(tool_name, arguments)


def execute_mcp_tool(
    db: Session, wire_name: str, args: dict[str, Any], ctx: TenantContext
) -> dict[str, Any]:
    """执行一次 MCP 工具调用（registry 全通道里的 ToolDef.func 落到这里）。

    结果 ``{"text", "is_error": False, "server", "tool"}``；工具级 isError 转
    ``AppError(MCP_ERROR)`` 回填语义（不计熔断）。连接级失败计熔断后原样上抛，
    由 registry/循环按既有口径回填错误消息。
    """
    parsed = parse_wire_name(wire_name)
    if parsed is None:
        raise AppError("TOOL_NOT_FOUND", f"非法 MCP 工具名：{wire_name}。", http_status=404)
    server8, tool_name = parsed
    found = resolve_server(db, ctx.tenant_id, server8, tool_name)
    if found is None:
        raise AppError(
            "MCP_ERROR",
            f"MCP 服务器不可用（已删除/停用/熔断或工具不在缓存快照内）：{wire_name}。",
            http_status=502,
        )
    srv, _entry = found
    try:
        url, headers = decrypt_config(srv)
    except AppError as e:
        # 密文不可解/缺 URL：同属连接级故障（配置已不可用），计入熔断
        record_connection_failure(db, srv, f"配置解密失败：{e.message}", request_id=ctx.request_id)
        raise
    try:
        outcome = remote_call(url, headers, tool_name, args, context=f"mcp_server:{srv.id[:8]}")
    except AppError as e:
        if e.code == "MCP_ERROR":
            record_connection_failure(db, srv, e.message, request_id=ctx.request_id)
        raise
    record_connection_success(db, srv)
    if outcome["is_error"]:
        # 工具级错误：服务连通（计数已清零），仅回填错误语义给模型
        raise AppError(
            "MCP_ERROR",
            f"MCP 工具返回错误：{outcome['text'][:500] or '（无错误详情）'}",
            http_status=502,
            details={"tool": tool_name, "is_tool_error": True},
        )
    return {
        "text": outcome["text"],
        "is_error": False,
        "server": srv.name,
        "tool": tool_name,
    }


# ---------------------------------------------------------------------------
# 查找（registry / declarative 的 MCP 回落入口）
# ---------------------------------------------------------------------------


def lookup_mcp_tool(db: Session, tenant_id: str, code: str) -> ToolDef | None:
    """mcp__ wire_name → 租户作用域 ToolDef（缓存命中才可见）；否则 None。

    风险档固定 external_side_effect + side_effect：发布层 assess_risk 判 high 走
    平台审核，运行层照走 registry 人工审批闸——两层都不因"MCP 工具看起来只是查询"
    而放松（外部服务器行为不可控，宁严勿松）。
    """
    parsed = parse_wire_name(code)
    if parsed is None:
        return None
    server8, tool_name = parsed
    found = resolve_server(db, tenant_id, server8, tool_name)
    if found is None:
        return None
    srv, entry = found
    description = str(entry.get("description") or tool_name)[:1024]
    schema = entry.get("input_schema")
    # 无 schema 时也必须禁止额外字段，避免客户端用未声明参数绕过审批/注入。
    input_schema = (
        schema
        if isinstance(schema, dict) and schema
        else {"type": "object", "additionalProperties": False}
    )

    async def _func(params: dict, ctx: TenantContext) -> dict:
        # 整轮（解密→出网→熔断计数）进线程池，避免 15s 超时冻结事件循环。
        # SQLite 连接 check_same_thread=False（db/session.py）；等待期间主线程不会
        # 并发使用同一 Session（工具循环串行执行，路由请求同理），计数写入挂
        # 调用方事务边界统一 commit。
        return await asyncio.to_thread(execute_mcp_tool, db, code, dict(params), ctx)

    return ToolDef(
        code=code,
        version=f"mcp:{srv.id[:SERVER_ID_PREFIX_LEN]}",
        name=tool_name,
        description=description,
        input_schema=input_schema,
        func=_func,
        risk_level="external_side_effect",
        side_effect=True,
        timeout_seconds=20,
    )
