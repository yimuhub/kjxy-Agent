"""streamable-HTTP MCP 客户端（W5a，纯 httpx 手写 JSON-RPC 2.0，不加任何新依赖）。

传输规范（MCP 2025-03-26 streamable-HTTP）：
- 所有请求都是 ``POST <url>``，请求头 ``Accept: application/json, text/event-stream``；
- 请求体 ``{"jsonrpc": "2.0", "id"?, "method", "params"}``（通知无 id）；
- 握手序列：``initialize`` → 通知 ``notifications/initialized``（4xx 一律容忍——
  规范要求客户端容忍服务端对该通知的多种实现差异）→ ``tools/list`` / ``tools/call``。

**SSE 帧与纯 JSON 双解析**：响应 content-type 含 ``text/event-stream`` 时逐帧收集
``data:`` 行并 JSON 化，取与请求 id 匹配的 JSON-RPC 响应（取不到则取最后一个
result/error 对象）；否则按纯 JSON 解析。两种形态在同一 ``_rpc`` 内收敛。

安全与资源纪律：
- **每次外呼前** ``check_egress_url`` 复检（配置后 DNS 可能已变——重绑定防线）；
- 超时 15s（httpx 全阶段）；响应体硬上限 1MB，超出即截断并判定不可安全解析；
- 网络/协议/HTTP 错误统一 ``AppError("MCP_ERROR", ..., 502)``；SSRF 拦截保持
  ``SSRF_BLOCKED(400)`` 原始错误码（配置错误要与连接故障区分）。

分页说明（诚实边界）：``tools/list`` 支持透传 cursor，但本实现**只取一页**；
``nextCursor`` 非空时如实返回 ``has_more`` 标记（快照带标记入库、界面可见），
不做自动翻页——超限服务器请由服务端收敛工具集。
"""

import json
from typing import Any

import httpx

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.net_guard import check_egress_url

log = get_logger(__name__)

#: 协商的 MCP 协议版本（2025-03-26，streamable-HTTP 引入版）
PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "kjxy-agent-mcp-client", "version": "1.0.0"}
#: 单请求 HTTP 超时（秒）：连接阶段与工具执行共用
MCP_TIMEOUT_SECONDS = 15.0
#: 响应体硬上限（字节）：超限截断并拒绝解析（防异常大响应打爆内存）
MAX_RESPONSE_BYTES = 1024 * 1024

#: 测试注入口：httpx.MockTransport 假扮 MCP 服务器（全离线纪律，生产恒为 None）
_transport_override: httpx.BaseTransport | None = None


def set_test_transport(transport: httpx.BaseTransport | None) -> None:
    """测试专用：注入/清除 MockTransport（模块级单点，杜绝真实出网）。"""
    global _transport_override
    _transport_override = transport


def _mcp_error(message: str, **details: Any) -> AppError:
    """连接/协议级故障统一口径（502：上游 MCP 服务器不可用/不守约）。"""
    return AppError("MCP_ERROR", message, http_status=502, details=details)


def parse_sse_json(text: str) -> list[dict[str, Any]]:
    """SSE 帧 → data 载荷中的 JSON 对象列表（非 JSON/非对象分片诚实忽略）。

    兼容多行 ``data:``（同帧按 \\n 拼接后整体 JSON 化）；``event:``/``id:``/
    ``retry:`` 字段忽略；``[NOTICE]`` 之类非 JSON 心跳行跳过。
    """
    events: list[dict[str, Any]] = []
    data_lines: list[str] = []

    def _flush() -> None:
        if not data_lines:
            return
        raw = "\n".join(data_lines)
        data_lines.clear()
        try:
            obj = json.loads(raw)
        except ValueError:
            return
        if isinstance(obj, dict):
            events.append(obj)

    for line in text.splitlines():
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        elif not line.strip():
            _flush()
    _flush()
    return events


class McpStreamableHttpClient:
    """单端点客户端：一次会话 = initialize 握手 + 若干 tools/list / tools/call。

    无长连接语义：每次逻辑操作自建短会话（与 devices/push 连接器同一 httpx 用法）；
    MCP 会话头（mcp-session-id）当前不下发——实测主流 streamable-HTTP 服务端在
    无状态模式下允许直接 tools/list/call，若上游强校验会话再迭代补下发。
    """

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        context: str = "mcp",
    ) -> None:
        self._url = url
        self._headers = dict(headers or {})
        self._context = context

    # ------------------------------------------------------------------ 底层
    def _post(self, body: dict[str, Any], *, expect_response: bool) -> dict[str, Any] | None:
        """一次 JSON-RPC POST：出网复检 → 上限读取 → SSE/JSON 双解析 → 错误归一。"""
        check_egress_url(self._url, context=f"{self._context}:{body.get('method')}")
        req_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self._headers,
        }
        try:
            with httpx.Client(timeout=MCP_TIMEOUT_SECONDS, transport=_transport_override) as client:
                with client.stream("POST", self._url, json=body, headers=req_headers) as resp:
                    status = resp.status_code
                    ctype = resp.headers.get("content-type", "").lower()
                    raw, truncated = self._read_capped(resp)
        except httpx.HTTPError as e:
            raise _mcp_error(
                f"MCP 服务器请求失败：{type(e).__name__}", url_host=self._host()
            ) from e
        text = raw.decode("utf-8", errors="replace")
        if status >= 400:
            raise _mcp_error(f"MCP 服务器返回 HTTP {status}：{text[:200]}", status=status)
        if not expect_response:
            return None
        if truncated:
            raise _mcp_error("MCP 响应体超过 1MB 已截断，无法安全解析", limit=MAX_RESPONSE_BYTES)
        return self._unwrap(text, ctype, expected_id=body.get("id"))

    @staticmethod
    def _read_capped(resp: httpx.Response) -> tuple[bytes, bool]:
        """流式读取响应体，超 ``MAX_RESPONSE_BYTES`` 即截断并置 truncated 标记。"""
        chunks: list[bytes] = []
        total = 0
        truncated = False
        for chunk in resp.iter_bytes():
            remain = MAX_RESPONSE_BYTES - total
            if remain <= 0:
                truncated = True
                break
            chunks.append(chunk[:remain])
            total += len(chunk)
            if total >= MAX_RESPONSE_BYTES and len(chunk) > remain:
                truncated = True
                break
        return b"".join(chunks), truncated

    def _unwrap(self, text: str, ctype: str, *, expected_id: Any) -> dict[str, Any]:  # noqa: ANN401 —— JSON-RPC id 允许 int/str
        """响应文本（SSE 或纯 JSON）→ JSON-RPC result 对象；error 帧转 AppError。"""
        obj: dict[str, Any] | None = None
        if "text/event-stream" in ctype:
            candidates = parse_sse_json(text)
            for cand in candidates:
                if expected_id is not None and cand.get("id") == expected_id:
                    obj = cand
                    break
            if obj is None:
                obj = next((c for c in reversed(candidates) if "result" in c or "error" in c), None)
            if obj is None:
                raise _mcp_error("MCP SSE 响应中未找到 JSON-RPC 响应帧")
        else:
            try:
                parsed = json.loads(text)
            except ValueError as e:
                raise _mcp_error(f"MCP 响应不是合法 JSON/SSE：{text[:120]}") from e
            if not isinstance(parsed, dict):
                raise _mcp_error("MCP 响应 JSON-RPC 信封缺失")
            obj = parsed
        err = obj.get("error")
        if isinstance(err, dict):
            raise _mcp_error(
                f"MCP 协议错误 {err.get('code')}：{str(err.get('message'))[:200]}",
                rpc_code=err.get("code"),
            )
        result = obj.get("result")
        return result if isinstance(result, dict) else {}

    def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        rpc_id: int | None,
        expect_response: bool,
    ) -> dict[str, Any] | None:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "params": params}
        if rpc_id is not None:
            body["id"] = rpc_id
        return self._post(body, expect_response=expect_response)

    def _host(self) -> str:
        from urllib.parse import urlparse

        return urlparse(self._url).netloc

    # ------------------------------------------------------------------ 协议
    def initialize(self) -> dict[str, Any]:
        """握手：initialize → （容忍失败的）notifications/initialized 通知。"""
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            rpc_id=1,
            expect_response=True,
        )
        try:
            self._rpc("notifications/initialized", {}, rpc_id=None, expect_response=False)
        except AppError as e:
            # 规范允许服务端对通知回 4xx/非 202：容忍并留痕（握手结论已拿到）
            log.info("mcp initialized notification not acked (tolerated): %s", e.message)
        return result or {}

    def list_tools(self, cursor: str | None = None) -> tuple[list[dict[str, Any]], str | None]:
        """tools/list（支持透传 cursor，但只取一页；nextCursor 原样回报）。"""
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        result = self._rpc("tools/list", params, rpc_id=2, expect_response=True) or {}
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise _mcp_error("MCP tools/list 响应缺少 tools 数组")
        items = [t for t in tools if isinstance(t, dict) and isinstance(t.get("name"), str)]
        next_cursor = result.get("nextCursor")
        return items, next_cursor if isinstance(next_cursor, str) and next_cursor else None

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """tools/call：透传参数，content[].text 合并返回；isError 原样回报（不抛）。"""
        result = (
            self._rpc(
                "tools/call", {"name": name, "arguments": arguments}, rpc_id=3, expect_response=True
            )
            or {}
        )
        content = result.get("content")
        texts: list[str] = []
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("text") or ""))
        return {"text": "\n".join(texts), "is_error": bool(result.get("isError"))}
