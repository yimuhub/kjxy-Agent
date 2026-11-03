"""本机能力动作目录：动作 → 所需权限 + 风险等级 + 参数约束。

口径：
- 动作必须一一对应到插件清单声明过的权限，未声明一律拒绝；
- 风险等级只用于提示与审计，不替代工具审批：本机动作仍按 ``tools:execute`` 闸门。
"""

from __future__ import annotations

import re
from typing import Any

from app.core.errors import AppError

ACTION_CATALOG: dict[str, dict[str, Any]] = {
    "screenshot": {
        "label": "屏幕截图",
        "permission": "screen_capture",
        "risk": "high",
        "description": "截取当前屏幕，返回 PNG（base64）。",
    },
    "list_windows": {
        "label": "列出应用窗口",
        "permission": "accessibility",
        "risk": "high",
        "description": "列出当前可见的应用进程名称。",
    },
    "click": {
        "label": "模拟点击",
        "permission": "input_control",
        "risk": "critical",
        "description": "在屏幕坐标处单击。",
    },
    "type_text": {
        "label": "输入文本",
        "permission": "input_control",
        "risk": "critical",
        "description": "向当前焦点窗口输入文本。",
    },
    "press_key": {
        "label": "按键",
        "permission": "input_control",
        "risk": "critical",
        "description": "发送按键或组合键，如 command+c。",
    },
    "clipboard_read": {
        "label": "读取剪贴板",
        "permission": "clipboard",
        "risk": "medium",
        "description": "读取剪贴板纯文本（最多 100KB）。",
    },
    "clipboard_write": {
        "label": "写入剪贴板",
        "permission": "clipboard",
        "risk": "medium",
        "description": "把文本写入剪贴板（最多 100KB）。",
    },
    "open_app": {
        "label": "打开应用",
        "permission": "input_control",
        "risk": "high",
        "description": "按应用名打开本机应用。",
    },
}

MAX_TEXT = 2000
MAX_CLIPBOARD = 100_000
MAX_SCREENSHOT_BYTES = 8 * 1024 * 1024

_APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,79}$")
_ALLOWED_KEYS = (
    {
        "return",
        "tab",
        "escape",
        "space",
        "delete",
        "forward_delete",
        "home",
        "end",
        "page_up",
        "page_down",
        "up",
        "down",
        "left",
        "right",
    }
    | {chr(code) for code in range(97, 123)}
    | {str(n) for n in range(10)}
)
_ALLOWED_MODIFIERS = {"command", "option", "shift", "control"}


def _fail(code: str, message: str, *, details: dict | None = None) -> AppError:
    return AppError(code, message, http_status=400, details=details)


def validate(action: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """校验并归一化参数；返回只含白名单字段的浅拷贝。"""
    if action not in ACTION_CATALOG:
        raise AppError(
            "RUNTIME_ACTION_UNKNOWN",
            f"不支持的本机动作：{action}",
            http_status=404,
            details={"action": action, "supported": sorted(ACTION_CATALOG)},
        )
    raw = dict(params or {})
    if action == "screenshot":
        return {}
    if action == "list_windows":
        return {}
    if action == "click":
        try:
            x = int(str(raw.get("x")))
            y = int(str(raw.get("y")))
        except (TypeError, ValueError):
            raise _fail("RUNTIME_PARAMS_INVALID", "点击坐标必须是整数。") from None
        if not (0 <= x <= 20000 and 0 <= y <= 20000):
            raise _fail("RUNTIME_PARAMS_INVALID", "点击坐标超出允许范围。")
        return {"x": x, "y": y}
    if action == "type_text":
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _fail("RUNTIME_PARAMS_INVALID", "text 必须是非空字符串。")
        if len(text) > MAX_TEXT:
            raise _fail("RUNTIME_PARAMS_INVALID", f"text 最长 {MAX_TEXT} 个字符。")
        return {"text": text}
    if action == "press_key":
        key = str(raw.get("key") or "").strip().lower()
        if key not in _ALLOWED_KEYS:
            raise _fail("RUNTIME_PARAMS_INVALID", "key 不在允许的按键列表内。")
        modifiers_raw = raw.get("modifiers") or []
        if not isinstance(modifiers_raw, list):
            raise _fail("RUNTIME_PARAMS_INVALID", "modifiers 必须是数组。")
        modifiers: list[str] = []
        for item in modifiers_raw:
            name = str(item or "").strip().lower()
            if name not in _ALLOWED_MODIFIERS:
                raise _fail("RUNTIME_PARAMS_INVALID", f"不支持的修饰键：{name}")
            if name not in modifiers:
                modifiers.append(name)
        return {"key": key, "modifiers": modifiers}
    if action == "clipboard_read":
        return {}
    if action == "clipboard_write":
        text = raw.get("text")
        if not isinstance(text, str):
            raise _fail("RUNTIME_PARAMS_INVALID", "text 必须是字符串。")
        if len(text) > MAX_CLIPBOARD:
            raise _fail("RUNTIME_PARAMS_INVALID", "剪贴板内容超过 100KB。")
        return {"text": text}
    if action == "open_app":
        name = str(raw.get("name") or "").strip()
        if not _APP_NAME_RE.match(name):
            raise _fail("RUNTIME_PARAMS_INVALID", "应用名不合法。")
        return {"name": name}
    raise AppError("RUNTIME_ACTION_UNKNOWN", "未知动作。", http_status=404)
