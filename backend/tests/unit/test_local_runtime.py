"""本机能力通道：权限声明、授权闸门、动作校验与审计。"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.models import AuditLog
from app.modules.library import manifest_v2
from app.modules.library.models import PluginConsent
from app.modules.local_runtime import bridge, service
from app.modules.plugins.models import PluginManifest

YARD = "yard-runtime"


def _ctx(roles: list[str] | None = None) -> YardContext:
    return YardContext(
        yard_id=YARD,
        user_id="u-runtime",
        roles=roles if roles is not None else ["yard_admin"],
        request_id="req-runtime",
    )


def _manifest(
    db,
    *,
    plugin_id: str = "computer_use",
    permissions: list[str] | None = None,
    status: str = "enabled",
    yard_id: str | None = YARD,
    platforms: list[str] | None = None,
) -> PluginManifest:
    row = PluginManifest(
        plugin_id=plugin_id,
        yard_id=yard_id,
        version="1.0.0",
        type="local_runtime",
        status=status,
        manifest_json={
            "plugin_id": plugin_id,
            "name": "电脑操作",
            "version": "1.0.0",
            "type": "local_runtime",
            "permissions": permissions
            if permissions is not None
            else ["screen_capture", "input_control"],
            "platforms": platforms or ["macos", "windows", "linux"],
            "schema_version": 2,
        },
        checksum="x",
        checksum_ok=True,
        installed_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def _grant(db, row: PluginManifest) -> PluginConsent:
    permissions = list(row.manifest_json["permissions"])
    consent = PluginConsent(
        yard_id=YARD,
        item_key=f"official:plugin:{row.plugin_id}",
        plugin_id=row.plugin_id,
        plugin_version=row.version,
        scope_hash=manifest_v2.scope_hash(permissions),
        permissions_json=permissions,
        risk_level=manifest_v2.risk_level(permissions),
        ack_version="v1",
        accepted_by="u-runtime",
        accepted_at=datetime.now(UTC),
    )
    db.add(consent)
    db.flush()
    return consent


@pytest.fixture()
def mac_bridge(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    def fake_execute(platform: str, action: str, params: dict):
        calls.append((platform, action, params))
        return {"ok": True, "action": action}

    monkeypatch.setattr(bridge, "platform_name", lambda: "macos")
    monkeypatch.setattr(bridge, "available", lambda platform=None: True)
    monkeypatch.setattr(bridge, "execute", fake_execute)
    return calls


def test_status_lists_plugins_actions_and_consent(db_session, mac_bridge):
    row = _manifest(db_session)
    _grant(db_session, row)
    status = service.status(db_session, _ctx())
    assert status["platform"] == "macos"
    assert status["bridge"]["available"] is True
    plugin = status["plugins"][0]
    assert plugin["plugin_id"] == "computer_use"
    assert plugin["consent_granted"] is True
    assert plugin["platform_supported"] is True
    actions = {item["code"] for item in plugin["actions"]}
    assert {"screenshot", "click", "type_text"} <= actions
    assert "clipboard_read" not in actions


def test_execute_requires_consent(db_session, mac_bridge):
    _manifest(db_session)
    with pytest.raises(AppError) as exc:
        service.execute(db_session, _ctx(), plugin_id="computer_use", action="screenshot")
    assert exc.value.code == "RUNTIME_CONSENT_REQUIRED"
    assert exc.value.http_status == 409


def test_execute_denies_permission_not_declared(db_session, mac_bridge):
    row = _manifest(db_session, permissions=["screen_capture"])
    _grant(db_session, row)
    with pytest.raises(AppError) as exc:
        service.execute(db_session, _ctx(), plugin_id="computer_use", action="click", params={})
    assert exc.value.code == "RUNTIME_PERMISSION_NOT_DECLARED"
    assert exc.value.http_status == 403
    assert mac_bridge == []


def test_execute_success_and_audit(db_session, mac_bridge):
    row = _manifest(db_session)
    _grant(db_session, row)
    result = service.execute(
        db_session,
        _ctx(),
        plugin_id="computer_use",
        action="type_text",
        params={"text": "hello"},
    )
    assert result["result"]["ok"] is True
    assert result["action"] == "type_text"
    assert mac_bridge == [("macos", "type_text", {"text": "hello"})]
    log = db_session.query(AuditLog).filter(AuditLog.action == "local_runtime_action").one()
    assert log.result == "success"
    assert log.metadata_json["action"] == "type_text"


def test_execute_rejects_disabled_plugin(db_session, mac_bridge):
    _manifest(db_session, status="pending_review")
    with pytest.raises(AppError) as exc:
        service.execute(db_session, _ctx(), plugin_id="computer_use", action="screenshot")
    assert exc.value.code == "RUNTIME_PLUGIN_NOT_ENABLED"


def test_execute_validates_params_before_bridge(db_session, mac_bridge):
    row = _manifest(db_session)
    _grant(db_session, row)
    with pytest.raises(AppError) as exc:
        service.execute(
            db_session,
            _ctx(),
            plugin_id="computer_use",
            action="click",
            params={"x": -1, "y": 10},
        )
    assert exc.value.code == "RUNTIME_PARAMS_INVALID"
    assert mac_bridge == []


def test_execute_unknown_plugin_404(db_session, mac_bridge):
    with pytest.raises(AppError) as exc:
        service.execute(db_session, _ctx(), plugin_id="nope", action="screenshot")
    assert exc.value.code == "RUNTIME_PLUGIN_NOT_FOUND"


def test_global_plugin_visible_but_platform_checked(db_session, mac_bridge):
    row = _manifest(db_session, yard_id=None, platforms=["windows"])
    _grant(db_session, row)
    with pytest.raises(AppError) as exc:
        service.execute(db_session, _ctx(), plugin_id="computer_use", action="screenshot")
    assert exc.value.code == "RUNTIME_PLATFORM_UNSUPPORTED"
    assert exc.value.http_status == 409


def test_bridge_failure_maps_to_502_and_audits(db_session, monkeypatch):
    row = _manifest(db_session)
    _grant(db_session, row)
    monkeypatch.setattr(bridge, "platform_name", lambda: "macos")
    monkeypatch.setattr(bridge, "available", lambda platform=None: True)

    def boom(platform: str, action: str, params: dict):
        raise bridge.BridgeError("未授予屏幕录制权限")

    monkeypatch.setattr(bridge, "execute", boom)
    with pytest.raises(AppError) as exc:
        service.execute(db_session, _ctx(), plugin_id="computer_use", action="screenshot")
    assert exc.value.code == "RUNTIME_ACTION_FAILED"
    assert exc.value.http_status == 502
    log = db_session.query(AuditLog).filter(AuditLog.action == "local_runtime_action").one()
    assert log.result == "failure"


def test_press_key_and_clipboard_validation():
    from app.modules.local_runtime import actions

    assert actions.validate("press_key", {"key": "c", "modifiers": ["command"]}) == {
        "key": "c",
        "modifiers": ["command"],
    }
    with pytest.raises(AppError):
        actions.validate("press_key", {"key": "f13"})
    with pytest.raises(AppError):
        actions.validate("press_key", {"key": "c", "modifiers": ["meta"]})
    with pytest.raises(AppError):
        actions.validate("clipboard_write", {"text": "x" * 100_001})
    assert actions.validate("open_app", {"name": "Safari"}) == {"name": "Safari"}
    with pytest.raises(AppError):
        actions.validate("open_app", {"name": "../../evil"})
    # 未知动作
    with pytest.raises(AppError) as exc:
        actions.validate("run_shell", {})
    assert exc.value.code == "RUNTIME_ACTION_UNKNOWN"
