"""搜索服务：供应商选择、降级链、缓存、日配额与联网状态探测。

缓存/配额都是进程内的（单实例部署足够；多实例需换 Redis，后续演进）。
"""

from __future__ import annotations

import asyncio
import copy
import time
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.search.providers import (
    MAX_RESULTS,
    SNIPPET_MAX_CHARS,
    BraveProvider,
    SearchHit,
    SearchProvider,
    SearxngProvider,
    TavilyProvider,
)
from app.modules.search.providers_builtin import (
    BUILTIN_SOURCE_CODES,
    create_builtin_provider,
    reset_runtime_state,
)
from app.modules.yards.settings_service import get_setting

log = get_logger(__name__)

CACHE_TTL_SECONDS = 600
STATUS_TTL_SECONDS = 60
STATUS_PROBE_TIMEOUT_SECONDS = 2.5
#: 公网连通性探测：任一 URL 返回 <500 即视为有网，单次超时 2s
INTERNET_PROBE_TIMEOUT_SECONDS = 2.0
INTERNET_PROBE_URLS = ("https://www.baidu.com", "https://cn.bing.com")
#: 内置源状态探测用真实查询，要求解析出 ≥1 条结果
STATUS_PROBE_QUERY = "农业"

#: 进程内缓存 / 配额
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_status_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_quota: dict[tuple[str, str], int] = {}

#: 测试注入口：生产恒为 None
_transport_override: httpx.AsyncBaseTransport | None = None


def set_test_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """测试专用：注入/清除 MockTransport（同时作用于供应商请求）。"""
    global _transport_override
    _transport_override = transport
    from app.modules.search.providers import set_test_transport as _set_provider_transport

    _set_provider_transport(transport)


def _reset_state() -> None:
    """测试专用：清空进程内缓存、配额与内置源限速/熔断状态。"""
    _cache.clear()
    _status_cache.clear()
    _quota.clear()
    reset_runtime_state()


def _client(timeout: float) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        transport=_transport_override,
        follow_redirects=False,
        trust_env=False,
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 供应商选择
# ---------------------------------------------------------------------------


def select_providers(db: Session, yard_id: str) -> tuple[str, list[SearchProvider]]:
    """返回 (主供应商 code, 降级链)；搜索关闭或无可用供应商时返回 ("", [])。

    provider == "auto"：启用的内置源（按 builtin_sources 顺序）→ 配置了地址的
    SearXNG → 配置了 Key 的 Tavily → 配置了 Key 的 Brave。
    指定了具体供应商：该供应商优先，其后是启用的内置源，再到其余已配置供应商。
    任何供应商不重复出现。
    """
    cfg = get_setting(db, yard_id, "search")
    if not cfg.get("enabled", True):
        return "", []
    preferred = str(cfg.get("provider") or "auto").lower()
    if preferred not in {"auto", "searxng", "tavily", "brave"}:
        preferred = "auto"  # 未知/历史脏值兜底为 auto
    timeout = float(cfg.get("timeout_seconds") or 8)
    tavily_key = str(cfg.get("tavily_api_key") or "").strip()
    brave_key = str(cfg.get("brave_api_key") or "").strip()
    searxng_url = str(cfg.get("searxng_url") or "").strip()
    raw_sources = cfg.get("builtin_sources")
    if isinstance(raw_sources, list | tuple):
        sources = [str(code) for code in raw_sources if str(code) in BUILTIN_SOURCE_CODES]
    else:
        sources = list(BUILTIN_SOURCE_CODES)

    def build(code: str) -> SearchProvider | None:
        if code in BUILTIN_SOURCE_CODES:
            return create_builtin_provider(code)
        if code == "searxng":
            return SearxngProvider(searxng_url, timeout)
        if code == "tavily" and tavily_key:
            return TavilyProvider(tavily_key, timeout)
        if code == "brave" and brave_key:
            return BraveProvider(brave_key, timeout)
        return None

    head = [] if preferred == "auto" else [preferred]
    order = [*head, *sources, "searxng", "tavily", "brave"]

    chain: list[SearchProvider] = []
    seen: set[str] = set()
    for code in order:
        if not code or code in seen:
            continue
        if code == "searxng" and not searxng_url:
            seen.add(code)  # 未配置 SearXNG 地址：不进链
            continue
        provider = build(code)
        if provider is None:
            continue
        seen.add(code)
        chain.append(provider)
    if not chain:
        return "", []
    return chain[0].code, chain


# ---------------------------------------------------------------------------
# 配额
# ---------------------------------------------------------------------------


def _daily_limit(db: Session, yard_id: str) -> int:
    cfg = get_setting(db, yard_id, "search")
    try:
        return int(cfg.get("daily_limit") or 500)
    except (TypeError, ValueError):
        return 500


def quota_snapshot(db: Session, yard_id: str) -> dict[str, Any]:
    limit = _daily_limit(db, yard_id)
    used = _quota.get((yard_id, _today()), 0)
    return {"used": used, "limit": limit, "remaining": max(0, limit - used)}


def _ensure_quota(yard_id: str, limit: int) -> None:
    used = _quota.get((yard_id, _today()), 0)
    if limit > 0 and used >= limit:
        raise AppError(
            "SEARCH_QUOTA_EXCEEDED",
            "今日联网搜索次数已达上限，请明日再试。",
            http_status=429,
            details={"limit": limit, "used": used, "date": _today()},
        )


def _consume_quota(yard_id: str) -> None:
    key = (yard_id, _today())
    _quota[key] = _quota.get(key, 0) + 1


# ---------------------------------------------------------------------------
# 搜索
# ---------------------------------------------------------------------------


def _hit_dict(hit: SearchHit) -> dict[str, Any]:
    return {
        "title": hit.title,
        "url": hit.url,
        "snippet": hit.snippet[:SNIPPET_MAX_CHARS],
        "source": hit.source,
        "published": hit.published,
    }


def _prune_cache(now: float) -> None:
    if len(_cache) <= 512:
        return
    for key in [k for k, (ts, _) in _cache.items() if now - ts >= CACHE_TTL_SECONDS]:
        _cache.pop(key, None)


async def search_web(
    db: Session,
    yard_id: str,
    query: str,
    count: int = 5,
    lang: str = "zh-CN",
) -> dict[str, Any]:
    """联网搜索：缓存 10 分钟、日配额、按降级链回退；全失败抛 503。"""
    q = (query or "").strip()
    if not q:
        raise AppError("INVALID_REQUEST", "搜索关键词不能为空。", http_status=400)
    try:
        limit_count = int(count or 5)
    except (TypeError, ValueError):
        limit_count = 5
    limit_count = max(1, min(limit_count, MAX_RESULTS))
    language = (lang or "zh-CN").strip() or "zh-CN"

    cache_key = f"{yard_id}|{q}|{language}|{limit_count}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached is not None and now - cached[0] < CACHE_TTL_SECONDS:
        result = copy.deepcopy(cached[1])
        result["cached"] = True
        return result

    limit = _daily_limit(db, yard_id)
    _ensure_quota(yard_id, limit)

    primary, chain = select_providers(db, yard_id)
    if not chain:
        raise AppError(
            "SEARCH_PROVIDER_UNAVAILABLE",
            "搜索功能未启用或未配置可用的搜索供应商。",
            http_status=503,
            details={"provider": "", "sources": []},
        )

    failures: list[dict[str, str]] = []
    for provider in chain:
        try:
            hits = await provider.search(q, count=limit_count, lang=language)
        except AppError as e:
            failures.append({"provider": provider.code, "message": e.message})
            continue
        except Exception as e:  # 防御：供应商实现不应抛出未包装异常
            log.warning("search provider %s unexpected error: %s", provider.code, e)
            failures.append({"provider": provider.code, "message": "供应商异常"})
            continue

        _consume_quota(yard_id)
        result = {
            "query": q,
            "provider": provider.code,
            "results": [_hit_dict(hit) for hit in hits[:limit_count]],
            "fetched_at": _now_iso(),
            "cached": False,
        }
        _cache[cache_key] = (now, copy.deepcopy(result))
        _prune_cache(now)
        return result

    log.warning("all search providers failed for yard=%s: %s", yard_id, failures)
    internet = await _probe_internet()
    message = (
        "当前设备无网络连接，暂时无法联网搜索。"
        if not internet
        else "联网受限：所有搜索源暂不可用，请稍后重试。"
    )
    raise AppError(
        "SEARCH_PROVIDER_UNAVAILABLE",
        message,
        http_status=503,
        details={
            "primary": primary,
            "failures": failures,
            "sources": [provider.code for provider in chain],
            "internet": internet,
        },
    )


# ---------------------------------------------------------------------------
# 联网状态探测
# ---------------------------------------------------------------------------


def _status(mode: str, provider: str, reason: str, internet: bool) -> dict[str, Any]:
    return {
        "mode": mode,
        "provider": provider,
        "internet": internet,
        "checked_at": _now_iso(),
        "reason": reason,
    }


async def _probe_internet() -> bool:
    """公网连通性：百度 / 必应任一返回 <500 即视为有网（单次超时 2s）。"""
    for url in INTERNET_PROBE_URLS:
        try:
            async with _client(INTERNET_PROBE_TIMEOUT_SECONDS) as client:
                resp = await client.get(url)
            if resp.status_code < 500:
                return True
        except Exception as e:  # 网络异常换下一个探测点
            log.info("internet probe failed url=%s: %s", url, e)
            continue
    return False


async def _probe_searxng(provider: SearxngProvider) -> bool:
    if not provider.base_url:
        return False
    async with _client(STATUS_PROBE_TIMEOUT_SECONDS) as client:
        resp = await client.get(f"{provider.base_url}/")
    return resp.status_code < 500


async def _probe_tavily(provider: TavilyProvider) -> bool:
    headers = {"Authorization": f"Bearer {provider.api_key}"}
    async with _client(STATUS_PROBE_TIMEOUT_SECONDS) as client:
        resp = await client.get("https://api.tavily.com/", headers=headers)
    # 401/403 也说明 API 可达；只看服务端是否 5xx
    return resp.status_code < 500


async def _probe_brave(provider: BraveProvider) -> bool:
    headers = {"X-Subscription-Token": provider.api_key}
    async with _client(STATUS_PROBE_TIMEOUT_SECONDS) as client:
        resp = await client.get("https://api.search.brave.com/", headers=headers)
    return resp.status_code < 500


async def _probe_builtin(provider: SearchProvider) -> bool:
    """内置源探测：发真实查询并要求解析出 ≥1 条结果（整体超时 2.5s）。"""
    try:
        hits = await asyncio.wait_for(
            provider.search(STATUS_PROBE_QUERY, count=1, lang="zh-CN"),
            STATUS_PROBE_TIMEOUT_SECONDS,
        )
    except Exception as e:  # 超时 / 不可用 / 0 条结果都视作不可用
        log.info("search probe failed provider=%s: %s", provider.code, e)
        return False
    return len(hits) > 0


async def _probe_provider(provider: SearchProvider) -> bool:
    try:
        if provider.code in BUILTIN_SOURCE_CODES:
            return await _probe_builtin(provider)
        if isinstance(provider, SearxngProvider):
            return await _probe_searxng(provider)
        if isinstance(provider, TavilyProvider):
            return await _probe_tavily(provider)
        if isinstance(provider, BraveProvider):
            return await _probe_brave(provider)
        return False
    except Exception as e:  # 任何网络异常都视作不可达
        log.info("search probe failed provider=%s: %s", provider.code, e)
        return False


async def _check_network(db: Session, yard_id: str) -> dict[str, Any]:
    internet = await _probe_internet()
    try:
        primary, chain = select_providers(db, yard_id)
    except Exception as e:
        log.warning("network_status select_providers failed: %s", e)
        if not internet:
            return _status("offline", "", "当前无网络连接。", False)
        return _status("degraded", "", "搜索配置读取失败。", True)
    if not chain:
        if not internet:
            return _status("offline", "", "当前无网络连接，且未启用任何搜索源。", False)
        return _status("degraded", "", "未启用任何搜索源。", True)

    for index, provider in enumerate(chain):
        if await _probe_provider(provider):
            if index == 0:
                return _status("online", provider.code, "搜索源可用。", internet)
            return _status(
                "degraded",
                provider.code,
                f"{primary} 不可用，已降级到 {provider.code}。",
                internet,
            )
    if not internet:
        return _status("offline", primary, "当前无网络连接，搜索源不可达。", False)
    return _status("degraded", primary, "联网受限：所有搜索源暂不可用。", True)


async def network_status(db: Session, yard_id: str) -> dict[str, Any]:
    """联网状态（60 秒缓存）；任何异常都返回 offline，不向上抛。"""
    now = time.time()
    cached = _status_cache.get(yard_id)
    if cached is not None and now - cached[0] < STATUS_TTL_SECONDS:
        return copy.deepcopy(cached[1])
    try:
        data = await _check_network(db, yard_id)
    except Exception as e:  # 最后一道防线：状态接口永不 5xx
        log.warning("network_status failed: %s", e)
        data = _status("offline", "", "联网状态探测失败。", False)
    _status_cache[yard_id] = (now, copy.deepcopy(data))
    return data


__all__ = [
    "CACHE_TTL_SECONDS",
    "STATUS_TTL_SECONDS",
    "network_status",
    "quota_snapshot",
    "search_web",
    "select_providers",
    "set_test_transport",
]
