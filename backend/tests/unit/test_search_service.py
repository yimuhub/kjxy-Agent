"""搜索服务单测：供应商降级链（含内置源）、缓存、配额、全失败 503、联网状态。

全部请求走 MockTransport，禁止真实出网。
"""

import httpx
import pytest
from app.core.errors import AppError
from app.modules.search import providers, service
from app.modules.yards import settings_service

YARD = "yard-001"

BASE_SETTINGS = {
    "enabled": True,
    "provider": "searxng",
    "builtin_sources": [],
    "searxng_url": "https://searx.local",
    "tavily_api_key": "",
    "brave_api_key": "",
    "daily_limit": 500,
    "timeout_seconds": 3,
}

BING_OK_HTML = (
    "<html><body><ol id='b_results'>"
    "<li class='b_algo'><h2><a href='https://example.com/bing1'>必应结果</a></h2>"
    "<div class='b_caption'><p>必应摘要</p></div></li>"
    "</ol></body></html>"
)

SOGOU_OK_HTML = (
    "<html><body><div class='vrwrap'>"
    "<h3><a href='https://example.com/sogou1'>搜狗结果</a></h3>"
    "<div class='str-text-info'>搜狗摘要</div></div></body></html>"
)


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


def _auto_settings(monkeypatch, **overrides) -> dict:
    overrides.setdefault("builtin_sources", ["bing", "sogou", "so360"])
    return _use_settings(monkeypatch, provider="auto", **overrides)


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


def _offline_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

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


def test_select_providers_auto_prefers_builtin_sources(monkeypatch):
    _auto_settings(monkeypatch)
    primary, chain = service.select_providers(None, YARD)
    assert primary == "bing"
    assert [p.code for p in chain] == ["bing", "sogou", "so360", "searxng"]


def test_select_providers_auto_follows_builtin_order_subset(monkeypatch):
    _auto_settings(monkeypatch, builtin_sources=["so360", "bing"])
    primary, chain = service.select_providers(None, YARD)
    assert primary == "so360"
    assert [p.code for p in chain] == ["so360", "bing", "searxng"]


def test_select_providers_auto_skips_searxng_without_url(monkeypatch):
    _auto_settings(monkeypatch, searxng_url="")
    primary, chain = service.select_providers(None, YARD)
    assert primary == "bing"
    assert [p.code for p in chain] == ["bing", "sogou", "so360"]


def test_select_providers_explicit_provider_then_builtin(monkeypatch):
    _use_settings(
        monkeypatch,
        provider="searxng",
        builtin_sources=["bing"],
        tavily_api_key="t-key",
    )
    primary, chain = service.select_providers(None, YARD)
    assert primary == "searxng"
    assert [p.code for p in chain] == ["searxng", "bing", "tavily"]


def test_select_providers_no_duplicates(monkeypatch):
    _use_settings(monkeypatch, provider="brave", brave_api_key="b", builtin_sources=["bing"])
    primary, chain = service.select_providers(None, YARD)
    codes = [p.code for p in chain]
    assert codes == ["brave", "bing", "searxng"]
    assert len(codes) == len(set(codes))


def test_select_providers_all_sources_disabled(monkeypatch):
    _use_settings(monkeypatch, provider="auto", builtin_sources=[], searxng_url="")
    assert service.select_providers(None, YARD) == ("", [])


def test_select_providers_zero_config_defaults(monkeypatch):
    cfg = dict(settings_service.DEFAULTS["search"])
    monkeypatch.setattr(service, "get_setting", lambda db, yard_id, key: dict(cfg))
    primary, chain = service.select_providers(None, YARD)
    assert primary == "bing"
    assert [p.code for p in chain] == ["bing", "sogou", "so360", "searxng"]


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


async def test_search_web_auto_uses_builtin_first(monkeypatch):
    _auto_settings(monkeypatch)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text=BING_OK_HTML)

    service.set_test_transport(httpx.MockTransport(handler))
    data = await service.search_web(None, YARD, "农业", count=3)

    assert seen["url"].startswith("https://cn.bing.com/search")
    assert data["provider"] == "bing"
    assert data["results"] == [
        {
            "title": "必应结果",
            "url": "https://example.com/bing1",
            "snippet": "必应摘要",
            "source": "bing",
            "published": None,
        }
    ]


async def test_search_web_auto_falls_back_to_next_builtin(monkeypatch):
    _auto_settings(monkeypatch, builtin_sources=["bing", "sogou"])

    def handler(request: httpx.Request) -> httpx.Response:
        if "cn.bing.com" in str(request.url):
            return httpx.Response(503)
        if "sogou.com" in str(request.url):
            return httpx.Response(200, text=SOGOU_OK_HTML)
        return httpx.Response(500)

    service.set_test_transport(httpx.MockTransport(handler))
    data = await service.search_web(None, YARD, "小麦", count=1)

    assert data["provider"] == "sogou"
    assert data["results"][0]["url"] == "https://example.com/sogou1"


async def test_search_web_auto_all_builtin_fail_falls_back_to_searxng(monkeypatch):
    _auto_settings(monkeypatch, builtin_sources=["bing"])

    def handler(request: httpx.Request) -> httpx.Response:
        if "cn.bing.com" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"title": "S", "url": "https://example.com/s", "content": "c"},
                ]
            },
        )

    service.set_test_transport(httpx.MockTransport(handler))
    data = await service.search_web(None, YARD, "玉米", count=1)
    assert data["provider"] == "searxng"


async def test_search_web_all_fail_details_include_internet_and_sources(monkeypatch):
    _auto_settings(monkeypatch)
    service.set_test_transport(
        httpx.MockTransport(lambda request: httpx.Response(500, json={"error": "x"}))
    )

    with pytest.raises(AppError) as exc:
        await service.search_web(None, YARD, "全失败", count=2)
    assert exc.value.code == "SEARCH_PROVIDER_UNAVAILABLE"
    assert exc.value.http_status == 503
    details = exc.value.details
    assert details["internet"] is False  # MockTransport 全部 500 → 视为无网
    assert details["sources"] == ["bing", "sogou", "so360", "searxng"]
    assert details["primary"] == "bing"
    assert [f["provider"] for f in details["failures"]] == details["sources"]


async def test_search_web_all_fail_with_internet_message(monkeypatch):
    _auto_settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        # 公网可达（百度 200），但所有搜索源失败
        if "baidu.com" in str(request.url):
            return httpx.Response(200)
        return httpx.Response(500)

    service.set_test_transport(httpx.MockTransport(handler))
    with pytest.raises(AppError) as exc:
        await service.search_web(None, YARD, "全失败", count=2)
    assert "联网受限" in exc.value.message
    assert exc.value.details["internet"] is True


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


async def test_network_status_online_with_builtin(monkeypatch):
    _auto_settings(monkeypatch)
    service.set_test_transport(
        httpx.MockTransport(lambda request: httpx.Response(200, text=BING_OK_HTML))
    )
    data = await service.network_status(None, YARD)
    assert data["mode"] == "online"
    assert data["provider"] == "bing"
    assert data["internet"] is True
    assert data["checked_at"].endswith("Z")


async def test_network_status_online_with_searxng(monkeypatch):
    _use_settings(monkeypatch)
    service.set_test_transport(httpx.MockTransport(lambda request: httpx.Response(200, text="ok")))
    data = await service.network_status(None, YARD)
    assert data["mode"] == "online"
    assert data["provider"] == "searxng"


async def test_network_status_degraded_to_next_builtin(monkeypatch):
    _auto_settings(monkeypatch, builtin_sources=["bing", "sogou"])

    def handler(request: httpx.Request) -> httpx.Response:
        if "cn.bing.com/search" in str(request.url):
            return httpx.Response(503)
        return httpx.Response(200, text=SOGOU_OK_HTML)

    service.set_test_transport(httpx.MockTransport(handler))
    data = await service.network_status(None, YARD)
    assert data["mode"] == "degraded"
    assert data["provider"] == "sogou"
    assert data["internet"] is True
    assert "已降级" in data["reason"]


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


async def test_network_status_degraded_when_all_sources_fail(monkeypatch):
    _auto_settings(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        # 公网可达（探测点 200），但所有搜索源失败
        url = str(request.url)
        if "baidu.com" in url or url.rstrip("/") == "https://cn.bing.com":
            return httpx.Response(200)
        return httpx.Response(500)

    service.set_test_transport(httpx.MockTransport(handler))
    data = await service.network_status(None, YARD)
    assert data["mode"] == "degraded"
    assert data["internet"] is True
    assert "联网受限" in data["reason"]
    assert "所有搜索源" in data["reason"]


async def test_network_status_degraded_when_chain_empty(monkeypatch):
    _use_settings(monkeypatch, provider="auto", builtin_sources=[], searxng_url="")
    service.set_test_transport(httpx.MockTransport(lambda request: httpx.Response(200)))
    data = await service.network_status(None, YARD)
    assert data["mode"] == "degraded"
    assert data["internet"] is True
    assert "未启用任何搜索源" in data["reason"]


async def test_network_status_offline_never_raises(monkeypatch):
    _auto_settings(monkeypatch)
    service.set_test_transport(_offline_transport())
    data = await service.network_status(None, YARD)
    assert data["mode"] == "offline"
    assert data["internet"] is False
    assert "无网络" in data["reason"]


async def test_network_status_offline_when_sources_unreachable(monkeypatch):
    _use_settings(monkeypatch, provider="searxng")
    service.set_test_transport(
        httpx.MockTransport(lambda request: httpx.Response(500, text="down"))
    )
    data = await service.network_status(None, YARD)
    assert data["mode"] == "offline"
    assert data["internet"] is False


async def test_network_status_cached_for_60s(monkeypatch):
    _use_settings(monkeypatch)
    counter: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] = counter.get("calls", 0) + 1
        return httpx.Response(200)

    service.set_test_transport(httpx.MockTransport(handler))
    await service.network_status(None, YARD)
    await service.network_status(None, YARD)
    # 公网探测 + SearXNG 探测各一次，第二次走 60s 缓存
    assert counter["calls"] == 2


async def test_network_status_settings_error_is_offline(monkeypatch):
    def boom(db, yard_id, key):
        raise RuntimeError("db down")

    monkeypatch.setattr(service, "get_setting", boom)
    service.set_test_transport(_offline_transport())
    data = await service.network_status(None, YARD)
    assert data["mode"] == "offline"
    assert data["internet"] is False
