"""Linux 桥占位（v3.62 接入 AT-SPI / X11 / Portal）。

在 3.62 完成前：``available()`` 返回 False，服务层据此返回
``RUNTIME_PLATFORM_UNSUPPORTED``，不会尝试执行任何动作。
"""

from __future__ import annotations

from typing import Any


class BridgeError(Exception):
    pass


def available() -> bool:
    return False


def execute(action: str, params: dict[str, Any]) -> dict[str, Any]:
    raise BridgeError("Linux 本机桥尚未就绪")
