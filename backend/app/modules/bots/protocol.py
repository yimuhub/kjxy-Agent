"""企微智能机器人 aibot 长连接协议层（出向 WebSocket）。

官方协议（developer.work.weixin.qq.com/document/path/101463，2026-10 现行）：
- 连接 wss://openws.work.weixin.qq.com，用 BotID+Secret 发 ``aibot_subscribe`` 订阅；
- 收 ``aibot_msg_callback``（用户消息）→ 回 ``aibot_respond_msg``（支持流式增量，
  ``finish: true`` 结束本条）；主动推送用 ``aibot_send_msg``（需用户先发过消息）；
- 收 ``aibot_event_callback``（进入会话/模板卡片点击/**连接被踢**）；
- 30s 心跳 ``ping``；**同一机器人同时只允许一条有效连接**，新连接会踢旧连接——
  多实例部署必须由 manager 的 Redis 选主保证单连接（被踢= error 等人工，绝不互踢）。

可测试性：本模块不直接触碰网络库——「连接」是注入的 ``ConnLike``
（send/recv/aclose 三个协程），单测用假连接回放 JSON 帧；manager 用
websockets 库构造真实连接对象适配 ConnLike。全离线，绝不真连外网。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger(__name__)

WECOM_WS_URL = "wss://openws.work.weixin.qq.com"

# 入站回调（gateway 消费）
OnInbound = Callable[["InboundMessage"], Awaitable[None]]
# 状态回填（manager 注入：写 status/status_reason/熔断计数）
OnState = Callable[[str, str], None]  # (status, reason)


class ConnLike(Protocol):
    """最小连接接口：json 帧的 send/recv/aclose（真连接与假连接同形）。"""

    async def send(self, text: str) -> None: ...

    async def recv(self) -> str: ...

    async def aclose(self) -> None: ...


@dataclass
class InboundMessage:
    """归一化入站消息（gateway 的唯一输入形态）。"""

    platform: str
    external_conv_id: str
    conv_type: str  # dm|group
    text: str
    external_msg_id: str
    display_name: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _sha12(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class WecomAibotAdapter:
    """企微 aibot 长连接适配器（单连接；帧协议在此，网络注入）。"""

    platform = "wecom_aibot"
    HEARTBEAT_SECONDS = 30

    def __init__(
        self,
        *,
        bot_id: str,
        secret: str,
        conn: ConnLike,
        on_inbound: OnInbound,
        on_state: OnState,
    ) -> None:
        self._bot_id = bot_id
        self._secret = secret
        self._conn = conn
        self._on_inbound = on_inbound
        self._on_state = on_state
        self._req_seq = 0
        # stream_id → 该条流式回复的累积状态（finish 语义按流 id 终结）
        self._closed_streams: set[str] = set()

    def _next_req_id(self) -> str:
        self._req_seq += 1
        return f"req{self._req_seq:06d}"

    async def subscribe(self) -> None:
        """连接建立后的订阅帧：失败抛异常（由 manager 计熔断）。"""
        await self._conn.send(
            json.dumps(
                {
                    "cmd": "aibot_subscribe",
                    "headers": {"req_id": self._next_req_id()},
                    "body": {"bot_id": self._bot_id, "secret": self._secret},
                },
                ensure_ascii=False,
            )
        )
        ack = json.loads(await self._conn.recv())
        if ack.get("code") not in (0, None):
            raise RuntimeError(f"企微订阅被拒：{ack.get('message') or ack}")
        self._on_state("online", "")

    async def run(self) -> None:
        """收帧主循环（心跳由外部 task 驱动心跳帧，见 heartbeat()）。

        正常退出=连接被关闭；被踢事件置 error 并返回（不再自动重连互踢）。
        """
        while True:
            try:
                raw = await self._conn.recv()
            except Exception as e:  # noqa: BLE001 连接断开：交给 manager 退避重连
                self._on_state("offline", f"连接断开：{e}")
                return
            try:
                frame = json.loads(raw)
            except ValueError:
                log.warning("wecom aibot 收到非 JSON 帧，忽略")
                continue
            cmd = frame.get("cmd", "")
            if cmd == "pong":
                continue
            if cmd == "aibot_msg_callback":
                inbound = self._parse_msg(frame)
                if inbound is not None:
                    with contextlib.suppress(Exception):
                        await self._on_inbound(inbound)
            elif cmd == "aibot_event_callback":
                event = (frame.get("body") or {}).get("event", {})
                etype = event.get("type", "")
                if etype == "disconnected":
                    # 被新连接踢：绝不重连互踢，置 error 等人工排查（manager 已停本实例）
                    self._on_state("error", "另有新连接接入本机器人（单连接限制），已停用")
                    return
                # 进入会话/卡片点击等事件暂不改变消息管线
            elif cmd == "aibot_respond_ack":
                continue
            else:
                log.debug("wecom aibot 未识别帧 cmd=%s 忽略", cmd)

    @staticmethod
    def _parse_msg(frame: dict[str, Any]) -> InboundMessage | None:
        body = frame.get("body") or {}
        msgtype = body.get("msgtype")
        text = ""
        if msgtype == "text":
            text = ((body.get("text") or {}).get("content") or "").strip()
        conv_id = body.get("chatid") or body.get("from", {}).get("userid") or ""
        if not conv_id or not text:
            return None
        conv_type = "group" if body.get("chattype") == "group" else "dm"
        return InboundMessage(
            platform="wecom_aibot",
            external_conv_id=str(conv_id),
            conv_type=conv_type,
            text=text,
            external_msg_id=str(body.get("msgid") or _sha12(text + conv_id)),
            display_name=body.get("chatname"),
            raw=frame,
        )

    async def send(self, conversation_id: str, text: str) -> None:
        """一次性回复（非流式）：直接一条 finish=true。"""
        await self.send_stream(conversation_id, text, stream_id=self._next_req_id(), finish=True)

    async def send_stream(
        self, conversation_id: str, text: str, *, stream_id: str, finish: bool
    ) -> None:
        """流式增量：同 stream_id 连续推送，finish=true 收尾（企微要求主动推 finish）。"""
        if finish:
            self._closed_streams.add(stream_id)
        await self._conn.send(
            json.dumps(
                {
                    "cmd": "aibot_respond_msg",
                    "headers": {"req_id": stream_id},
                    "body": {
                        "chatid": conversation_id,
                        "msgtype": "stream",
                        "stream": {"id": stream_id, "finish": finish, "content": text},
                    },
                },
                ensure_ascii=False,
            )
        )

    async def proactive_send(self, conversation_id: str, text: str) -> None:
        """主动推送（告警播报场景）：aibot_send_msg，用户须先发过消息。"""
        await self._conn.send(
            json.dumps(
                {
                    "cmd": "aibot_send_msg",
                    "headers": {"req_id": self._next_req_id()},
                    "body": {
                        "chatid": conversation_id,
                        "msgtype": "text",
                        "text": {"content": text},
                    },
                },
                ensure_ascii=False,
            )
        )

    async def heartbeat(self) -> None:
        with contextlib.suppress(Exception):
            await self._conn.send(json.dumps({"cmd": "ping"}))

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._conn.aclose()


async def run_wecom_connection(
    *,
    bot_id: str,
    secret: str,
    resume_token: str | None,
    on_inbound: OnInbound,
    on_state: OnState,
    stop: asyncio.Event,
) -> None:
    """真连接入口（manager 调用）：websockets 建连 → 订阅 → 收帧循环 + 心跳。

    本函数在 CI/测试永不被调用（测试全部注入假连接走 WecomAibotAdapter）。
    """
    import websockets  # 惰性导入：主应用不强制需要

    url = f"{WECOM_WS_URL}"
    if resume_token:
        url = f"{url}?resume_token={resume_token}"
    async with websockets.connect(url, ping_interval=None, max_size=1024 * 1024) as ws:

        class _Wrap:
            async def send(self, text: str) -> None:
                await ws.send(text)

            async def recv(self) -> str:
                m = await ws.recv()
                return m if isinstance(m, str) else m.decode()

            async def aclose(self) -> None:
                await ws.close()

        def _state(s: str, r: str) -> None:
            # error 即停：被踢后绝不重连互踢（企微单连接铁律）
            on_state(s, r)
            if s == "error":
                stop.set()

        adapter = WecomAibotAdapter(
            bot_id=bot_id,
            secret=secret,
            conn=_Wrap(),
            on_inbound=on_inbound,
            on_state=_state,
        )
        await adapter.subscribe()

        async def _beat() -> None:
            while not stop.is_set():
                with contextlib.suppress(Exception):
                    await adapter.heartbeat()
                await asyncio.sleep(WecomAibotAdapter.HEARTBEAT_SECONDS)

        beat_task = asyncio.create_task(_beat())
        try:
            await adapter.run()
        finally:
            beat_task.cancel()
