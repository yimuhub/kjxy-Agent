"""官方技能库/插件库服务（v3.35）。

能力边界：
- 源管理：官方源（yard_id 为空）仅平台管理员可建/改；小院管理员只能建本小院私有源；
- 同步：只认 https + 发布者公钥验签，校验 schema_version/channel/sequence/expires_at，
  只 upsert 元数据并把最新版镜像成 ``MarketItem(source="official", verified=True)``，
  下载安装时才取包；撤销只置 revoked/blocked，绝不删除已安装数据；
- 安装/更新/回滚：下载后重算 sha256 并用发布者公钥验包，复用既有 Skill/Plugin 落地流程；
  回滚只依赖本地 Skill 版本表，因此离线可回滚；
- 离线兜底：无源、源不可达、包下载失败都不影响已安装能力的读写；同步失败只记账不抛到主流程；
- 不依赖 Redis/Celery：``run_due_library_syncs`` 供启动钩子/定时任务直接调用。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.net_guard import check_egress_url
from app.modules.audit.service import log_audit
from app.modules.evolution import attestation
from app.modules.evolution.models import Skill
from app.modules.evolution.service import attest_published_skill, create_skill
from app.modules.library import crypto, package
from app.modules.library.models import (
    LibraryInstall,
    LibraryItem,
    LibrarySource,
    LibrarySyncRun,
)
from app.modules.market.models import MarketItem
from app.modules.plugins.models import PluginManifest
from app.modules.plugins.service import compute_checksum

log = get_logger(__name__)

Fetcher = Callable[..., Any]

SYNC_TIMEOUT_SECONDS = 15
MAX_DOWNLOAD_BYTES = package.MAX_PACKAGE_BYTES
DEFAULT_SYNC_INTERVAL_MINUTES = 360
MAX_SOURCES_PER_RUN = 5
MAX_AUTO_UPDATE_YARDS = 20

ITEM_TYPES = ("skill", "plugin", "agent_template", "deck_theme", "doc_template")
INSTALLABLE_TYPES = ("skill", "plugin")
ITEM_STATUS_AVAILABLE = "available"
ITEM_STATUS_INSTALLED = "installed"
ITEM_STATUS_BLOCKED = "blocked"
ITEM_STATUS_REVOKED = "revoked"
SOURCE_STATUS_UNCONFIGURED = "unconfigured"
SOURCE_STATUS_OK = "ok"
SOURCE_STATUS_ERROR = "error"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ITEM_KEY_RE = re.compile(r"^official:[a-z_]{3,24}:[A-Za-z0-9_.:-]{1,120}$")


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _ts(value: datetime | None) -> float:
    aware = _aware(value)
    return aware.timestamp() if aware is not None else 0.0


def _iso(value: datetime | None) -> str | None:
    aware = _aware(value)
    return aware.isoformat() if aware is not None else None


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


# ---------------------------------------------------------------------------
# HTTP 抓取（可注入，测试全离线）
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# 序列化（绝不回传私钥/原始密钥以外的敏感信息）
# ---------------------------------------------------------------------------


def serialize_source(
    source: LibrarySource, *, can_manage: bool = False
) -> dict[str, Any]:
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


def serialize_install(
    db: Session,
    install: LibraryInstall,
    *,
    latest: LibraryItem | None = None,
) -> dict[str, Any]:
    latest = latest if latest is not None else _latest_item(db, install.item_key)
    latest_version = latest.version if latest is not None else install.installed_version
    risk = latest.risk_level if latest is not None else "low"
    update_available = bool(
        latest is not None
        and latest.status in (ITEM_STATUS_AVAILABLE, ITEM_STATUS_INSTALLED)
        and latest.version != install.installed_version
        and _version_key(latest.version) > _version_key(install.installed_version)
    )
    return {
        "id": install.id,
        "yard_id": install.yard_id,
        "source_id": install.source_id,
        "item_key": install.item_key,
        "item_type": install.item_type,
        "code": _code_from_item_key(install.item_key, install.item_type),
        "installed_version": install.installed_version,
        "previous_version": install.previous_version,
        "package_sha256": install.package_sha256,
        "skill_id": install.skill_id,
        "plugin_id": install.plugin_id,
        "auto_update": bool(install.auto_update),
        "pinned": bool(install.pinned),
        "installed_at": _iso(install.installed_at),
        "updated_at": _iso(install.updated_at),
        "latest_version": latest_version,
        "risk_level": risk,
        "update_available": update_available,
        "can_auto_update": bool(
            install.auto_update and not install.pinned and risk == "low" and update_available
        ),
        "rollback_available": bool(install.previous_version and install.item_type == "skill"),
    }


# ---------------------------------------------------------------------------
# 源管理
# ---------------------------------------------------------------------------


def _get_source(db: Session, source_id: str) -> LibrarySource:
    source = db.get(LibrarySource, source_id)
    if source is None:
        raise AppError(
            "LIBRARY_SOURCE_NOT_FOUND",
            "官方库源不存在。",
            http_status=404,
            details={"source_id": source_id},
        )
    return source


def _source_visible(source: LibrarySource, yard_id: str | None) -> bool:
    return source.yard_id is None or source.yard_id == yard_id


def _can_manage_source(source: LibrarySource, ctx: YardContext, is_platform_admin: bool) -> bool:
    if source.yard_id is None:
        return is_platform_admin
    return is_platform_admin or source.yard_id == ctx.yard_id


def _require_manage_source(
    source: LibrarySource, ctx: YardContext, is_platform_admin: bool
) -> None:
    if _can_manage_source(source, ctx, is_platform_admin):
        return
    raise AppError(
        "LIBRARY_FORBIDDEN",
        "官方源仅平台管理员可编辑；私有源仅本小院可编辑。",
        http_status=403,
        details={"source_id": source.id},
    )


def _validate_public_key(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if not text:
        return "", ""
    fingerprint = crypto.public_key_fingerprint(text)
    if not fingerprint["key_id"]:
        raise AppError(
            "LIBRARY_SOURCE_INVALID",
            "发布者公钥必须是 base64url 的 32 字节 Ed25519 公钥。",
            http_status=422,
        )
    return text, fingerprint["key_id"]


def _field(payload: Any, name: str, default: Any = None) -> Any:
    if isinstance(payload, dict):
        return payload.get(name, default)
    return getattr(payload, name, default)


def create_source(
    db: Session,
    ctx: YardContext,
    payload: Any,
    *,
    is_platform_admin: bool = False,
) -> dict[str, Any]:
    kind = str(_field(payload, "kind", "private") or "private").strip().lower()
    if kind not in ("official", "private"):
        raise AppError("LIBRARY_SOURCE_INVALID", "源类型只能是 official/private。", http_status=400)

    yard_id = _field(payload, "yard_id")
    if kind == "official":
        if not is_platform_admin:
            raise AppError(
                "LIBRARY_FORBIDDEN",
                "只有平台管理员可以创建官方源。",
                http_status=403,
            )
        yard_id = None
    else:
        if is_platform_admin:
            yard_id = str(yard_id).strip() if yard_id else None
            if not yard_id:
                raise AppError(
                    "LIBRARY_SOURCE_INVALID",
                    "平台管理员创建私有源时必须指定 yard_id。",
                    http_status=422,
                )
        else:
            yard_id = ctx.yard_id

    base_url = _require_https(
        _text(_field(payload, "base_url"), 500), field_name="base_url"
    )
    channel = str(_field(payload, "channel", "stable") or "stable").strip().lower()
    if channel not in ("stable", "beta"):
        raise AppError("LIBRARY_SOURCE_INVALID", "channel 只能是 stable/beta。", http_status=400)
    public_key, key_id = _validate_public_key(str(_field(payload, "public_key_b64", "") or ""))
    metadata = _field(payload, "metadata") or _field(payload, "metadata_json") or None
    if metadata is not None and not isinstance(metadata, dict):
        raise AppError("LIBRARY_SOURCE_INVALID", "metadata 必须是 JSON 对象。", http_status=422)

    source = LibrarySource(
        yard_id=yard_id,
        name=_text(_field(payload, "name"), 80),
        kind=kind,
        base_url=base_url,
        channel=channel,
        public_key_b64=public_key,
        # key_id 一律由公钥派生，避免客户端填写与公钥不符的指纹
        key_id=key_id or None,
        enabled=bool(_field(payload, "enabled", True)),
        sequence=0,
        status=SOURCE_STATUS_OK if public_key else SOURCE_STATUS_UNCONFIGURED,
        metadata_json=metadata,
    )
    if not source.name:
        raise AppError("LIBRARY_SOURCE_INVALID", "源名称不能为空。", http_status=422)
    db.add(source)
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="library_source_created",
        resource_type="library_source",
        resource_id=source.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"kind": kind, "yard_id": yard_id, "channel": channel},
    )
    return serialize_source(source, can_manage=True)


def update_source(
    db: Session,
    ctx: YardContext,
    source_id: str,
    payload: Any,
    *,
    is_platform_admin: bool = False,
) -> dict[str, Any]:
    source = _get_source(db, source_id)
    _require_manage_source(source, ctx, is_platform_admin)

    if _field(payload, "name") is not None:
        name = _text(_field(payload, "name"), 80)
        if not name:
            raise AppError("LIBRARY_SOURCE_INVALID", "源名称不能为空。", http_status=422)
        source.name = name
    if _field(payload, "base_url") is not None:
        source.base_url = _require_https(
            _text(_field(payload, "base_url"), 500), field_name="base_url"
        )
    if _field(payload, "channel") is not None:
        channel = str(_field(payload, "channel")).strip().lower()
        if channel not in ("stable", "beta"):
            raise AppError(
                "LIBRARY_SOURCE_INVALID", "channel 只能是 stable/beta。", http_status=400
            )
        source.channel = channel
    # PATCH 语义：空串/未传都表示「不更换公钥」，避免编辑源名称时误清空验签密钥
    raw_public_key = _field(payload, "public_key_b64")
    if raw_public_key is not None and str(raw_public_key).strip():
        public_key, key_id = _validate_public_key(str(raw_public_key))
        source.public_key_b64 = public_key
        # 指纹始终由公钥派生；清空公钥时一并清空指纹
        source.key_id = key_id or None
        if not public_key:
            source.status = SOURCE_STATUS_UNCONFIGURED
    if _field(payload, "enabled") is not None:
        source.enabled = bool(_field(payload, "enabled"))
    if _field(payload, "metadata") is not None:
        metadata = _field(payload, "metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise AppError("LIBRARY_SOURCE_INVALID", "metadata 必须是 JSON 对象。", http_status=422)
        source.metadata_json = metadata
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="library_source_updated",
        resource_type="library_source",
        resource_id=source.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"enabled": bool(source.enabled), "channel": source.channel},
    )
    return serialize_source(source, can_manage=True)


def delete_source(
    db: Session,
    ctx: YardContext,
    source_id: str,
    *,
    is_platform_admin: bool = False,
) -> dict[str, Any]:
    source = _get_source(db, source_id)
    _require_manage_source(source, ctx, is_platform_admin)
    installs = (
        db.query(LibraryInstall.id).filter(LibraryInstall.source_id == source.id).count()
    )
    if installs:
        raise AppError(
            "LIBRARY_SOURCE_IN_USE",
            "该源下仍有安装记录，请先卸载或保留源（撤销条目即可）。",
            http_status=409,
            details={"installs": installs},
        )
    db.query(LibraryItem).filter(LibraryItem.source_id == source.id).delete(
        synchronize_session=False
    )
    db.query(LibrarySyncRun).filter(LibrarySyncRun.source_id == source.id).delete(
        synchronize_session=False
    )
    db.delete(source)
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="library_source_deleted",
        resource_type="library_source",
        resource_id=source_id,
        result="success",
        request_id=ctx.request_id,
        metadata={"kind": source.kind},
    )
    return {"id": source_id, "deleted": True}


def list_sources(
    db: Session, ctx: YardContext, *, is_platform_admin: bool = False
) -> dict[str, Any]:
    query = db.query(LibrarySource)
    if not is_platform_admin:
        query = query.filter(
            (LibrarySource.yard_id.is_(None)) | (LibrarySource.yard_id == ctx.yard_id)
        )
    rows = query.order_by(LibrarySource.kind.asc(), LibrarySource.created_at.asc()).all()
    items = [
        serialize_source(row, can_manage=_can_manage_source(row, ctx, is_platform_admin))
        for row in rows
    ]
    return {"items": items, "total": len(items)}


def _get_source_for_yard(db: Session, source_id: str, yard_id: str | None) -> LibrarySource:
    source = _get_source(db, source_id)
    if not _source_visible(source, yard_id):
        raise AppError(
            "LIBRARY_SOURCE_NOT_FOUND",
            "官方库源不存在。",
            http_status=404,
            details={"source_id": source_id},
        )
    return source


# ---------------------------------------------------------------------------
# 同步
# ---------------------------------------------------------------------------


def _validate_manifest_header(manifest: dict, source: LibrarySource) -> int:
    if int(manifest.get("schema_version") or 0) != 1:
        raise AppError(
            "LIBRARY_SCHEMA_UNSUPPORTED",
            "官方库清单 schema_version 必须为 1。",
            http_status=422,
        )
    channel = str(manifest.get("channel") or "").strip().lower()
    if channel != source.channel:
        raise AppError(
            "LIBRARY_CHANNEL_MISMATCH",
            f"清单通道 {channel or '(空)'} 与源配置 {source.channel} 不一致。",
            http_status=409,
        )
    expires_at = _parse_dt(manifest.get("expires_at"), field_name="expires_at")
    if expires_at is not None and expires_at < _utcnow():
        raise AppError(
            "LIBRARY_MANIFEST_EXPIRED",
            "官方库清单已过期。",
            http_status=409,
            details={"expires_at": _iso(expires_at)},
        )
    try:
        sequence = int(manifest.get("sequence") or 0)
    except (TypeError, ValueError) as e:
        raise AppError(
            "LIBRARY_MANIFEST_INVALID", "清单 sequence 必须是整数。", http_status=422
        ) from e
    if sequence < 0:
        raise AppError("LIBRARY_MANIFEST_INVALID", "清单 sequence 不能为负。", http_status=422)
    return sequence


def _validate_item(raw: Any, sequence: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise AppError("LIBRARY_MANIFEST_INVALID", "清单 items 每项必须是对象。", http_status=422)
    item_type = str(raw.get("item_type") or raw.get("kind") or "").strip().lower()
    if item_type not in ITEM_TYPES:
        raise AppError(
            "LIBRARY_MANIFEST_INVALID",
            f"不支持的条目类型：{item_type or '(空)'}",
            http_status=422,
            details={"item_type": item_type},
        )
    code = _text(raw.get("code"), 120)
    version = _text(raw.get("version"), 50)
    name = _text(raw.get("name"), 120) or code
    if not code or not version:
        raise AppError(
            "LIBRARY_MANIFEST_INVALID",
            "条目必须包含 code 与 version。",
            http_status=422,
            details={"code": code, "version": version},
        )
    sha256 = _text(raw.get("sha256"), 64).lower()
    if not _SHA256_RE.fullmatch(sha256):
        raise AppError(
            "LIBRARY_MANIFEST_INVALID",
            "条目 sha256 必须是 64 位十六进制。",
            http_status=422,
            details={"code": code, "version": version},
        )
    download_url = _require_https(
        _text(raw.get("download_url"), 500), field_name="download_url"
    )
    try:
        size_bytes = int(raw.get("size_bytes") or 0)
    except (TypeError, ValueError) as e:
        raise AppError(
            "LIBRARY_MANIFEST_INVALID", "size_bytes 必须是整数。", http_status=422
        ) from e
    if size_bytes < 0 or size_bytes > MAX_DOWNLOAD_BYTES:
        raise AppError(
            "LIBRARY_MANIFEST_INVALID",
            "size_bytes 超出允许范围（≤50MB）。",
            http_status=422,
            details={"code": code, "size_bytes": size_bytes},
        )
    item_key = item_key_for(item_type, code)
    declared_key = _text(raw.get("item_key"), 220)
    if declared_key and declared_key != item_key:
        raise AppError(
            "LIBRARY_MANIFEST_INVALID",
            "item_key 与 item_type/code 不一致。",
            http_status=422,
            details={"item_key": declared_key, "expected": item_key},
        )
    manifest_json = raw.get("manifest")
    if manifest_json is not None and not isinstance(manifest_json, dict):
        raise AppError("LIBRARY_MANIFEST_INVALID", "条目 manifest 必须是对象。", http_status=422)
    risk_level = _text(raw.get("risk_level"), 16).lower() or "low"
    if risk_level not in ("low", "medium", "high"):
        raise AppError(
            "LIBRARY_MANIFEST_INVALID",
            "条目 risk_level 只能是 low/medium/high。",
            http_status=422,
            details={"code": code, "risk_level": risk_level},
        )
    return {
        "item_key": item_key,
        "item_type": item_type,
        "code": code,
        "name": name,
        "version": version,
        "min_app_version": _text(raw.get("min_app_version"), 20) or None,
        "max_app_version": _text(raw.get("max_app_version"), 20) or None,
        "description": str(raw.get("description") or "")[:4000],
        "category": _text(raw.get("category"), 40) or None,
        "risk_level": risk_level,
        "license": _text(raw.get("license"), 50) or None,
        "author": _text(raw.get("author"), 120) or None,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "download_url": download_url,
        "manifest_json": manifest_json,
        "sequence": sequence,
        "published_at": _parse_dt(raw.get("published_at"), field_name="published_at"),
    }


def _mirror_market_item(db: Session, item: LibraryItem, source: LibrarySource) -> MarketItem:
    market = db.query(MarketItem).filter(MarketItem.item_key == item.item_key).first()
    metadata = {
        "library_source_id": source.id,
        "library_item_id": item.id,
        "category": item.category,
        "sequence": item.sequence,
        "channel": source.channel,
    }
    if market is None:
        market = MarketItem(
            item_key=item.item_key,
            item_type=item.item_type,
            source="official",
            code=item.code,
            name=item.name,
            version=item.version,
            description=item.description,
            source_url=item.download_url,
            license=item.license,
            author=item.author,
            checksum=item.sha256,
            risk_level=item.risk_level,
            status=ITEM_STATUS_AVAILABLE,
            definition_json=None,
            manifest_json=None,
            metadata_json=metadata,
            verified=True,
            verified_at=_utcnow(),
        )
        db.add(market)
        db.flush()
        return market
    # 已被撤销/拦截的镜像保持不可用；后续清单不会自动「洗白」
    market.source = "official"
    market.item_type = item.item_type
    market.code = item.code
    market.name = item.name
    market.description = item.description
    market.source_url = item.download_url
    market.license = item.license
    market.author = item.author
    market.checksum = item.sha256
    market.risk_level = item.risk_level
    if _version_key(item.version) >= _version_key(market.version):
        market.version = item.version
    market.metadata_json = metadata
    market.verified = True
    market.verified_at = market.verified_at or _utcnow()
    if market.status in (ITEM_STATUS_REVOKED, ITEM_STATUS_BLOCKED):
        market.status = ITEM_STATUS_BLOCKED
    return market


def _upsert_library_item(
    db: Session, source: LibrarySource, data: dict[str, Any]
) -> tuple[LibraryItem, bool, bool]:
    row = (
        db.query(LibraryItem)
        .filter(
            LibraryItem.source_id == source.id,
            LibraryItem.item_key == data["item_key"],
            LibraryItem.version == data["version"],
        )
        .first()
    )
    created = row is None
    changed = False
    if row is None:
        row = LibraryItem(source_id=source.id, status=ITEM_STATUS_AVAILABLE, **data)
        db.add(row)
        db.flush()
        changed = True
    else:
        for key, value in data.items():
            if getattr(row, key) != value:
                setattr(row, key, value)
                changed = True
        # 已安装状态保留；被撤销的条目由新清单显式恢复为可用
        if row.status == ITEM_STATUS_REVOKED:
            row.status = ITEM_STATUS_AVAILABLE
            changed = True
    _mirror_market_item(db, row, source)
    return row, created, changed


def _revoked_keys(manifest: dict) -> list[str]:
    keys: list[str] = []
    for field_name in ("removed", "revoked"):
        raw = manifest.get(field_name)
        if not isinstance(raw, list):
            continue
        for entry in raw:
            if isinstance(entry, str):
                key = entry.strip()
            elif isinstance(entry, dict):
                key = _text(entry.get("item_key"), 220)
                if not key:
                    item_type = _text(entry.get("item_type") or entry.get("kind"), 24)
                    code = _text(entry.get("code"), 120)
                    key = item_key_for(item_type, code) if item_type and code else ""
            else:
                key = ""
            if key and key not in keys:
                keys.append(key)
    return keys


def _apply_revocations(db: Session, source: LibrarySource, keys: list[str]) -> int:
    removed = 0
    for key in keys:
        rows = (
            db.query(LibraryItem)
            .filter(LibraryItem.source_id == source.id, LibraryItem.item_key == key)
            .all()
        )
        if not rows:
            continue
        for row in rows:
            row.status = ITEM_STATUS_REVOKED
        market = db.query(MarketItem).filter(MarketItem.item_key == key).first()
        if market is not None:
            market.status = ITEM_STATUS_BLOCKED
        removed += 1
    return removed


def _record_run(
    db: Session,
    *,
    source_id: str,
    yard_id: str | None,
    status: str,
    started_at: datetime,
    added: int = 0,
    updated: int = 0,
    removed: int = 0,
    error: str | None = None,
) -> LibrarySyncRun:
    run = LibrarySyncRun(
        source_id=source_id,
        yard_id=yard_id,
        status=status,
        started_at=started_at,
        finished_at=_utcnow(),
        added=added,
        updated=updated,
        removed=removed,
        error=error,
    )
    db.add(run)
    db.flush()
    return run


def _fail_sync(
    db: Session, source_id: str, yard_id: str | None, started_at: datetime, error: AppError
) -> None:
    """失败落账：调用方已回滚同步 savepoint，这里只记录源错误与失败运行。"""
    db.expire_all()
    source = db.get(LibrarySource, source_id)
    if source is not None:
        source.status = SOURCE_STATUS_ERROR
        source.last_error = str(error.message)[:2000]
        source.last_checked_at = _utcnow()
    _record_run(
        db,
        source_id=source_id,
        yard_id=yard_id,
        status="error",
        started_at=started_at,
        error=str(error.message)[:2000],
    )


def sync_source(
    db: Session,
    yard_id: str | None,
    source_id: str,
    fetcher: Fetcher | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """拉取并验签清单，upsert 元数据并镜像 MarketItem；返回增量统计。

    ``fetcher(url, headers)`` 可注入以跳过网络（测试/CLI）；默认 httpx + 出网校验。
    """
    source = _get_source_for_yard(db, source_id, yard_id)
    if not source.enabled:
        raise AppError(
            "LIBRARY_SOURCE_DISABLED",
            "该官方库源已停用。",
            http_status=409,
            details={"source_id": source_id},
        )
    if not source.public_key_b64:
        raise AppError(
            "LIBRARY_SOURCE_UNCONFIGURED",
            "该源尚未配置发布者公钥，无法验签。",
            http_status=409,
            details={"source_id": source_id},
        )

    started_at = _utcnow()
    run_yard_id = source.yard_id
    url = manifest_url(source.base_url)
    headers = {"Accept": "application/json"}
    if source.etag and not force:
        headers["If-None-Match"] = source.etag

    # 同步自身的写入包在 SAVEPOINT 里：失败只回滚本次同步，不牵连调用方已提交/待提交的其它工作
    savepoint = db.begin_nested()
    try:
        response = _fetch(url, headers, fetcher)
        source.last_checked_at = _utcnow()
        if response.status_code == 304:
            source.status = SOURCE_STATUS_OK
            source.last_error = None
            source.last_sync_at = _utcnow()
            _record_run(
                db,
                source_id=source.id,
                yard_id=run_yard_id,
                status="skipped",
                started_at=started_at,
                error="not_modified",
            )
            db.flush()
            savepoint.commit()
            return {
                "status": "skipped",
                "reason": "not_modified",
                "added": 0,
                "updated": 0,
                "removed": 0,
                "skipped": 1,
                "sequence": int(source.sequence or 0),
            }
        if not 200 <= response.status_code < 300:
            raise AppError(
                "LIBRARY_SYNC_FAILED",
                f"官方库清单请求失败（HTTP {response.status_code}）。",
                http_status=502,
                details={"status_code": response.status_code},
            )
        try:
            manifest = json.loads(response.content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise AppError(
                "LIBRARY_MANIFEST_INVALID", "官方库清单不是合法 UTF-8 JSON。", http_status=422
            ) from e
        if not isinstance(manifest, dict):
            raise AppError(
                "LIBRARY_MANIFEST_INVALID", "官方库清单必须是 JSON 对象。", http_status=422
            )

        verification = crypto.verify_manifest(manifest, source.public_key_b64)
        if not verification["valid"]:
            raise AppError(
                "LIBRARY_SIGNATURE_INVALID",
                f"官方库清单签名无效（{verification['reason']}）。",
                http_status=422,
                details={"reason": verification["reason"]},
            )
        sequence = _validate_manifest_header(manifest, source)
        if sequence <= int(source.sequence or 0) and not force:
            source.status = SOURCE_STATUS_OK
            source.last_error = None
            source.etag = response.headers.get("etag") or _text(manifest.get("etag"), 200) or (
                source.etag
            )
            _record_run(
                db,
                source_id=source.id,
                yard_id=run_yard_id,
                status="skipped",
                started_at=started_at,
                error="stale_sequence",
            )
            db.flush()
            savepoint.commit()
            return {
                "status": "skipped",
                "reason": "stale_sequence",
                "added": 0,
                "updated": 0,
                "removed": 0,
                "skipped": 1,
                "sequence": int(source.sequence or 0),
            }

        raw_items = manifest.get("items")
        if not isinstance(raw_items, list):
            raise AppError(
                "LIBRARY_MANIFEST_INVALID", "清单 items 必须是数组。", http_status=422
            )
        added = 0
        updated = 0
        for raw in raw_items:
            data = _validate_item(raw, sequence)
            _, created, changed = _upsert_library_item(db, source, data)
            if created:
                added += 1
            elif changed:
                updated += 1

        removed = _apply_revocations(db, source, _revoked_keys(manifest))

        source.sequence = sequence
        source.etag = response.headers.get("etag") or _text(manifest.get("etag"), 200) or (
            source.etag
        )
        source.key_id = source.key_id or verification["key_id"]
        source.status = SOURCE_STATUS_OK
        source.last_error = None
        source.last_sync_at = _utcnow()
        _record_run(
            db,
            source_id=source.id,
            yard_id=run_yard_id,
            status="ok",
            started_at=started_at,
            added=added,
            updated=updated,
            removed=removed,
        )
        db.flush()
        savepoint.commit()
        return {
            "status": "ok",
            "added": added,
            "updated": updated,
            "removed": removed,
            "skipped": 0,
            "sequence": sequence,
            "key_id": source.key_id,
        }
    except AppError as e:
        savepoint.rollback()
        _fail_sync(db, source.id, run_yard_id, started_at, e)
        raise
    except Exception as e:
        savepoint.rollback()
        log.warning("library sync crashed (source=%s): %s", source_id, e)
        wrapped = AppError(
            "LIBRARY_SYNC_FAILED",
            "同步官方库时发生异常，已回滚本次同步。",
            http_status=500,
            details={"source_id": source_id},
        )
        _fail_sync(db, source.id, run_yard_id, started_at, wrapped)
        raise wrapped from e


# ---------------------------------------------------------------------------
# 安装 / 更新 / 回滚
# ---------------------------------------------------------------------------


def _get_install(db: Session, yard_id: str, install_id: str) -> LibraryInstall:
    install = (
        db.query(LibraryInstall)
        .filter(LibraryInstall.id == install_id, LibraryInstall.yard_id == yard_id)
        .first()
    )
    if install is None:
        raise AppError(
            "LIBRARY_INSTALL_NOT_FOUND",
            "安装记录不存在。",
            http_status=404,
            details={"install_id": install_id},
        )
    return install


def _latest_item(
    db: Session, item_key: str, *, statuses: tuple[str, ...] | None = None
) -> LibraryItem | None:
    query = db.query(LibraryItem).filter(LibraryItem.item_key == item_key)
    if statuses:
        query = query.filter(LibraryItem.status.in_(statuses))
    rows = query.all()
    if not rows:
        return None

    def sort_key(row: LibraryItem) -> tuple[Any, ...]:
        return (_version_key(row.version), int(row.sequence or 0), _ts(row.published_at))

    return max(rows, key=sort_key)


def _installed_version(db: Session, yard_id: str, item_key: str) -> str | None:
    row = (
        db.query(LibraryInstall.installed_version)
        .filter(LibraryInstall.yard_id == yard_id, LibraryInstall.item_key == item_key)
        .first()
    )
    return str(row[0]) if row else None


def _download_package(
    item: LibraryItem, source: LibrarySource, fetcher: Fetcher | None
) -> dict[str, Any]:
    _require_https(item.download_url, field_name="download_url")
    response = _fetch(item.download_url, {"Accept": "application/octet-stream"}, fetcher)
    if not 200 <= response.status_code < 300:
        raise AppError(
            "LIBRARY_DOWNLOAD_FAILED",
            f"官方包下载失败（HTTP {response.status_code}）。",
            http_status=502,
            details={"item_key": item.item_key, "version": item.version},
        )
    content = response.content
    if len(content) > MAX_DOWNLOAD_BYTES:
        raise AppError(
            "LIBRARY_PACKAGE_TOO_LARGE", "官方包超过 50MB。", http_status=413
        )
    if not content:
        raise AppError("LIBRARY_DOWNLOAD_FAILED", "官方包内容为空。", http_status=502)
    digest = hashlib.sha256(content).hexdigest()
    if not item.sha256 or digest != item.sha256.lower():
        raise AppError(
            "LIBRARY_CHECKSUM_MISMATCH",
            "下载包 SHA-256 与清单不一致，已拒绝安装。",
            http_status=422,
            details={"expected": item.sha256, "actual": digest},
        )
    loaded = package.load_package(content, source.public_key_b64)
    top = loaded["manifest"]
    if (
        loaded["kind"] != item.item_type
        or str(top.get("code")) != item.code
        or str(top.get("version")) != item.version
    ):
        raise AppError(
            "LIBRARY_PACKAGE_MISMATCH",
            "包清单的 kind/code/version 与源清单条目不一致。",
            http_status=422,
            details={"item_key": item.item_key, "version": item.version},
        )
    loaded["sha256"] = digest
    return loaded


def _install_skill_package(
    db: Session, ctx: YardContext, item: LibraryItem, loaded: dict[str, Any]
) -> dict[str, Any]:
    definition = dict(loaded["definition"])
    definition["market_item_key"] = item.item_key
    version = item.version
    skill = (
        db.query(Skill)
        .filter(
            Skill.yard_id == ctx.yard_id,
            Skill.skill_code == item.code,
            Skill.version == version,
        )
        .first()
    )
    if skill is None:
        skill = create_skill(
            db,
            ctx,
            item.code,
            version,
            definition,
            created_from="market_official",
            request_id=ctx.request_id,
            origin="official_library",
        )
    skill.status = "published"
    skill.approved_by = ctx.user_id
    # 与市场安装同口径：上架即有静态报告 + 实例签名
    attest_published_skill(db, ctx, skill, kind="install", request_id=ctx.request_id)
    db.flush()

    market = db.query(MarketItem).filter(MarketItem.item_key == item.item_key).first()
    if market is not None:
        market.definition_json = definition
        market.manifest_json = loaded["manifest"]
        market.status = ITEM_STATUS_INSTALLED
        market.version = max(market.version, item.version, key=_version_key)
    db.flush()
    return {
        "installed_as": skill.skill_code,
        "skill_id": skill.id,
        "skill_status": skill.status,
        "review_status": skill.status,
    }


def _install_plugin_package(
    db: Session, ctx: YardContext, item: LibraryItem, loaded: dict[str, Any]
) -> dict[str, Any]:
    manifest = dict(loaded["definition"])
    # 官方包以清单 code 为稳定 plugin_id，跨版本更新不换标识
    manifest["plugin_id"] = item.code
    declared = str(manifest.get("checksum") or "")
    computed = compute_checksum(manifest)
    if not declared:
        manifest["checksum"] = computed
    checksum_ok = bool(declared) and declared == computed
    record = (
        db.query(PluginManifest)
        .filter(
            PluginManifest.yard_id == ctx.yard_id,
            PluginManifest.plugin_id == item.code,
        )
        .first()
    )
    if record is None:
        record = PluginManifest(
            plugin_id=item.code,
            yard_id=ctx.yard_id,
            version=item.version,
            type=str(manifest.get("type")),
            status="pending_review",
            manifest_json=manifest,
            checksum=declared or computed,
            checksum_ok=checksum_ok,
            installed_at=_utcnow(),
        )
        db.add(record)
    else:
        record.version = item.version
        record.type = str(manifest.get("type"))
        record.manifest_json = manifest
        record.checksum = declared or computed
        record.checksum_ok = checksum_ok
        record.installed_at = _utcnow()
        # 换版必须重新过审核闸门，不继承旧版本的启用状态
        record.status = "pending_review"
    db.flush()

    market = db.query(MarketItem).filter(MarketItem.item_key == item.item_key).first()
    if market is not None:
        market.manifest_json = manifest
        market.status = ITEM_STATUS_INSTALLED
        market.version = max(market.version, item.version, key=_version_key)
    db.flush()
    return {
        "installed_as": item.code,
        "plugin_id": item.code,
        "plugin_status": record.status,
        "checksum_ok": checksum_ok,
        "review_status": record.status,
    }


def _install_loaded(
    db: Session, ctx: YardContext, item: LibraryItem, loaded: dict[str, Any]
) -> dict[str, Any]:
    if item.item_type == "skill":
        return _install_skill_package(db, ctx, item, loaded)
    if item.item_type == "plugin":
        return _install_plugin_package(db, ctx, item, loaded)
    raise AppError(
        "LIBRARY_ITEM_UNSUPPORTED",
        f"该条目类型暂不支持直接安装：{item.item_type}",
        http_status=422,
        details={"item_key": item.item_key},
    )


def install_item(
    db: Session,
    ctx: YardContext,
    library_item_id: str,
    version: str | None = None,
    fetcher: Fetcher | None = None,
) -> dict[str, Any]:
    """安装（幂等）：同 yard+item_key 已装同版本直接返回，不重复落地/签名。"""
    item = db.get(LibraryItem, library_item_id)
    if item is None:
        raise AppError(
            "LIBRARY_ITEM_NOT_FOUND",
            "官方库条目不存在。",
            http_status=404,
            details={"item_id": library_item_id},
        )
    source = _get_source_for_yard(db, item.source_id, ctx.yard_id)
    if version:
        target = (
            db.query(LibraryItem)
            .filter(
                LibraryItem.source_id == item.source_id,
                LibraryItem.item_key == item.item_key,
                LibraryItem.version == version,
            )
            .first()
        )
        if target is None:
            raise AppError(
                "LIBRARY_ITEM_NOT_FOUND",
                "该版本不存在。",
                http_status=404,
                details={"version": version},
            )
        item = target
    else:
        latest = _latest_item(
            db, item.item_key, statuses=(ITEM_STATUS_AVAILABLE, ITEM_STATUS_INSTALLED)
        )
        item = latest or item
    if item.status == ITEM_STATUS_REVOKED:
        raise AppError(
            "LIBRARY_ITEM_REVOKED",
            "该条目已被发布者撤销，不能安装。",
            http_status=409,
            details={"item_key": item.item_key, "version": item.version},
        )
    if item.status == ITEM_STATUS_BLOCKED:
        raise AppError(
            "LIBRARY_ITEM_BLOCKED",
            "该条目已被阻断，不能安装。",
            http_status=403,
            details={"item_key": item.item_key},
        )
    if item.item_type not in INSTALLABLE_TYPES:
        raise AppError(
            "LIBRARY_ITEM_UNSUPPORTED",
            f"该条目类型暂不支持直接安装：{item.item_type}",
            http_status=422,
            details={"item_key": item.item_key},
        )

    install = (
        db.query(LibraryInstall)
        .filter(
            LibraryInstall.yard_id == ctx.yard_id,
            LibraryInstall.item_key == item.item_key,
        )
        .first()
    )
    if install is not None and install.installed_version == item.version:
        return {
            "install": serialize_install(db, install),
            "idempotent": True,
            "installed_version": install.installed_version,
        }

    loaded = _download_package(item, source, fetcher)
    try:
        result = _install_loaded(db, ctx, item, loaded)
    except AppError:
        raise
    except Exception as e:
        db.rollback()
        raise AppError(
            "LIBRARY_INSTALL_FAILED",
            "官方包落地失败，已回滚本次事务。",
            http_status=500,
            details={"item_key": item.item_key, "version": item.version},
        ) from e

    now = _utcnow()
    previous_version = None
    if install is None:
        install = LibraryInstall(
            yard_id=ctx.yard_id,
            source_id=source.id,
            item_key=item.item_key,
            item_type=item.item_type,
            installed_version=item.version,
            previous_version=None,
            package_sha256=loaded["sha256"],
            skill_id=result.get("skill_id"),
            plugin_id=result.get("plugin_id"),
            auto_update=True,
            pinned=False,
            installed_at=now,
        )
        db.add(install)
    else:
        previous_version = install.installed_version
        if previous_version != item.version:
            install.previous_version = previous_version
        install.source_id = source.id
        install.item_type = item.item_type
        install.installed_version = item.version
        install.package_sha256 = loaded["sha256"]
        install.skill_id = result.get("skill_id") or install.skill_id
        install.plugin_id = result.get("plugin_id") or install.plugin_id
        install.installed_at = now
    item.status = ITEM_STATUS_INSTALLED
    db.flush()

    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="library_item_installed",
        resource_type="library_item",
        resource_id=item.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "item_key": item.item_key,
            "item_type": item.item_type,
            "version": item.version,
            "previous_version": previous_version,
            "package_sha256": loaded["sha256"],
            "installed_as": result.get("installed_as"),
        },
    )
    return {
        "install": serialize_install(db, install),
        "idempotent": False,
        "installed_version": item.version,
        "previous_version": previous_version,
        **result,
    }


def update_item(
    db: Session,
    ctx: YardContext,
    install_id: str,
    fetcher: Fetcher | None = None,
) -> dict[str, Any]:
    """同 item_key 有更新版本时安装新版本；旧版本保留在 Skill 版本表。"""
    install = _get_install(db, ctx.yard_id, install_id)
    latest = _latest_item(db, install.item_key, statuses=(ITEM_STATUS_AVAILABLE,))
    if latest is None or latest.version == install.installed_version:
        return {
            "updated": False,
            "reason": "up_to_date",
            "install": serialize_install(db, install),
        }
    if _version_key(latest.version) <= _version_key(install.installed_version):
        return {
            "updated": False,
            "reason": "up_to_date",
            "install": serialize_install(db, install),
        }
    try:
        result = install_item(db, ctx, latest.id, version=latest.version, fetcher=fetcher)
    except Exception:
        # 失败回滚本次事务：已安装版本与台账保持原样
        db.rollback()
        raise
    return {"updated": True, "reason": "updated", **result}


def rollback_item(db: Session, ctx: YardContext, install_id: str) -> dict[str, Any]:
    """离线回滚：仅用本地 Skill 版本表把上一版本重新置 published。"""
    install = _get_install(db, ctx.yard_id, install_id)
    if install.item_type != "skill":
        raise AppError(
            "LIBRARY_ROLLBACK_UNSUPPORTED",
            "插件包不在本地留存，暂不支持离线回滚；请重新安装指定版本。",
            http_status=409,
            details={"item_key": install.item_key},
        )
    if not install.previous_version:
        raise AppError(
            "LIBRARY_NO_ROLLBACK",
            "没有可回滚的上一版本。",
            http_status=409,
            details={"install_id": install.id},
        )
    code = _code_from_item_key(install.item_key, install.item_type)
    if not code:
        raise AppError("LIBRARY_INSTALL_INVALID", "安装记录中的条目键不合法。", http_status=422)
    target = (
        db.query(Skill)
        .filter(
            Skill.yard_id == ctx.yard_id,
            Skill.skill_code == code,
            Skill.version == install.previous_version,
        )
        .first()
    )
    if target is None:
        raise AppError(
            "LIBRARY_ROLLBACK_UNAVAILABLE",
            "上一版本不在本地版本表中，无法离线回滚。",
            http_status=409,
            details={"version": install.previous_version},
        )
    verification = attestation.verify_skill(target)
    if verification["reason"] not in ("ok", "unsigned"):
        raise AppError(
            "LIBRARY_ATTESTATION_INVALID",
            f"回滚目标 {target.version} 的签名无法验证，拒绝恢复。",
            http_status=409,
            details={"verification": verification},
        )
    current = (
        db.query(Skill)
        .filter(
            Skill.yard_id == ctx.yard_id,
            Skill.skill_code == code,
            Skill.version == install.installed_version,
        )
        .first()
    )
    if current is not None and current.status == "published":
        current.status = "deprecated"
    if target.status != "published":
        target.status = "published"

    rolled_back_from = install.installed_version
    rolled_back_to = install.previous_version
    install.installed_version = rolled_back_to
    install.previous_version = rolled_back_from
    install.skill_id = target.id
    previous_item = (
        db.query(LibraryItem)
        .filter(
            LibraryItem.item_key == install.item_key,
            LibraryItem.version == rolled_back_to,
        )
        .first()
    )
    if previous_item is not None and previous_item.sha256:
        # 摘要随版本一起回到上一版，避免台账显示的校验和与实际版本不符
        install.package_sha256 = previous_item.sha256
    # 回滚后暂停自动更新，避免下一次同步立刻把用户升回问题版本
    install.auto_update = False
    for row in db.query(LibraryItem).filter(
        LibraryItem.item_key == install.item_key,
        LibraryItem.version.in_((rolled_back_from, rolled_back_to)),
    ):
        row.status = (
            ITEM_STATUS_INSTALLED if row.version == rolled_back_to else ITEM_STATUS_AVAILABLE
        )
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="library_item_rolled_back",
        resource_type="library_install",
        resource_id=install.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "item_key": install.item_key,
            "from_version": rolled_back_from,
            "to_version": rolled_back_to,
            "skill_id": target.id,
            "auto_update_paused": True,
        },
    )
    serialized = serialize_install(db, install)
    return {
        "install": serialized,
        # 顶层别名：前端回滚成功提示直接读 installed_version
        "installed_version": serialized["installed_version"],
        "previous_version": serialized["previous_version"],
        "rolled_back_from": rolled_back_from,
        "rolled_back_to": rolled_back_to,
        "verification": verification,
    }


def pin_item(
    db: Session, ctx: YardContext, install_id: str, pinned: bool = True
) -> dict[str, Any]:
    install = _get_install(db, ctx.yard_id, install_id)
    install.pinned = bool(pinned)
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="library_item_pinned" if install.pinned else "library_item_unpinned",
        resource_type="library_install",
        resource_id=install.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"item_key": install.item_key, "pinned": install.pinned},
    )
    return serialize_install(db, install)


def set_auto_update(
    db: Session, ctx: YardContext, install_id: str, enabled: bool = True
) -> dict[str, Any]:
    install = _get_install(db, ctx.yard_id, install_id)
    install.auto_update = bool(enabled)
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="library_auto_update_enabled"
        if install.auto_update
        else "library_auto_update_disabled",
        resource_type="library_install",
        resource_id=install.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"item_key": install.item_key, "auto_update": install.auto_update},
    )
    return serialize_install(db, install)


# ---------------------------------------------------------------------------
# 列表 / 状态 / 自动更新 / 定时同步
# ---------------------------------------------------------------------------


def list_items(
    db: Session,
    ctx: YardContext,
    *,
    source_id: str | None = None,
    item_type: str | None = None,
    keyword: str = "",
    limit: int = 100,
) -> dict[str, Any]:
    source_query = db.query(LibrarySource)
    source_query = source_query.filter(
        (LibrarySource.yard_id.is_(None)) | (LibrarySource.yard_id == ctx.yard_id)
    )
    if source_id:
        source_query = source_query.filter(LibrarySource.id == source_id)
    source_rows = source_query.all()
    source_ids = [row.id for row in source_rows]
    if not source_ids:
        return {"items": [], "total": 0, "offline": True}
    offline = not any(
        row.enabled
        and row.public_key_b64
        and row.status == SOURCE_STATUS_OK
        and row.last_sync_at is not None
        for row in source_rows
    )
    query = db.query(LibraryItem).filter(LibraryItem.source_id.in_(source_ids))
    if item_type:
        query = query.filter(LibraryItem.item_type == item_type)
    text = (keyword or "").strip()
    if text:
        like = f"%{text.lower()}%"
        query = query.filter(
            (LibraryItem.name.ilike(like))
            | (LibraryItem.code.ilike(like))
            | (LibraryItem.description.ilike(like))
        )
    capped = max(1, min(int(limit or 100), 200))
    rows = query.all()
    rows.sort(
        key=lambda row: (
            _version_key(row.version),
            int(row.sequence or 0),
            _ts(row.published_at),
        ),
        reverse=True,
    )
    latest_by_key: dict[str, str] = {}
    for row in rows:
        if row.item_key not in latest_by_key and row.status != ITEM_STATUS_REVOKED:
            latest_by_key[row.item_key] = row.version
    installs = {
        row.item_key: row.installed_version
        for row in db.query(LibraryInstall).filter(LibraryInstall.yard_id == ctx.yard_id).all()
    }
    items = [
        serialize_item(
            row,
            installed_version=installs.get(row.item_key),
            latest_version=latest_by_key.get(row.item_key),
        )
        for row in rows[:capped]
    ]
    return {"items": items, "total": len(rows), "offline": offline}


def list_installs(db: Session, ctx: YardContext) -> dict[str, Any]:
    rows = (
        db.query(LibraryInstall)
        .filter(LibraryInstall.yard_id == ctx.yard_id)
        .order_by(LibraryInstall.updated_at.desc())
        .all()
    )
    items = [serialize_install(db, row) for row in rows]
    return {
        "items": items,
        "total": len(items),
        "updatable": sum(1 for item in items if item["update_available"]),
    }


def status(
    db: Session, ctx: YardContext, *, is_platform_admin: bool = False
) -> dict[str, Any]:
    source_rows = db.query(LibrarySource)
    if not is_platform_admin:
        source_rows = source_rows.filter(
            (LibrarySource.yard_id.is_(None)) | (LibrarySource.yard_id == ctx.yard_id)
        )
    sources = source_rows.all()
    installs = list_installs(db, ctx)
    enabled_ok = [
        row
        for row in sources
        if row.enabled
        and row.public_key_b64
        and row.status == SOURCE_STATUS_OK
        and row.last_sync_at is not None
    ]
    last_sync = max(
        (_aware(row.last_sync_at) for row in sources if row.last_sync_at is not None),
        default=None,
    )
    recent = (
        db.query(LibrarySyncRun)
        .filter(LibrarySyncRun.source_id.in_([row.id for row in sources] or [""]))
        .order_by(LibrarySyncRun.started_at.desc())
        .limit(5)
        .all()
    )
    return {
        "sources": [
            serialize_source(
                row, can_manage=_can_manage_source(row, ctx, is_platform_admin)
            )
            for row in sources
        ],
        "source_count": len(sources),
        "configured_sources": sum(1 for row in sources if row.public_key_b64),
        "last_sync_at": _iso(last_sync),
        "installed": installs["total"],
        "updatable": installs["updatable"],
        # 兼容前端既有字段名（/library/status 汇总可更新数量）
        "update_count": installs["updatable"],
        # 无可用源（未配置/不可达/停用）时前端应完全按离线口径展示
        "offline": not enabled_ok,
        "recent_syncs": [
            {
                "id": row.id,
                "source_id": row.source_id,
                "status": row.status,
                "started_at": _iso(row.started_at),
                "finished_at": _iso(row.finished_at),
                "added": row.added,
                "updated": row.updated,
                "removed": row.removed,
                "error": row.error,
            }
            for row in recent
        ],
    }


def apply_auto_updates(
    db: Session, yard_id: str, fetcher: Fetcher | None = None
) -> dict[str, Any]:
    """自动更新策略：仅 auto_update+未 pin+risk=low 才升级；中高风险只提示。"""
    updated: list[dict[str, Any]] = []
    prompted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    ctx = YardContext(
        yard_id=yard_id,
        user_id="system:library-sync",
        roles=["platform_admin"],
        request_id="library_auto_update",
    )
    install_ids = [
        row[0]
        for row in db.query(LibraryInstall.id)
        .filter(LibraryInstall.yard_id == yard_id)
        .all()
    ]
    for install_id in install_ids:
        install = db.get(LibraryInstall, install_id)
        if install is None or not install.auto_update or install.pinned:
            continue
        latest = _latest_item(db, install.item_key, statuses=(ITEM_STATUS_AVAILABLE,))
        if latest is None or _version_key(latest.version) <= _version_key(
            install.installed_version
        ):
            continue
        if latest.risk_level != "low":
            prompted.append(
                {
                    "install_id": install.id,
                    "item_key": install.item_key,
                    "installed_version": install.installed_version,
                    "latest_version": latest.version,
                    "risk_level": latest.risk_level,
                }
            )
            continue
        try:
            result = update_item(db, ctx, install.id, fetcher=fetcher)
            # 批量自动更新逐条提交：单条失败不回滚此前已成功的升级
            db.commit()
        except AppError as e:
            db.rollback()
            failed.append(
                {
                    "install_id": install.id,
                    "item_key": install.item_key,
                    "error": e.message,
                }
            )
            continue
        if result.get("updated"):
            updated.append(
                {
                    "install_id": install.id,
                    "item_key": install.item_key,
                    "version": result.get("installed_version"),
                }
            )
        else:
            skipped.append({"install_id": install.id, "reason": result.get("reason")})
    return {
        "updated": updated,
        "prompted": prompted,
        "skipped": skipped,
        "failed": failed,
    }


def _sync_interval(source: LibrarySource) -> timedelta:
    metadata = source.metadata_json if isinstance(source.metadata_json, dict) else {}
    try:
        minutes = int(metadata.get("sync_interval_minutes") or DEFAULT_SYNC_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_SYNC_INTERVAL_MINUTES
    return timedelta(minutes=max(5, minutes))


def run_due_library_syncs(
    db: Session, fetcher: Fetcher | None = None, *, limit: int = MAX_SOURCES_PER_RUN
) -> dict[str, Any]:
    """启动/定时任务入口：单次最多 5 个源，任何异常都不影响主流程。"""
    now = _utcnow()
    candidates = (
        db.query(LibrarySource)
        .filter(
            LibrarySource.enabled.is_(True),
            LibrarySource.public_key_b64 != "",
        )
        .order_by(
            LibrarySource.last_sync_at.is_(None).desc(),
            LibrarySource.last_sync_at.asc(),
        )
        .limit(50)
        .all()
    )
    due: list[tuple[str, str | None]] = []
    for row in candidates:
        last = _aware(row.last_sync_at)
        if last is not None and now - last < _sync_interval(row):
            continue
        due.append((row.id, row.yard_id))
        if len(due) >= max(1, int(limit or MAX_SOURCES_PER_RUN)):
            break

    results: list[dict[str, Any]] = []
    auto_updated: list[dict[str, Any]] = []
    for source_id, source_yard in due:
        try:
            result = sync_source(db, source_yard, source_id, fetcher)
            db.commit()
            results.append({"source_id": source_id, "yard_id": source_yard, **result})
        except Exception as e:  # 单源失败不阻断其它源
            # 失败运行由 sync_source 写在 savepoint 之外：先提交错误台账再继续
            try:
                db.commit()
            except Exception:  # pragma: no cover - 极端 DB 故障
                db.rollback()
            log.warning("library sync failed (source=%s): %s", source_id, e)
            results.append(
                {
                    "source_id": source_id,
                    "yard_id": source_yard,
                    "status": "error",
                    "error": getattr(e, "message", str(e)),
                }
            )
            continue
        # 同步后只对本源有安装记录的小院尝试低风险自动更新（官方源/私有源同一口径）
        yard_ids = [
            row[0]
            for row in db.query(LibraryInstall.yard_id)
            .filter(LibraryInstall.source_id == source_id)
            .distinct()
            .limit(MAX_AUTO_UPDATE_YARDS)
            .all()
        ]
        for yard_id in yard_ids:
            try:
                outcome = apply_auto_updates(db, yard_id, fetcher)
                db.commit()
                if outcome["updated"]:
                    auto_updated.append({"yard_id": yard_id, **outcome})
            except Exception as e:
                db.rollback()
                log.warning("library auto update failed (yard=%s): %s", yard_id, e)
    return {
        "processed": len(results),
        "results": results,
        "auto_updated": auto_updated,
    }


__all__ = [
    "FetchResponse",
    "apply_auto_updates",
    "create_source",
    "delete_source",
    "install_item",
    "item_key_for",
    "list_installs",
    "list_items",
    "list_sources",
    "manifest_url",
    "pin_item",
    "rollback_item",
    "run_due_library_syncs",
    "serialize_install",
    "serialize_item",
    "serialize_source",
    "set_auto_update",
    "status",
    "sync_source",
    "update_item",
    "update_source",
]
