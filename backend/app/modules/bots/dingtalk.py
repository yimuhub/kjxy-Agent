"""钉钉 Stream 模式适配器（官方「五零接入」：零公网/域名/证书/网关/穿透）。

流程（open.dingtalk.com Stream 官方文档）：
1. POST https://api.dingtalk.com/v1.0/gateway/connections/open
   body {clientId, clientSecret, subscriptions:[{type:CALLBACK, topic:/v1.0/im/bot/messages/get}]}
   → 返回 {endpoint, ticket}；
2. GET endpoint?ticket=xxx 建 WebSocket（连接本身由 manager 注入 ConnLike，
   httpx 段用 MockTransport 测）；
3. 帧格式：{specVersion, type: SYSTEM|EVENT|CALLBACK, headers:{topic, contentType, messageId}, data}
   - SYSTEM ping → 必须回 {code:200, headers:{messageId, contentType}, data:{}}；
   - CALLBACK机器人消息 → 业务处理后回 ACK（headers 带原 messageId；2 秒内需响应，
     LLM 慢应答走「先 ACK 再主动 update 卡片/发消息」——本实现收帧即 ACK、异步答题）。

回调 2 秒铁律：ACK 与业务解耦——收到消息先回 ACK（send 由 ack() 闭包注入），
再把 InboundMessage 交给 gateway 慢慢答（答完走 sessionWebhook 或主动消息）。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.modules.bots.protocol import InboundMessage, OnInbound, OnState

log = logging.getLogger(__name__)

DINGTALK_GATEWAY_OPEN = "https://api.dingtalk.com/v1.0/gateway/connections/open"
DINGTALK_ROBOT_TOPIC = "/v1.0/im/bot/messages/get"


class DingTalkAdapter:
    """钉钉 Stream 适配器（帧协议在此；HTTP 换 endpoint 与 WS 连接均注入）。"""

    platform = "dingtalk"

    def __init__(
        self,
        *,
        conn: Any,  # ConnLike（send/recv/aclose）
        on_inbound: OnInbound,
        on_state: OnState,
    ) -> None:
        self._conn = conn
        self._on_inbound = on_inbound
        self._on_state = on_state

    async def open_connection(self, http_client, client_id: str, client_secret: str) -> str:
        """STEP1+2：换取 endpoint+ticket，返回完整 ws url（真连由 manager 建 ConnLike）。"""
        resp = await http_client.post(
            DINGTALK_GATEWAY_OPEN,
            json={
                "clientId": client_id,
                "clientSecret": client_secret,
                "subscriptions": [{"type": "CALLBACK", "topic": DINGTALK_ROBOT_TOPIC}],
                "ua": "kjxy-agent/1.4",
                "localIp": "",
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(f"钉钉网关 open 返回 {resp.status_code}")
        data = resp.json()
        endpoint, ticket = data.get("endpoint"), data.get("ticket")
        if not endpoint or not ticket:
            raise RuntimeError("钉钉网关 open 缺少 endpoint/ticket")
        sep = "&" if "?" in endpoint else "?"
        return f"{endpoint}{sep}ticket={ticket}"

    async def run(self) -> None:
        """收帧循环：SYSTEM 必回 pong、CALLBACK 先 ACK 后异步投递业务。"""
        while True:
            try:
                raw = await self._conn.recv()
            except Exception as e:  # noqa: BLE001
                self._on_state("offline", f"连接断开：{e}")
                return
            try:
                frame = json.loads(raw)
            except ValueError:
                continue
            ftype = frame.get("type", "")
            headers = frame.get("headers") or {}
            if ftype == "SYSTEM":
                topic = headers.get("topic", "")
                if topic == "ping":
                    await self._reply_ok(headers, {"opaque": frame.get("data", "")})
                continue
            if ftype == "CALLBACK" and headers.get("topic") == DINGTALK_ROBOT_TOPIC:
                inbound = self._parse(frame)
                # 官方铁律：先 ACK（2s 内），业务异步做
                await self._reply_ok(headers, {})
                if inbound is not None:
                    await self._on_inbound(inbound)
                continue
            # EVENT 等其它帧不影响消息管线

    @staticmethod
    def _parse(frame: dict[str, Any]) -> InboundMessage | None:
        try:
            data = json.loads(frame.get("data") or "{}")
        except ValueError:
            return None
        text = ((data.get("text") or {}).get("content") or "").strip()
        conv_id = data.get("conversationId") or ""
        if not conv_id or not text:
            return None
        conv_type = "group" if str(data.get("conversationType")) == "2" else "dm"
        return InboundMessage(
            platform="dingtalk",
            external_conv_id=str(conv_id),
            conv_type=conv_type,
            text=text,
            external_msg_id=str(data.get("msgId") or frame.get("headers", {}).get("messageId", "")),
            display_name=data.get("conversationTitle"),
            # sessionWebhook：有效期内可直接 POST 回消息（send 用）
            raw={"session_webhook": data.get("sessionWebhook")},
        )

    async def _reply_ok(self, headers: dict[str, str], data: dict[str, Any]) -> None:
        await self._conn.send(
            json.dumps(
                {
                    "code": 200,
                    "headers": {
                        "messageId": headers.get("messageId", ""),
                        "contentType": headers.get("contentType", "application/json"),
                    },
                    "message": "OK",
                    "data": "{}",
                }
            )
        )

    async def send(
        self, conversation_id: str, text: str, session_webhook: str | None = None
    ) -> None:
        """出站：优先 sessionWebhook（有效期内直接 POST，官方推荐被动回复通道）。

        webhook 是 http 而非 WS——manager 注入 http 发送函数；此处协议层只留接口。
        """
        raise NotImplementedError("钉钉出站走 sessionWebhook HTTP，由 manager 注入发送实现")

    async def close(self) -> None:
        with __import__("contextlib").suppress(Exception):
            await self._conn.aclose()


# manager 注入的出站发送函数类型
DingTalkOutbound = Callable[[str, str], Awaitable[None]]  # (session_webhook, text)
