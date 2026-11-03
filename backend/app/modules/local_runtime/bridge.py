"""平台桥分发：darwin → macOS；Windows → PowerShell；Linux → X11/Wayland 工具。

统一入口 ``execute(platform, action, params)``、``available(platform)`` 与
``capabilities(platform)``，服务层只依赖本模块，不直接关心平台细节。
测试可 monkeypatch ``execute``。
"""

from __future__ import annotations

import sys
from typing import Any

from app.modules.local_runtime import bridge_macos


class BridgeError(Exception):
    """桥执行失败（跨平台统一异常类型）。"""


def platform_name() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def available(platform: str | None = None) -> bool:
    target = platform or platform_name()
    if target == "macos":
        return bridge_macos.available()
    try:
        from app.modules.local_runtime import bridge_linux, bridge_windows
    except ImportError:
        return False
    if target == "windows":
        return bridge_windows.available()
    if target == "linux":
        return bridge_linux.available()
    return False


def capabilities(platform: str | None = None) -> dict[str, bool]:
    """逐动作可用性：桥不支持的组合为 False，缺失的外部工具也如实返回。"""
    target = platform or platform_name()
    if target == "macos":
        from app.modules.local_runtime import actions

        ready = bridge_macos.available()
        return {code: ready for code in actions.ACTION_CATALOG}
    if target == "windows":
        from app.modules.local_runtime import actions, bridge_windows

        ready = bridge_windows.available()
        return {code: ready for code in actions.ACTION_CATALOG}
    if target == "linux":
        from app.modules.local_runtime import bridge_linux

        return bridge_linux.capabilities()
    return {}


def execute(platform: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
    if platform == "macos":
        try:
            return bridge_macos.execute(action, params)
        except bridge_macos.BridgeError as e:
            raise BridgeError(str(e)) from e
    if platform == "windows":
        from app.modules.local_runtime import bridge_windows

        try:
            return bridge_windows.execute(action, params)
        except bridge_windows.BridgeError as e:
            raise BridgeError(str(e)) from e
    if platform == "linux":
        from app.modules.local_runtime import bridge_linux

        try:
            return bridge_linux.execute(action, params)
        except bridge_linux.BridgeError as e:
            raise BridgeError(str(e)) from e
    raise BridgeError(f"暂不支持的系统：{platform}")
