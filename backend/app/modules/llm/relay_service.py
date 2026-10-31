"""API 中转站通道服务（v3.33）。

一个中转站（``relay_channels``）暴露一份模型目录，平台按目录同步出一个
``provider_type=official_relay`` 的供应商及其 ``supplier_models``：

- 通道分 official（平台级，官方通道开关）与 private（小院自建中转）；
- 目录 GET ``{base_url}{catalog_path}``，Bearer 用解密后的密钥，**密钥绝不出现在
  任何返回/审计/日志**；
- 兼容 OpenAI ``{"data": [{"id": ...}]}`` 与扩展目录（modality/protocol/
  capabilities/context_window/pricing/display_name）；
- 配置了 ``public_key`` 时对目录做 Ed25519 验签（可选能力，失败整次拒绝）；
- 目录里消失的模型：被绑定引用的置 ``status=2``（不物理删除），未引用的删除，
  两种情况都在 ``removed`` 里如实列出；
- 通道改名/换地址/换密钥后下一次 sync 即生效，无需重启。
"""

import base64
import json
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.llm import supplier_service
from app.modules.llm.supplier_crypto import (
    decrypt_api_key,
    encrypt_api_key,
    mask_api_key,
)
from app.modules.llm.supplier_models import (
    CHANNEL_KIND_RELAY,
    PROVIDER_TYPE_OFFICIAL_RELAY,
    RelayChannel,
    Supplier,
    SupplierModel,
)
from app.modules.llm.supplier_schemas import (
    RelayChannelCreate,
    RelayChannelOut,
    RelayChannelUpdate,
    RelaySyncResult,
    SupplierImportBundle,
    SupplierImportPreview,
    SupplierImportResult,
    SupplierModelCreate,
)

log = get_logger(__name__)

CHANNEL_KIND_PRIVATE = "private"
KIND_OFFICIAL = "official"
KIND_PRIVATE = "private"

CATALOG_TIMEOUT_SECONDS = 15
CATALOG_MAX_BYTES = 2 * 1024 * 1024
CATALOG_MAX_ITEMS = 1000

#: 测试注入口：``(url, headers) -> httpx.Response``，生产恒为 None（绝不真实出网）
_fetcher_override: Callable[[str, dict[str, str]], Awaitable[httpx.Response]] | None = None


def set_test_fetcher(
    fetcher: Callable[[str, dict[str, str]], Awaitable[httpx.Response]] | None,
) -> None:
    """测试专用：注入目录拉取器（单点注入，避免单元测试打公网）。"""
    global _fetcher_override
    _fetcher_override = fetcher


# ---------- 查询与 CRUD ----------


def _validated_base_url(raw: str) -> str:
    url = (raw or "").strip().rstrip("/")
    if not url:
        raise AppError("INVALID_REQUEST", "中转站地址不能为空。", http_status=400)
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AppError(
            "INVALID_REQUEST", "中转站地址必须是 http(s) 形式的完整 URL。", http_status=400
        )
    return url


def _visible_query(db: Session, yard_id: str | None):
    if yard_id:
        return db.query(RelayChannel).filter(
            or_(RelayChannel.yard_id.is_(None), RelayChannel.yard_id == yard_id)
        )
    return db.query(RelayChannel).filter(RelayChannel.yard_id.is_(None))


def get_channel(db: Session, yard_id: str | None, channel_id: str) -> RelayChannel:
    row = _visible_query(db, yard_id).filter(RelayChannel.id == channel_id).first()
    if row is None:
        raise AppError("RELAY_CHANNEL_NOT_FOUND", "中转站通道不存在。", http_status=404)
    return row


def _writable_channel(
    db: Session, yard_id: str | None, channel_id: str, *, is_platform_admin: bool
) -> RelayChannel:
    row = get_channel(db, yard_id, channel_id)
    if not is_platform_admin and row.yard_id is None:
        raise AppError("RELAY_CHANNEL_NOT_FOUND", "中转站通道不存在。", http_status=404)
    return row


def channel_to_out(row: RelayChannel, *, is_platform_admin: bool = False) -> RelayChannelOut:
    masked = ""
    if row.api_key_enc:
        masked = mask_api_key(decrypt_api_key(row.api_key_enc))
    return RelayChannelOut(
        id=row.id,
        yard_id=row.yard_id,
        name=row.name,
        kind=row.kind or KIND_PRIVATE,
        base_url=row.base_url,
        api_key_masked=masked,
        public_key=row.public_key,
        has_webhook_secret=bool(row.webhook_secret_enc),
        catalog_path=row.catalog_path or "/models",
        enabled=bool(row.enabled),
        status=row.status or "unconfigured",
        last_sync_at=row.last_sync_at.isoformat() if row.last_sync_at else None,
        last_error=row.last_error,
        readonly=(not is_platform_admin) and row.yard_id is None,
        created_at=row.created_at.isoformat(),
        updated_at=row.updated_at.isoformat(),
    )


def list_channels(
    db: Session, yard_id: str | None, *, is_platform_admin: bool = False
) -> list[RelayChannelOut]:
    rows = (
        _visible_query(db, yard_id)
        .order_by(RelayChannel.created_at.asc())
        .all()
    )
    return [channel_to_out(row, is_platform_admin=is_platform_admin) for row in rows]


def create_channel(
    db: Session,
    yard_id: str | None,
    payload: RelayChannelCreate,
    *,
    is_platform_admin: bool,
) -> RelayChannelOut:
    if payload.scope == "global":
        if not is_platform_admin:
            raise AppError("FORBIDDEN", "只有平台管理员可以创建全局中转站通道。", http_status=403)
        scope_yard_id: str | None = None
    else:
        if not yard_id:
            raise AppError("YARD_REQUIRED", "创建小院中转站通道需要小院上下文。", 400)
        scope_yard_id = yard_id
    if payload.kind == KIND_OFFICIAL and not is_platform_admin:
        raise AppError("FORBIDDEN", "官方模型通道只能由平台管理员配置。", http_status=403)
    row = RelayChannel(
        yard_id=scope_yard_id,
        name=payload.name.strip(),
        kind=payload.kind,
        base_url=_validated_base_url(payload.base_url),
        api_key_enc=encrypt_api_key(payload.api_key) if payload.api_key else None,
        public_key=(payload.public_key or "").strip() or None,
        webhook_secret_enc=(
            encrypt_api_key(payload.webhook_secret) if payload.webhook_secret else None
        ),
        catalog_path=(payload.catalog_path or "/models").strip() or "/models",
        enabled=payload.enabled,
        status="unconfigured",
    )
    db.add(row)
    db.flush()
    return channel_to_out(row, is_platform_admin=is_platform_admin)


def update_channel(
    db: Session,
    yard_id: str | None,
    channel_id: str,
    payload: RelayChannelUpdate,
    *,
    is_platform_admin: bool,
) -> RelayChannelOut:
    row = _writable_channel(db, yard_id, channel_id, is_platform_admin=is_platform_admin)
    data = payload.model_dump(exclude_unset=True)
    api_key = data.pop("api_key", None)
    webhook_secret = data.pop("webhook_secret", None)
    for field in ("name", "base_url", "catalog_path"):
        if field in data and data[field] is not None:
            value = str(data[field]).strip()
            if field == "base_url":
                value = _validated_base_url(value)
            if field == "catalog_path" and not value:
                value = "/models"
            setattr(row, field, value)
    if data.get("kind") is not None:
        if data["kind"] == KIND_OFFICIAL and not is_platform_admin:
            raise AppError("FORBIDDEN", "官方模型通道只能由平台管理员配置。", http_status=403)
        row.kind = data["kind"]
    if "public_key" in data:
        row.public_key = (data["public_key"] or "").strip() or None
    if "enabled" in data and data["enabled"] is not None:
        row.enabled = bool(data["enabled"])
    if api_key:
        row.api_key_enc = encrypt_api_key(api_key)  # 换密钥：下一次 sync 生效
        row.status = "unconfigured"
        row.last_error = None
    if webhook_secret:
        row.webhook_secret_enc = encrypt_api_key(webhook_secret)
    db.flush()
    # 连接信息（地址/密钥）在下一次 sync 时写回供应商；此处只保证缓存不串旧模型
    supplier_service.invalidate_supplier_cache()
    return channel_to_out(row, is_platform_admin=is_platform_admin)


def delete_channel(
    db: Session, yard_id: str | None, channel_id: str, *, is_platform_admin: bool
) -> None:
    row = _writable_channel(db, yard_id, channel_id, is_platform_admin=is_platform_admin)
    # 关联供应商不删（可能已被绑定引用），只置禁用并解除通道关联
    for supplier in (
        db.query(Supplier)
        .filter(Supplier.relay_channel_id == row.id, Supplier.channel_kind == CHANNEL_KIND_RELAY)
        .all()
    ):
        supplier.status = 2
        supplier.relay_channel_id = None
    db.delete(row)
    db.flush()
    supplier_service.invalidate_supplier_cache()


def get_official_channel(db: Session, yard_id: str | None = None) -> RelayChannel | None:
    """当前启用的官方通道（小院级优先，其次平台级）。没有则返回 None（默认关闭）。"""
    query = db.query(RelayChannel).filter(
        RelayChannel.kind == KIND_OFFICIAL, RelayChannel.enabled.is_(True)
    )
    if yard_id:
        row = query.filter(RelayChannel.yard_id == yard_id).first()
        if row is not None:
            return row
    return query.filter(RelayChannel.yard_id.is_(None)).first()


def official_enabled(db: Session, yard_id: str | None = None) -> bool:
    return get_official_channel(db, yard_id) is not None


# ---------- 目录拉取与验签 ----------


def _load_public_key(raw: str):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    text = (raw or "").strip()
    if text.startswith("-----BEGIN"):
        return serialization.load_pem_public_key(text.encode("utf-8"))
    try:
        return ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(text))
    except Exception as e:  # noqa: BLE001 - 配置错误统一转中文
        raise AppError(
            "RELAY_PUBLIC_KEY_INVALID",
            "中转站验签公钥不合法（需 PEM 或 base64 的 Ed25519 公钥）。",
            http_status=400,
        ) from e


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    """签名口径：去掉 signature 字段后的稳定 JSON（sort_keys + 紧凑分隔符）。"""
    clone = {
        key: value
        for key, value in payload.items()
        if key not in ("signature", "signature_alg")
    }
    return json.dumps(clone, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _verify_catalog_signature(
    payload: dict[str, Any], signature: str, public_key: str
) -> bool:
    try:
        key = _load_public_key(public_key)
        key.verify(base64.b64decode(signature), _canonical_payload(payload))
        return True
    except AppError:
        raise
    except Exception:  # noqa: BLE001 - 任何验签异常都按失败处理
        return False


async def _fetch_catalog(
    channel: RelayChannel, api_key: str | None
) -> tuple[dict[str, Any], dict[str, str]]:
    """拉目录：注入 fetcher 优先；否则真实 httpx（SSRF 校验 + 2MB 上限）。"""
    url = f"{(channel.base_url or '').rstrip('/')}{(channel.catalog_path or '/models')}"
    headers = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if _fetcher_override is not None:
        resp = await _fetcher_override(url, headers)
    else:
        from app.core.net_guard import check_egress_url

        check_egress_url(url, context="relay_catalog", allow_local=True)
        try:
            async with httpx.AsyncClient(timeout=CATALOG_TIMEOUT_SECONDS) as client:
                resp = await client.get(url, headers=headers)
        except httpx.TimeoutException as e:
            raise AppError("RELAY_UNREACHABLE", "中转站目录请求超时。", 502) from e
        except httpx.HTTPError as e:
            raise AppError("RELAY_UNREACHABLE", "无法连接中转站地址。", 502) from e
    if resp.status_code in (401, 403):
        raise AppError("RELAY_AUTH_FAILED", "中转站鉴权失败：请检查通道 API Key。", 502)
    if resp.status_code >= 400:
        raise AppError(
            "RELAY_CATALOG_ERROR", f"中转站目录返回 {resp.status_code}。", 502
        )
    body = resp.content
    if len(body) > CATALOG_MAX_BYTES:
        raise AppError("RELAY_CATALOG_TOO_LARGE", "中转站目录超过 2MB 上限。", 502)
    try:
        payload = resp.json()
    except ValueError as e:
        raise AppError("RELAY_CATALOG_INVALID", "中转站目录不是合法 JSON。", 502) from e
    if not isinstance(payload, dict):
        raise AppError("RELAY_CATALOG_INVALID", "中转站目录结构不符合约定。", 502)
    return payload, headers


def _catalog_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("data")
    if not isinstance(items, list):
        raise AppError("RELAY_CATALOG_INVALID", "中转站目录缺少 data 数组。", 502)
    entries: list[dict[str, Any]] = []
    for item in items[:CATALOG_MAX_ITEMS]:
        if isinstance(item, str) and item:
            entries.append({"id": item})
        elif isinstance(item, dict) and item.get("id"):
            entries.append(item)
    if not entries:
        raise AppError("RELAY_CATALOG_EMPTY", "中转站目录没有可用模型。", 502)
    return entries


#: 目录里允许的模态/协议（未知值按关键字推断，避免整条目录被丢弃）
_ALLOWED_MODALITIES = {
    "text",
    "image",
    "video",
    "audio",
    "multimodal",
    "embedding",
    "rerank",
    "asr",
    "tts",
}
_ALLOWED_PROTOCOLS = {
    "openai_chat",
    "openai_images",
    "dashscope_image",
    "openai_video",
    "anthropic",
    "gemini",
    "openai_audio",
    "openai_embedding",
    "custom",
}


def catalog_entry_to_model(entry: dict[str, Any]) -> SupplierModelCreate:
    """目录条目 → 模型契约（未知字段忽略，模态/协议缺省按关键字推断）。"""
    raw: dict[str, Any] = {
        "model_key": str(entry.get("model_key") or entry["id"])[:100],
        "upstream_model_id": str(entry.get("upstream_model_id") or entry["id"])[:200],
        "display_name": str(entry.get("display_name") or entry.get("name") or entry["id"])[
            :120
        ],
    }
    upstream = raw["upstream_model_id"]
    modality = str(entry.get("modality") or "").strip().lower()
    if modality not in _ALLOWED_MODALITIES:
        modality = supplier_service.guess_modality(upstream)
    raw["modality"] = modality
    protocol = str(entry.get("protocol") or "").strip().lower()
    if protocol not in _ALLOWED_PROTOCOLS:
        protocol = supplier_service.protocol_for_modality(modality)
    raw["protocol"] = protocol
    if isinstance(entry.get("capabilities"), dict):
        raw["capabilities"] = entry["capabilities"]
    for field in ("context_window", "max_output_tokens"):
        if entry.get(field) not in (None, ""):
            try:
                raw[field] = int(entry[field])
            except (TypeError, ValueError):
                continue
    if isinstance(entry.get("pricing"), dict):
        raw["pricing"] = entry["pricing"]
    if entry.get("endpoint_path"):
        raw["endpoint_path"] = str(entry["endpoint_path"])[:200]
    raw["source"] = "relay"
    return SupplierModelCreate(**raw)


def _ensure_relay_supplier(db: Session, channel: RelayChannel) -> Supplier:
    """为通道创建/复用供应商：同一通道只对应一个 official_relay 供应商。"""
    supplier = (
        db.query(Supplier).filter(Supplier.relay_channel_id == channel.id).first()
    )
    if supplier is None:
        supplier = Supplier(
            yard_id=channel.yard_id,
            name=(channel.name or "relay")[:40],
            provider_type=PROVIDER_TYPE_OFFICIAL_RELAY,
            base_url=channel.base_url,
            model_id="placeholder",
            api_key_enc=channel.api_key_enc or "",
            models=None,
            priority=0,
            status=1,
            channel_kind=CHANNEL_KIND_RELAY,
            relay_channel_id=channel.id,
        )
        db.add(supplier)
        db.flush()
    else:
        supplier.name = (channel.name or supplier.name)[:40]
        supplier.base_url = channel.base_url
        supplier.api_key_enc = channel.api_key_enc or supplier.api_key_enc
        supplier.channel_kind = CHANNEL_KIND_RELAY
        supplier.status = 1
    return supplier


async def sync_relay_catalog(
    db: Session,
    yard_id: str | None,
    channel_id: str,
    *,
    is_platform_admin: bool = False,
) -> RelaySyncResult:
    """拉取目录并 upsert 到通道对应的供应商；返回 created/updated/removed/items。"""
    channel = _writable_channel(db, yard_id, channel_id, is_platform_admin=is_platform_admin)
    if not channel.enabled:
        raise AppError("RELAY_CHANNEL_DISABLED", "该中转站通道已停用。", 400)
    if not channel.api_key_enc:
        raise AppError(
            "RELAY_KEY_MISSING", "中转站未配置 API Key，无法同步模型目录。", 400
        )
    started = time.perf_counter()
    api_key = decrypt_api_key(channel.api_key_enc)
    try:
        payload, headers = await _fetch_catalog(channel, api_key)
    except AppError as e:
        channel.status = "error"
        channel.last_error = e.message
        db.flush()
        raise
    finally:
        del api_key

    signature = str(payload.get("signature") or headers.get("x-catalog-signature") or "").strip()
    if signature and channel.public_key:
        if not _verify_catalog_signature(payload, signature, channel.public_key):
            channel.status = "error"
            channel.last_error = "目录验签失败，已拒绝本次同步。"
            db.flush()
            raise AppError(
                "RELAY_SIGNATURE_INVALID",
                "中转站目录验签失败，已拒绝本次同步。",
                http_status=502,
            )

    entries = _catalog_entries(payload)
    supplier = _ensure_relay_supplier(db, channel)
    models: list[SupplierModelCreate] = []
    for entry in entries:
        try:
            models.append(catalog_entry_to_model(entry))
        except Exception as e:  # noqa: BLE001 - 单条非法目录不拖垮整次同步
            log.warning("relay catalog entry skipped: %s", e)
    if not models:
        # 目录整体不可用：宁可报错，也不能当成「模型全被删除」清空本地目录
        raise AppError("RELAY_CATALOG_INVALID", "中转站目录没有可用模型，已中止同步。", 502)

    bundle = SupplierImportBundle(models=models)
    preview = supplier_service.import_models(
        db,
        yard_id,
        supplier.id,
        bundle,
        dry_run=True,
        is_platform_admin=True,  # 系统同步：通道归属已在上方校验
        source="relay",
    )
    result = supplier_service.import_models(
        db,
        yard_id,
        supplier.id,
        bundle,
        dry_run=False,
        is_platform_admin=True,
        source="relay",
    )
    # 目录里消失的模型：被引用则停用（不物理删除），否则删除
    from app.modules.llm.binding_models import LLMBinding

    catalog_keys = {m.model_key for m in models} | {m.upstream_model_id for m in models}
    removed_items: list[dict[str, Any]] = []
    for row in (
        db.query(SupplierModel)
        .filter(SupplierModel.supplier_id == supplier.id, SupplierModel.source == "relay")
        .all()
    ):
        if row.model_key in catalog_keys or row.upstream_model_id in catalog_keys:
            continue
        referenced = (
            db.query(LLMBinding)
            .filter(
                or_(
                    LLMBinding.supplier_model_id == row.id,
                    LLMBinding.model_name.in_([row.model_key, row.upstream_model_id]),
                )
            )
            .count()
        )
        if referenced:
            row.status = 2
            mode = "disabled"
        else:
            db.delete(row)
            mode = "deleted"
        removed_items.append(
            {"action": "removed", "model_key": row.model_key, "mode": mode}
        )
    db.flush()
    supplier_service.sync_supplier_model_defaults(db, supplier)
    supplier_service.invalidate_supplier_cache(supplier_id=supplier.id)

    channel.status = "ok"
    channel.last_error = None
    channel.last_sync_at = datetime.now(UTC)
    db.flush()
    latency_ms = int((time.perf_counter() - started) * 1000)
    created = result.created if isinstance(result, SupplierImportResult) else 0
    updated = result.updated if isinstance(result, SupplierImportResult) else 0
    items: list[dict[str, Any]] = []
    if isinstance(preview, SupplierImportPreview):
        items.extend(
            {"action": item.action, "model_key": item.model_key}
            for item in preview.items
        )
    items.extend(removed_items)
    return RelaySyncResult(
        supplier_id=supplier.id,
        created=created,
        updated=updated,
        removed=len(removed_items),
        items=items,
        source="relay",
        message=(
            f"同步完成：新增 {created}、更新 {updated}、"
            f"移除 {len(removed_items)}，耗时 {latency_ms}ms。"
        ),
    )


def list_channel_models(
    db: Session, yard_id: str | None, channel_id: str
) -> list[Any]:
    channel = get_channel(db, yard_id, channel_id)
    supplier = db.query(Supplier).filter(Supplier.relay_channel_id == channel.id).first()
    if supplier is None:
        return []
    return supplier_service.list_supplier_models(db, yard_id, supplier.id)


__all__ = [
    "CHANNEL_KIND_PRIVATE",
    "KIND_OFFICIAL",
    "KIND_PRIVATE",
    "catalog_entry_to_model",
    "channel_to_out",
    "create_channel",
    "delete_channel",
    "get_channel",
    "get_official_channel",
    "list_channel_models",
    "list_channels",
    "official_enabled",
    "set_test_fetcher",
    "sync_relay_catalog",
    "update_channel",
]
