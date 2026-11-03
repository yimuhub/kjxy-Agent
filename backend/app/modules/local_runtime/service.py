"""本机能力通道服务：插件 → 权限 → 授权 → 桥执行。

口径：
- 只允许已启用的 ``local_runtime`` / ``workspace`` 插件调用；
- 动作所需权限必须在插件清单里声明；未声明直接 403；
- 授权必须覆盖插件当前权限集合（按 scope_hash），否则 409；
- 当前系统没有对应桥（Windows/Linux 未实现）时 409 ``RUNTIME_PLATFORM_UNSUPPORTED``；
- 每次动作都写审计（只记动作、插件、耗时与结局，不记屏幕/剪贴板内容）；
- 进程内按小院限流（默认 60 次/分钟），多实例部署需换集中计数。
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.library import manifest_v2
from app.modules.library.models import PluginConsent
from app.modules.local_runtime import actions, bridge
from app.modules.plugins.models import PluginManifest

SUPPORTED_TYPES = ("local_runtime", "workspace")
ACTION_LIMIT_PER_MINUTE = 60


class _MinuteLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = {}

    def check(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            bucket = self._events.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                return False
            bucket.append(now)
            return True


_limiter = _MinuteLimiter(ACTION_LIMIT_PER_MINUTE)


def _plugin_row(db: Session, yard_id: str, plugin_id: str) -> PluginManifest:
    row = (
        db.query(PluginManifest)
        .filter(
            PluginManifest.plugin_id == plugin_id,
            PluginManifest.yard_id == yard_id,
            PluginManifest.type.in_(SUPPORTED_TYPES),
        )
        .first()
    )
    if row is None:
        row = (
            db.query(PluginManifest)
            .filter(
                PluginManifest.plugin_id == plugin_id,
                PluginManifest.yard_id.is_(None),
                PluginManifest.type.in_(SUPPORTED_TYPES),
            )
            .first()
        )
    if row is None:
        raise AppError(
            "RUNTIME_PLUGIN_NOT_FOUND",
            "本机能力插件不存在或未安装。",
            http_status=404,
            details={"plugin_id": plugin_id},
        )
    return row


def _permissions(manifest: dict[str, Any]) -> list[str]:
    raw = manifest.get("permissions") or []
    if not isinstance(raw, list):
        return []
    return manifest_v2.normalize([str(item) for item in raw])


def _active_consent(
    db: Session, yard_id: str, plugin_id: str, scope_hash: str
) -> PluginConsent | None:
    return (
        db.query(PluginConsent)
        .filter(
            PluginConsent.yard_id == yard_id,
            PluginConsent.plugin_id == plugin_id,
            PluginConsent.scope_hash == scope_hash,
            PluginConsent.revoked_at.is_(None),
        )
        .order_by(PluginConsent.accepted_at.desc())
        .first()
    )


def _serialize_plugin(
    db: Session,
    ctx: YardContext,
    row: PluginManifest,
    *,
    platform: str,
) -> dict[str, Any]:
    manifest = row.manifest_json or {}
    permissions = _permissions(manifest)
    scope_hash = manifest_v2.scope_hash(permissions)
    consent = _active_consent(db, ctx.yard_id, row.plugin_id, scope_hash)
    platforms = manifest.get("platforms")
    if not isinstance(platforms, list) or not platforms:
        platforms = list(manifest_v2.PLATFORMS)
    available = {
        action: spec
        for action, spec in actions.ACTION_CATALOG.items()
        if spec["permission"] in permissions
    }
    return {
        "plugin_id": row.plugin_id,
        "name": str(manifest.get("name") or row.plugin_id),
        "version": row.version,
        "type": row.type,
        "status": row.status,
        "platforms": platforms,
        "platform_supported": platform in platforms,
        "permissions": permissions,
        "scope_hash": scope_hash,
        "consent_granted": consent is not None,
        "consent": {
            "id": consent.id,
            "accepted_at": consent.accepted_at.isoformat() if consent.accepted_at else None,
            "risk_level": consent.risk_level,
        }
        if consent is not None
        else None,
        "actions": [
            {
                "code": code,
                "label": spec["label"],
                "risk": spec["risk"],
                "description": spec["description"],
                "permission": spec["permission"],
            }
            for code, spec in available.items()
        ],
    }


def status(db: Session, ctx: YardContext) -> dict[str, Any]:
    platform = bridge.platform_name()
    rows = (
        db.query(PluginManifest)
        .filter(
            PluginManifest.type.in_(SUPPORTED_TYPES),
            (PluginManifest.yard_id.is_(None)) | (PluginManifest.yard_id == ctx.yard_id),
        )
        .order_by(PluginManifest.plugin_id)
        .all()
    )
    plugins = [_serialize_plugin(db, ctx, row, platform=platform) for row in rows]
    return {
        "platform": platform,
        "bridge": {
            "available": bridge.available(platform),
            "capabilities": bridge.capabilities(platform),
            "actions": [
                {
                    "code": code,
                    "label": spec["label"],
                    "risk": spec["risk"],
                    "permission": spec["permission"],
                    "description": spec["description"],
                }
                for code, spec in actions.ACTION_CATALOG.items()
            ],
        },
        "plugins": plugins,
        "limit_per_minute": ACTION_LIMIT_PER_MINUTE,
    }


def _audit(
    db: Session,
    ctx: YardContext,
    *,
    plugin_id: str,
    action: str,
    result: str,
    latency_ms: int,
    error: str | None = None,
) -> None:
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="local_runtime_action",
        resource_type="local_runtime",
        resource_id=plugin_id,
        result=result,
        request_id=ctx.request_id,
        metadata={
            "action": action,
            "latency_ms": latency_ms,
            "error": (error or "")[:120] or None,
        },
    )


def execute(
    db: Session,
    ctx: YardContext,
    *,
    plugin_id: str,
    action: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    platform = bridge.platform_name()
    if not bridge.available(platform):
        raise AppError(
            "RUNTIME_PLATFORM_UNSUPPORTED",
            f"当前系统（{platform}）暂未提供本机能力桥。",
            http_status=409,
            details={"platform": platform},
        )
    row = _plugin_row(db, ctx.yard_id, plugin_id)
    if row.status != "enabled":
        raise AppError(
            "RUNTIME_PLUGIN_NOT_ENABLED",
            "插件尚未启用，不能执行本机动作。",
            http_status=409,
            details={"plugin_id": plugin_id, "status": row.status},
        )
    manifest = row.manifest_json or {}
    permissions = _permissions(manifest)
    spec = actions.ACTION_CATALOG.get(action)
    if spec is None:
        raise AppError(
            "RUNTIME_ACTION_UNKNOWN",
            f"不支持的本机动作：{action}",
            http_status=404,
            details={"action": action, "supported": sorted(actions.ACTION_CATALOG)},
        )
    if spec["permission"] not in permissions:
        raise AppError(
            "RUNTIME_PERMISSION_NOT_DECLARED",
            "该插件没有声明执行此动作所需的权限。",
            http_status=403,
            details={"action": action, "required": spec["permission"]},
        )
    platforms = manifest.get("platforms")
    if isinstance(platforms, list) and platforms and platform not in platforms:
        raise AppError(
            "RUNTIME_PLATFORM_UNSUPPORTED",
            "该插件声明的平台不包含当前系统。",
            http_status=409,
            details={"platform": platform, "supported": platforms},
        )
    scope_hash = manifest_v2.scope_hash(permissions)
    if _active_consent(db, ctx.yard_id, plugin_id, scope_hash) is None:
        raise AppError(
            "RUNTIME_CONSENT_REQUIRED",
            "请先在插件中心确认该插件的权限授权。",
            http_status=409,
            details={"plugin_id": plugin_id, "scope_hash": scope_hash, "permissions": permissions},
        )
    if not _limiter.check(ctx.yard_id):
        raise AppError(
            "RATE_LIMITED",
            "本机动作调用过于频繁，请稍后再试。",
            http_status=429,
        )
    clean = actions.validate(action, params)
    started = time.perf_counter()
    try:
        result = bridge.execute(platform, action, clean)
    except bridge.BridgeError as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        _audit(
            db,
            ctx,
            plugin_id=plugin_id,
            action=action,
            result="failure",
            latency_ms=latency_ms,
            error=str(e),
        )
        raise AppError(
            "RUNTIME_ACTION_FAILED",
            f"本机动作失败：{e}",
            http_status=502,
            details={"action": action, "platform": platform},
        ) from e
    latency_ms = int((time.perf_counter() - started) * 1000)
    _audit(
        db,
        ctx,
        plugin_id=plugin_id,
        action=action,
        result="success",
        latency_ms=latency_ms,
    )
    return {
        "plugin_id": plugin_id,
        "action": action,
        "platform": platform,
        "duration_ms": latency_ms,
        "result": result,
    }
