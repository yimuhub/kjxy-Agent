"""搜索供应商适配：SearXNG / Tavily / Brave。

统一返回 ``SearchHit``；任何上游失败都收敛为
``AppError("SEARCH_PROVIDER_UNAVAILABLE", ..., 503)``，由 service 做降级链。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol
from urllib.parse import urlparse

import httpx

from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger(__name__)

#: 单次最多返回条数 / 摘要截断长度
MAX_RESULTS = 8
SNIPPET_MAX_CHARS = 500

TAVILY_ENDPOINT = "https://api.tavily.com/search"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

#: 测试注入口：生产恒为 None
_transport_override: httpx.AsyncBaseTransport | None = None


def set_test_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """测试专用：注入/清除 MockTransport（杜绝真实出网）。"""
    global _transport_override
    _transport_override = transport


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        transport=_transport_override,
        follow_redirects=False,
        trust_env=False,
    )


def _clip(text: Any, limit: int = SNIPPET_MAX_CHARS) -> str:
    value = str(text or "").strip()
    return value[:limit]


def _is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.hostname)


def _unavailable(provider: str, exc: Exception | None = None) -> AppError:
    if exc is not None:
        log.warning("search provider %s unavailable: %s", provider, exc)
    return AppError(
        "SEARCH_PROVIDER_UNAVAILABLE",
        f"搜索供应商 {provider} 不可用，请稍后重试。",
        http_status=503,
        details={"provider": provider},
    )


@dataclass(slots=True)
class SearchHit:
    title: str
    url: str
    snippet: str
    source: str
    published: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SearchProvider(Protocol):
    code: str
    requires_key: bool

    async def search(self, q: str, *, count: int, lang: str) -> list[SearchHit]: ...


def _make_hit(
    *,
    title: Any,
    url: Any,
    snippet: Any,
    source: str,
    published: Any = None,
) -> SearchHit | None:
    clean_url = str(url or "").strip()
    if not _is_http_url(clean_url):
        return None
    published_text = str(published).strip() if published else None
    return SearchHit(
        title=_clip(title, 300) or clean_url,
        url=clean_url,
        snippet=_clip(snippet),
        source=source,
        published=published_text or None,
    )


def _count(count: int) -> int:
    return max(1, min(int(count or 5), MAX_RESULTS))


class SearxngProvider:
    """自建 SearXNG：GET {base}/search?format=json，无需 Key。"""

    code = "searxng"
    requires_key = False

    def __init__(self, base_url: str, timeout: float = 8.0) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout = timeout

    async def search(self, q: str, *, count: int, lang: str) -> list[SearchHit]:
        try:
            if not self.base_url:
                raise ValueError("searxng_url 未配置")
            params = {"q": q, "format": "json", "language": lang}
            async with _client(self.timeout) as client:
                resp = await client.get(f"{self.base_url}/search", params=params)
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("results")
            if not isinstance(rows, list):
                raise ValueError("响应缺少 results 列表")
        except Exception as e:
            raise _unavailable(self.code, e) from e

        hits: list[SearchHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            hit = _make_hit(
                title=row.get("title"),
                url=row.get("url"),
                snippet=row.get("content"),
                source=str(row.get("engine") or self.code),
                published=row.get("publishedDate") or row.get("published"),
            )
            if hit is not None:
                hits.append(hit)
        return hits[: _count(count)]


class TavilyProvider:
    """Tavily：POST JSON，api_key 放 body。"""

    code = "tavily"
    requires_key = True

    def __init__(self, api_key: str, timeout: float = 8.0) -> None:
        self.api_key = str(api_key or "")
        self.timeout = timeout

    async def search(self, q: str, *, count: int, lang: str) -> list[SearchHit]:
        try:
            if not self.api_key:
                raise ValueError("tavily_api_key 未配置")
            body = {
                "api_key": self.api_key,
                "query": q,
                "max_results": _count(count),
                "search_depth": "basic",
                "include_answer": False,
            }
            async with _client(self.timeout) as client:
                resp = await client.post(TAVILY_ENDPOINT, json=body)
            resp.raise_for_status()
            payload = resp.json()
            rows = payload.get("results")
            if not isinstance(rows, list):
                raise ValueError("响应缺少 results 列表")
        except Exception as e:
            raise _unavailable(self.code, e) from e

        hits: list[SearchHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            hit = _make_hit(
                title=row.get("title"),
                url=row.get("url"),
                snippet=row.get("content"),
                source=self.code,
                published=row.get("published_date"),
            )
            if hit is not None:
                hits.append(hit)
        return hits[: _count(count)]


class BraveProvider:
    """Brave Search：GET + X-Subscription-Token。"""

    code = "brave"
    requires_key = True

    def __init__(self, api_key: str, timeout: float = 8.0) -> None:
        self.api_key = str(api_key or "")
        self.timeout = timeout

    async def search(self, q: str, *, count: int, lang: str) -> list[SearchHit]:
        try:
            if not self.api_key:
                raise ValueError("brave_api_key 未配置")
            headers = {
                "Accept": "application/json",
                "X-Subscription-Token": self.api_key,
            }
            params: dict[str, str | int] = {
                "q": q,
                "count": _count(count),
                "search_lang": lang,
            }
            async with _client(self.timeout) as client:
                resp = await client.get(BRAVE_ENDPOINT, params=params, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
            web = payload.get("web") if isinstance(payload, dict) else None
            rows = web.get("results") if isinstance(web, dict) else None
            if not isinstance(rows, list):
                raise ValueError("响应缺少 web.results 列表")
        except Exception as e:
            raise _unavailable(self.code, e) from e

        hits: list[SearchHit] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            hit = _make_hit(
                title=row.get("title"),
                url=row.get("url"),
                snippet=row.get("description"),
                source=self.code,
                published=row.get("page_age") or row.get("age"),
            )
            if hit is not None:
                hits.append(hit)
        return hits[: _count(count)]


__all__ = [
    "BRAVE_ENDPOINT",
    "MAX_RESULTS",
    "SNIPPET_MAX_CHARS",
    "TAVILY_ENDPOINT",
    "BraveProvider",
    "SearchHit",
    "SearchProvider",
    "SearxngProvider",
    "TavilyProvider",
    "set_test_transport",
]
