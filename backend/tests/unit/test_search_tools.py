"""联网搜索工具注册与上下文校验单测。"""

import jsonschema
import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.search import tools
from app.modules.tools.registry import tool_registry


@pytest.fixture()
def _isolated_registry():
    saved = dict(tool_registry._tools)
    tool_registry._tools.clear()
    yield
    tool_registry._tools.clear()
    tool_registry._tools.update(saved)


def _ctx(yard_id: str = "yard-001") -> YardContext:
    return YardContext(yard_id=yard_id, user_id="u-1", roles=["viewer"], request_id="req-test")


def test_register_search_tools_idempotent(_isolated_registry):
    tools.register_search_tools()
    tools.register_search_tools()

    search = tool_registry.get("web.search")
    fetch = tool_registry.get("web.fetch")
    assert search.risk_level == "read_only"
    assert fetch.risk_level == "read_only"
    assert "联网搜索" in search.description
    assert "禁止内网" in fetch.description
    assert [t.code for t in tool_registry.list_tools()] == ["web.fetch", "web.search"]


def test_search_input_schema_bounds(_isolated_registry):
    tools.register_search_tools()
    schema = tool_registry.get("web.search").input_schema

    jsonschema.validate({"query": "水稻", "count": 8}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"query": "水稻", "count": 9}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"count": 3}, schema)

    fetch_schema = tool_registry.get("web.fetch").input_schema
    jsonschema.validate({"url": "https://example.com", "max_chars": 6000}, fetch_schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"url": "https://example.com", "max_chars": 6001}, fetch_schema)


async def test_tool_requires_yard_context():
    with pytest.raises(AppError) as exc:
        await tools._web_search_tool_func({"query": "x"}, _ctx(yard_id=""))
    assert exc.value.code == "YARD_REQUIRED"

    with pytest.raises(AppError) as exc:
        await tools._web_fetch_tool_func({"url": "https://example.com"}, _ctx(yard_id=""))
    assert exc.value.code == "YARD_REQUIRED"


async def test_fetch_tool_blocks_internal():
    # yard 上下文存在时直接走 egress 的拦截分支（不联网）
    with pytest.raises(AppError) as exc:
        await tools._web_fetch_tool_func({"url": "http://169.254.169.254/"}, _ctx())
    assert exc.value.code == "WEB_FETCH_BLOCKED"
