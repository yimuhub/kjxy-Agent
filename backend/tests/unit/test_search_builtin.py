"""内置免密钥搜索源单测：HTML 解析、广告/跳转过滤、限速、熔断（全离线）。

fixture HTML 为各引擎结果页的代表性缩样；全部请求走 MockTransport，禁止真实出网。
"""

import time

import httpx
import pytest
from app.core.errors import AppError
from app.modules.search import providers, providers_builtin
from app.modules.yards import settings_service

BING_HTML = """
<html><body><ol id="b_results">
  <li class="b_algo">
    <h2><a href="https://www.example.com/rice">水稻  分蘖期管理技术</a></h2>
    <div class="b_caption"><p>  浅水勤灌
    适时晒田 </p></div>
  </li>
  <li class="b_algo">
    <h2><a class="ec_tuiguang_ppzq" href="https://ad.example.com/promo">推广位标题</a></h2>
    <div class="b_caption"><p>广告摘要</p></div>
  </li>
  <li class="b_algo">
    <h2><a href="https://cn.bing.com/ck/a?u=x">跳转标题</a></h2>
    <p>跳转摘要</p>
  </li>
  <li class="b_algo"><h2><a href="ftp://example.com/file">非 http</a></h2></li>
  <li class="b_algo"><h2><a href="https://example.com/empty"></a></h2></li>
</ol></body></html>
"""

SOGOU_HTML = """
<html><body><div class="results">
  <div class="vrwrap">
    <h3 class="vr-title"><a href="https://www.example.org/maize">玉米种植要点</a></h3>
    <div class="str-text-info"> 合理密植   适时追肥 </div>
  </div>
  <div class="vrwrap">
    <h3 class="vr-title"><a class="ec-ad" href="https://ad.sogou.com/x">搜狗广告</a></h3>
    <div class="str-text-info">广告内容</div>
  </div>
  <div class="rb">
    <h3><a href="https://www.sogou.com/link?url=xyz">搜狗跳转</a></h3>
    <p>跳转说明</p>
  </div>
  <div class="rb"><h3><a href="javascript:void(0)">无效链接</a></h3></div>
</div></body></html>
"""

SO360_HTML = """
<html><body><ul class="result">
  <li class="res-list">
    <h3 class="res-title"><a href="https://www.example.net/wheat">小麦赤霉病防治</a></h3>
    <p class="res-desc"> 扬花期   一喷三防 </p>
  </li>
  <li class="res-list">
    <h3 class="res-title"><a class="promote" href="https://ad.so.com/y">360 推广</a></h3>
    <p class="res-desc">推广内容</p>
  </li>
  <li class="res-list">
    <h3 class="res-title"><a href="https://www.so.com/link?m=z">360 跳转</a></h3>
    <p class="res-desc">跳转说明</p>
  </li>
  <li class="res-list">
    <h3 class="res-title"><a href="https://ai.so.com/search/soc-xyz?src=onebox">水稻稻瘟病的防治方法</a></h3>
    <p class="res-desc">引擎 AI 问答页</p>
  </li>
  <li class="res-list">
    <h3 class="res-title"><a href="https://wenku.so.com/s?q=x">360 文库</a></h3>
    <p class="res-desc">引擎垂直页</p>
  </li>
  <li class="res-list">
    <h3 class="res-title"><a href="/relative/path">相对路径</a></h3>
  </li>
</ul></body></html>
"""


@pytest.fixture(autouse=True)
def _clean_runtime(monkeypatch):
    providers.set_test_transport(None)
    providers_builtin.reset_runtime_state()
    monkeypatch.setattr(providers_builtin, "REQUEST_INTERVAL_SECONDS", 0.0)
    yield
    providers.set_test_transport(None)
    providers_builtin.reset_runtime_state()


def _html_transport(html: str, seen: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["url"] = str(request.url)
            seen["ua"] = request.headers.get("user-agent")
            seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, text=html)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 三个解析器
# ---------------------------------------------------------------------------


async def test_bing_parses_results_and_filters_ads():
    seen: dict = {}
    providers.set_test_transport(_html_transport(BING_HTML, seen))

    hits = await providers_builtin.BingHtmlProvider().search("农业", count=5, lang="zh-CN")

    assert len(hits) == 1
    hit = hits[0]
    assert hit.url == "https://www.example.com/rice"
    assert hit.title == "水稻 分蘖期管理技术"
    assert hit.snippet == "浅水勤灌 适时晒田"
    assert hit.source == "bing"
    assert seen["ua"] == providers_builtin.USER_AGENT
    assert seen["cookie"] is None  # 不携带 Cookie
    assert "q=" in seen["url"]


async def test_sogou_parses_results_and_filters_ads():
    seen: dict = {}
    providers.set_test_transport(_html_transport(SOGOU_HTML, seen))

    hits = await providers_builtin.SogouHtmlProvider().search("农业", count=5, lang="zh-CN")

    assert len(hits) == 1
    assert hits[0].url == "https://www.example.org/maize"
    assert hits[0].title == "玉米种植要点"
    assert hits[0].snippet == "合理密植 适时追肥"
    assert hits[0].source == "sogou"
    assert "query=" in seen["url"]


async def test_so360_parses_results_and_filters_ads():
    seen: dict = {}
    providers.set_test_transport(_html_transport(SO360_HTML, seen))

    hits = await providers_builtin.So360HtmlProvider().search("农业", count=5, lang="zh-CN")

    assert len(hits) == 1
    assert hits[0].url == "https://www.example.net/wheat"
    assert hits[0].title == "小麦赤霉病防治"
    assert hits[0].snippet == "扬花期 一喷三防"
    assert hits[0].source == "so360"
    assert "q=" in seen["url"]


async def test_builtin_filters_engine_vertical_pages():
    """AI 问答/文库等引擎垂直页不是可引用内容页，必须被过滤。"""
    providers.set_test_transport(_html_transport(SO360_HTML))

    hits = await providers_builtin.So360HtmlProvider().search("农业", count=8, lang="zh-CN")

    assert all("ai.so.com" not in h.url for h in hits)
    assert all("wenku.so.com" not in h.url for h in hits)


async def test_builtin_snippet_clipped_to_limit():
    html = (
        "<html><body><ol id='b_results'><li class='b_algo'>"
        "<h2><a href='https://example.com/rice'>水稻</a></h2>"
        f"<div class='b_caption'><p>{'字' * 800}</p></div>"
        "</li></ol></body></html>"
    )
    providers.set_test_transport(_html_transport(html))

    hits = await providers_builtin.BingHtmlProvider().search("q", count=1, lang="zh-CN")

    assert len(hits[0].snippet) == providers.SNIPPET_MAX_CHARS


async def test_builtin_respects_max_results(monkeypatch):
    rows = "".join(
        f'<li class="b_algo"><h2><a href="https://example.com/{i}">标题{i}</a></h2></li>'
        for i in range(20)
    )
    providers.set_test_transport(
        _html_transport(f"<html><body><ol id='b_results'>{rows}</ol></body></html>")
    )

    hits = await providers_builtin.BingHtmlProvider().search("q", count=99, lang="zh-CN")

    assert len(hits) == providers.MAX_RESULTS


async def test_builtin_redirect_response_not_followed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/next"})

    providers.set_test_transport(httpx.MockTransport(handler))
    with pytest.raises(AppError) as exc:
        await providers_builtin.BingHtmlProvider().search("q", count=1, lang="zh-CN")
    assert exc.value.code == "SEARCH_PROVIDER_UNAVAILABLE"
    assert exc.value.http_status == 503


async def test_builtin_zero_parsed_results_maps_to_503():
    providers.set_test_transport(_html_transport("<html><body><p>验证码</p></body></html>"))

    with pytest.raises(AppError) as exc:
        await providers_builtin.SogouHtmlProvider().search("q", count=1, lang="zh-CN")
    assert exc.value.http_status == 503


# ---------------------------------------------------------------------------
# 限速与熔断
# ---------------------------------------------------------------------------


async def test_rate_limit_allows_sequential_calls(monkeypatch):
    monkeypatch.setattr(providers_builtin, "REQUEST_INTERVAL_SECONDS", 0.02)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=BING_HTML)

    providers.set_test_transport(httpx.MockTransport(handler))
    provider = providers_builtin.BingHtmlProvider()
    await provider.search("a", count=1, lang="zh-CN")
    await provider.search("b", count=1, lang="zh-CN")

    assert calls["n"] == 2


async def test_breaker_opens_after_three_failures():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    providers.set_test_transport(httpx.MockTransport(handler))
    provider = providers_builtin.BingHtmlProvider()

    for _ in range(3):
        with pytest.raises(AppError) as exc:
            await provider.search("q", count=1, lang="zh-CN")
        assert exc.value.http_status == 503
    assert calls["n"] == 3

    # 连续 3 次失败后熔断：直接跳过，不再发请求
    with pytest.raises(AppError) as exc:
        await provider.search("q", count=1, lang="zh-CN")
    assert calls["n"] == 3
    assert exc.value.http_status == 503


async def test_breaker_half_open_after_cooldown():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=BING_HTML)

    providers.set_test_transport(httpx.MockTransport(handler))
    provider = providers_builtin.BingHtmlProvider()
    providers_builtin._record_failure("bing")
    providers_builtin._record_failure("bing")
    providers_builtin._record_failure("bing")
    assert providers_builtin._breaker["bing"].opened_at is not None

    # 冷却结束：放行真实尝试并恢复
    providers_builtin._breaker["bing"].opened_at = (
        time.monotonic() - providers_builtin.BREAKER_COOLDOWN_SECONDS - 1
    )
    hits = await provider.search("q", count=1, lang="zh-CN")

    assert len(hits) == 1
    assert calls["n"] == 1
    assert "bing" not in providers_builtin._breaker


async def test_success_resets_failure_counter():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=BING_HTML)

    providers.set_test_transport(httpx.MockTransport(handler))
    provider = providers_builtin.BingHtmlProvider()
    providers_builtin._record_failure("bing")
    providers_builtin._record_failure("bing")

    await provider.search("q", count=1, lang="zh-CN")

    assert "bing" not in providers_builtin._breaker  # 计数清零，不会累积熔断


def test_create_builtin_provider_unknown_code():
    assert providers_builtin.create_builtin_provider("google") is None
    assert providers_builtin.is_builtin_source("bing") is True
    assert providers_builtin.is_builtin_source("searxng") is False


# ---------------------------------------------------------------------------
# 搜索设置：provider=auto 与 builtin_sources 校验
# ---------------------------------------------------------------------------


def test_search_settings_defaults_are_zero_config():
    search = settings_service.DEFAULTS["search"]
    assert search["provider"] == "auto"
    assert search["builtin_sources"] == ["bing", "sogou", "so360"]


def test_builtin_codes_consistent_between_modules():
    assert set(settings_service.BUILTIN_SEARCH_SOURCES) == set(
        providers_builtin.BUILTIN_SOURCE_CODES
    )


def test_validate_accepts_subset_and_dedupes():
    merged = {
        **settings_service.DEFAULTS["search"],
        "builtin_sources": ["so360", "bing", "so360"],
    }
    settings_service._validate("search", {}, merged)
    assert merged["builtin_sources"] == ["so360", "bing"]


def test_validate_allows_empty_builtin_sources():
    merged = {**settings_service.DEFAULTS["search"], "builtin_sources": []}
    settings_service._validate("search", {}, merged)


@pytest.mark.parametrize("sources", ["bing", ["google"], ["bing", 1], None])
def test_validate_rejects_invalid_builtin_sources(sources):
    merged = {**settings_service.DEFAULTS["search"], "builtin_sources": sources}
    with pytest.raises(AppError) as exc:
        settings_service._validate("search", {}, merged)
    assert exc.value.code == "INVALID_REQUEST"
    assert exc.value.http_status == 400


def test_validate_rejects_unknown_provider():
    merged = {**settings_service.DEFAULTS["search"], "provider": "google"}
    with pytest.raises(AppError) as exc:
        settings_service._validate("search", {}, merged)
    assert exc.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("provider", ["searxng", "tavily", "brave", "auto"])
def test_validate_accepts_legacy_and_new_providers(provider):
    merged = {**settings_service.DEFAULTS["search"], "provider": provider}
    settings_service._validate("search", {}, merged)  # 不抛即通过
