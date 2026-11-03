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


def test_windows_sendkeys_tokens():
    from app.modules.local_runtime import bridge_windows

    assert bridge_windows.build_sendkeys("c", ["control"]) == "^c"
    assert bridge_windows.build_sendkeys("return", ["shift"]) == "+{ENTER}"
    assert bridge_windows.build_sendkeys("page_down", []) == "{PGDN}"
    assert bridge_windows.build_sendkeys("a", ["command", "shift"]) == "+^a"


def test_windows_bridge_availability_requires_platform_and_powershell(monkeypatch):
    from app.modules.local_runtime import bridge_windows

    monkeypatch.setattr(bridge_windows.sys, "platform", "win32")
    monkeypatch.setattr(bridge_windows.shutil, "which", lambda name: None)
    assert bridge_windows.available() is False
    monkeypatch.setattr(bridge_windows.shutil, "which", lambda name: "powershell.exe")
    assert bridge_windows.available() is True
    monkeypatch.setattr(bridge_windows.sys, "platform", "darwin")
    assert bridge_windows.available() is False


def test_windows_bridge_dispatch(monkeypatch, tmp_path):
    from app.modules.local_runtime import bridge_windows

    calls: list[tuple[str, dict]] = []

    def fake_ps(script, *, env=None, timeout=15.0, strip_output=True):
        calls.append((script, env or {}))
        if "ConvertTo-Json" in script:
            return (
                '[{"ProcessName":"Code","MainWindowTitle":"main.py"},'
                '{"ProcessName":"x","MainWindowTitle":""}]'
            )
        return ""

    monkeypatch.setattr(bridge_windows, "_run_ps", fake_ps)
    assert bridge_windows.execute("list_windows", {}) == {"apps": ["main.py"], "count": 1}
    assert bridge_windows.execute("click", {"x": 10, "y": 20}) == {"clicked": {"x": 10, "y": 20}}
    assert bridge_windows.execute("press_key", {"key": "c", "modifiers": ["control"]}) == {
        "key": "c",
        "modifiers": ["control"],
    }
    assert bridge_windows.execute("type_text", {"text": "hi"}) == {"typed_chars": 2}
    typed_env = [env for script, env in calls if "UIAutomationClient" in script][0]
    typed_script = [script for script, _ in calls if "UIAutomationClient" in script][0]
    # SendKeys 兜底必须先转义 +^%~(){}[]，否则文本会被当成快捷键
    assert "SendKeys]::Escape" in typed_script
    # type_text 的临时文件在调用结束后必须清理
    from pathlib import Path

    assert not Path(typed_env["KJXY_INPUT"]).exists()
    assert calls[1][1]["KJXY_X"] == "10"
    assert any(env.get("KJXY_KEYS") == "^c" for _, env in calls)


def test_windows_clipboard_roundtrip_does_not_strip(monkeypatch):
    from app.modules.local_runtime import bridge_windows

    seen: dict[str, str] = {}

    def fake_ps(script, *, env=None, timeout=15.0, strip_output=True):
        seen["script"] = script
        seen["strip"] = str(strip_output)
        if "Set-Clipboard" in script:
            from pathlib import Path

            seen["text"] = Path(env["KJXY_INPUT"]).read_text(encoding="utf-8")
            return ""
        return "  keep spaces\n"

    monkeypatch.setattr(bridge_windows, "_run_ps", fake_ps)
    assert bridge_windows.execute("clipboard_write", {"text": "a b"}) == {"written_chars": 3}
    assert seen["text"] == "a b"
    assert bridge_windows.execute("clipboard_read", {}) == {
        "text": "  keep spaces\n",
        "truncated": False,
    }


def test_linux_capabilities_follow_session_and_tools(monkeypatch):
    from app.modules.local_runtime import bridge_linux

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.setattr(bridge_linux, "_has", lambda name: True)
    assert not any(bridge_linux.capabilities().values())
    assert bridge_linux.available() is False

    monkeypatch.setenv("DISPLAY", ":0")
    assert all(bridge_linux.capabilities().values())
    assert bridge_linux.available() is True

    # 只有 xdotool：输入可用，截屏/剪贴板/启动应用如实报缺失
    monkeypatch.setattr(bridge_linux, "_has", lambda name: name == "xdotool")
    caps = bridge_linux.capabilities()
    assert caps["click"] and caps["type_text"] and caps["press_key"]
    assert caps["list_windows"] is True
    assert caps["screenshot"] is False
    assert caps["clipboard_read"] is False
    assert caps["open_app"] is False


def test_linux_bridge_capabilities_dispatch(monkeypatch):
    from app.modules.local_runtime import bridge_linux

    monkeypatch.setattr(bridge_linux, "capabilities", lambda: {"screenshot": True})
    assert bridge.capabilities("linux") == {"screenshot": True}


def test_linux_screenshot_prefers_session_tool(monkeypatch):
    from pathlib import Path

    from app.modules.local_runtime import bridge_linux

    seen: list[list[str]] = []

    def fake_run(cmd, *, timeout=10.0, input_bytes=None, strip_output=True):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b"\x89PNG-data")
        return b""

    monkeypatch.setattr(bridge_linux, "_run", fake_run)
    monkeypatch.setattr(bridge_linux, "_has", lambda name: name in {"grim", "scrot"})

    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("DISPLAY", raising=False)
    result = bridge_linux.execute("screenshot", {})
    assert seen[-1][0] == "grim"
    assert result["mime"] == "image/png"
    assert result["size_bytes"] == 9

    monkeypatch.delenv("WAYLAND_DISPLAY")
    monkeypatch.setenv("DISPLAY", ":0")
    bridge_linux.execute("screenshot", {})
    assert seen[-1][0] == "scrot"


def test_linux_clipboard_roundtrip_keeps_whitespace(monkeypatch):
    from app.modules.local_runtime import bridge_linux

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(bridge_linux, "_has", lambda name: name == "xclip")
    seen: list[tuple[list[str], bytes | None]] = []

    def fake_run(cmd, *, timeout=10.0, input_bytes=None, strip_output=True):
        seen.append((cmd, input_bytes))
        return b"  keep spaces\n"

    monkeypatch.setattr(bridge_linux, "_run", fake_run)
    assert bridge_linux.execute("clipboard_read", {}) == {
        "text": "  keep spaces\n",
        "truncated": False,
    }
    assert bridge_linux.execute("clipboard_write", {"text": "a b"}) == {"written_chars": 3}
    assert seen[-1][0] == ["xclip", "-selection", "clipboard", "-i"]
    assert seen[-1][1] == b"a b"


def test_linux_list_windows_parses_wmctrl(monkeypatch):
    from app.modules.local_runtime import bridge_linux

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(bridge_linux, "_has", lambda name: name == "wmctrl")

    def fake_run(cmd, *, timeout=10.0, input_bytes=None, strip_output=True):
        return b"0x03a00003  0 host Terminal\n0x03a00004 -1 host   \n"

    monkeypatch.setattr(bridge_linux, "_run", fake_run)
    assert bridge_linux.execute("list_windows", {}) == {"apps": ["Terminal"], "count": 1}


def test_linux_input_uses_xdotool(monkeypatch):
    from app.modules.local_runtime import bridge_linux

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(bridge_linux, "_has", lambda name: name == "xdotool")
    seen: list[list[str]] = []

    def fake_run(cmd, *, timeout=10.0, input_bytes=None, strip_output=True):
        seen.append(cmd)
        return b""

    monkeypatch.setattr(bridge_linux, "_run", fake_run)
    assert bridge_linux.execute("click", {"x": 10, "y": 20}) == {"clicked": {"x": 10, "y": 20}}
    assert seen[-1] == ["xdotool", "mousemove", "--sync", "10", "20", "click", "1"]

    bridge_linux.execute("type_text", {"text": "--weird"})
    assert seen[-1] == ["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", "--weird"]

    bridge_linux.execute("press_key", {"key": "c", "modifiers": ["command", "shift"]})
    assert seen[-1] == ["xdotool", "key", "--clearmodifiers", "shift+ctrl+c"]


def test_linux_input_uses_ydotool_keycodes(monkeypatch):
    from app.modules.local_runtime import bridge_linux

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(bridge_linux, "_has", lambda name: name == "ydotool")
    seen: list[list[str]] = []

    def fake_run(cmd, *, timeout=10.0, input_bytes=None, strip_output=True):
        seen.append(cmd)
        return b""

    monkeypatch.setattr(bridge_linux, "_run", fake_run)
    bridge_linux.execute("press_key", {"key": "a", "modifiers": ["command", "shift"]})
    assert seen[-1] == [
        "ydotool",
        "key",
        "42:1",
        "29:1",
        "30:1",
        "30:0",
        "29:0",
        "42:0",
    ]


def test_linux_missing_tools_raise_readable_errors(monkeypatch):
    from app.modules.local_runtime import bridge_linux

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(bridge_linux, "_has", lambda name: False)
    for action, params in (
        ("screenshot", {}),
        ("list_windows", {}),
        ("type_text", {"text": "hi"}),
    ):
        with pytest.raises(bridge_linux.BridgeError) as exc:
            bridge_linux.execute(action, params)
        assert "缺少" in str(exc.value)


def test_linux_open_app_prefers_gtk_launch(monkeypatch):
    from app.modules.local_runtime import bridge_linux

    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(bridge_linux, "_has", lambda name: name in {"gtk-launch", "gio"})
    seen: list[list[str]] = []

    def fake_run(cmd, *, timeout=10.0, input_bytes=None, strip_output=True):
        seen.append(cmd)
        return b""

    monkeypatch.setattr(bridge_linux, "_run", fake_run)
    assert bridge_linux.execute("open_app", {"name": "firefox"}) == {"opened": "firefox"}
    assert seen[-1] == ["gtk-launch", "firefox"]


def test_linux_unknown_action_raises(monkeypatch):
    from app.modules.local_runtime import bridge_linux

    with pytest.raises(bridge_linux.BridgeError):
        bridge_linux.execute("run_shell", {})
