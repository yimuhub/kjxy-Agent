"""搜索供应商解析单测：三供应商解析、URL 过滤、截断、失败 503（全离线）。"""

import json

import httpx
import pytest
from app.core.errors import AppError
from app.modules.search import providers


@pytest.fixture(autouse=True)
def _reset_transport():
    providers.set_test_transport(None)
    yield
    providers.set_test_transport(None)


def _transport(handler) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


async def test_searxng_parses_and_filters_non_http():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "水稻分蘖期管理",
                        "url": "https://example.com/rice",
                        "content": "浅水勤灌，" + "字" * 800,
                        "engine": "bing",
                        "publishedDate": "2026-09-01",
                    },
                    {
                        "title": "内网地址",
                        "url": "ftp://127.0.0.1/admin",
                        "content": "不应保留",
                    },
                    {"title": "缺失 URL", "content": "x"},
                ]
            },
        )

    providers.set_test_transport(_transport(handler))
    hits = await providers.SearxngProvider("https://searx.local/").search(
        "水稻", count=5, lang="zh-CN"
    )

    assert len(hits) == 1
    hit = hits[0]
    assert hit.url == "https://example.com/rice"
    assert hit.source == "bing"
    assert hit.published == "2026-09-01"
    assert len(hit.snippet) == providers.SNIPPET_MAX_CHARS
    assert "q=" in seen["url"] and "format=json" in seen["url"]
    assert "language=zh-CN" in seen["url"]


async def test_tavily_posts_api_key_in_body():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "title": "Tavily 结果",
                        "url": "https://example.org/a",
                        "content": "摘要",
                        "published_date": "2026-10-01",
                    },
                    {"title": "非 http", "url": "javascript:alert(1)", "content": "x"},
                ]
            },
        )

    providers.set_test_transport(_transport(handler))
    hits = await providers.TavilyProvider("tvly-secret", timeout=3).search(
        "玉米", count=20, lang="zh-CN"
    )

    assert captured["auth"] is None  # 密钥只在 body
    assert captured["body"]["api_key"] == "tvly-secret"
    assert captured["body"]["query"] == "玉米"
    assert captured["body"]["max_results"] == providers.MAX_RESULTS
    assert len(hits) == 1
    assert hits[0].source == "tavily"
    assert hits[0].published == "2026-10-01"


async def test_brave_uses_subscription_header_and_web_results():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["token"] = request.headers.get("x-subscription-token")
        captured["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "web": {
                    "results": [
                        {
                            "title": "Brave 结果",
                            "url": "https://example.net/b",
                            "description": "描述",
                            "page_age": "2 days ago",
                        }
                    ]
                }
            },
        )

    providers.set_test_transport(_transport(handler))
    hits = await providers.BraveProvider("brave-key").search("小麦", count=3, lang="zh-CN")

    assert captured["token"] == "brave-key"
    assert "count=3" in captured["url"]
    assert hits[0].source == "brave"
    assert hits[0].published == "2 days ago"


@pytest.mark.parametrize(
    "provider",
    [
        providers.SearxngProvider("https://searx.local"),
        providers.TavilyProvider("key"),
        providers.BraveProvider("key"),
    ],
)
async def test_provider_failure_maps_to_503(provider):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    providers.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as exc:
        await provider.search("q", count=5, lang="zh-CN")
    assert exc.value.code == "SEARCH_PROVIDER_UNAVAILABLE"
    assert exc.value.http_status == 503


async def test_provider_bad_payload_maps_to_503():
    providers.set_test_transport(_transport(lambda request: httpx.Response(200, json={"x": 1})))
    with pytest.raises(AppError) as exc:
        await providers.SearxngProvider("https://searx.local").search("q", count=1, lang="zh-CN")
    assert exc.value.code == "SEARCH_PROVIDER_UNAVAILABLE"


async def test_provider_network_error_maps_to_503():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    providers.set_test_transport(_transport(handler))
    with pytest.raises(AppError) as exc:
        await providers.BraveProvider("key").search("q", count=1, lang="zh-CN")
    assert exc.value.http_status == 503


async def test_missing_key_raises_503():
    with pytest.raises(AppError) as exc:
        await providers.TavilyProvider("").search("q", count=1, lang="zh-CN")
    assert exc.value.code == "SEARCH_PROVIDER_UNAVAILABLE"
