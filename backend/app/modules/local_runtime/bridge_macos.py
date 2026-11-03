"""macOS 本机桥：截图、窗口枚举、剪贴板与键鼠模拟。

实现全部走系统自带命令（screencapture / osascript / pbcopy / pbpaste），
不引入第三方二进制；所有参数通过 argv 传给 AppleScript 的 ``on run argv``，
不拼接脚本文本，避免注入。首次使用需用户在「系统设置 → 隐私与安全性」中
授予屏幕录制与辅助功能权限，未授权时返回可读的错误。
"""

from __future__ import annotations

import base64
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.modules.local_runtime.actions import MAX_CLIPBOARD, MAX_SCREENSHOT_BYTES

_ENV = {
    "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
    "LANG": "en_US.UTF-8",
    "HOME": str(Path.home()),
}

_WINDOW_SCRIPT = (
    'tell application "System Events" to get name of every process '
    "whose background only is false"
)

_TYPE_SCRIPT = (
    "on run argv\n" 'tell application "System Events" to keystroke (item 1 of argv)\n' "end run"
)

_CLICK_SCRIPT = (
    "on run argv\n"
    "set px to (item 1 of argv) as integer\n"
    "set py to (item 2 of argv) as integer\n"
    'tell application "System Events" to click at {px, py}\n'
    "end run"
)

_KEY_SCRIPT = (
    "on run argv\n"
    "set theKey to item 1 of argv\n"
    "set theMods to item 2 of argv\n"
    "set modList to text items of theMods\n"
    'tell application "System Events"\n'
    "repeat with m in modList\n"
    'if m is "command" then key down command\n'
    'if m is "option" then key down option\n'
    'if m is "shift" then key down shift\n'
    'if m is "control" then key down control\n'
    "end repeat\n"
    'if theKey is "return" then key code 36\n'
    'else if theKey is "tab" then key code 48\n'
    'else if theKey is "escape" then key code 53\n'
    'else if theKey is "space" then key code 49\n'
    'else if theKey is "delete" then key code 51\n'
    'else if theKey is "forward_delete" then key code 117\n'
    'else if theKey is "home" then key code 115\n'
    'else if theKey is "end" then key code 119\n'
    'else if theKey is "page_up" then key code 116\n'
    'else if theKey is "page_down" then key code 121\n'
    'else if theKey is "up" then key code 126\n'
    'else if theKey is "down" then key code 125\n'
    'else if theKey is "left" then key code 123\n'
    'else if theKey is "right" then key code 124\n'
    "else\n"
    "keystroke theKey\n"
    "end if\n"
    "repeat with m in modList\n"
    'if m is "command" then key up command\n'
    'if m is "option" then key up option\n'
    'if m is "shift" then key up shift\n'
    'if m is "control" then key up control\n'
    "end repeat\n"
    "end tell\n"
    "end run"
)


class BridgeError(Exception):
    """桥调用失败；message 只保留短错误，不回显屏幕/剪贴板内容。"""


def _run(cmd: list[str], *, timeout: float = 10.0, input_bytes: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(
            cmd,
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
            env=_ENV,
            check=False,
        )
    except FileNotFoundError as e:
        raise BridgeError(f"缺少系统命令：{cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise BridgeError("系统动作超时") from e
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        # 系统隐私权限未授予时 stderr 会很长，只保留前 200 字
        raise BridgeError(stderr[:200] or f"{cmd[0]} 退出码 {completed.returncode}")
    return completed.stdout


def available() -> bool:
    return all(shutil.which(name) for name in ("screencapture", "osascript", "pbpaste", "pbcopy"))


def _osascript(script: str, args: list[str] | None = None, timeout: float = 10.0) -> str:
    cmd = ["osascript", "-e", script]
    if args:
        cmd.extend(["--", *args])
    return _run(cmd, timeout=timeout).decode("utf-8", errors="replace").strip()


def execute(action: str, params: dict[str, Any]) -> dict[str, Any]:
    if action == "screenshot":
        return _screenshot()
    if action == "list_windows":
        output = _osascript(_WINDOW_SCRIPT)
        names = [item.strip() for item in output.split(",") if item.strip()]
        return {"apps": names[:60], "count": len(names[:60])}
    if action == "click":
        _osascript(_CLICK_SCRIPT, [str(params["x"]), str(params["y"])])
        return {"clicked": {"x": params["x"], "y": params["y"]}}
    if action == "type_text":
        _osascript(_TYPE_SCRIPT, [params["text"]])
        return {"typed_chars": len(params["text"])}
    if action == "press_key":
        modifiers = ",".join(params.get("modifiers") or [])
        _osascript(_KEY_SCRIPT, [params["key"], modifiers or "none"])
        return {"key": params["key"], "modifiers": params.get("modifiers") or []}
    if action == "clipboard_read":
        text = _run(["pbpaste"], timeout=5.0).decode("utf-8", errors="replace")
        return {"text": text[:MAX_CLIPBOARD], "truncated": len(text) > MAX_CLIPBOARD}
    if action == "clipboard_write":
        _run(["pbcopy"], input_bytes=params["text"].encode("utf-8"), timeout=5.0)
        return {"written_chars": len(params["text"])}
    if action == "open_app":
        _run(["open", "-a", params["name"]], timeout=10.0)
        return {"opened": params["name"]}
    raise BridgeError(f"不支持的动作：{action}")


def _screenshot() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="kjxy-shot-") as tmp:
        path = Path(tmp) / "screen.png"
        _run(["screencapture", "-x", "-t", "png", str(path)], timeout=15.0)
        if not path.exists():
            raise BridgeError("未生成截图文件（可能未授予屏幕录制权限）")
        data = path.read_bytes()
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise BridgeError("截图超过 8MB，请缩小屏幕分辨率后重试")
    return {
        "mime": "image/png",
        "size_bytes": len(data),
        "base64": base64.b64encode(data).decode("ascii"),
    }
