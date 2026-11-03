"""插件权限授权（consent）服务。

口径：
- 授权对象是「权限集合指纹」，权限没变则升级不重复确认，权限变化必须重新确认；
- 未授权的高风险插件不能安装；本机能力插件在没有授权时一律 409；
- 撤销保留历史行（只写 revoked_at），授权与撤销都写审计。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.library import manifest_v2
from app.modules.library.models import LibraryItem, PluginConsent

ACK_VERSION = "v1"
ACK_TEXT = (
    "我已阅读该插件声明的平台、权限与风险，并同意在当前设备上为本小院授权使用。"
    "该插件产生的全部操作与后果由使用者自行承担，平台不承担由此产生的任何责任。"
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def serialize(row: PluginConsent) -> dict[str, Any]:
    return {
        "id": row.id,
        "item_key": row.item_key,
        "plugin_id": row.plugin_id,
        "plugin_version": row.plugin_version,
        "scope_hash": row.scope_hash,
        "permissions": list(row.permissions_json or []),
        "permission_details": manifest_v2.permission_details(list(row.permissions_json or [])),
        "risk_level": row.risk_level,
        "ack_version": row.ack_version,
        "accepted_by": row.accepted_by,
        "accepted_by_name": row.accepted_by_name,
        "accepted_at": _iso(row.accepted_at),
        "revoked_at": _iso(row.revoked_at),
        "revoked_by": row.revoked_by,
        "active": row.revoked_at is None,
    }


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.isoformat()


def active_consent(
    db: Session, yard_id: str, item_key: str, scope_hash: str
) -> PluginConsent | None:
    return (
        db.query(PluginConsent)
        .filter(
            PluginConsent.yard_id == yard_id,
            PluginConsent.item_key == item_key,
            PluginConsent.scope_hash == scope_hash,
            PluginConsent.revoked_at.is_(None),
        )
        .order_by(PluginConsent.accepted_at.desc())
        .first()
    )


def list_consents(db: Session, ctx: YardContext) -> dict[str, Any]:
    rows = (
        db.query(PluginConsent)
        .filter(PluginConsent.yard_id == ctx.yard_id)
        .order_by(PluginConsent.accepted_at.desc())
        .all()
    )
    latest: dict[str, PluginConsent] = {}
    for row in rows:
        if row.item_key not in latest:
            latest[row.item_key] = row
    return {
        "items": [serialize(row) for row in latest.values()],
        "total": len(latest),
        "active": sum(1 for row in latest.values() if row.revoked_at is None),
        "ack_text": ACK_TEXT,
        "ack_version": ACK_VERSION,
    }


def record_consent(
    db: Session,
    ctx: YardContext,
    *,
    item: LibraryItem,
    manifest: dict[str, Any],
    accepted_by_name: str | None = None,
) -> dict[str, Any]:
    permissions = list(manifest.get("permissions") or [])
    scope = str(manifest.get("scope_hash") or manifest_v2.scope_hash(permissions))
    existing = active_consent(db, ctx.yard_id, item.item_key, scope)
    if existing is not None:
        return {"consent": serialize(existing), "idempotent": True}
    # 同一插件换了权限范围：旧授权立即失效，避免「旧同意覆盖新权限」
    revoked = (
        db.query(PluginConsent)
        .filter(
            PluginConsent.yard_id == ctx.yard_id,
            PluginConsent.item_key == item.item_key,
            PluginConsent.revoked_at.is_(None),
        )
        .all()
    )
    now = _utcnow()
    for row in revoked:
        row.revoked_at = now
        row.revoked_by = ctx.user_id
    row = PluginConsent(
        yard_id=ctx.yard_id,
        item_key=item.item_key,
        plugin_id=item.code,
        plugin_version=item.version,
        scope_hash=scope,
        permissions_json=permissions,
        risk_level=str(manifest.get("risk_level") or "low"),
        ack_version=ACK_VERSION,
        accepted_by=ctx.user_id,
        accepted_by_name=accepted_by_name,
        accepted_at=now,
    )
    db.add(row)
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="plugin_consent_granted",
        resource_type="library_item",
        resource_id=item.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "item_key": item.item_key,
            "version": item.version,
            "scope_hash": scope,
            "permissions": permissions,
            "risk_level": row.risk_level,
        },
    )
    return {"consent": serialize(row), "idempotent": False}


def revoke_consent(db: Session, ctx: YardContext, consent_id: str) -> dict[str, Any]:
    row = db.get(PluginConsent, consent_id)
    if row is None or row.yard_id != ctx.yard_id:
        raise AppError("PLUGIN_CONSENT_NOT_FOUND", "授权记录不存在。", http_status=404)
    if row.revoked_at is None:
        row.revoked_at = _utcnow()
        row.revoked_by = ctx.user_id
        db.flush()
        log_audit(
            db,
            yard_id=ctx.yard_id,
            actor_id=ctx.user_id,
            action="plugin_consent_revoked",
            resource_type="library_item",
            resource_id=row.item_key,
            result="success",
            request_id=ctx.request_id,
            metadata={"consent_id": row.id, "scope_hash": row.scope_hash},
        )
    return {"consent": serialize(row)}


def assert_consent(
    db: Session,
    ctx: YardContext,
    *,
    item: LibraryItem,
    manifest: dict[str, Any],
) -> PluginConsent | None:
    if not manifest.get("consent_required"):
        return None
    scope = str(manifest.get("scope_hash") or "")
    row = active_consent(db, ctx.yard_id, item.item_key, scope)
    if row is None:
        raise AppError(
            "PLUGIN_CONSENT_REQUIRED",
            "该插件需要先确认权限授权后才能安装。",
            http_status=409,
            details={
                "item_key": item.item_key,
                "version": item.version,
                "scope_hash": scope,
                "risk_level": manifest.get("risk_level"),
                "permissions": manifest.get("permission_details") or [],
                "ack_text": ACK_TEXT,
            },
        )
    return row


def manifest_view(item: LibraryItem, loaded: dict[str, Any]) -> dict[str, Any]:
    """把包清单 + plugin.json 合并成 v2 视图（权限优先取 definition）。"""
    definition = loaded.get("definition") or {}
    top = loaded.get("manifest") or {}
    raw = dict(definition)
    raw.setdefault("permissions", top.get("permissions") or [])
    raw.setdefault("platforms", top.get("platforms"))
    raw.setdefault("artifacts", top.get("artifacts"))
    raw.setdefault("schema_version", top.get("schema_version") or 1)
    return manifest_v2.normalize_manifest(raw, item_type=item.item_type)


def requirement(
    db: Session,
    ctx: YardContext,
    *,
    item: LibraryItem,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    permissions = list(manifest.get("permissions") or [])
    scope = str(manifest.get("scope_hash") or manifest_v2.scope_hash(permissions))
    granted = active_consent(db, ctx.yard_id, item.item_key, scope)
    return {
        "item_key": item.item_key,
        "version": item.version,
        "required": bool(manifest.get("consent_required")),
        "scope_hash": scope,
        "permissions": manifest.get("permission_details") or [],
        "platforms": manifest.get("platforms") or list(manifest_v2.PLATFORMS),
        "risk_level": manifest.get("risk_level") or "low",
        "granted": granted is not None,
        "consent": serialize(granted) if granted is not None else None,
        "ack_text": ACK_TEXT,
        "ack_version": ACK_VERSION,
    }
