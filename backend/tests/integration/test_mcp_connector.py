"""MCP 工具连接器测试（W5a）。

覆盖：
1. 客户端：initialize→tools/list（**SSE 帧**与**纯 JSON** 双形态）、多帧 SSE 取
   匹配 id、JSON-RPC error 帧、1MB 截断、cursor/nextCursor 透传；
2. 管理 API：创建（URL/头加密+掩码不回显）、SSRF（localhost 拒）、重名 409、
   transport 白名单、PATCH（换 URL 清零计数/重新启用清零）、DELETE、
   跨小院统一 404、GET /tools 读缓存；
3. 连接测试端点：成功刷新 tool_cache_json + last_test_at + 清零计数；失败
   last_error + 熔断计数持久化；
4. 执行链路：registry.execute 对 ``mcp__`` 回落（已批审批单放行）→ tools/call
   成功回填并清零熔断；工具 isError → MCP_ERROR 错误语义回填且不计熔断；
   连接级 5 连败自动停用 + 审计 + 缓存不可见；未知 mcp 工具仍 TOOL_NOT_FOUND；
5. declarative 集成：allowed_tools 含 mcp__ 工具 → schema 下发、循环真实调用
   一次、结果以 role=tool 回填 LLM 请求；assess_risk mcp__ → high → 发布进
   平台审核队列。

全部离线：httpx MockTransport 假扮 MCP 服务器（client._transport_override 注入）；
URL 用 .invalid 保留域（DNS 必不可解析 → net_guard 放行给 mock）。
"""

import json
import os

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-chars-for-unit-tests")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("SUPPLIER_KEY_SECRET", Fernet.generate_key().decode())

import httpx  # noqa: E402
import pytest  # noqa: E402
from app.core.deps import YardContext  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.modules.agents import declarative  # noqa: E402
from app.modules.agents import run_steps as rs  # noqa: E402
from app.modules.agents.models import (  # noqa: E402
    RISK_HIGH,
    STATUS_PENDING,
    STATUS_PUBLISHED,
    YardAgent,
)
from app.modules.audit.models import AuditLog  # noqa: E402
from app.modules.llm import providers_fake  # noqa: E402
from app.modules.llm.service import complete_with_logging  # noqa: E402
from app.modules.mcp import client as client_mod  # noqa: E402
from app.modules.mcp import service, tool_bridge  # noqa: E402
from app.modules.mcp.client import McpStreamableHttpClient  # noqa: E402
from app.modules.mcp.models import McpServer  # noqa: E402
from app.modules.tools.approvals import params_hash  # noqa: E402
from app.modules.tools.models import ToolApproval  # noqa: E402
from app.modules.tools.registry import tool_registry  # noqa: E402
from app.modules.yards.repository import create_yard  # noqa: E402

YARD = "t-mcp-a"
OTHER_YARD = "t-mcp-b"
SECRET_URL = "https://mcp-srv.invalid/rpc?key=sk-mcp-666"
HEADER_SECRET = "Bearer sup3r-s3cr3t-headervalue"
DEMO_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}},
    "required": ["city"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# 假 MCP 服务器（MockTransport）
# ---------------------------------------------------------------------------


def _rpc_ok(rpc_id, result: dict, sse: bool) -> httpx.Response:
    body = {"jsonrpc": "2.0", "id": rpc_id, "result": result}
    if sse:
        frame = f"event: message\ndata: {json.dumps(body, ensure_ascii=False)}\n\n"
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=frame.encode()
        )
    return httpx.Response(200, json=body)


def _make_handler(state: dict):
    """按 state 配置分发的假服务器：tools/call 次数与最近入参都记进 state。"""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "text/event-stream" in request.headers.get("accept", "")
        body = json.loads(request.content)
        method = body.get("method")
        state.setdefault("methods", []).append(method)
        if request.headers.get("authorization"):
            state["last_auth"] = request.headers["authorization"]
        if method == "initialize":
            params = body.get("params") or {}
            assert params.get("protocolVersion") == "2025-03-26"
            assert "clientInfo" in params
            return _rpc_ok(
                body["id"],
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fake-mcp", "version": "0.1"},
                },
                state.get("sse", True),
            )
        if method == "notifications/initialized":
            if state.get("notify_4xx"):
                return httpx.Response(404, text="no such notification route")
            return httpx.Response(202)
        if method == "tools/list":
            if state.get("raise_connect"):
                raise httpx.ConnectError("dns down")
            if state.get("http_503"):
                return httpx.Response(503, text="upstream unavailable")
            if state.get("huge_json"):
                pad = "x" * (1_500_000)
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": [], "pad": pad}},
                )
            if state.get("multi_frame"):
                noise = {"jsonrpc": "2.0", "method": "notifications/progress", "params": {}}
                final = {
                    "jsonrpc": "2.0",
                    "id": body["id"],
                    "result": {"tools": state.get("tools", _demo_tools())},
                }
                frames = (
                    f"data: {json.dumps(noise)}\n\n"
                    f"event: message\ndata: {json.dumps(final, ensure_ascii=False)}\n\n"
                )
                return httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, content=frames.encode()
                )
            result: dict = {"tools": state.get("tools", _demo_tools())}
            if state.get("next_cursor"):
                result["nextCursor"] = state["next_cursor"]
            return _rpc_ok(body["id"], result, state.get("sse", True))
        if method == "tools/call":
            state["call_count"] = state.get("call_count", 0) + 1
            state["last_arguments"] = (body.get("params") or {}).get("arguments")
            if state.get("raise_connect"):
                raise httpx.ConnectError("connection refused")
            result = {
                "content": [
                    {"type": "text", "text": state.get("call_text", "mock 工具结果")},
                    {"type": "image", "data": "…"},
                ],
                "isError": state.get("call_is_error", False),
            }
            return _rpc_ok(body["id"], result, state.get("sse", True))
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "error": {"code": -32601, "message": "method not found"},
            },
        )

    return handler


def _demo_tools() -> list[dict]:
    return [
        {
            "name": "demo",
            "description": "演示工具：" + "很长的中文描述" * 30,
            "inputSchema": DEMO_SCHEMA,
        }
    ]


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="MCP 小院A")
    db_session.commit()
    return t


@pytest.fixture()
def other_yard(db_session):
    t = create_yard(db_session, name="MCP 小院B")
    db_session.commit()
    return t


def _ctx(yard_id: str = YARD) -> YardContext:
    return YardContext(yard_id=yard_id, user_id="u-mcp", roles=["yard_admin"], request_id="req-mcp")


def _hdr(yard_id: str = YARD, roles=("yard_admin",)):
    token = create_access_token(user_id="u-mcp", yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _mk_server(db, yard_id: str = YARD, *, name="主服务器", url=SECRET_URL, headers=None):
    srv = McpServer(
        yard_id=yard_id,
        name=name,
        url_enc=service.prepare_url_enc(url),
        headers_enc=service.prepare_headers_enc(headers),
        created_by="u-mcp",
    )
    db.add(srv)
    db.commit()
    return srv


@pytest.fixture()
def fake_transport(monkeypatch):
    """注入 MockTransport 的工厂：返回可写的 state 字典。"""

    def _install(state: dict) -> dict:
        transport = httpx.MockTransport(_make_handler(state))
        monkeypatch.setattr(client_mod, "_transport_override", transport)
        return state

    return _install


@pytest.fixture(autouse=True)
def _script_isolation():
    """fake provider 脚本是进程级状态：每条用例前后强制清空，防串测。"""
    providers_fake.reset_scripted_tool_responses()
    yield
    providers_fake.reset_scripted_tool_responses()


# ---------------------------------------------------------------------------
# 1) 客户端：SSE / 纯 JSON 双解析
# ---------------------------------------------------------------------------


def test_client_sse_initialize_and_tools_list(fake_transport):
    state = fake_transport({"sse": True})
    state["headers"] = {"Authorization": HEADER_SECRET}
    c = McpStreamableHttpClient(SECRET_URL, state["headers"], context="test")
    info = c.initialize()
    assert info["serverInfo"]["name"] == "fake-mcp"
    tools, cursor = c.list_tools()
    assert [t["name"] for t in tools] == ["demo"]
    assert cursor is None
    # 握手序列 + 通知在 tools/list 之前
    assert state["methods"] == ["initialize", "notifications/initialized", "tools/list"]
    assert state["last_auth"] == HEADER_SECRET  # 鉴权头原样透传


def test_client_plain_json_and_rpc_error_frame(fake_transport):
    fake_transport({"sse": False})
    c = McpStreamableHttpClient(SECRET_URL, context="test")
    c.initialize()
    tools, _ = c.list_tools()
    assert tools[0]["name"] == "demo"
    # 未知方法 → JSON-RPC error 帧 → 统一 MCP_ERROR(502)
    with pytest.raises(AppError) as ei:
        c._rpc("bogus/method", {}, rpc_id=9, expect_response=True)
    assert ei.value.code == "MCP_ERROR"
    assert ei.value.http_status == 502
    assert "-32601" in ei.value.message


def test_client_sse_multi_frame_picks_matching_id(fake_transport):
    fake_transport({"multi_frame": True})
    c = McpStreamableHttpClient(SECRET_URL, context="test")
    tools, cursor = c.list_tools()
    assert [t["name"] for t in tools] == ["demo"]  # 噪声通知帧不能顶掉响应帧
    assert cursor is None


def test_client_notification_4xx_is_tolerated(fake_transport):
    fake_transport({"notify_4xx": True})
    c = McpStreamableHttpClient(SECRET_URL, context="test")
    info = c.initialize()  # initialized 通知 404：容忍，不抛
    assert info["serverInfo"]["name"] == "fake-mcp"


def test_client_truncates_oversized_body(fake_transport):
    fake_transport({"sse": False, "huge_json": True})
    c = McpStreamableHttpClient(SECRET_URL, context="test")
    with pytest.raises(AppError) as ei:
        c.list_tools()
    assert ei.value.code == "MCP_ERROR"
    assert "1MB" in ei.value.message


def test_client_connection_error_maps_to_mcp_error(fake_transport):
    fake_transport({"raise_connect": True})
    c = McpStreamableHttpClient(SECRET_URL, context="test")
    c.initialize()
    with pytest.raises(AppError) as ei:
        c.list_tools()
    assert ei.value.code == "MCP_ERROR"


# ---------------------------------------------------------------------------
# 2) 管理 API：CRUD / 掩码 / SSRF / 权限 / 跨小院
# ---------------------------------------------------------------------------


def test_create_masks_url_and_headers_never_echo(client, db_session, yard):
    resp = client.post(
        "/api/v1/settings/mcp-servers",
        json={"name": "主服务器", "url": SECRET_URL, "headers": {"Authorization": HEADER_SECRET}},
        headers=_hdr(),
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["url_masked"] == "https://mcp-srv.invalid/***"
    assert data["headers_masked"]["Authorization"].startswith("Bear")
    assert data["tool_count"] == 0
    assert data["enabled"] is True
    # 明文（URL 带 key 段、头值）绝不回显
    assert "sk-mcp-666" not in resp.text
    assert "sup3r-s3cr3t-headervalue" not in resp.text
    row = db_session.get(McpServer, data["id"])
    assert row is not None and SECRET_URL not in (row.url_enc or "")
    assert HEADER_SECRET not in json.dumps(row.headers_enc or {})


def test_create_local_url_blocked(client, db_session, yard):
    resp = client.post(
        "/api/v1/settings/mcp-servers",
        json={"name": "内网", "url": "http://localhost:8123/mcp"},
        headers=_hdr(),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "SSRF_BLOCKED"
    from sqlalchemy import select

    assert db_session.scalars(select(McpServer.id)).first() is None


def test_create_duplicate_name_and_bad_transport(client, db_session, yard):
    _mk_server(db_session)
    resp = client.post(
        "/api/v1/settings/mcp-servers",
        json={"name": "主服务器", "url": SECRET_URL},
        headers=_hdr(),
    )
    assert resp.status_code == 409
    resp2 = client.post(
        "/api/v1/settings/mcp-servers",
        json={"name": "另一台", "url": SECRET_URL, "transport": "stdio"},
        headers=_hdr(),
    )
    assert resp2.status_code == 400
    assert resp2.json()["error"]["code"] == "INVALID_REQUEST"


def test_patch_semantics_and_delete(client, db_session, yard):
    srv = _mk_server(db_session)
    # 换 URL：清零失败计数
    srv.consecutive_failures = 3
    db_session.commit()
    resp = client.patch(
        f"/api/v1/settings/mcp-servers/{srv.id}",
        json={"url": "https://mcp-alt.invalid/rpc"},
        headers=_hdr(),
    )
    assert resp.status_code == 200
    db_session.refresh(srv)
    assert srv.consecutive_failures == 0
    # 停用再启用：重新启用同样清零
    client.patch(f"/api/v1/settings/mcp-servers/{srv.id}", json={"enabled": False}, headers=_hdr())
    srv.consecutive_failures = 2
    db_session.commit()
    resp = client.patch(
        f"/api/v1/settings/mcp-servers/{srv.id}", json={"enabled": True}, headers=_hdr()
    )
    assert resp.status_code == 200
    db_session.refresh(srv)
    assert srv.enabled is True and srv.consecutive_failures == 0
    # 删除后 404
    deleted = client.delete(f"/api/v1/settings/mcp-servers/{srv.id}", headers=_hdr())
    assert deleted.status_code == 200
    assert (
        client.patch(
            f"/api/v1/settings/mcp-servers/{srv.id}", json={"name": "x"}, headers=_hdr()
        ).status_code
        == 404
    )


def test_cross_yard_unified_404(client, db_session, yard, other_yard):
    srv = _mk_server(db_session)
    b = _hdr(yard_id=OTHER_YARD)
    assert client.get("/api/v1/settings/mcp-servers", headers=b).json()["data"] == []
    for call in (
        client.get(f"/api/v1/settings/mcp-servers/{srv.id}/tools", headers=b),
        client.post(f"/api/v1/settings/mcp-servers/{srv.id}/test", headers=b),
        client.patch(f"/api/v1/settings/mcp-servers/{srv.id}", json={"name": "改"}, headers=b),
        client.delete(f"/api/v1/settings/mcp-servers/{srv.id}", headers=b),
    ):
        assert call.status_code == 404


def test_non_admin_forbidden(client, db_session, yard):
    resp = client.get("/api/v1/settings/mcp-servers", headers=_hdr(roles=("operator",)))
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3) 连接测试端点：快照 / 失败计数
# ---------------------------------------------------------------------------


def test_test_endpoint_snapshots_cache(client, db_session, yard, fake_transport):
    fake_transport({"sse": True})
    srv = _mk_server(db_session, headers={"Authorization": HEADER_SECRET})
    srv.consecutive_failures = 2
    db_session.commit()
    resp = client.post(f"/api/v1/settings/mcp-servers/{srv.id}/test", headers=_hdr())
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ok"] is True and data["tool_count"] == 1 and data["has_more"] is False
    assert len(data["tools"][0]["description"]) <= 80  # 展示层截断 80 字
    db_session.refresh(srv)
    entry = srv.tool_cache_json["demo"]
    assert entry["input_schema"] == DEMO_SCHEMA and entry["cached_at"]
    assert srv.last_test_at is not None
    assert srv.consecutive_failures == 0
    # 列表体现缓存数；GET /tools 读缓存不联网
    listed = client.get("/api/v1/settings/mcp-servers", headers=_hdr()).json()["data"]
    assert listed[0]["tool_count"] == 1
    tools_resp = client.get(f"/api/v1/settings/mcp-servers/{srv.id}/tools", headers=_hdr())
    assert tools_resp.json()["data"]["tools"][0]["name"] == "demo"


def test_test_endpoint_next_cursor_honest_flag(client, db_session, yard, fake_transport):
    fake_transport({"sse": False, "next_cursor": "abc"})
    srv = _mk_server(db_session)
    data = client.post(f"/api/v1/settings/mcp-servers/{srv.id}/test", headers=_hdr()).json()["data"]
    assert data["has_more"] is True  # 只取一页：如实回报


def test_test_endpoint_failure_records_error(client, db_session, yard, fake_transport):
    fake_transport({"http_503": True})
    srv = _mk_server(db_session)
    resp = client.post(f"/api/v1/settings/mcp-servers/{srv.id}/test", headers=_hdr())
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "MCP_ERROR"
    db_session.refresh(srv)
    assert srv.consecutive_failures == 1 and "503" in (srv.last_error or "")
    audits = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "mcp_server_failed", AuditLog.resource_id == srv.id)
        .count()
    )
    assert audits == 1  # 失败持久化（错误响应不吞计数）


# ---------------------------------------------------------------------------
# 4) 执行链路（registry 回落 / 熔断 / 审批闸）
# ---------------------------------------------------------------------------


def _wire(srv: McpServer, tool: str = "demo") -> str:
    return tool_bridge.build_wire_name(srv.id, tool)


def _approve(db, yard_id: str, tool_code: str, args: dict) -> None:
    db.add(
        ToolApproval(
            yard_id=yard_id,
            tool_code=tool_code,
            params_json=args,
            params_hash=params_hash(args),
            status="approved",
            requested_by="u-mcp",
            decided_by="u-admin",
        )
    )
    db.commit()


async def test_registry_execute_mcp_success_resets_breaker(db_session, yard, fake_transport):
    state = fake_transport({"sse": True})
    srv = _mk_server(db_session, yard.id)
    service.test_connection(db_session, srv, request_id="req")
    db_session.commit()
    assert srv.tool_cache_json and "demo" in srv.tool_cache_json
    srv.consecutive_failures = 3  # 之前失败过：本轮连接成功必须清零
    db_session.commit()
    wire = _wire(srv)
    args = {"city": "北京"}
    _approve(db_session, yard.id, wire, args)
    result = await tool_registry.execute(db_session, wire, args, _ctx(yard.id), "req-exec")
    assert result["text"] == "mock 工具结果"
    assert result["tool"] == "demo" and result["is_error"] is False
    db_session.refresh(srv)
    assert srv.consecutive_failures == 0
    assert state["last_arguments"] == args
    assert state["methods"].count("tools/call") == 1


async def test_registry_execute_tool_is_error_semantics(db_session, yard, fake_transport):
    fake_transport({"sse": False, "call_is_error": True, "call_text": "上游炸了"})
    srv = _mk_server(db_session, yard.id)
    service.test_connection(db_session, srv, request_id="req")
    srv.consecutive_failures = 2
    db_session.commit()
    wire = _wire(srv)
    args = {"city": "上海"}
    _approve(db_session, yard.id, wire, args)
    with pytest.raises(AppError) as ei:
        await tool_registry.execute(db_session, wire, args, _ctx(yard.id), "req-err")
    assert ei.value.code == "MCP_ERROR"
    assert "MCP 工具返回错误" in ei.value.message and "上游炸了" in ei.value.message
    assert ei.value.details.get("is_tool_error") is True
    db_session.refresh(srv)
    assert srv.consecutive_failures == 0  # 服务连通：工具级错误不计熔断


async def test_connection_failures_break_after_five(db_session, yard, fake_transport):
    state = fake_transport({"sse": True})
    srv = _mk_server(db_session, yard.id)
    service.test_connection(db_session, srv, request_id="req")
    db_session.commit()
    state["raise_connect"] = True  # 缓存就绪后才让服务器"断网"：只打 tools/call
    wire = _wire(srv)
    for _ in range(5):
        with pytest.raises(AppError) as ei:
            tool_bridge.execute_mcp_tool(db_session, wire, {"city": "x"}, _ctx(yard.id))
        assert ei.value.code == "MCP_ERROR"
        db_session.commit()
    db_session.refresh(srv)
    assert srv.enabled is False and srv.consecutive_failures == 5
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "mcp_server_failed", AuditLog.resource_id == srv.id)
        .count()
        == 5
    )
    assert (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "mcp_server_disabled", AuditLog.resource_id == srv.id)
        .count()
        == 1
    )
    # 熔断后：缓存工具即刻不可见（registry 回落 → TOOL_NOT_FOUND）
    assert tool_bridge.lookup_mcp_tool(db_session, yard.id, wire) is None
    with pytest.raises(AppError) as ei2:
        await tool_registry.execute(db_session, wire, {"city": "x"}, _ctx(yard.id), "req")
    assert ei2.value.code == "TOOL_NOT_FOUND"
    assert state["call_count"] == 5  # 停用后没有第 6 次外呼


async def test_unknown_mcp_tool_still_404(db_session, yard, fake_transport):
    fake_transport({"sse": True})
    _mk_server(db_session)
    ghost = tool_bridge.build_wire_name("ffffffff-1111", "ghost")
    with pytest.raises(AppError) as ei:
        await tool_registry.execute(db_session, ghost, {}, _ctx(yard.id), "req")
    assert ei.value.code == "TOOL_NOT_FOUND"


# ---------------------------------------------------------------------------
# 5) declarative 集成：审核队列 + 循环真实调用
# ---------------------------------------------------------------------------


def test_assess_risk_mcp_high_and_review_queue(client, db_session, yard):
    # 纯函数口径：mcp__ 一律 high（不看工具名内容）
    assert declarative.assess_risk(["mcp__abcd1234__query"], ["yard"]) == RISK_HIGH
    body = {
        "agent_code": "mcp_helper",
        "name": "MCP 助手",
        "persona_prompt": "你是接入外部工具的助手。",
        "task_prompt_template": "问题：{{question}}",
        "allowed_tools": ["mcp__abcd1234__query"],
        "knowledge_scopes": ["yard"],
    }
    created = client.post("/api/v1/agents/declarative", json=body, headers=_hdr())
    assert created.status_code == 200
    data = created.json()["data"]
    assert data["risk_level"] == "high"
    pub = client.post(f"/api/v1/agents/declarative/{data['id']}/publish", headers=_hdr())
    assert pub.status_code == 200
    # yard_admin 不能自批高风险：进平台审核队列
    assert pub.json()["data"]["status"] == STATUS_PENDING


async def test_declarative_loop_executes_mcp_tool(db_session, yard, fake_transport, monkeypatch):
    state = fake_transport({"sse": True})
    srv = _mk_server(db_session, yard_id=yard.id)
    service.test_connection(db_session, srv, request_id="req")
    db_session.commit()
    wire = _wire(srv)
    args = {"city": "济南"}
    _approve(db_session, yard.id, wire, args)

    agent = YardAgent(
        yard_id=yard.id,
        agent_code="mcp_loop_agent",
        name="MCP 循环助手",
        persona_prompt="你是外部工具助手。",
        task_prompt_template="请回答：{{question}}",
        allowed_tools=[wire],
        knowledge_scopes=["yard"],
        status=STATUS_PUBLISHED,
        risk_level=RISK_HIGH,
        version="1.0.0",
    )
    db_session.add(agent)
    db_session.commit()

    providers_fake.set_scripted_tool_responses(
        [
            {"tool_calls": [{"id": "c1", "name": wire, "args": args}]},
            {"content": "根据 MCP 工具结果作答"},
        ]
    )
    seen: list = []
    orig = complete_with_logging

    async def _spy(db, request):
        seen.append(request)
        return await orig(db, request)

    monkeypatch.setattr("app.modules.llm.service.complete_with_logging", _spy)

    result = await declarative.run_declarative_agent(
        db_session,
        agent=agent,
        input_data={"question": "济南天气如何"},
        ctx=_ctx(yard.id),
        task_id="run-mcp-1",
    )
    assert result.output["answer"] == "根据 MCP 工具结果作答"
    # ① 工具定义随首轮请求下发（wire 名 + 缓存 inputSchema）
    names = [t["function"]["name"] for t in (seen[0].tools or [])]
    assert wire in names
    # ② 真实外呼恰好一次，结果以 role=tool 回填进第二次 LLM 请求
    assert state["call_count"] == 1
    tool_msgs = [m for m in seen[1].messages if m.role == "tool"]
    assert len(tool_msgs) == 1 and "mock 工具结果" in (tool_msgs[0].content or "")
    # ③ 步骤形状与工具步记录
    steps = rs.list_steps(db_session, yard_id=yard.id, run_id="run-mcp-1")
    assert [(s.step_type, s.status) for s in steps] == [
        ("llm_call", "ok"),
        ("tool_call", "ok"),
        ("llm_call", "ok"),
        ("final", "ok"),
    ]
    assert steps[1].tool_code == wire
    db_session.refresh(srv)
    assert srv.consecutive_failures == 0 and srv.last_error is None
