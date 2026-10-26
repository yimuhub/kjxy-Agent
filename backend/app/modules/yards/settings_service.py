"""小院设置域：注册、安全、搜索与网络。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.yards.models import YardSettings
from app.modules.yards.permissions import ASSIGNABLE_ROLES

#: 默认值（未写入 yard_settings 时使用）
DEFAULTS: dict[str, dict[str, Any]] = {
    "registration": {
        "mode": "approval",  # closed | approval | open
        "default_role": "viewer",
        "require_email": False,
        "require_phone": False,
        "allow_new_yard": True,
    },
    "security": {
        "password_min_length": 8,
        "max_login_attempts": 10,
        "lockout_seconds": 300,
    },
    "search": {
        "enabled": True,
        "provider": "searxng",  # searxng | tavily | brave
        "searxng_url": "http://127.0.0.1:8888",
        "tavily_api_key": "",
        "brave_api_key": "",
        "daily_limit": 500,
        "timeout_seconds": 8,
    },
}

REGISTRATION_MODES = {"closed", "approval", "open"}
SEARCH_PROVIDERS = {"searxng", "tavily", "brave"}


def _row(db: Session, yard_id: str, key: str) -> YardSettings | None:
    return db.scalar(
        select(YardSettings).where(YardSettings.yard_id == yard_id, YardSettings.key == key)
    )


def get_setting(db: Session, yard_id: str, key: str) -> dict[str, Any]:
    merged = deepcopy(DEFAULTS.get(key, {}))
    row = _row(db, yard_id, key)
    if row is not None and isinstance(row.value_json, dict):
        merged.update(row.value_json)
    return merged


def get_all(db: Session, yard_id: str) -> dict[str, dict[str, Any]]:
    return {key: get_setting(db, yard_id, key) for key in DEFAULTS}


def _validate(key: str, value: dict[str, Any], merged: dict[str, Any]) -> None:
    if key == "registration":
        if merged["mode"] not in REGISTRATION_MODES:
            raise AppError("INVALID_REQUEST", "注册模式不合法。", http_status=400)
        if merged["default_role"] not in ASSIGNABLE_ROLES:
            raise AppError("INVALID_REQUEST", "默认角色不合法。", http_status=400)
    elif key == "security":
        try:
            minimum = int(merged["password_min_length"])
            attempts = int(merged["max_login_attempts"])
            lockout = int(merged["lockout_seconds"])
        except (TypeError, ValueError) as e:
            raise AppError("INVALID_REQUEST", "安全策略必须是整数。", http_status=400) from e
        if not 6 <= minimum <= 64 or not 3 <= attempts <= 100 or not 30 <= lockout <= 86400:
            raise AppError("INVALID_REQUEST", "安全策略超出允许范围。", http_status=400)
    elif key == "search":
        if merged["provider"] not in SEARCH_PROVIDERS:
            raise AppError("INVALID_REQUEST", "搜索供应商不合法。", http_status=400)
        try:
            limit = int(merged["daily_limit"])
            timeout = int(merged["timeout_seconds"])
        except (TypeError, ValueError) as e:
            raise AppError("INVALID_REQUEST", "搜索配额必须是整数。", http_status=400) from e
        if not 1 <= limit <= 100000 or not 2 <= timeout <= 30:
            raise AppError("INVALID_REQUEST", "搜索配额/超时超出允许范围。", http_status=400)
        parsed = urlparse(str(merged.get("searxng_url") or ""))
        if merged["provider"] == "searxng" and parsed.scheme not in {"http", "https"}:
            raise AppError("INVALID_REQUEST", "SearXNG 地址必须是 HTTP(S)。", http_status=400)
    else:
        raise AppError("INVALID_REQUEST", f"未知设置项：{key}", http_status=400)


def update_setting(
    db: Session,
    *,
    yard_id: str,
    key: str,
    value: dict[str, Any],
    actor_id: str,
    request_id: str,
) -> dict[str, Any]:
    if key not in DEFAULTS:
        raise AppError("INVALID_REQUEST", f"未知设置项：{key}", http_status=400)
    merged = get_setting(db, yard_id, key)
    for k, v in value.items():
        if k not in merged:
            raise AppError("INVALID_REQUEST", f"未知配置字段：{k}", http_status=400)
        # 空字符串表示“保持原密钥不变”（前端不回显明文）
        if k.endswith("_api_key") and v == "":
            continue
        merged[k] = v
    _validate(key, value, merged)
    row = _row(db, yard_id, key)
    if row is None:
        row = YardSettings(yard_id=yard_id, key=key, value_json=merged, updated_by=actor_id)
        db.add(row)
    else:
        row.value_json = merged
        row.updated_by = actor_id
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=actor_id,
        action="yard_settings_updated",
        resource_type="yard_settings",
        resource_id=key,
        result="success",
        request_id=request_id,
        metadata={"key": key, "fields": sorted(value.keys())},
    )
    db.flush()
    return merged


def public_view(db: Session, yard_id: str) -> dict[str, dict[str, Any]]:
    """对外视图：密钥只暴露是否已配置。"""
    data = get_all(db, yard_id)
    search = data["search"]
    search["tavily_configured"] = bool(search.pop("tavily_api_key", ""))
    search["brave_configured"] = bool(search.pop("brave_api_key", ""))
    return data


__all__ = [
    "DEFAULTS",
    "REGISTRATION_MODES",
    "SEARCH_PROVIDERS",
    "get_all",
    "get_setting",
    "public_view",
    "update_setting",
]
