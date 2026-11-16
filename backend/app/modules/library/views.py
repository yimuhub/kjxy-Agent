"""官方技能库/插件库 HTTP 抓取与序列化视图（从 library/service.py 拆分，保持对外重导出兼容）。"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from app.core.errors import AppError
from app.core.net_guard import check_egress_url
from app.core.timezone import ensure_utc as _aware
from app.core.timezone import to_iso as _iso
from app.modules.library import crypto
from app.modules.library.models import LibraryItem, LibrarySource

Fetcher = Callable[..., Any]

SYNC_TIMEOUT_SECONDS = 15


def _ts(value: datetime | None) -> float:
    aware = _aware(value)
    return aware.timestamp() if aware is not None else 0.0


def _version_key(version: str) -> tuple[tuple[int, Any], ...]:
    """semver 友好排序键：数字段按数值、其余按字符串，长度不同也能稳定比较。"""
    parts: list[tuple[int, Any]] = []
    for token in re.split(r"[.\-+_]", str(version or "")):
        if token.isdigit():
            parts.append((0, int(token)))
        elif token:
            parts.append((1, token))
    return tuple(parts)


def _parse_dt(raw: Any, *, field_name: str) -> datetime | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=UTC)
    text = str(raw).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as e:
        raise AppError(
            "LIBRARY_MANIFEST_INVALID",
            f"清单字段 {field_name} 不是合法时间。",
            http_status=422,
            details={"field": field_name},
        ) from e
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _text(raw: Any, limit: int, default: str = "") -> str:
    return str(raw or default).strip()[:limit]


def item_key_for(item_type: str, code: str) -> str:
    return f"official:{item_type}:{code}"


def manifest_url(base_url: str) -> str:
    """base_url 末尾带 ``/`` 视为目录，拼 ``manifest.json``；否则视为清单本身。"""
    base = (base_url or "").strip()
    if base.endswith("/"):
        return urljoin(base, "manifest.json")
    return base


def _require_https(url: str, *, field_name: str) -> str:
    parsed = urlparse(url or "")
    if parsed.scheme != "https" or not parsed.netloc:
        raise AppError(
            "LIBRARY_SOURCE_INSECURE",
            f"{field_name} 必须是 https 地址。",
            http_status=400,
            details={"url": url},
        )
    return url


def _code_from_item_key(item_key: str, item_type: str) -> str:
    prefix = f"official:{item_type}:"
    return item_key[len(prefix) :] if item_key.startswith(prefix) else ""


@dataclass
class FetchResponse:
    status_code: int
    content: bytes
    headers: dict[str, str] = field(default_factory=dict)


def _normalize_response(raw: Any) -> FetchResponse:
    if isinstance(raw, dict):
        status = int(raw.get("status_code", raw.get("status", 200)) or 200)
        content = raw.get("content", raw.get("body", b"")) or b""
        headers = raw.get("headers") or {}
    else:
        status = int(getattr(raw, "status_code", 200) or 200)
        content = getattr(raw, "content", b"") or b""
        headers = getattr(raw, "headers", {}) or {}
    if isinstance(content, str):
        content = content.encode("utf-8")
    return FetchResponse(
        status_code=status,
        content=bytes(content),
        headers={str(key).lower(): str(value) for key, value in dict(headers).items()},
    )


def _default_fetch(url: str, headers: dict[str, str]) -> FetchResponse:
    try:
        response = httpx.get(
            url,
            headers=headers,
            timeout=SYNC_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError as e:
        raise AppError(
            "LIBRARY_SYNC_FAILED",
            "官方库源暂不可达（离线时已安装能力不受影响）。",
            http_status=502,
            details={"url": url},
        ) from e
    return _normalize_response(response)


def _call_fetcher(fetcher: Fetcher, url: str, headers: dict[str, str]) -> FetchResponse:
    try:
        raw = fetcher(url, headers)
    except TypeError:
        raw = fetcher(url)
    return _normalize_response(raw)


def _fetch(url: str, headers: dict[str, str], fetcher: Fetcher | None) -> FetchResponse:
    _require_https(url, field_name="官方库请求地址")
    if fetcher is None:
        check_egress_url(url, context="library_source")
        return _default_fetch(url, headers)
    return _call_fetcher(fetcher, url, headers)


def serialize_source(source: LibrarySource, *, can_manage: bool = False) -> dict[str, Any]:
    fingerprint = crypto.public_key_fingerprint(source.public_key_b64 or "")
    return {
        "id": source.id,
        "yard_id": source.yard_id,
        "name": source.name,
        "kind": source.kind,
        "base_url": source.base_url,
        "channel": source.channel,
        "key_id": source.key_id or fingerprint["key_id"],
        "key_algorithm": fingerprint["alg"],
        "enabled": bool(source.enabled),
        "sequence": int(source.sequence or 0),
        "status": source.status,
        "etag": source.etag,
        "last_checked_at": _iso(source.last_checked_at),
        "last_sync_at": _iso(source.last_sync_at),
        "last_error": source.last_error,
        "metadata": source.metadata_json or {},
        "can_manage": can_manage,
    }


def serialize_item(
    item: LibraryItem,
    *,
    installed_version: str | None = None,
    latest_version: str | None = None,
) -> dict[str, Any]:
    installed = bool(installed_version)
    return {
        "id": item.id,
        "source_id": item.source_id,
        "item_key": item.item_key,
        "item_type": item.item_type,
        "code": item.code,
        "name": item.name,
        "version": item.version,
        "min_app_version": item.min_app_version,
        "max_app_version": item.max_app_version,
        "description": item.description,
        "category": item.category,
        "risk_level": item.risk_level,
        "license": item.license,
        "author": item.author,
        "sha256": item.sha256,
        "size_bytes": int(item.size_bytes or 0),
        "download_url": item.download_url,
        "status": item.status,
        "sequence": int(item.sequence or 0),
        "published_at": _iso(item.published_at),
        "installed": installed,
        "installed_version": installed_version,
        "is_latest": latest_version is not None and latest_version == item.version,
    }


__all__ = [
    "FetchResponse",
    "Fetcher",
    "SYNC_TIMEOUT_SECONDS",
    "_call_fetcher",
    "_code_from_item_key",
    "_default_fetch",
    "_fetch",
    "_normalize_response",
    "_parse_dt",
    "_require_https",
    "_text",
    "_ts",
    "_version_key",
    "item_key_for",
    "manifest_url",
    "serialize_item",
    "serialize_source",
]
