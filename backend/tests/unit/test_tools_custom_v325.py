"""自定义工具（v3.25 B-b）运行时与目录测试。

覆盖：一句话规格的静态校验与规范化、草稿→启用→解析→执行的完整链路、
prompt 工具复用模型、http 工具的出网调用与状态码处理、
内置工具「元数据下线 = 运行不可用」的一致性。
"""

import json
import os

import pytest

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-chars-for-unit-tests")
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDING_PROVIDER", "mock")

from cryptography.fernet import Fernet  # noqa: E402

os.environ.setdefault("SUPPLIER_KEY_SECRET", Fernet.generate_key().decode())

from app.core.deps import YardContext  # noqa: E402
from app.core.errors import AppError  # noqa: E402
from app.modules.tools import catalog_service, generator  # noqa: E402
from app.modules.tools.models import ToolDefinition  # noqa: E402
from app.modules.tools.registry import tool_registry  # noqa: E402
from app.modules.yards.repository import create_yard  # noqa: E402

YARD_ROLE = ["yard_admin"]
SCHEMA = {
    "type": "object",
    "properties": {"crop": {"type": "string", "description": "作物名"}},
    "required": ["crop"],
    "additionalProperties": False,
}


@pytest.fixture()
def yard(db_session):
    row = create_yard(db_session, name="工具目录测试院")
    db_session.commit()
    return row.id


def _ctx(yard_id: str) -> YardContext:
    return YardContext(yard_id=yard_id, user_id="u-tool", roles=YARD_ROLE, request_id="req-tool")


def _spec(**kw):
    base = {
        "code": "pest.alert_read",
        "name": "告警速读",
        "description": "把今天的设备告警读成人话",
        "input_schema": SCHEMA,
        "impl_kind": "prompt",
        "impl": {"prompt_template": "请总结 {crop} 的今日告警"},
        "risk_level": "read_only",
        "side_effect": False,
    }
    base.update(kw)
    return base


# ---------- 规格校验与规范化 ----------


def test_validate_spec_rejects_bad_schema_and_impl():
    assert generator.validate_spec(_spec(code="Bad-Code"))  # 非法 code
    assert generator.validate_spec(_spec(input_schema={"type": "array"}))
    assert generator.validate_spec(_spec(input_schema={"type": "object", "required": ["x"]}))
    assert generator.validate_spec(_spec(impl_kind="http", impl={"url": "ftp://x"}))
    assert generator.validate_spec(_spec(impl_kind="prompt", impl={}))
    assert generator.validate_spec(_spec(impl_kind="soap"))
    assert generator.validate_spec(_spec(risk_level="nope"))
    assert generator.validate_spec(_spec()) == []


def test_normalize_forces_closed_schema_and_escalates_write_http():
    spec = generator.normalize_spec(_spec(), utterance="x")
    assert spec["input_schema"]["additionalProperties"] is False

    http_spec = generator.normalize_spec(
        _spec(impl_kind="http", impl={"url": "https://api.example.test/v1/x", "method": "post"}),
        utterance="x",
    )
    assert http_spec["risk_level"] == "external_side_effect"  # 非 GET 一律升级为需审批
    assert http_spec["side_effect"] is True


def test_fallback_spec_is_valid_and_conservative():
    spec = generator.fallback_spec("帮我把今天的告警总结一下")
    assert generator.validate_spec(spec) == []
    assert spec["impl_kind"] == "prompt"
    assert spec["risk_level"] == "read_only"


# ---------- 目录：草稿 → 启用 → 解析 → 执行 ----------


def test_draft_never_resolves_until_enabled(db_session, yard):
    item = catalog_service.create_custom(db_session, yard, "u-tool", _spec())
    assert item["enabled"] is False
    assert item["source"] == "custom"

    with pytest.raises(AppError) as exc:
        tool_registry._resolve(db_session, item["code"], _ctx(yard))
    assert exc.value.code == "TOOL_NOT_FOUND"

    enabled = catalog_service.set_enabled(db_session, yard, item["code"], True)
    db_session.commit()
    assert enabled["enabled"] is True
    tool = tool_registry._resolve(db_session, item["code"], _ctx(yard))
    assert tool.name == "告警速读"
    assert tool.input_schema["additionalProperties"] is False


@pytest.mark.asyncio
async def test_prompt_tool_executes_and_renders_params(db_session, yard, monkeypatch):
    from app.modules.tools import custom_runtime

    captured: dict = {}

    async def fake_complete(db, request):
        captured["prompt"] = request.messages[0].content
        captured["purpose"] = request.metadata.purpose
        from app.modules.llm.schemas import LLMResponse

        return LLMResponse(content="今天 3 条告警，都在阈值内", model="fake-mock-1", latency_ms=8)

    monkeypatch.setattr(custom_runtime, "complete_with_logging", fake_complete)

    item = catalog_service.create_custom(db_session, yard, "u-tool", _spec())
    catalog_service.set_enabled(db_session, yard, item["code"], True)
    db_session.commit()

    tool = tool_registry._resolve(db_session, item["code"], _ctx(yard))
    out = await tool.func({"crop": "番茄"}, _ctx(yard))

    assert out["text"] == "今天 3 条告警，都在阈值内"
    # 模板里的 {crop} 被入参真实替换，且用途码登记在能力清单里
    assert "请总结 番茄 的今日告警" in captured["prompt"]
    assert captured["purpose"] == "tool_run"


class _FakeResponse:
    def __init__(self, payload, status=200, text=None):
        self._payload = payload
        self.status_code = status
        self.content = (text if text is not None else json.dumps(payload)).encode()
        self.text = self.content.decode()

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _FakeClient:
    """离线假 httpx.Client：记录请求，按预设返回（测试绝不出网）。"""

    outcome = _FakeResponse({"ok": True, "value": 42})
    calls: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, method, url, **kwargs):
        type(self).calls.append((method, url, kwargs))
        return self.outcome


@pytest.mark.asyncio
async def test_http_tool_calls_endpoint_and_masks_headers(db_session, yard, monkeypatch):
    import httpx

    _FakeClient.calls = []
    _FakeClient.outcome = _FakeResponse({"ok": True, "value": 42})
    monkeypatch.setattr(httpx, "Client", lambda **kw: _FakeClient(**kw))

    spec = _spec(
        code="farm.sync_records",
        name="同步农事记录",
        impl_kind="http",
        impl={
            "url": "https://api.example.test/v1/sync",
            "method": "POST",
            "headers": {"X-Token": "super-secret-token-value"},
        },
    )
    item = catalog_service.create_custom(db_session, yard, "u-tool", spec)
    catalog_service.set_enabled(db_session, yard, item["code"], True)
    db_session.commit()

    # 头值密文存储：详情视图只出打码，数据库里也看不到明文
    detail = catalog_service.get_detail(db_session, yard, item["code"])
    masked = detail["implementation"]["headers"]["X-Token"]
    assert "super-secret" not in masked
    row = db_session.query(ToolDefinition).filter_by(code=item["code"]).first()
    assert "super-secret-token-value" not in json.dumps(row.impl_json)

    tool = tool_registry._resolve(db_session, item["code"], _ctx(yard))
    out = await tool.func({"crop": "番茄"}, _ctx(yard))

    method, url, kwargs = _FakeClient.calls[0]
    assert (method, url) == ("POST", "https://api.example.test/v1/sync")
    assert kwargs["headers"]["X-Token"] == "super-secret-token-value"  # 运行时真实解密
    assert kwargs["json"] == {"crop": "番茄"}
    assert json.loads(out["text"])["value"] == 42

    # 异常状态码如实报错，不假装成功
    _FakeClient.outcome = _FakeResponse(None, status=500, text="boom")
    with pytest.raises(AppError) as exc:
        await tool.func({"crop": "番茄"}, _ctx(yard))
    assert exc.value.code == "TOOL_ERROR"


def test_builtin_metadata_disable_blocks_runtime(db_session, yard):
    """元数据行显式下线 → 运行时同样拒绝（与 GET /tools、插件校验同一口径）。"""
    code = "knowledge.search"
    registered = tool_registry._tools.get(code)
    if registered is None:  # 未注册该内置实现的本模块环境：跳过而非造假
        pytest.skip("当前进程未注册 knowledge.search 实现")
    row = db_session.query(ToolDefinition).filter_by(code=code).first()
    if row is None:
        row = ToolDefinition(
            code=code,
            name="知识检索",
            description="",
            input_schema_json={"type": "object"},
            required_permissions_json=[],
            risk_level="read_only",
            enabled=True,
        )
        db_session.add(row)
    row.enabled = False
    db_session.commit()

    with pytest.raises(AppError) as exc:
        tool_registry._resolve(db_session, code, _ctx(yard))
    assert exc.value.code == "TOOL_NOT_FOUND"
    row.enabled = True
    db_session.commit()


# ---------- 目录读写边界 ----------


def test_builtin_is_readonly_and_mcp_toggle_rejected(db_session, yard):
    row = ToolDefinition(
        code="builtin.demo",
        name="内置样例",
        description="",
        input_schema_json={"type": "object"},
        required_permissions_json=[],
        enabled=True,
        source="builtin",
    )
    db_session.add(row)
    db_session.commit()

    with pytest.raises(AppError) as exc:
        catalog_service.update_custom(db_session, yard, "builtin.demo", {"name": "改个名"})
    assert exc.value.code == "TOOL_READONLY"

    with pytest.raises(AppError) as mcp:
        catalog_service.set_enabled(db_session, yard, "mcp__abcd1234__ping", False)
    assert mcp.value.code == "TOOL_READONLY"


def test_catalog_merges_mcp_snapshot_as_readonly(db_session, yard):
    from app.modules.llm.supplier_crypto import encrypt_api_key
    from app.modules.mcp.models import McpServer

    db_session.add(
        McpServer(
            yard_id=yard,
            name="演示 MCP",
            url_enc=encrypt_api_key("http://127.0.0.1:9/mcp"),
            headers_enc={},
            enabled=True,
            tool_cache_json={"today_summary": {"description": "今日概况", "input_schema": {}}},
        )
    )
    db_session.commit()

    catalog = catalog_service.list_all(db_session, yard)
    mcp_items = [i for i in catalog["items"] if i["source"] == "mcp"]
    assert mcp_items and mcp_items[0]["readonly"] is True
    assert mcp_items[0]["risk_level"] == "external_side_effect"
    assert mcp_items[0]["code"].startswith("mcp__")
    assert catalog["source_labels"]["mcp"] == "来自 MCP 服务器"
