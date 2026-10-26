"""搜索服务单测：供应商降级链、缓存、配额、全失败 503、联网状态（全离线）。"""

import httpx
import pytest
from app.core.errors import AppError
from app.modules.search import providers, service

YARD = "yard-001"

BASE_SETTINGS = {
    "enabled": True,
    "provider": "searxng",
    "searxng_url": "https://searx.local",
    "tavily_api_key": "",
    "brave_api_key": "",
    "daily_limit": 500,
    "timeout_seconds": 3,
}


@pytest.fixture(autouse=True)
def _clean_state():
    service._reset_state()
    service.set_test_transport(None)
    providers.set_test_transport(None)
    yield
    service._reset_state()
    service.set_test_transport(None)


def _use_settings(monkeypatch, **overrides) -> dict:
    cfg = {**BASE_SETTINGS, **overrides}
    monkeypatch.setattr(service, "get_setting", lambda db, yard_id, key: dict(cfg))
    return cfg


def _ok_handler(counter: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter["calls"] = counter.get("calls", 0) + 1
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "结果",
                        "url": "https://example.com/1",
                        "content": "摘要内容",
                        "engine": "bing",
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 供应商选择
# ---------------------------------------------------------------------------


def test_select_providers_preferred_first_and_key_gated(monkeypatch):
    _use_settings(
        monkeypatch,
        provider="brave",
        tavily_api_key="t-key",
        brave_api_key="b-key",
    )
    primary, chain = service.select_providers(None, YARD)
    assert primary == "brave"
    assert [p.code for p in chain] == ["brave", "searxng", "tavily"]


def test_select_providers_skips_unconfigured_keys(monkeypatch):
    _use_settings(monkeypatch, provider="tavily")
    primary, chain = service.select_providers(None, YARD)
    # tavily 没 Key → 主供应商回落 searxng，链里也没有 tavily
    assert primary == "searxng"
    assert [p.code for p in chain] == ["searxng"]


def test_select_providers_disabled(monkeypatch):
    _use_settings(monkeypatch, enabled=False)
    assert service.select_providers(None, YARD) == ("", [])


# ---------------------------------------------------------------------------
# 搜索主流程
# ---------------------------------------------------------------------------


async def test_search_web_returns_normalized_result(monkeypatch):
    _use_settings(monkeypatch)
    service.set_test_transport(_ok_handler())

    data = await service.search_web(None, YARD, " 水稻 ", count=99, lang="zh-CN")

    assert data["query"] == "水稻"
    assert data["provider"] == "searxng"
    assert data["cached"] is False
    assert data["fetched_at"].endswith("Z")
    assert data["results"] == [
        {
            "title": "结果",
            "url": "https://example.com/1",
            "snippet": "摘要内容",
            "source": "bing",
            "published": None,
        }
    ]


async def test_search_web_cache_hit_skips_upstream(monkeypatch):
    _use_settings(monkeypatch)
    counter: dict = {}
    service.set_test_transport(_ok_handler(counter))

    first = await service.search_web(None, YARD, "玉米", count=3)
    second = await service.search_web(None, YARD, "玉米", count=3)

    assert counter["calls"] == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["fetched_at"] == first["fetched_at"]


async def test_search_web_falls_back_to_tavily(monkeypatch):
    _use_settings(monkeypatch, provider="searxng", tavily_api_key="t-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if "searx.local" in str(request.url):
            return httpx.Response(502)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "T", "url": "https://example.com/t", "content": "c"},
                ]
            },
        )

    service.set_test_transport(httpx.MockTransport(handler))
    data = await service.search_web(None, YARD, "小麦", count=1)
    assert data["provider"] == "tavily"
    assert data["results"][0]["url"] == "https://example.com/t"


async def test_search_web_all_fail_raises_503(monkeypatch):
    _use_settings(monkeypatch, provider="searxng", tavily_api_key="t-key")
    service.set_test_transport(
        httpx.MockTransport(lambda request: httpx.Response(500, json={"error": "x"}))
    )

    with pytest.raises(AppError) as exc:
        await service.search_web(None, YARD, "全失败", count=2)
    assert exc.value.code == "SEARCH_PROVIDER_UNAVAILABLE"
    assert exc.value.http_status == 503


async def test_search_web_quota_exceeded(monkeypatch):
    _use_settings(monkeypatch, daily_limit=2)
    service.set_test_transport(_ok_handler())

    await service.search_web(None, YARD, "q1", count=1)
    await service.search_web(None, YARD, "q2", count=1)
    with pytest.raises(AppError) as exc:
        await service.search_web(None, YARD, "q3", count=1)
    assert exc.value.code == "SEARCH_QUOTA_EXCEEDED"
    assert exc.value.http_status == 429

    snapshot = service.quota_snapshot(None, YARD)
    assert snapshot["used"] == 2
    assert snapshot["limit"] == 2
    assert snapshot["remaining"] == 0


async def test_search_web_cache_hit_does_not_consume_quota(monkeypatch):
    _use_settings(monkeypatch, daily_limit=1)
    service.set_test_transport(_ok_handler())

    await service.search_web(None, YARD, "same", count=1)
    again = await service.search_web(None, YARD, "same", count=1)
    assert again["cached"] is True
    assert service.quota_snapshot(None, YARD)["used"] == 1


async def test_search_web_empty_query_rejected(monkeypatch):
    _use_settings(monkeypatch)
    with pytest.raises(AppError) as exc:
        await service.search_web(None, YARD, "   ")
    assert exc.value.code == "INVALID_REQUEST"


async def test_search_web_no_provider_raises_503(monkeypatch):
    _use_settings(monkeypatch, provider="searxng", searxng_url="")
    with pytest.raises(AppError) as exc:
        await service.search_web(None, YARD, "无供应商")
    assert exc.value.http_status == 503


# ---------------------------------------------------------------------------
# 联网状态
# ---------------------------------------------------------------------------


async def test_network_status_online(monkeypatch):
    _use_settings(monkeypatch)
    service.set_test_transport(httpx.MockTransport(lambda request: httpx.Response(200, text="ok")))
    data = await service.network_status(None, YARD)
    assert data["mode"] == "online"
    assert data["provider"] == "searxng"
    assert data["checked_at"].endswith("Z")


async def test_network_status_degraded(monkeypatch):
    _use_settings(monkeypatch, provider="searxng", tavily_api_key="t-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if "searx.local" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(401)  # API 可达但未授权，仍算可达

    service.set_test_transport(httpx.MockTransport(handler))
    data = await service.network_status(None, YARD)
    assert data["mode"] == "degraded"
    assert data["provider"] == "tavily"


async def test_network_status_offline_never_raises(monkeypatch):
    _use_settings(monkeypatch, tavily_api_key="t-key")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    service.set_test_transport(httpx.MockTransport(handler))
    data = await service.network_status(None, YARD)
    assert data["mode"] == "offline"
    assert "不可达" in data["reason"]


async def test_network_status_cached_for_60s(monkeypatch):
    _use_settings(monkeypatch)
    counter: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] = counter.get("calls", 0) + 1
        return httpx.Response(200)

    service.set_test_transport(httpx.MockTransport(handler))
    await service.network_status(None, YARD)
    await service.network_status(None, YARD)
    assert counter["calls"] == 1


async def test_network_status_settings_error_is_offline(monkeypatch):
    def boom(db, yard_id, key):
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "get_setting", boom)
    data = await service.network_status(None, YARD)
    assert data["mode"] == "offline"
