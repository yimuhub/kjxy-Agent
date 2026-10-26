"""搜索服务：供应商选择、降级链、缓存、日配额与联网状态探测。

缓存/配额都是进程内的（单实例部署足够；多实例需换 Redis，后续演进）。
"""

from __future__ import annotations

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
from app.modules.yards.settings_service import get_setting

log = get_logger(__name__)

CACHE_TTL_SECONDS = 600
STATUS_TTL_SECONDS = 60
STATUS_PROBE_TIMEOUT_SECONDS = 2.5

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
    """测试专用：清空进程内缓存与配额。"""
    _cache.clear()
    _status_cache.clear()
    _quota.clear()


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

    链顺序：小院指定供应商优先，其后按 searxng → tavily → brave 补齐；
    tavily / brave 仅在配置了 Key 时进入链。
    """
    cfg = get_setting(db, yard_id, "search")
    if not cfg.get("enabled", True):
        return "", []
    preferred = str(cfg.get("provider") or "searxng").lower()
    timeout = float(cfg.get("timeout_seconds") or 8)
    tavily_key = str(cfg.get("tavily_api_key") or "").strip()
    brave_key = str(cfg.get("brave_api_key") or "").strip()
    searxng_url = str(cfg.get("searxng_url") or "").strip()

    def build(code: str) -> SearchProvider | None:
        if code == "searxng":
            return SearxngProvider(searxng_url, timeout)
        if code == "tavily" and tavily_key:
            return TavilyProvider(tavily_key, timeout)
        if code == "brave" and brave_key:
            return BraveProvider(brave_key, timeout)
        return None

    chain: list[SearchProvider] = []
    seen: set[str] = set()
    for code in (preferred, "searxng", "tavily", "brave"):
        if code in seen:
            continue
        seen.add(code)
        provider = build(code)
        if provider is not None:
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
            details={"provider": ""},
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
    raise AppError(
        "SEARCH_PROVIDER_UNAVAILABLE",
        "所有搜索供应商均不可用，请检查网络或供应商配置。",
        http_status=503,
        details={"primary": primary, "failures": failures},
    )


# ---------------------------------------------------------------------------
# 联网状态探测
# ---------------------------------------------------------------------------


def _status(mode: str, provider: str, reason: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "provider": provider,
        "checked_at": _now_iso(),
        "reason": reason,
    }


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


async def _probe_provider(provider: SearchProvider) -> bool:
    try:
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
    try:
        primary, chain = select_providers(db, yard_id)
    except Exception as e:
        log.warning("network_status select_providers failed: %s", e)
        return _status("offline", "", "搜索配置读取失败。")
    if not chain:
        return _status("offline", "", "搜索功能未启用或未配置供应商。")

    for index, provider in enumerate(chain):
        if await _probe_provider(provider):
            if index == 0:
                return _status("online", provider.code, "搜索供应商可用。")
            return _status(
                "degraded",
                provider.code,
                f"{primary} 不可达，已降级到 {provider.code}。",
            )
    return _status("offline", primary, "所有搜索供应商均不可达。")


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
        data = _status("offline", "", "联网状态探测失败。")
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
