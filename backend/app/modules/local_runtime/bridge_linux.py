"""Linux 本机桥（v3.62）：X11 / Wayland 命令行工具 + xdotool / ydotool。

Linux 没有像 macOS 的 ``osascript`` 或 Windows 的 PowerShell 那样统一的系统接口，
因此按桌面会话选择现成工具，且只做「缺什么报什么」：

- 截屏：Wayland 用 ``grim``；X11 用 ``scrot``、ImageMagick ``import``；
  ``gnome-screenshot`` 作为通用兜底；
- 窗口：``wmctrl -l``，无 wmctrl 时用 ``xdotool`` 逐个取标题；
- 输入：X11/XWayland 用 ``xdotool``；纯 Wayland 用 ``ydotool``（需 ``ydotoold``）；
- 剪贴板：Wayland ``wl-paste`` / ``wl-copy``；X11 ``xclip``，``xsel`` 兜底；
- 打开应用：``gtk-launch``（无则 ``gio launch``）。

所有命令以 argv 数组执行，不经 shell；用户文本通过 argv 或 stdin 传递。
工具缺失、无图形会话或权限不足时返回可读错误，不记录屏幕/剪贴板内容。
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.modules.local_runtime.actions import MAX_CLIPBOARD, MAX_SCREENSHOT_BYTES

_SESSION_VARS = ("WAYLAND_DISPLAY", "DISPLAY")
_XDOTOOL_KEYS = {
    "return": "Return",
    "tab": "Tab",
    "escape": "Escape",
    "space": "space",
    "delete": "BackSpace",
    "forward_delete": "Delete",
    "home": "Home",
    "end": "End",
    "page_up": "Prior",
    "page_down": "Next",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
}
_XDOTOOL_MODS = {"command": "ctrl", "control": "ctrl", "option": "alt", "shift": "shift"}
# Linux evdev keycode（ydotool 使用）；command 在 Linux 映射为 Ctrl。
_EVDEV_KEYS = {
    "return": 28,
    "tab": 15,
    "escape": 1,
    "space": 57,
    "delete": 14,
    "forward_delete": 111,
    "home": 102,
    "end": 107,
    "page_up": 104,
    "page_down": 109,
    "up": 103,
    "down": 108,
    "left": 105,
    "right": 106,
}
_EVDEV_LETTERS = {
    "a": 30,
    "b": 48,
    "c": 46,
    "d": 32,
    "e": 18,
    "f": 33,
    "g": 34,
    "h": 35,
    "i": 23,
    "j": 36,
    "k": 37,
    "l": 38,
    "m": 50,
    "n": 49,
    "o": 24,
    "p": 25,
    "q": 16,
    "r": 19,
    "s": 31,
    "t": 20,
    "u": 22,
    "v": 47,
    "w": 17,
    "x": 45,
    "y": 21,
    "z": 44,
}
_EVDEV_DIGITS = {str(n): code for n, code in zip("1234567890", range(2, 12), strict=True)}
_EVDEV_MODS = {"command": 29, "control": 29, "option": 56, "shift": 42}
_EVDEV_MOD_ORDER = ("shift", "command", "control", "option")


class BridgeError(Exception):
    """桥调用失败；message 只保留短错误，不回显屏幕/剪贴板内容。"""


def _env() -> dict[str, str]:
    env = dict(os.environ)
    path = env.get("PATH", "")
    if "/usr/bin" not in path.split(":"):
        path = f"/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:{path}".rstrip(":")
    env["PATH"] = path
    env.setdefault("LANG", "C.UTF-8")
    return env


def _run(
    cmd: list[str],
    *,
    timeout: float = 10.0,
    input_bytes: bytes | None = None,
    strip_output: bool = True,
) -> bytes:
    try:
        completed = subprocess.run(
            cmd,
            input=input_bytes,
            capture_output=True,
            timeout=timeout,
            env=_env(),
            check=False,
        )
    except FileNotFoundError as e:
        raise BridgeError(f"缺少系统命令：{cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise BridgeError("系统动作超时") from e
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        raise BridgeError(stderr[:200] or f"{cmd[0]} 退出码 {completed.returncode}")
    output = completed.stdout
    return output.strip() if strip_output else output


def _has(name: str) -> bool:
    return shutil.which(name) is not None


def session() -> str:
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


def _input_backend() -> str | None:
    if _has("xdotool"):
        return "xdotool"
    if _has("ydotool"):
        return "ydotool"
    return None


def _screenshot_cmd(path: Path) -> list[str] | None:
    wayland = session() == "wayland"
    if wayland and _has("grim"):
        return ["grim", "-t", "png", str(path)]
    if _has("scrot"):
        return ["scrot", "-o", str(path)]
    if _has("gnome-screenshot"):
        return ["gnome-screenshot", "-f", str(path)]
    if _has("grim"):
        return ["grim", "-t", "png", str(path)]
    if _has("import"):
        return ["import", "-window", "root", str(path)]
    return None


def _clipboard_read_cmd() -> list[str] | None:
    if session() == "wayland" and _has("wl-paste"):
        return ["wl-paste", "--no-newline"]
    if _has("xclip"):
        return ["xclip", "-selection", "clipboard", "-o"]
    if _has("xsel"):
        return ["xsel", "--clipboard", "--output"]
    if _has("wl-paste"):
        return ["wl-paste", "--no-newline"]
    return None


def _clipboard_write_cmd() -> list[str] | None:
    if session() == "wayland" and _has("wl-copy"):
        return ["wl-copy"]
    if _has("xclip"):
        return ["xclip", "-selection", "clipboard", "-i"]
    if _has("xsel"):
        return ["xsel", "--clipboard", "--input"]
    if _has("wl-copy"):
        return ["wl-copy"]
    return None


def _list_windows_capable() -> bool:
    return _has("wmctrl") or _has("xdotool")


def capabilities() -> dict[str, bool]:
    graphical = session() != "unknown"
    screenshot = graphical and _screenshot_cmd(Path("/tmp/probe.png")) is not None
    clipboard_read = graphical and _clipboard_read_cmd() is not None
    clipboard_write = graphical and _clipboard_write_cmd() is not None
    backend = _input_backend() if graphical else None
    return {
        "screenshot": screenshot,
        "list_windows": graphical and _list_windows_capable(),
        "click": backend is not None,
        "type_text": backend is not None,
        "press_key": backend is not None,
        "clipboard_read": clipboard_read,
        "clipboard_write": clipboard_write,
        "open_app": graphical and (_has("gtk-launch") or _has("gio")),
    }


def available() -> bool:
    return any(capabilities().values())


def _xdotool_combo(key: str, modifiers: list[str]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for name in _EVDEV_MOD_ORDER:
        if name in modifiers:
            mapped = _XDOTOOL_MODS[name]
            if mapped not in seen:
                seen.add(mapped)
                parts.append(mapped)
    parts.append(_XDOTOOL_KEYS.get(key, key))
    return "+".join(parts)


def _ydotool_combo(key: str, modifiers: list[str]) -> list[str]:
    codes: list[int] = []
    seen: set[int] = set()
    for name in _EVDEV_MOD_ORDER:
        if name in modifiers:
            code = _EVDEV_MODS[name]
            if code not in seen:
                seen.add(code)
                codes.append(code)
    target = _EVDEV_KEYS.get(key) or _EVDEV_LETTERS.get(key) or _EVDEV_DIGITS.get(key)
    if target is None:
        raise BridgeError(f"ydotool 不支持按键：{key}")
    codes.append(target)
    tokens = [f"{code}:1" for code in codes] + [f"{code}:0" for code in reversed(codes)]
    return tokens


def execute(action: str, params: dict[str, Any]) -> dict[str, Any]:
    if action == "screenshot":
        return _screenshot()
    if action == "list_windows":
        return _list_windows()
    if action == "click":
        _click(params["x"], params["y"])
        return {"clicked": {"x": params["x"], "y": params["y"]}}
    if action == "type_text":
        _type_text(params["text"])
        return {"typed_chars": len(params["text"])}
    if action == "press_key":
        _press_key(params["key"], params.get("modifiers") or [])
        return {"key": params["key"], "modifiers": params.get("modifiers") or []}
    if action == "clipboard_read":
        cmd = _clipboard_read_cmd()
        if cmd is None:
            raise BridgeError("缺少剪贴板工具：请安装 wl-clipboard、xclip 或 xsel")
        text = _run(cmd, timeout=5.0, strip_output=False).decode("utf-8", errors="replace")
        return {"text": text[:MAX_CLIPBOARD], "truncated": len(text) > MAX_CLIPBOARD}
    if action == "clipboard_write":
        cmd = _clipboard_write_cmd()
        if cmd is None:
            raise BridgeError("缺少剪贴板工具：请安装 wl-clipboard、xclip 或 xsel")
        _run(cmd, input_bytes=params["text"].encode("utf-8"), timeout=5.0)
        return {"written_chars": len(params["text"])}
    if action == "open_app":
        _open_app(params["name"])
        return {"opened": params["name"]}
    raise BridgeError(f"不支持的动作：{action}")


def _screenshot() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="kjxy-shot-") as tmp:
        path = Path(tmp) / "screen.png"
        cmd = _screenshot_cmd(path)
        if cmd is None:
            raise BridgeError("缺少截屏工具：请安装 grim、scrot 或 ImageMagick")
        _run(cmd, timeout=15.0)
        if not path.exists() or path.stat().st_size == 0:
            raise BridgeError("未生成截图文件（可能无图形会话或权限不足）")
        data = path.read_bytes()
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise BridgeError("截图超过 8MB，请缩小屏幕分辨率后重试")
    return {
        "mime": "image/png",
        "size_bytes": len(data),
        "base64": base64.b64encode(data).decode("ascii"),
    }


def _list_windows() -> dict[str, Any]:
    titles: list[str] = []
    if _has("wmctrl"):
        output = _run(["wmctrl", "-l"], timeout=10.0).decode("utf-8", errors="replace")
        for line in output.splitlines():
            parts = line.split(None, 3)
            if len(parts) == 4 and parts[3].strip():
                titles.append(parts[3].strip())
    elif _has("xdotool"):
        ids = _run(
            ["xdotool", "search", "--onlyvisible", "--name", ""],
            timeout=10.0,
        ).decode("ascii", errors="replace")
        for window_id in ids.split()[:60]:
            try:
                title = _run(
                    ["xdotool", "getwindowname", window_id],
                    timeout=3.0,
                ).decode("utf-8", errors="replace")
            except BridgeError:
                continue
            if title:
                titles.append(title)
    else:
        raise BridgeError("缺少窗口工具：请安装 wmctrl 或 xdotool")
    names = titles[:60]
    return {"apps": names, "count": len(names)}


def _click(x: int, y: int) -> None:
    backend = _input_backend()
    if backend == "xdotool":
        _run(["xdotool", "mousemove", "--sync", str(x), str(y), "click", "1"], timeout=10.0)
        return
    if backend == "ydotool":
        _run(["ydotool", "mousemove", "--absolute", str(x), str(y)], timeout=10.0)
        _run(["ydotool", "click", "1"], timeout=10.0)
        return
    raise BridgeError("缺少输入工具：请安装 xdotool 或 ydotool")


def _type_text(text: str) -> None:
    backend = _input_backend()
    if backend == "xdotool":
        _run(
            ["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", text],
            timeout=15.0,
        )
        return
    if backend == "ydotool":
        _run(["ydotool", "type", "--", text], timeout=15.0)
        return
    raise BridgeError("缺少输入工具：请安装 xdotool 或 ydotool")


def _press_key(key: str, modifiers: list[str]) -> None:
    backend = _input_backend()
    if backend == "xdotool":
        _run(
            ["xdotool", "key", "--clearmodifiers", _xdotool_combo(key, modifiers)],
            timeout=10.0,
        )
        return
    if backend == "ydotool":
        _run(["ydotool", "key", *_ydotool_combo(key, modifiers)], timeout=10.0)
        return
    raise BridgeError("缺少输入工具：请安装 xdotool 或 ydotool")


def _open_app(name: str) -> None:
    if _has("gtk-launch"):
        _run(["gtk-launch", name], timeout=10.0)
        return
    if _has("gio"):
        _run(["gio", "launch", name], timeout=10.0)
        return
    raise BridgeError("缺少应用启动工具：请安装 gtk-launch 或 gio")
