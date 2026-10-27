"""免密钥内置搜索源：必应中国 / 搜狗 / 360 搜索（HTML + XPath 解析）。

与 ``providers.py`` 的 API 供应商不同，这里直接抓取结果页 HTML 并用
``lxml.html`` XPath 解析（不引入 cssselect）。任何失败——网络异常、非 2xx、
页面改版、解析出 0 条结果——都统一收敛为
``AppError("SEARCH_PROVIDER_UNAVAILABLE", ..., 503)``，由 service 降级链处理。

进程内限速与熔断（单实例足够，与 service 缓存同一风格）：
- 每源 ≤1 req/s（间隔不足时等待）；
- 连续 3 次失败熔断 5 分钟，熔断期内直接视为不可用（跳过，不等待）。
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from lxml import html as lxml_html

from app.core.logging import get_logger
from app.modules.search import providers as providers_mod
from app.modules.search.providers import (
    MAX_RESULTS,
    SNIPPET_MAX_CHARS,
    SearchHit,
    SearchProvider,
    _count,
    _is_http_url,
    _make_hit,
    _unavailable,
)

log = get_logger(__name__)

#: 内置源请求标识。必须用真实浏览器 UA：cn.bing/搜狗/360 对非浏览器 UA 会
#: 直接 301 到首页 / 反爬页 / 验证码页（2026-10 实测），浏览器 UA 才返回结果页。
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
BUILTIN_TIMEOUT_SECONDS = 3.0
REQUEST_INTERVAL_SECONDS = 1.0
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_SECONDS = 300.0
#: 引擎握手跳数上限（cn.bing → www.bing 一跳即达；防御异常循环）
MAX_ENGINE_REDIRECTS = 5

#: 内置源 code（与 yards.settings_service.BUILTIN_SEARCH_SOURCES 保持一致）
BUILTIN_SOURCE_CODES: tuple[str, ...] = ("bing", "sogou", "so360")

_WS_RE = re.compile(r"\s+")

#: 广告/推广结果的常见类名标记（小写子串匹配）
_AD_CLASS_MARKERS = ("ec_tuiguang", "ec-", "b_ad", "ad_bg", "promote", "sponsor")

#: 搜索引擎自家跳转链接（/link、/ck）不指向真实目标页
_REDIRECT_HOSTS = {
    "www.baidu.com",
    "baidu.com",
    "m.baidu.com",
    "www.sogou.com",
    "sogou.com",
    "m.sogou.com",
    "www.so.com",
    "so.com",
    "m.so.com",
    "cn.bing.com",
    "www.bing.com",
    "bing.com",
}
_REDIRECT_PATHS = ("/link", "/ck")


@dataclass
class _BreakerState:
    failures: int = 0
    opened_at: float | None = None


#: 进程内限速 / 熔断状态
_last_request_at: dict[str, float] = {}
_breaker: dict[str, _BreakerState] = {}

#: 引擎匿名 cookie jar（按源隔离）。搜索引擎首次访问会 302 下发自己的匿名
#: cookie（cn.bing→www.bing 的地域跳转、搜狗 antispider、360 首页会话），
#: 带 cookie 重访才能拿到真实结果页。jar 只存引擎下发的 cookie——绝不注入
#: 任何用户登录态（测试断言首请求不带 Cookie 即此口径）。
_cookie_jars: dict[str, httpx.Cookies] = {}


def reset_runtime_state() -> None:
    """测试专用：清空限速、熔断与引擎 cookie 状态。"""
    _last_request_at.clear()
    _breaker.clear()
    _cookie_jars.clear()


def _breaker_open(code: str) -> bool:
    state = _breaker.get(code)
    if state is None or state.opened_at is None:
        return False
    if time.monotonic() - state.opened_at >= BREAKER_COOLDOWN_SECONDS:
        # 冷却结束：半开，放行下一次真实尝试
        _breaker.pop(code, None)
        return False
    return True


def _record_success(code: str) -> None:
    _breaker.pop(code, None)


def _record_failure(code: str) -> None:
    state = _breaker.setdefault(code, _BreakerState())
    state.failures += 1
    if state.failures >= BREAKER_THRESHOLD:
        state.opened_at = time.monotonic()


async def _throttle(code: str) -> None:
    """每源 ≤1 req/s：距上次请求不足间隔时等待。"""
    last = _last_request_at.get(code)
    if last is not None:
        remaining = REQUEST_INTERVAL_SECONDS - (time.monotonic() - last)
        if remaining > 0:
            await asyncio.sleep(remaining)
    _last_request_at[code] = time.monotonic()


def _collapse(value: Any) -> str:
    return _WS_RE.sub(" ", str(value or "")).strip()


def _is_redirect_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    host = (parsed.hostname or "").lower()
    return host in _REDIRECT_HOSTS and (parsed.path or "").startswith(_REDIRECT_PATHS)


#: 搜索引擎自家垂直页（AI 问答/文库/图片/视频/地图）不是可引用的内容页
_VERTICAL_HOST_SUFFIXES = (
    "ai.so.com",
    "wenku.so.com",
    "image.so.com",
    "video.so.com",
    "map.so.com",
    "pic.sogou.com",
    "v.sogou.com",
    "wenku.sogou.com",
    "cn.bing.com/images",
    "cn.bing.com/videos",
    "cn.bing.com/academic",
)


def _is_vertical_url(url: str) -> bool:
    lowered = url.lower()
    return any(suffix in lowered for suffix in _VERTICAL_HOST_SUFFIXES)


def _has_ad_marker(anchor: Any, container: Any, title: str) -> bool:
    classes = " ".join(str(node.get("class") or "") for node in (anchor, container)).lower()
    if any(marker in classes for marker in _AD_CLASS_MARKERS):
        return True
    return title in {"广告", "推广"}


class _HtmlSearchProvider:
    """内置 HTML 搜索源骨架：熔断检查 → 限速 → 抓取 → XPath 解析。"""

    code: str = ""
    requires_key: bool = False
    endpoint: str = ""
    query_param: str = "q"
    item_xpath: str = ""
    link_xpath: str = ""
    snippet_xpath: str = ""

    async def search(self, q: str, *, count: int, lang: str) -> list[SearchHit]:
        code = self.code
        if _breaker_open(code):
            # 熔断期：直接视为不可用（跳过，不等待、不发请求）
            raise _unavailable(code, RuntimeError(f"{code} 熔断中"))
        await _throttle(code)
        try:
            root = await self._fetch(q)
            hits = self._parse(root)[: _count(count)]
        except Exception as e:
            _record_failure(code)
            raise _unavailable(code, e) from e
        if not hits:
            # 解析出 0 条视同失败（被风控或页面改版），交给降级链处理
            _record_failure(code)
            raise _unavailable(code, ValueError("结果页未解析出任何结果"))
        _record_success(code)
        return hits

    async def _fetch(self, q: str) -> Any:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        # 不走 providers._client：内置源需要 cookie jar + 跟随引擎握手跳转
        # （cn.bing 301 下发 cookie 后落到 www.bing 结果页）。测试注入口沿用。
        jar = _cookie_jars.setdefault(self.code, httpx.Cookies())
        async with httpx.AsyncClient(
            timeout=BUILTIN_TIMEOUT_SECONDS,
            # 动态读 providers 模块属性：测试经 set_test_transport 重新赋值，
            # from-import 的值拷贝收不到注入（会导致测试真实出网）
            transport=providers_mod._transport_override,
            follow_redirects=True,
            max_redirects=MAX_ENGINE_REDIRECTS,
            trust_env=False,
            cookies=jar,
        ) as client:
            resp = await client.get(self.endpoint, params={self.query_param: q}, headers=headers)
        resp.raise_for_status()
        return lxml_html.fromstring(resp.text)

    def _parse(self, root: Any) -> list[SearchHit]:
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in root.xpath(self.item_xpath):
            hit = self._parse_item(item, seen)
            if hit is not None:
                hits.append(hit)
                if len(hits) >= MAX_RESULTS:
                    break
        return hits

    def _parse_item(self, item: Any, seen: set[str]) -> SearchHit | None:
        for anchor in item.xpath(self.link_xpath):
            href = str(anchor.get("href") or "").strip()
            title = _collapse(anchor.text_content())
            if not title or not _is_http_url(href) or href in seen:
                continue
            if (
                _is_redirect_url(href)
                or _is_vertical_url(href)
                or _has_ad_marker(anchor, item, title)
            ):
                continue
            seen.add(href)
            return _make_hit(title=title, url=href, snippet=self._snippet(item), source=self.code)
        return None

    def _snippet(self, item: Any) -> str:
        for node in item.xpath(self.snippet_xpath):
            text = _collapse(node.text_content())
            if text:
                return text[:SNIPPET_MAX_CHARS]
        return ""


class BingHtmlProvider(_HtmlSearchProvider):
    """必应中国：li.b_algo > h2 > a；摘要在 b_caption 段落。"""

    code = "bing"
    endpoint = "https://cn.bing.com/search"
    query_param = "q"
    item_xpath = '//li[contains(concat(" ", normalize-space(@class), " "), " b_algo ")]'
    link_xpath = ".//h2//a"
    snippet_xpath = './/div[contains(@class,"b_caption")]//p | .//p'


class SogouHtmlProvider(_HtmlSearchProvider):
    """搜狗：vrwrap / rb 容器 + h3 > a；摘要在 str-* 段落。"""

    code = "sogou"
    endpoint = "https://www.sogou.com/web"
    query_param = "query"
    item_xpath = '//div[contains(@class,"vrwrap")] | //div[contains(@class,"rb")]'
    link_xpath = ".//h3//a"
    snippet_xpath = (
        './/div[contains(@class,"str-text-info")]'
        ' | .//p[contains(@class,"str_info")]'
        ' | .//div[contains(@class,"str_info")]'
        " | .//p"
    )


class So360HtmlProvider(_HtmlSearchProvider):
    """360 搜索：li.res-list + h3 > a；摘要在 res-desc / res-rich 段落。"""

    code = "so360"
    endpoint = "https://www.so.com/s"
    query_param = "q"
    item_xpath = '//li[contains(@class,"res-list")]'
    link_xpath = ".//h3//a"
    snippet_xpath = './/p[contains(@class,"res-desc")] | .//p[contains(@class,"res-rich")] | .//p'


_BUILTIN_PROVIDER_CLASSES: dict[str, type[_HtmlSearchProvider]] = {
    provider.code: provider for provider in (BingHtmlProvider, SogouHtmlProvider, So360HtmlProvider)
}


def is_builtin_source(code: str) -> bool:
    return code in _BUILTIN_PROVIDER_CLASSES


def create_builtin_provider(code: str) -> SearchProvider | None:
    """按 code 构造内置源实例；未知 code 返回 None。"""
    cls = _BUILTIN_PROVIDER_CLASSES.get(code)
    return cls() if cls is not None else None


__all__ = [
    "BUILTIN_SOURCE_CODES",
    "BREAKER_COOLDOWN_SECONDS",
    "BREAKER_THRESHOLD",
    "BUILTIN_TIMEOUT_SECONDS",
    "BingHtmlProvider",
    "REQUEST_INTERVAL_SECONDS",
    "So360HtmlProvider",
    "SogouHtmlProvider",
    "USER_AGENT",
    "create_builtin_provider",
    "is_builtin_source",
    "reset_runtime_state",
]
