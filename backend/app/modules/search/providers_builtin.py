"""免密钥内置搜索源（零配置可用）。

2026-10 在本机网络实测：百度/360 经常直接跳验证码，搜狗首次可用、第二次跳
antispider，必应 RSS 返回与查询无关的垃圾结果，DuckDuckGo 返回人机验证页。
当前最稳定的是 **Brave 网页版**（服务端渲染、中文结果相关性高），因此默认顺序
为 Brave → 百度 → 搜狗 → 360 → 必应；DuckDuckGo 作为可选项默认关闭。

与其他供应商一样，任何失败（网络、非 2xx、验证码/反爬、页面改版、0 条相关结果）
都收敛为 ``AppError("SEARCH_PROVIDER_UNAVAILABLE", ..., 503)``，由 service 的降级链
继续尝试下一家。原因写进 message/details，前端据此区分「没网」与「被反爬」。

进程内限速与熔断（单实例足够）：
- 每源 ≤1 req/s（间隔不足时等待）；
- 连续 3 次失败熔断 5 分钟，熔断期内直接跳过；
- 「未找到相关结果」不算失败，不触发熔断（避免小众关键词误伤）。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from lxml import etree
from lxml import html as lxml_html

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.search import providers as providers_mod
from app.modules.search.providers import (
    MAX_RESULTS,
    SearchHit,
    SearchProvider,
    _count,
    _is_http_url,
    _make_hit,
)

log = get_logger(__name__)

DESKTOP_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
    "Mobile/15E148 Safari/604.1"
)
BUILTIN_TIMEOUT_SECONDS = 8.0
REQUEST_INTERVAL_SECONDS = 1.0
BREAKER_THRESHOLD = 3
BREAKER_COOLDOWN_SECONDS = 300.0
MAX_ENGINE_REDIRECTS = 5

#: 内置源 code（与 yards.settings_service.BUILTIN_SEARCH_SOURCES 保持一致）
BUILTIN_SOURCE_CODES: tuple[str, ...] = (
    "brave_web",
    "baidu",
    "sogou",
    "so360",
    "bing_news",
    "bing",
    "google_news",
    "duckduckgo",
)
BUILTIN_SOURCE_LABELS: dict[str, str] = {
    "brave_web": "Brave 网页版",
    "baidu": "百度",
    "sogou": "搜狗",
    "so360": "360 搜索",
    "bing_news": "必应新闻",
    "bing": "必应网页",
    "google_news": "谷歌新闻",
    "duckduckgo": "DuckDuckGo",
}

_WS_RE = re.compile(r"\s+")
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

#: 引擎自家跳转链接（/link、/ck 等）不指向真实目标页
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
    "search.brave.com",
    "html.duckduckgo.com",
    "duckduckgo.com",
}
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


class _GuardError(Exception):
    """被验证码/反爬页拦截；与网络错误区分，便于给出可执行提示。"""


@dataclass
class _BreakerState:
    failures: int = 0
    opened_at: float | None = None


#: 进程内限速 / 熔断状态
_last_request_at: dict[str, float] = {}
_breaker: dict[str, _BreakerState] = {}

#: 引擎匿名 cookie jar（按源隔离）。只存引擎下发的匿名 cookie，不注入用户登录态。
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


def _label(code: str) -> str:
    return BUILTIN_SOURCE_LABELS.get(code, code)


def _reason_error(code: str, reason: str) -> AppError:
    """带具体原因的不可用错误（service 会把 message 汇总给前端）。"""
    return AppError(
        "SEARCH_PROVIDER_UNAVAILABLE",
        f"{_label(code)}暂时不可用：{reason}",
        http_status=503,
        details={"provider": code, "reason": reason},
    )


def _network_reason(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "请求超时"
    if isinstance(exc, httpx.ConnectError):
        return "连接失败"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, ValueError) and str(exc):
        return str(exc)
    return "页面解析失败"


def _is_redirect_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return True
    host = (parsed.hostname or "").lower()
    if host in _REDIRECT_HOSTS and (parsed.path or "").startswith(("/link", "/ck")):
        return True
    return host in {"search.brave.com", "html.duckduckgo.com"}


def _is_vertical_url(url: str) -> bool:
    lowered = url.lower()
    return any(suffix in lowered for suffix in _VERTICAL_HOST_SUFFIXES)


def _query_tokens(query: str) -> set[str]:
    """查询词拆成字母/数字词 + 中文字二元组，用于过滤搜索引擎返回的无关噪声。"""
    tokens: set[str] = set(_ASCII_TOKEN_RE.findall(query.lower()))
    for run in _CJK_RUN_RE.findall(query):
        if len(run) >= 2:
            tokens.add(run)
            tokens.update(run[i : i + 2] for i in range(len(run) - 1))
        else:
            tokens.add(run)
    return tokens


def _relevant(title: str, snippet: str, query: str) -> bool:
    tokens = _query_tokens(query)
    if not tokens:
        return True
    haystack = f"{title} {snippet}".lower()
    return any(token in haystack for token in tokens)


def _extract_param_url(href: str, key: str) -> str:
    """从引擎跳转链接里取真实目标地址（搜狗 url= / 360 u=）。"""
    if not href:
        return ""
    parsed = urlparse(href)
    values = parse_qs(parsed.query).get(key)
    if values:
        return unquote(values[0]).strip()
    match = re.search(rf"[?&]{re.escape(key)}=([^&]+)", href)
    if match:
        return unquote(match.group(1)).strip()
    if href.startswith("http"):
        return href
    return ""


def _walk_key(node: Any, key: str) -> str:
    if isinstance(node, dict):
        value = node.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
        for child in node.values():
            found = _walk_key(child, key)
            if found:
                return found
    elif isinstance(node, list):
        for child in node:
            found = _walk_key(child, key)
            if found:
                return found
    return ""


def _extract_json_url(attr: str, key: str = "mu") -> str:
    try:
        found = _walk_key(json.loads(attr), key)
        if found:
            return found
    except (TypeError, ValueError):
        pass
    match = re.search(rf'"{key}"\s*:\s*"(https?://[^"]+)"', attr)
    return match.group(1) if match else ""


def _plain(value: Any) -> str:
    text = str(value or "")
    if "<" not in text:
        return _collapse(text)
    try:
        return _collapse(lxml_html.fromstring(f"<div>{text}</div>").text_content())
    except (etree.ParserError, ValueError):
        return _collapse(text)


def _first_text(node: Any, xpaths: list[str]) -> str:
    for path in xpaths:
        rows = node.xpath(path)
        if rows:
            text = _collapse(rows[0].text_content())
            if text:
                return text
    return ""


class _BuiltinProvider:
    """内置源骨架：熔断/限速 → 抓取 → 反爬识别 → 解析 → 相关性过滤。"""

    code: str = ""
    requires_key: bool = False
    endpoint: str = ""
    query_param: str = "q"
    user_agent: str = DESKTOP_UA

    def _params(self, query: str) -> dict[str, str]:
        return {self.query_param: query}

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        }

    def _check_status(self, resp: httpx.Response) -> None:
        if resp.status_code == 429:
            raise _GuardError("请求过于频繁，已被限流")
        if resp.status_code in {401, 403}:
            raise _GuardError(f"拒绝访问（HTTP {resp.status_code}）")

    def _guard(self, final_url: str, text: str) -> None:
        url_lower = final_url.lower()
        if "wappass.baidu.com" in url_lower:
            raise _GuardError("触发百度安全验证")
        if "antispider" in url_lower:
            raise _GuardError("触发搜狗反爬验证")
        if "qcaptcha.so.com" in url_lower:
            raise _GuardError("触发 360 安全验证")
        if "captcha" in url_lower or "challenge" in url_lower:
            raise _GuardError("触发人机验证")
        head = text[:4000]
        if "antispider" in head or "请输入验证码" in head or "安全验证" in head:
            raise _GuardError("触发安全验证")

    async def search(self, q: str, *, count: int, lang: str) -> list[SearchHit]:
        code = self.code
        if _breaker_open(code):
            raise _reason_error(code, "连续失败已暂停，稍后自动恢复")
        await _throttle(code)
        try:
            text, final_url = await self._fetch(q)
            self._guard(final_url, text)
            hits = self._parse(text, q)
        except _GuardError as e:
            _record_failure(code)
            raise _reason_error(code, str(e)) from e
        except Exception as e:
            _record_failure(code)
            raise _reason_error(code, _network_reason(e)) from e
        relevant = [hit for hit in hits if _relevant(hit.title, hit.snippet, q)]
        if not relevant:
            # 没有相关结果不触发熔断：可能只是该关键词本身没有内容
            raise _reason_error(code, "未找到与关键词相关的结果")
        _record_success(code)
        return relevant[: _count(count)]

    async def probe(self, q: str) -> list[SearchHit]:
        """状态探测：跳过熔断/限速，返回结果供 service 判定可用性。"""
        try:
            text, final_url = await self._fetch(q)
            self._guard(final_url, text)
            return [hit for hit in self._parse(text, q) if _relevant(hit.title, hit.snippet, q)]
        except _GuardError as e:
            raise _reason_error(self.code, str(e)) from e
        except Exception as e:
            raise _reason_error(self.code, _network_reason(e)) from e

    async def _fetch(self, q: str) -> tuple[str, str]:
        jar = _cookie_jars.setdefault(self.code, httpx.Cookies())
        attempts = 2
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                async with httpx.AsyncClient(
                    timeout=BUILTIN_TIMEOUT_SECONDS,
                    # 动态读 providers 模块属性：测试经 set_test_transport 重新赋值
                    transport=providers_mod._transport_override,
                    follow_redirects=True,
                    max_redirects=MAX_ENGINE_REDIRECTS,
                    trust_env=False,
                    cookies=jar,
                ) as client:
                    resp = await client.get(
                        self.endpoint, params=self._params(q), headers=self._headers()
                    )
                self._check_status(resp)
                resp.raise_for_status()
                return resp.text, str(resp.url)
            except _GuardError:
                raise
            except (httpx.ConnectError, httpx.ConnectTimeout) as e:
                last = e
                if attempt + 1 < attempts:
                    await asyncio.sleep(0.6)
            except Exception as e:
                raise e
        raise last or RuntimeError("请求失败")

    def _parse(self, text: str, q: str) -> list[SearchHit]:
        raise NotImplementedError


class BraveWebProvider(_BuiltinProvider):
    """Brave 网页版：div.snippet[data-type=web] + 真链 + search-snippet-title。"""

    code = "brave_web"
    endpoint = "https://search.brave.com/search"
    query_param = "q"
    user_agent = DESKTOP_UA

    def _parse(self, text: str, q: str) -> list[SearchHit]:
        root = lxml_html.fromstring(text)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in root.xpath(
            '//div[contains(concat(" ", normalize-space(@class), " "), " snippet ")'
            ' and @data-type="web"]'
        ):
            anchor = None
            for node in item.xpath('.//div[contains(@class,"result-content")]//a[@href]'):
                href = str(node.get("href") or "").strip()
                if href.startswith("http") and not _is_redirect_url(href):
                    anchor = node
                    break
            if anchor is None:
                continue
            url = str(anchor.get("href")).strip()
            if url in seen or _is_vertical_url(url):
                continue
            title = _first_text(
                item,
                [
                    './/div[contains(@class,"search-snippet-title")]',
                    './/div[contains(@class,"title")]',
                ],
            ) or _collapse(anchor.text_content())
            snippet = _first_text(
                item,
                [
                    './/div[contains(@class,"generic-snippet")]',
                    './/div[contains(@class,"snippet-description")]',
                ],
            )
            hit = _make_hit(title=title, url=url, snippet=snippet, source=self.code)
            if hit is not None:
                seen.add(url)
                hits.append(hit)
                if len(hits) >= MAX_RESULTS:
                    break
        return hits


class BaiduMobileProvider(_BuiltinProvider):
    """百度移动版：div.c-result + data-log.mu 里的真实地址。"""

    code = "baidu"
    endpoint = "https://m.baidu.com/s"
    query_param = "word"
    user_agent = MOBILE_UA

    _SKIP_LOG_MARKERS = ("recommend_list", "fakeurl", "ikan", "nourl", "aladdin")

    def _parse(self, text: str, q: str) -> list[SearchHit]:
        root = lxml_html.fromstring(text)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in root.xpath(
            '//div[contains(concat(" ", normalize-space(@class), " "), " c-result ")]'
        ):
            log_attr = str(item.get("data-log") or "")
            if not log_attr or any(marker in log_attr for marker in self._SKIP_LOG_MARKERS):
                continue
            url = _extract_json_url(log_attr)
            if not url or _is_redirect_url(url) or _is_vertical_url(url):
                continue
            if url in seen:
                continue
            title = _first_text(
                item,
                [
                    './/div[contains(@class,"c-title")]',
                    ".//h3",
                    './/div[contains(@class,"c-title-text")]',
                ],
            )
            if not title:
                continue
            snippet = _first_text(
                item,
                [
                    './/div[contains(@class,"c-abstract")]',
                    './/div[contains(@class,"c-span-last")]',
                    './/span[contains(@class,"c-color-text")]',
                ],
            )
            hit = _make_hit(title=title, url=url, snippet=snippet, source=self.code)
            if hit is not None:
                seen.add(url)
                hits.append(hit)
                if len(hits) >= MAX_RESULTS:
                    break
        return hits


class SogouMobileProvider(_BuiltinProvider):
    """搜狗移动版：h3.vr-tit 的 resultLink + url= 查询参数里的真链。"""

    code = "sogou"
    endpoint = "https://m.sogou.com/web/searchList.jsp"
    query_param = "keyword"
    user_agent = MOBILE_UA

    def _parse(self, text: str, q: str) -> list[SearchHit]:
        root = lxml_html.fromstring(text)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for heading in root.xpath(
            '//h3[contains(concat(" ", normalize-space(@class), " "), " vr-tit ")]'
        ):
            title = _collapse(heading.text_content())
            if not title or title in {"大家还在搜", "相关搜索"}:
                continue
            href = ""
            anchors = heading.xpath('.//a[contains(@class,"resultLink") and @href]')
            if anchors:
                href = str(anchors[0].get("href") or "")
            else:
                parents = heading.xpath("ancestor::a[@href][1]")
                if parents:
                    href = str(parents[0].get("href") or "")
            url = _extract_param_url(href, "url")
            if not url or not _is_http_url(url) or _is_redirect_url(url) or _is_vertical_url(url):
                continue
            if url in seen:
                continue
            snippet = ""
            for ancestor in heading.xpath("ancestor::div")[:4]:
                snippet = _first_text(
                    ancestor,
                    [
                        './/div[contains(@class,"abstract-module")]',
                        './/p[contains(@class,"str-text-info")]',
                        './/p[contains(@class,"str_info")]',
                    ],
                )
                if snippet:
                    break
            hit = _make_hit(title=title, url=url, snippet=snippet, source=self.code)
            if hit is not None:
                seen.add(url)
                hits.append(hit)
                if len(hits) >= MAX_RESULTS:
                    break
        return hits


class So360MobileProvider(_BuiltinProvider):
    """360 移动版：res-list 容器；真链优先 data-url，其次 u= 参数。"""

    code = "so360"
    endpoint = "https://m.so.com/s"
    query_param = "q"
    user_agent = MOBILE_UA

    def _parse(self, text: str, q: str) -> list[SearchHit]:
        root = lxml_html.fromstring(text)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in root.xpath(
            '//li[contains(@class,"res-list")]'
            ' | //div[contains(@class,"res-list")]'
            ' | //div[contains(@class,"result")]'
        ):
            anchors = item.xpath(".//h3//a[@href] | .//a[@data-url] | .//a[@href]")
            if not anchors:
                continue
            anchor = anchors[0]
            raw = str(anchor.get("data-url") or anchor.get("href") or "")
            url = _extract_param_url(raw, "u")
            if not url or not _is_http_url(url) or _is_redirect_url(url) or _is_vertical_url(url):
                continue
            if url in seen:
                continue
            title = _first_text(item, [".//h3", './/div[contains(@class,"res-title")]'])
            if not title:
                continue
            snippet = _first_text(
                item,
                [
                    './/p[contains(@class,"res-desc")]',
                    './/p[contains(@class,"res-rich")]',
                    ".//p",
                ],
            )
            hit = _make_hit(title=title, url=url, snippet=snippet, source=self.code)
            if hit is not None:
                seen.add(url)
                hits.append(hit)
                if len(hits) >= MAX_RESULTS:
                    break
        return hits


class BingRssProvider(_BuiltinProvider):
    """必应 RSS：只保留与查询词相关的结果，挡掉必应偶发的无关推荐。"""

    code = "bing"
    endpoint = "https://www.bing.com/search"
    query_param = "q"
    user_agent = DESKTOP_UA

    def _params(self, query: str) -> dict[str, str]:
        # 只带 q/format：加 mkt/setlang 会被必应 302 回首页（2026-10 实测）
        return {"q": query, "format": "rss"}

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
        }

    def _parse(self, text: str, q: str) -> list[SearchHit]:
        try:
            root = etree.fromstring(text.encode("utf-8"))
        except (etree.XMLSyntaxError, ValueError) as e:
            raise ValueError("RSS 解析失败") from e
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in root.iter("item"):
            url = (item.findtext("link") or "").strip()
            if not url or url in seen or _is_redirect_url(url) or _is_vertical_url(url):
                continue
            title = _collapse(item.findtext("title"))
            description = _collapse(item.findtext("description"))
            hit = _make_hit(title=title, url=url, snippet=description, source=self.code)
            if hit is not None:
                seen.add(url)
                hits.append(hit)
                if len(hits) >= MAX_RESULTS:
                    break
        return hits


class BingNewsProvider(BingRssProvider):
    """必应新闻 RSS：apiclick 链接里的 url= 是可直接引用的原始链接。"""

    code = "bing_news"
    endpoint = "https://www.bing.com/news/search"

    def _params(self, query: str) -> dict[str, str]:
        return {"q": query, "format": "rss"}

    def _parse(self, text: str, q: str) -> list[SearchHit]:
        try:
            root = etree.fromstring(text.encode("utf-8"))
        except (etree.XMLSyntaxError, ValueError) as e:
            raise ValueError("未返回 RSS 结果（可能被重定向）") from e
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in root.iter("item"):
            raw_link = (item.findtext("link") or "").strip()
            url = _extract_param_url(raw_link, "url") or raw_link
            if not url or url in seen or not _is_http_url(url) or _is_vertical_url(url):
                continue
            title = _collapse(item.findtext("title"))
            snippet = _plain(item.findtext("description"))
            hit = _make_hit(
                title=title,
                url=url,
                snippet=snippet,
                source=self.code,
                published=item.findtext("pubDate"),
            )
            if hit is not None:
                seen.add(url)
                hits.append(hit)
                if len(hits) >= MAX_RESULTS:
                    break
        return hits


class GoogleNewsProvider(BingNewsProvider):
    """谷歌新闻 RSS：结果链接是新闻中转页，适合看标题，默认关闭。"""

    code = "google_news"
    endpoint = "https://news.google.com/rss/search"

    def _params(self, query: str) -> dict[str, str]:
        return {
            "q": query,
            "hl": "zh-CN",
            "gl": "CN",
            "ceid": "CN:zh-Hans",
        }

    def _parse(self, text: str, q: str) -> list[SearchHit]:
        try:
            root = etree.fromstring(text.encode("utf-8"))
        except (etree.XMLSyntaxError, ValueError) as e:
            raise ValueError("RSS 解析失败") from e
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in root.iter("item"):
            url = (item.findtext("link") or "").strip()
            if not url or url in seen or not _is_http_url(url):
                continue
            title = _collapse(item.findtext("title"))
            snippet = _plain(item.findtext("description"))
            hit = _make_hit(
                title=title,
                url=url,
                snippet=snippet,
                source=self.code,
                published=item.findtext("pubDate"),
            )
            if hit is not None:
                seen.add(url)
                hits.append(hit)
                if len(hits) >= MAX_RESULTS:
                    break
        return hits


class DuckDuckGoHtmlProvider(_BuiltinProvider):
    """DuckDuckGo HTML 版：默认关闭；被墙/被挑战时给出明确原因。"""

    code = "duckduckgo"
    endpoint = "https://html.duckduckgo.com/html/"
    query_param = "q"
    user_agent = DESKTOP_UA

    def _check_status(self, resp: httpx.Response) -> None:
        if resp.status_code == 202:
            raise _GuardError("被要求完成人机验证")
        super()._check_status(resp)

    def _parse(self, text: str, q: str) -> list[SearchHit]:
        root = lxml_html.fromstring(text)
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for item in root.xpath('//div[contains(@class,"web-result")]'):
            anchors = item.xpath('.//a[contains(@class,"result__a") and @href]')
            if not anchors:
                continue
            anchor = anchors[0]
            href = str(anchor.get("href") or "")
            url = _extract_param_url(href, "uddg") or href
            if not url or not _is_http_url(url) or _is_redirect_url(url):
                continue
            if url in seen:
                continue
            title = _collapse(anchor.text_content())
            snippet = _first_text(
                item,
                [
                    './/a[contains(@class,"result__snippet")]',
                    './/div[contains(@class,"result__snippet")]',
                ],
            )
            hit = _make_hit(title=title, url=url, snippet=snippet, source=self.code)
            if hit is not None:
                seen.add(url)
                hits.append(hit)
                if len(hits) >= MAX_RESULTS:
                    break
        return hits


_BUILTIN_PROVIDER_CLASSES: dict[str, type[_BuiltinProvider]] = {
    provider.code: provider
    for provider in (
        BraveWebProvider,
        BaiduMobileProvider,
        SogouMobileProvider,
        So360MobileProvider,
        BingRssProvider,
        BingNewsProvider,
        GoogleNewsProvider,
        DuckDuckGoHtmlProvider,
    )
}


def is_builtin_source(code: str) -> bool:
    return code in _BUILTIN_PROVIDER_CLASSES


def create_builtin_provider(code: str) -> SearchProvider | None:
    """按 code 构造内置源实例；未知 code 返回 None。"""
    cls = _BUILTIN_PROVIDER_CLASSES.get(code)
    return cls() if cls is not None else None


__all__ = [
    "BUILTIN_SOURCE_CODES",
    "BUILTIN_SOURCE_LABELS",
    "BREAKER_COOLDOWN_SECONDS",
    "BREAKER_THRESHOLD",
    "BUILTIN_TIMEOUT_SECONDS",
    "BaiduMobileProvider",
    "BingNewsProvider",
    "BingRssProvider",
    "BraveWebProvider",
    "GoogleNewsProvider",
    "DESKTOP_UA",
    "DuckDuckGoHtmlProvider",
    "MOBILE_UA",
    "REQUEST_INTERVAL_SECONDS",
    "So360MobileProvider",
    "SogouMobileProvider",
    "create_builtin_provider",
    "is_builtin_source",
    "reset_runtime_state",
]
