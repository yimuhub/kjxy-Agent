"""插件清单 v2、权限授权与更新中心。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import pytest
from app.core.deps import CurrentUser, YardContext, get_current_user, get_db_session
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.middleware import RequestIDMiddleware
from app.modules.library import consent, crypto, manifest_v2, package, service
from app.modules.library.models import LibraryInstall, LibraryItem, PluginConsent
from app.modules.library.router import router as library_router
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

SEED = bytes(range(32))
PUBLIC = crypto.public_key_b64_from_seed(SEED)
MANIFEST_URL = "https://library.test/stable/manifest.json"
YARD = "yard-plugin-consent"


def _ctx() -> YardContext:
    return YardContext(yard_id=YARD, user_id="u-consent", roles=["yard_admin"], request_id="req_c")


def _platform_ctx() -> YardContext:
    return YardContext(
        yard_id="yard-platform",
        user_id="u-platform",
        roles=["platform_admin"],
        request_id="req_platform",
    )


def _local_definition(
    version: str, permissions: list[str], platforms: tuple[str, ...] = ("macos",)
) -> dict:
    return {
        "plugin_id": "computer_use",
        "name": "电脑操作（官方推荐）",
        "version": version,
        "type": "local_runtime",
        "api_compatibility": "2.0",
        "dependencies": [],
        "permissions": permissions,
        "platforms": list(platforms),
        "artifacts": {
            platform: {
                "entry": f"bridge/{platform}/computer-use",
                "sha256": hashlib.sha256(f"{platform}{version}".encode()).hexdigest(),
                "size_bytes": 1024,
            }
            for platform in platforms
        },
        "entry_point": None,
        "config_schema": {},
        "checksum": "",
        "description": "通过本机桥接完成界面操作",
        "risk_notes": "所有操作后果由使用者自行承担。",
    }


def _build_local(version: str, permissions: list[str]) -> bytes:
    return package.build_package(
        manifest={
            "kind": "plugin",
            "code": "computer_use",
            "name": "电脑操作（官方推荐）",
            "version": version,
            "channel": "stable",
            "schema_version": 2,
            "platforms": ["macos"],
            "permissions": permissions,
        },
        definition=_local_definition(version, permissions),
        seed=SEED,
        schema_version=2,
    )


def _entry(version: str, data: bytes) -> dict:
    return {
        "item_type": "plugin",
        "code": "computer_use",
        "name": "电脑操作（官方推荐）",
        "version": version,
        "description": "通过本机桥接完成界面操作",
        "category": "local",
        "risk_level": "high",
        "license": "官方授权",
        "author": "官方",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size_bytes": len(data),
        "download_url": f"https://library.test/packages/computer_use-{version}.kjxpack",
        "published_at": "2026-11-01T00:00:00+00:00",
    }


def _manifest_body(sequence: int, items: list[dict]) -> dict:
    now = datetime.now(UTC)
    body = {
        "schema_version": 1,
        "channel": "stable",
        "sequence": sequence,
        "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(days=30)).isoformat(),
        "publisher": {"name": "官方", "key_id": crypto.public_key_fingerprint(PUBLIC)["key_id"]},
        "items": items,
    }
    return crypto.sign_manifest(body, SEED)


class FakeFetcher:
    def __init__(self, routes: dict):
        self.routes = routes

    def __call__(self, url: str, headers: dict):
        route = self.routes.get(url)
        if route is None:
            return {"status_code": 404, "content": b"", "headers": {}}
        return route(headers) if callable(route) else route


def _json_response(body: dict) -> dict:
    return {
        "status_code": 200,
        "content": json.dumps(body).encode(),
        "headers": {"Content-Type": "application/json"},
    }


def _binary_response(data: bytes) -> dict:
    return {"status_code": 200, "content": data, "headers": {}}


def _make_source(db_session) -> str:
    created = service.create_source(
        db_session,
        _platform_ctx(),
        {
            "name": "官方源",
            "kind": "official",
            "base_url": "https://library.test/stable/",
            "channel": "stable",
            "public_key_b64": PUBLIC,
        },
        is_platform_admin=True,
    )
    return created["id"]


def _sync(db_session, source_id: str, fetcher: FakeFetcher, version: str, data: bytes) -> None:
    fetcher.routes[MANIFEST_URL] = _json_response(_manifest_body(1, [_entry(version, data)]))
    service.sync_source(db_session, YARD, source_id, fetcher)


def test_manifest_v2_scope_hash_and_risk():
    view = manifest_v2.normalize_manifest(
        {
            "schema_version": 2,
            "type": "local_runtime",
            "permissions": ["screen_capture", "network", "input_control"],
            "platforms": ["macos"],
            "artifacts": {
                "macos": {"entry": "bridge/a", "sha256": "0" * 64, "size_bytes": 1},
            },
        }
    )
    assert view["permissions"] == ["input_control", "network", "screen_capture"]
    assert view["risk_level"] == "critical"
    assert view["consent_required"] is True
    assert view["scope_hash"] == manifest_v2.scope_hash(
        ["screen_capture", "network", "input_control"]
    )
    assert [item["code"] for item in view["permission_details"]] == [
        "input_control",
        "network",
        "screen_capture",
    ]


def test_manifest_v2_rejects_unknown_permission_and_incomplete_artifacts():
    with pytest.raises(AppError) as exc:
        manifest_v2.normalize_manifest({"type": "workflow", "permissions": ["rootkit"]})
    assert exc.value.code == "PLUGIN_MANIFEST_INVALID"

    with pytest.raises(AppError) as exc:
        manifest_v2.normalize_manifest(
            {
                "schema_version": 2,
                "type": "local_runtime",
                "permissions": ["network"],
                "platforms": ["macos", "windows"],
                "artifacts": {
                    "macos": {"entry": "a", "sha256": "0" * 64, "size_bytes": 1},
                },
            }
        )
    assert exc.value.code == "PLUGIN_MANIFEST_INVALID"
    assert "windows" in str(exc.value.details)


def test_install_requires_consent_then_succeeds(db_session):
    source_id = _make_source(db_session)
    data = _build_local("1.0.0", ["input_control", "screen_capture"])
    routes = {
        MANIFEST_URL: _json_response(_manifest_body(1, [_entry("1.0.0", data)])),
        "https://library.test/packages/computer_use-1.0.0.kjxpack": _binary_response(data),
    }
    fetcher = FakeFetcher(routes)
    service.sync_source(db_session, YARD, source_id, fetcher)
    item = db_session.query(LibraryItem).one()
    ctx = _ctx()

    preview = service.consent_preview(db_session, ctx, item.id, fetcher)
    assert preview["required"] is True
    assert preview["granted"] is False
    assert preview["risk_level"] == "critical"
    assert {row["code"] for row in preview["permissions"]} == {"input_control", "screen_capture"}

    with pytest.raises(AppError) as exc:
        service.install_item(db_session, ctx, item.id, fetcher=fetcher)
    assert exc.value.code == "PLUGIN_CONSENT_REQUIRED"
    assert exc.value.http_status == 409
    assert db_session.query(LibraryInstall).count() == 0

    granted = service.grant_consent(db_session, ctx, item.id, fetcher=fetcher)
    assert granted["idempotent"] is False
    assert granted["consent"]["active"] is True
    assert db_session.query(PluginConsent).count() == 1

    again = service.grant_consent(db_session, ctx, item.id, fetcher=fetcher)
    assert again["idempotent"] is True

    installed = service.install_item(db_session, ctx, item.id, fetcher=fetcher)
    assert installed["installed_version"] == "1.0.0"
    assert db_session.query(LibraryInstall).count() == 1


def test_permission_change_requires_new_consent(db_session):
    source_id = _make_source(db_session)
    v1 = _build_local("1.0.0", ["screen_capture"])
    v2 = _build_local("1.1.0", ["screen_capture", "input_control"])
    routes = {
        MANIFEST_URL: _json_response(_manifest_body(1, [_entry("1.0.0", v1)])),
        "https://library.test/packages/computer_use-1.0.0.kjxpack": _binary_response(v1),
        "https://library.test/packages/computer_use-1.1.0.kjxpack": _binary_response(v2),
    }
    fetcher = FakeFetcher(routes)
    service.sync_source(db_session, YARD, source_id, fetcher)
    db_session.commit()
    item = db_session.query(LibraryItem).one()
    ctx = _ctx()

    service.grant_consent(db_session, ctx, item.id, fetcher=fetcher)
    service.install_item(db_session, ctx, item.id, fetcher=fetcher)
    db_session.commit()

    routes[MANIFEST_URL] = _json_response(_manifest_body(2, [_entry("1.1.0", v2)]))
    service.sync_source(db_session, YARD, source_id, fetcher)
    db_session.commit()
    install = db_session.query(LibraryInstall).one()
    with pytest.raises(AppError) as exc:
        service.update_item(db_session, ctx, install.id, fetcher=fetcher)
    assert exc.value.code == "PLUGIN_CONSENT_REQUIRED"

    granted = service.grant_consent(db_session, ctx, item.id, fetcher=fetcher)
    assert granted["consent"]["permissions"] == ["input_control", "screen_capture"]
    rows = db_session.query(PluginConsent).order_by(PluginConsent.scope_hash).all()
    assert any(row.revoked_at is not None for row in rows)

    updated = service.update_item(db_session, ctx, install.id, fetcher=fetcher)
    assert updated["updated"] is True
    assert updated["installed_version"] == "1.1.0"


def test_update_center_and_revoke(db_session):
    source_id = _make_source(db_session)
    v1 = _build_local("1.0.0", ["screen_capture"])
    v2 = _build_local("1.1.0", ["screen_capture"])
    routes = {
        MANIFEST_URL: _json_response(_manifest_body(1, [_entry("1.0.0", v1)])),
        "https://library.test/packages/computer_use-1.0.0.kjxpack": _binary_response(v1),
        "https://library.test/packages/computer_use-1.1.0.kjxpack": _binary_response(v2),
    }
    fetcher = FakeFetcher(routes)
    service.sync_source(db_session, YARD, source_id, fetcher)
    item = db_session.query(LibraryItem).one()
    ctx = _ctx()

    granted = service.grant_consent(db_session, ctx, item.id, fetcher=fetcher)
    service.install_item(db_session, ctx, item.id, fetcher=fetcher)

    routes[MANIFEST_URL] = _json_response(_manifest_body(2, [_entry("1.1.0", v2)]))
    service.sync_source(db_session, YARD, source_id, fetcher)
    updates = service.list_updates(db_session, ctx)
    assert updates["total"] == 1
    assert updates["items"][0]["installed_version"] == "1.0.0"
    assert updates["items"][0]["latest_version"] == "1.1.0"
    assert updates["items"][0]["name"] == "电脑操作（官方推荐）"
    assert updates["risk_summary"]["high"] == 1

    revoked = service.revoke_plugin_consent(db_session, ctx, granted["consent"]["id"])
    assert revoked["consent"]["active"] is False
    listed = service.list_plugin_consents(db_session, ctx)
    assert listed["active"] == 0
    assert listed["ack_text"]


def test_consent_scope_ignores_version(db_session):
    """权限集合不变时，升级不重复确认。"""
    source_id = _make_source(db_session)
    v1 = _build_local("1.0.0", ["screen_capture"])
    v2 = _build_local("1.1.0", ["screen_capture"])
    routes = {
        MANIFEST_URL: _json_response(_manifest_body(1, [_entry("1.0.0", v1)])),
        "https://library.test/packages/computer_use-1.0.0.kjxpack": _binary_response(v1),
        "https://library.test/packages/computer_use-1.1.0.kjxpack": _binary_response(v2),
    }
    fetcher = FakeFetcher(routes)
    service.sync_source(db_session, YARD, source_id, fetcher)
    item = db_session.query(LibraryItem).one()
    ctx = _ctx()
    service.grant_consent(db_session, ctx, item.id, fetcher=fetcher)
    service.install_item(db_session, ctx, item.id, fetcher=fetcher)

    routes[MANIFEST_URL] = _json_response(_manifest_body(2, [_entry("1.1.0", v2)]))
    service.sync_source(db_session, YARD, source_id, fetcher)
    install = db_session.query(LibraryInstall).one()
    updated = service.update_item(db_session, ctx, install.id, fetcher=fetcher)
    assert updated["updated"] is True
    assert db_session.query(PluginConsent).count() == 1


def _api_client(db) -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(library_router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id="u-consent",
        yard_id=YARD,
        roles=["yard_admin"],
        request_id="req-c",
    )
    return TestClient(app)


def test_consent_ack_required_by_router(db_session, monkeypatch):
    """接口层：POST 不带 ack=true 直接 400，不产生授权。"""
    source_id = _make_source(db_session)
    data = _build_local("1.0.0", ["input_control"])
    routes = {
        MANIFEST_URL: _json_response(_manifest_body(1, [_entry("1.0.0", data)])),
        "https://library.test/packages/computer_use-1.0.0.kjxpack": _binary_response(data),
    }
    fetcher = FakeFetcher(routes)
    service.sync_source(db_session, YARD, source_id, fetcher)
    item = db_session.query(LibraryItem).one()

    def _no_network(*args, **kwargs):
        raise AssertionError("不应在 ack 校验前下载")

    monkeypatch.setattr(service, "_download_package", _no_network)
    response = _api_client(db_session).post(f"/api/v1/library/items/{item.id}/consent", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "PLUGIN_CONSENT_ACK_REQUIRED"
    assert db_session.query(PluginConsent).count() == 0


def test_consent_revoke_is_yard_scoped(db_session):
    other = YardContext(yard_id="other-yard", user_id="u-x", roles=["yard_admin"], request_id="r")
    source_id = _make_source(db_session)
    data = _build_local("1.0.0", ["screen_capture"])
    fetcher = FakeFetcher(
        {
            MANIFEST_URL: _json_response(_manifest_body(1, [_entry("1.0.0", data)])),
            "https://library.test/packages/computer_use-1.0.0.kjxpack": _binary_response(data),
        }
    )
    service.sync_source(db_session, YARD, source_id, fetcher)
    item = db_session.query(LibraryItem).one()
    granted = service.grant_consent(db_session, _ctx(), item.id, fetcher=fetcher)
    with pytest.raises(AppError) as exc:
        service.revoke_plugin_consent(db_session, other, granted["consent"]["id"])
    assert exc.value.code == "PLUGIN_CONSENT_NOT_FOUND"


def test_consent_module_ack_text_matches_version():
    assert consent.ACK_VERSION == "v1"
    assert "自行承担" in consent.ACK_TEXT
