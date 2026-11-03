"""Windows 本机桥（v3.61）：UIAutomation + Win32 + PowerShell。

实现只依赖 Windows 自带组件：
- 截屏：System.Drawing ``CopyFromScreen``；
- 窗口枚举：``Get-Process`` 的主窗口标题；
- 剪贴板：``Get-Clipboard`` / ``Set-Clipboard``；
- 输入文本：UIAutomation ``ValuePattern``，取不到时退回 SendKeys；
- 点击：Win32 ``SetCursorPos`` + ``mouse_event``；
- 按键：SendKeys（修饰键映射为 ^%+）；
- 打开应用：``Start-Process``。

所有参数通过环境变量 / 临时文件传入，PowerShell 脚本以 ``-EncodedCommand`` 执行，
不拼接用户输入到脚本文本；文本经临时文件传递并在 finally 中删除。
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from app.modules.local_runtime.actions import MAX_CLIPBOARD, MAX_SCREENSHOT_BYTES

_PS = "powershell"


class BridgeError(Exception):
    pass


def available() -> bool:
    return sys.platform.startswith("win") and shutil.which(_PS) is not None


def _run_ps(
    script: str,
    *,
    env: dict[str, str] | None = None,
    timeout: float = 15.0,
    strip_output: bool = True,
) -> str:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    merged = dict(os.environ)
    merged.update(env or {})
    try:
        completed = subprocess.run(
            [
                _PS,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            timeout=timeout,
            env=merged,
            check=False,
        )
    except FileNotFoundError as e:
        raise BridgeError("未找到 PowerShell") from e
    except subprocess.TimeoutExpired as e:
        raise BridgeError("系统动作超时") from e
    if completed.returncode != 0:
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
        raise BridgeError(stderr[:200] or f"PowerShell 退出码 {completed.returncode}")
    output = completed.stdout.decode("utf-8", errors="replace")
    return output.strip() if strip_output else output


def _temp_text(text: str) -> Path:
    handle, name = tempfile.mkstemp(prefix="kjxy-win-", suffix=".txt")
    os.close(handle)
    path = Path(name)
    path.write_text(text, encoding="utf-8")
    return path


def execute(action: str, params: dict[str, Any]) -> dict[str, Any]:
    if action == "screenshot":
        return _screenshot()
    if action == "list_windows":
        return _list_windows()
    if action == "click":
        return _click(params["x"], params["y"])
    if action == "type_text":
        return _type_text(params["text"])
    if action == "press_key":
        return _press_key(params["key"], params.get("modifiers") or [])
    if action == "clipboard_read":
        text = _run_ps("Get-Clipboard -Raw", timeout=5.0, strip_output=False)
        return {"text": text[:MAX_CLIPBOARD], "truncated": len(text) > MAX_CLIPBOARD}
    if action == "clipboard_write":
        path = _temp_text(params["text"])
        try:
            _run_ps(
                "$t = Get-Content -Raw -LiteralPath $env:KJXY_INPUT; Set-Clipboard -Value $t",
                env={"KJXY_INPUT": str(path)},
                timeout=10.0,
            )
        finally:
            path.unlink(missing_ok=True)
        return {"written_chars": len(params["text"])}
    if action == "open_app":
        _run_ps("Start-Process -FilePath $env:KJXY_APP", env={"KJXY_APP": params["name"]})
        return {"opened": params["name"]}
    raise BridgeError(f"不支持的动作：{action}")


def _screenshot() -> dict[str, Any]:
    handle, name = tempfile.mkstemp(prefix="kjxy-shot-", suffix=".png")
    os.close(handle)
    path = Path(name)
    try:
        _run_ps(
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Location,[System.Drawing.Point]::Empty,$b.Size);"
            "$bmp.Save($env:KJXY_SHOT,[System.Drawing.Imaging.ImageFormat]::Png);"
            "$g.Dispose();$bmp.Dispose()",
            env={"KJXY_SHOT": str(path)},
            timeout=20.0,
        )
        data = path.read_bytes()
    finally:
        path.unlink(missing_ok=True)
    if not data:
        raise BridgeError("截图为空")
    if len(data) > MAX_SCREENSHOT_BYTES:
        raise BridgeError("截图超过 8MB，请缩小屏幕分辨率后重试")
    return {
        "mime": "image/png",
        "size_bytes": len(data),
        "base64": base64.b64encode(data).decode("ascii"),
    }


def _list_windows() -> dict[str, Any]:
    output = _run_ps(
        "Get-Process | Where-Object { $_.MainWindowTitle -ne '' } | "
        "Select-Object -First 60 -Property ProcessName, MainWindowTitle | "
        "ConvertTo-Json -Compress",
        timeout=10.0,
    )
    if not output:
        return {"apps": [], "count": 0}
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return {"apps": [], "count": 0}
    rows = parsed if isinstance(parsed, list) else [parsed]
    apps: list[str] = []
    for row in rows:
        if isinstance(row, dict) and row.get("MainWindowTitle"):
            apps.append(str(row["MainWindowTitle"]))
    return {"apps": apps, "count": len(apps)}


def _click(x: int, y: int) -> dict[str, Any]:
    _run_ps(
        "Add-Type @'\n"
        "using System;\n"
        "using System.Runtime.InteropServices;\n"
        "public class KjxyMouse {\n"
        '  [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);\n'
        '  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();\n'
        '  [DllImport("user32.dll")] public static extern void mouse_event('
        "uint f, uint dx, uint dy, uint d, IntPtr e);\n"
        "}\n"
        "'@;"
        "[KjxyMouse]::SetProcessDPIAware() | Out-Null;"
        "[KjxyMouse]::SetCursorPos([int]$env:KJXY_X,[int]$env:KJXY_Y) | Out-Null;"
        "[KjxyMouse]::mouse_event(0x0002,0,0,0,[IntPtr]::Zero);"
        "[KjxyMouse]::mouse_event(0x0004,0,0,0,[IntPtr]::Zero)",
        env={"KJXY_X": str(x), "KJXY_Y": str(y)},
        timeout=10.0,
    )
    return {"clicked": {"x": x, "y": y}}


def _type_text(text: str) -> dict[str, Any]:
    path = _temp_text(text)
    try:
        _run_ps(
            "Add-Type -AssemblyName UIAutomationClient;"
            "Add-Type -AssemblyName UIAutomationTypes;"
            "$text = Get-Content -Raw -LiteralPath $env:KJXY_INPUT;"
            "$el = [System.Windows.Automation.AutomationElement]::FocusedElement;"
            "$p = $null;"
            "if ($el -ne $null -and $el.TryGetCurrentPattern("
            "[System.Windows.Automation.ValuePattern]::Pattern,[ref]$p)) {"
            "  $p.SetValue($text)"
            "} else {"
            "  Add-Type -AssemblyName System.Windows.Forms;"
            "  [System.Windows.Forms.SendKeys]::SendWait("
            "[System.Windows.Forms.SendKeys]::Escape($text))"
            "}",
            env={"KJXY_INPUT": str(path)},
            timeout=15.0,
        )
    finally:
        path.unlink(missing_ok=True)
    return {"typed_chars": len(text)}


_KEY_TOKENS = {
    "return": "{ENTER}",
    "tab": "{TAB}",
    "escape": "{ESC}",
    "space": " ",
    "delete": "{BACKSPACE}",
    "forward_delete": "{DELETE}",
    "home": "{HOME}",
    "end": "{END}",
    "page_up": "{PGUP}",
    "page_down": "{PGDN}",
    "up": "{UP}",
    "down": "{DOWN}",
    "left": "{LEFT}",
    "right": "{RIGHT}",
}
_MODIFIERS = {"shift": "+", "command": "^", "control": "^", "option": "%"}
# SendKeys 要求修饰键按固定顺序出现：Shift(+)、Ctrl(^)、Alt(%)。
_MODIFIER_ORDER = ("shift", "command", "control", "option")


def build_sendkeys(key: str, modifiers: list[str]) -> str:
    selected = {name for name in modifiers if name in _MODIFIERS}
    prefix = ""
    for name in _MODIFIER_ORDER:
        if name in selected:
            char = _MODIFIERS[name]
            if char not in prefix:
                prefix += char
    token = _KEY_TOKENS.get(key, key)
    return f"{prefix}{token}"


def _press_key(key: str, modifiers: list[str]) -> dict[str, Any]:
    combo = build_sendkeys(key, modifiers)
    _run_ps(
        "Add-Type -AssemblyName System.Windows.Forms;"
        "[System.Windows.Forms.SendKeys]::SendWait($env:KJXY_KEYS)",
        env={"KJXY_KEYS": combo},
        timeout=10.0,
    )
    return {"key": key, "modifiers": modifiers}
