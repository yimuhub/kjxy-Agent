"""飞书长连接适配器（官方 SDK 可选依赖）。

飞书官方 `lark-oapi` SDK 内置长连接（WebSocket 全双工，免公网/免加解密），
但 SDK 依赖链较重，本平台作为 **optional extra**：

- 未安装：配置仍可创建保存，manager 置 status=error、reason 明确提示
  `pip install lark-oapi`（诚实口径，绝不静默降级）；
- 已安装：用 `lark.ws.Client(app_id, app_secret, event_handler=...)` 建连，
  收 im.message.receive_v1 事件 → 归一化 InboundMessage；出站走
  开放 API（tenant_access_token + im/v1/messages），httpx 可 MockTransport 测。
"""

from __future__ import annotations

import logging
from typing import Any

from app.modules.bots.protocol import InboundMessage, OnInbound, OnState

log = logging.getLogger(__name__)

try:  # optional import：未装不影响应用启动与其余平台
    import lark_oapi  # type: ignore[import-not-found]

    LARK_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于环境
    lark_oapi = None  # type: ignore[assignment]
    LARK_AVAILABLE = False


async def _await(coro: Any) -> None:  # create_task 需要协程对象的中转
    await coro


MISSING_SDK_REASON = "飞书平台需安装官方 SDK：pip install 'lark-oapi>=1.4'"


def feishu_available() -> bool:
    return LARK_AVAILABLE


class FeishuAdapter:
    """飞书适配器（薄封装；SDK 缺席时构造即报错，由 manager 转成 status=error）。"""

    platform = "feishu"

    def __init__(self, *, app_id: str, app_secret: str, on_inbound: OnInbound, on_state: OnState):
        if not LARK_AVAILABLE:
            raise RuntimeError(MISSING_SDK_REASON)
        self._app_id = app_id
        self._app_secret = app_secret
        self._on_inbound = on_inbound
        self._on_state = on_state
        self._client: Any = None

    def _build(self) -> Any:  # pragma: no cover - 需要 SDK 才能跑
        import lark_oapi as lark

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._handle_event)
            .build()
        )
        return lark.ws.Client(  # type: ignore[union-attr]
            self._app_id, self._app_secret, event_handler=handler, log_level=lark.LogLevel.WARNING
        )

    def _handle_event(self, data: Any) -> None:  # pragma: no cover - SDK 线程回调
        import asyncio
        import json

        try:
            msg = data.event.message
            text = json.loads(msg.content or "{}").get("text", "").strip()
            conv_id = msg.chat_id
            if not conv_id or not text:
                return
            inbound = InboundMessage(
                platform="feishu",
                external_conv_id=conv_id,
                conv_type="group" if msg.chat_type == "group" else "dm",
                text=text,
                external_msg_id=msg.message_id,
                raw={},
            )
            asyncio.get_running_loop().create_task(_await(self._on_inbound(inbound)))
        except Exception as e:  # noqa: BLE001
            log.warning("飞书事件解析失败：%s", e)

    async def run(self) -> None:  # pragma: no cover
        self._client = self._build()
        self._on_state("online", "")
        # lark ws Client.start() 是阻塞调用：放线程池，断开即回 offline
        import asyncio

        try:
            await asyncio.get_running_loop().run_in_executor(None, self._client.start)
        finally:
            self._on_state("offline", "飞书长连接已断开")

    async def send(self, conversation_id: str, text: str) -> None:  # pragma: no cover
        """出站发消息（im/v1/messages）；httpx 可测部分在 service 层封装。"""
        raise NotImplementedError("由 manager 注入带 tenant_access_token 的 http 发送实现")

    async def close(self) -> None:
        return None
