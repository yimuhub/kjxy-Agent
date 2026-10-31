"""内置免密钥搜索源单测：五个解析器、相关性过滤、反爬识别、限速与熔断（全离线）。

fixture 为各引擎结果页的代表性缩样（2026-10 按真机页面结构裁剪）；
全部请求走 MockTransport，禁止真实出网。
"""

import time

import httpx
import pytest
from app.core.errors import AppError
from app.modules.search import providers, providers_builtin
from app.modules.yards import settings_service

BING_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item>
    <title>水稻稻瘟病防治技术</title>
    <link>https://www.example.com/rice</link>
    <description>  浅水勤灌   适时晒田 </description>
  </item>
  <item>
    <title>爱壹帆-海量高清视频免费在线观看</title>
    <link>https://www.example.com/tv</link>
    <description>电视剧</description>
  </item>
  <item>
    <title>水稻稻瘟病</title>
    <link>https://cn.bing.com/ck/a?u=x</link>
  </item>
</channel></rss>
"""

BRAVE_HTML = """
<html><body>
  <div class="snippet svelte-jmfu5f" data-type="web" data-pos="0">
    <div class="result-body"><div class="result-content">
      <a href="https://www.example.com/rice">
        <div class="title search-snippet-title">水稻稻瘟病防治技术</div>
      </a>
      <div class="generic-snippet"><div class="content">
        浅水勤灌 · 适时晒田
      </div></div>
    </div></div>
  </div>
  <div class="snippet svelte-jmfu5f" data-type="ads" data-pos="1">
    <div class="result-content"><a href="https://ad.example.com/promo">
      <div class="title">推广位标题</div>
    </a></div>
  </div>
  <div class="snippet svelte-jmfu5f" data-type="web" data-pos="2">
    <div class="result-content"><a href="https://search.brave.com/search?q=x">
      <div class="title search-snippet-title">引擎站内链接</div>
    </a></div>
  </div>
</body></html>
"""

BAIDU_HTML = """
<html><body>
  <div class="c-result result" data-log='{"mu":"https://www.example.com/rice","order":"x"}'>
    <div class="c-title">水稻稻瘟病防治</div>
    <div class="c-abstract">  稻瘟病   防治要点 </div>
  </div>
  <div class="c-result result" data-log='{"mu":"https://ad.example.com/promo","order":"recommend_list"}'>
    <div class="c-title">推荐位</div>
  </div>
  <div class="c-result result" data-log='{"mu":"","order":"x"}'>
    <div class="c-title">无真链结果</div>
  </div>
</body></html>
"""

SOGOU_HTML = """
<html><body>
  <div class="result">
    <h3 class="vr-tit"><a class="resultLink"
      href="./id=x/tc?url=https%3A%2F%2Fwww.example.org%2Fmaize">
      玉米种植要点</a></h3>
    <div class="abstract-module"> 合理密植   适时追肥 </div>
  </div>
  <div class="hint">
    <h3 class="vr-tit"><span class="resultLink">大家还在搜</span></h3>
  </div>
</body></html>
"""

SO360_HTML = """
<html><body><ul class="result">
  <li class="res-list">
    <h3 class="res-title"><a href="https://m.so.com/s?u=https%3A%2F%2Fwww.example.net%2Fwheat">
      小麦赤霉病防治</a></h3>
    <p class="res-desc"> 扬花期   一喷三防 </p>
  </li>
  <li class="res-list">
    <h3 class="res-title"><a href="https://ai.so.com/search/soc-xyz?src=onebox">
      水稻稻瘟病的防治方法</a></h3>
    <p class="res-desc">引擎 AI 问答页</p>
  </li>
  <li class="res-list">
    <h3 class="res-title"><a href="/relative/path">相对路径</a></h3>
  </li>
</ul></body></html>
"""

BING_NEWS_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item>
    <title>水稻稻瘟病抗性新机制被破解</title>
    <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fwww.example.com%2fnews1</link>
    <description>科研团队发现新的抗性机制</description>
    <pubDate>Sat, 31 Oct 2026 00:00:00 GMT</pubDate>
  </item>
  <item>
    <title>热播电视剧</title>
    <link>http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;url=https%3a%2f%2fwww.example.com%2ftv</link>
    <description>影视资讯</description>
  </item>
</channel></rss>
"""

GOOGLE_NEWS_RSS = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel>
  <item>
    <title>水稻稻瘟病防治新进展 - 人民日报</title>
    <link>https://news.google.com/rss/articles/CBMiABC?oc=5</link>
    <description>防治新进展</description>
    <pubDate>Sat, 31 Oct 2026 00:00:00 GMT</pubDate>
  </item>
</channel></rss>
"""

DDG_HTML = """
<html><body>
  <div class="result results_links web-result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fddg">
      水稻稻瘟病防治</a>
    <a class="result__snippet" href="#">防治要点</a>
  </div>
</body></html>
"""


@pytest.fixture(autouse=True)
def _clean_runtime(monkeypatch):
    providers.set_test_transport(None)
    providers_builtin.reset_runtime_state()
    monkeypatch.setattr(providers_builtin, "REQUEST_INTERVAL_SECONDS", 0.0)
    yield
    providers.set_test_transport(None)
    providers_builtin.reset_runtime_state()


def _transport(body: str, content_type: str = "text/html", seen: dict | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen["url"] = str(request.url)
            seen["ua"] = request.headers.get("user-agent")
            seen["cookie"] = request.headers.get("cookie")
        return httpx.Response(200, text=body, headers={"content-type": content_type})

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# 五个解析器
# ---------------------------------------------------------------------------


async def test_brave_parses_results_and_skips_ads():
    seen: dict = {}
    providers.set_test_transport(_transport(BRAVE_HTML, seen=seen))

    hits = await providers_builtin.BraveWebProvider().search(
        "水稻稻瘟病防治", count=5, lang="zh-CN"
    )

    assert len(hits) == 1
    hit = hits[0]
    assert hit.url == "https://www.example.com/rice"
    assert hit.title == "水稻稻瘟病防治技术"
    assert hit.snippet == "浅水勤灌 · 适时晒田"
    assert hit.source == "brave_web"
    assert seen["ua"].startswith("Mozilla/5.0")
    assert seen["cookie"] is None  # 不携带用户登录态
    assert "q=" in seen["url"]


async def test_baidu_extracts_real_url_from_data_log():
    seen: dict = {}
    providers.set_test_transport(_transport(BAIDU_HTML, seen=seen))

    hits = await providers_builtin.BaiduMobileProvider().search(
        "水稻稻瘟病防治", count=5, lang="zh-CN"
    )

    assert len(hits) == 1
    assert hits[0].url == "https://www.example.com/rice"
    assert hits[0].title == "水稻稻瘟病防治"
    assert hits[0].snippet == "稻瘟病 防治要点"
    assert hits[0].source == "baidu"
    assert "word=" in seen["url"]
    assert "iPhone" in seen["ua"]  # 移动端 UA 才拿得到结果页


async def test_sogou_decodes_url_param():
    seen: dict = {}
    providers.set_test_transport(_transport(SOGOU_HTML, seen=seen))

    hits = await providers_builtin.SogouMobileProvider().search(
        "玉米种植要点", count=5, lang="zh-CN"
    )

    assert len(hits) == 1
    assert hits[0].url == "https://www.example.org/maize"
    assert hits[0].title == "玉米种植要点"
    assert hits[0].snippet == "合理密植 适时追肥"
    assert "keyword=" in seen["url"]


async def test_so360_decodes_u_param_and_filters_vertical():
    providers.set_test_transport(_transport(SO360_HTML))

    hits = await providers_builtin.So360MobileProvider().search(
        "小麦赤霉病防治", count=8, lang="zh-CN"
    )

    assert len(hits) == 1
    assert hits[0].url == "https://www.example.net/wheat"
    assert hits[0].snippet == "扬花期 一喷三防"
    assert all("ai.so.com" not in h.url for h in hits)


async def test_bing_rss_relevance_filter_drops_junk():
    providers.set_test_transport(_transport(BING_RSS, content_type="application/rss+xml"))

    hits = await providers_builtin.BingRssProvider().search("水稻稻瘟病防治", count=5, lang="zh-CN")

    assert len(hits) == 1
    assert hits[0].url == "https://www.example.com/rice"
    assert hits[0].snippet == "浅水勤灌 适时晒田"
    assert hits[0].source == "bing"


async def test_bing_news_extracts_publisher_url():
    providers.set_test_transport(_transport(BING_NEWS_RSS, content_type="application/rss+xml"))

    hits = await providers_builtin.BingNewsProvider().search("水稻稻瘟病", count=5, lang="zh-CN")

    assert len(hits) == 1
    assert hits[0].url == "https://www.example.com/news1"
    assert hits[0].published is not None
    assert hits[0].source == "bing_news"


async def test_google_news_parses_items():
    providers.set_test_transport(_transport(GOOGLE_NEWS_RSS, content_type="application/rss+xml"))

    hits = await providers_builtin.GoogleNewsProvider().search("水稻稻瘟病", count=5, lang="zh-CN")

    assert len(hits) == 1
    assert hits[0].url.startswith("https://news.google.com/rss/articles/")
    assert hits[0].source == "google_news"


async def test_duckduckgo_decodes_uddg_param():
    providers.set_test_transport(_transport(DDG_HTML))

    hits = await providers_builtin.DuckDuckGoHtmlProvider().search(
        "水稻稻瘟病防治", count=5, lang="zh-CN"
    )

    assert len(hits) == 1
    assert hits[0].url == "https://example.com/ddg"
    assert hits[0].source == "duckduckgo"


async def test_builtin_snippet_clipped_to_limit():
    body = BRAVE_HTML.replace("浅水勤灌 · 适时晒田", "字" * 800)
    providers.set_test_transport(_transport(body))

    hits = await providers_builtin.BraveWebProvider().search(
        "水稻稻瘟病防治", count=1, lang="zh-CN"
    )

    assert len(hits[0].snippet) == providers.SNIPPET_MAX_CHARS


async def test_builtin_respects_max_results():
    blocks = "".join(
        f'<div class="snippet" data-type="web"><div class="result-content">'
        f'<a href="https://example.com/{i}"><div class="title search-snippet-title">'
        f"水稻稻瘟病防治 {i}</div></a></div></div>"
        for i in range(20)
    )
    providers.set_test_transport(_transport(f"<html><body>{blocks}</body></html>"))

    hits = await providers_builtin.BraveWebProvider().search(
        "水稻稻瘟病防治", count=99, lang="zh-CN"
    )

    assert len(hits) == providers.MAX_RESULTS


# ---------------------------------------------------------------------------
# 反爬 / 验证码 / 空结果
# ---------------------------------------------------------------------------


async def test_baidu_captcha_redirect_reports_security_verification():
    def handler(request: httpx.Request) -> httpx.Response:
        if "wappass.baidu.com" in str(request.url):
            return httpx.Response(200, text="<html>验证</html>")
        return httpx.Response(
            302, headers={"location": "https://wappass.baidu.com/static/captcha/x.html"}
        )

    providers.set_test_transport(httpx.MockTransport(handler))
    with pytest.raises(AppError) as exc:
        await providers_builtin.BaiduMobileProvider().search("农业", count=1, lang="zh-CN")
    assert exc.value.http_status == 503
    assert "安全验证" in exc.value.message


async def test_sogou_antispider_reports_reason():
    def handler(request: httpx.Request) -> httpx.Response:
        if "antispider" in str(request.url):
            return httpx.Response(200, text="<html>antispider</html>")
        return httpx.Response(
            302, headers={"location": "https://m.sogou.com/antispider/?from=%2Fweb"}
        )

    providers.set_test_transport(httpx.MockTransport(handler))
    with pytest.raises(AppError) as exc:
        await providers_builtin.SogouMobileProvider().search("农业", count=1, lang="zh-CN")
    assert "反爬" in exc.value.message


async def test_rate_limited_reports_reason():
    providers.set_test_transport(httpx.MockTransport(lambda request: httpx.Response(429)))
    with pytest.raises(AppError) as exc:
        await providers_builtin.BraveWebProvider().search("农业", count=1, lang="zh-CN")
    assert "限流" in exc.value.message


async def test_duckduckgo_challenge_reports_reason():
    providers.set_test_transport(httpx.MockTransport(lambda request: httpx.Response(202)))
    with pytest.raises(AppError) as exc:
        await providers_builtin.DuckDuckGoHtmlProvider().search("农业", count=1, lang="zh-CN")
    assert "人机验证" in exc.value.message


async def test_zero_relevant_results_does_not_trip_breaker():
    providers.set_test_transport(_transport("<html><body><p>没有任何结果</p></body></html>"))
    provider = providers_builtin.BraveWebProvider()

    with pytest.raises(AppError) as exc:
        await provider.search("农业", count=1, lang="zh-CN")

    assert "未找到与关键词相关的结果" in exc.value.message
    assert "brave_web" not in providers_builtin._breaker


# ---------------------------------------------------------------------------
# 限速与熔断
# ---------------------------------------------------------------------------


async def test_rate_limit_allows_sequential_calls(monkeypatch):
    monkeypatch.setattr(providers_builtin, "REQUEST_INTERVAL_SECONDS", 0.02)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=BRAVE_HTML)

    providers.set_test_transport(httpx.MockTransport(handler))
    provider = providers_builtin.BraveWebProvider()
    await provider.search("水稻稻瘟病防治", count=1, lang="zh-CN")
    await provider.search("水稻稻瘟病防治", count=1, lang="zh-CN")

    assert calls["n"] == 2


async def test_breaker_opens_after_three_failures():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    providers.set_test_transport(httpx.MockTransport(handler))
    provider = providers_builtin.BraveWebProvider()

    for _ in range(3):
        with pytest.raises(AppError) as exc:
            await provider.search("q", count=1, lang="zh-CN")
        assert exc.value.http_status == 503
    assert calls["n"] == 3

    # 连续 3 次失败后熔断：直接跳过，不再发请求
    with pytest.raises(AppError) as exc:
        await provider.search("q", count=1, lang="zh-CN")
    assert calls["n"] == 3
    assert "brave_web" in exc.value.message or exc.value.http_status == 503


async def test_breaker_half_open_after_cooldown():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=BRAVE_HTML)

    providers.set_test_transport(httpx.MockTransport(handler))
    provider = providers_builtin.BraveWebProvider()
    for _ in range(3):
        providers_builtin._record_failure("brave_web")
    assert providers_builtin._breaker["brave_web"].opened_at is not None

    # 冷却结束：放行真实尝试并恢复
    providers_builtin._breaker["brave_web"].opened_at = (
        time.monotonic() - providers_builtin.BREAKER_COOLDOWN_SECONDS - 1
    )
    hits = await provider.search("水稻稻瘟病防治", count=1, lang="zh-CN")

    assert len(hits) == 1
    assert calls["n"] == 1
    assert "brave_web" not in providers_builtin._breaker


async def test_probe_bypasses_breaker_and_throttle():
    """状态探测不应被熔断挡住，也不应更新熔断计数。"""
    providers_builtin._record_failure("brave_web")
    providers_builtin._record_failure("brave_web")
    providers_builtin._record_failure("brave_web")
    providers.set_test_transport(_transport(BRAVE_HTML))

    hits = await providers_builtin.BraveWebProvider().probe("水稻稻瘟病防治")

    assert len(hits) == 1


def test_create_builtin_provider_unknown_code():
    assert providers_builtin.create_builtin_provider("google") is None
    assert providers_builtin.is_builtin_source("bing") is True
    assert providers_builtin.is_builtin_source("searxng") is False


# ---------------------------------------------------------------------------
# 搜索设置：默认值、legacy 升级与 builtin_sources 校验
# ---------------------------------------------------------------------------


def test_search_settings_defaults_are_zero_config():
    search = settings_service.DEFAULTS["search"]
    assert search["provider"] == "auto"
    assert search["builtin_sources"] == [
        "brave_web",
        "baidu",
        "sogou",
        "so360",
        "bing_news",
        "google_news",
    ]
    assert search["searxng_url"] == ""


def test_builtin_codes_consistent_between_modules():
    assert set(settings_service.BUILTIN_SEARCH_SOURCES) == set(
        providers_builtin.BUILTIN_SOURCE_CODES
    )


def test_legacy_zero_config_is_upgraded():
    stored = {
        "builtin_sources": ["bing", "sogou", "so360"],
        "searxng_url": "http://127.0.0.1:8888",
    }
    merged = {**settings_service.DEFAULTS["search"], **stored}
    settings_service._upgrade_search_defaults(merged, stored)

    assert merged["builtin_sources"] == settings_service.DEFAULTS["search"]["builtin_sources"]
    assert merged["searxng_url"] == ""


def test_custom_search_config_is_not_upgraded():
    stored = {
        "builtin_sources": ["bing", "sogou", "so360"],
        "searxng_url": "https://searx.example.com",
    }
    merged = {**settings_service.DEFAULTS["search"], **stored}
    settings_service._upgrade_search_defaults(merged, stored)

    assert merged["builtin_sources"] == ["bing", "sogou", "so360"]
    assert merged["searxng_url"] == "https://searx.example.com"


def test_validate_accepts_subset_and_dedupes():
    merged = {
        **settings_service.DEFAULTS["search"],
        "builtin_sources": ["so360", "brave_web", "so360"],
    }
    settings_service._validate("search", {}, merged)
    assert merged["builtin_sources"] == ["so360", "brave_web"]


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


@pytest.mark.parametrize(
    ("provider", "searxng_url"),
    [
        ("searxng", "https://searx.example.com"),
        ("tavily", ""),
        ("brave", ""),
        ("auto", ""),
    ],
)
def test_validate_accepts_legacy_and_new_providers(provider, searxng_url):
    merged = {
        **settings_service.DEFAULTS["search"],
        "provider": provider,
        "searxng_url": searxng_url,
    }
    settings_service._validate("search", {}, merged)  # 不抛即通过
