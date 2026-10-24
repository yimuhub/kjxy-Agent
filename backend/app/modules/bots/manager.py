"""进程内机器人连接管家（BotManager）。

职责：按 BotConfig 起停各平台长连接协程；**多实例部署由 Redis 选主**——
只有持有 ``bot:leader:<bot_id>`` 锁（SET NX EX + 周期续租）的进程才建连，
杜绝企微「单机器人单连接」互踢。Redis 不可用（桌面单机/测试）时退化为
本进程直接持有（单机没有竞争者，语义等价）。

测试策略：本模块的协程主体在测试环境不启动（app_env=test 或 BOT_MANAGER_DISABLED
直接跳过 resume）；选主逻辑单独用假 redis（内存 dict）测；连接协程逻辑在
protocol/dingtalk 层用假连接测——manager 只做编排。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from typing import Any

from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.modules.bots import service
from app.modules.bots.models import BotConfig

log = logging.getLogger(__name__)


class LeaderGate:
    """Redis SET NX 选主 + 周期续租；无 redis 时恒为主（单机退化）。"""

    def __init__(self, redis_client: Any, bot_id: str, *, ttl: int, renew: float) -> None:
        self._redis = redis_client
        self._key = f"bot:leader:{bot_id}"
        self._token = uuid.uuid4().hex
        self._ttl = ttl
        self._renew = renew
        self._task: asyncio.Task[None] | None = None

    async def acquire(self) -> bool:
        ok = await asyncio.to_thread(self._redis.set, self._key, self._token, nx=True, ex=self._ttl)
        return bool(ok)

    async def start_renew(self) -> None:
        async def _loop() -> None:
            while True:
                await asyncio.sleep(self._renew)
                try:
                    held = await asyncio.to_thread(self._redis.get, self._key)
                    if (
                        held is None
                        or (
                            isinstance(held, str | bytes) and held.decode()
                            if isinstance(held, bytes)
                            else held
                        )
                        != self._token
                    ):
                        # 锁易主：本进程必须立刻停连（可能其它实例接管）
                        log.warning("bot leader lock lost: %s", self._key)
                        return
                    await asyncio.to_thread(self._redis.expire, self._key, self._ttl)
                except Exception as e:  # noqa: BLE001 redis 抖动：等下一轮
                    log.debug("leader renew failed: %s", e)

        self._task = asyncio.create_task(_loop())

    async def release(self) -> None:
        if self._task:
            self._task.cancel()
        with contextlib.suppress(Exception):
            held = await asyncio.to_thread(self._redis.get, self._key)
            val = held.decode() if isinstance(held, bytes) else held
            if val == self._token:
                await asyncio.to_thread(self._redis.delete, self._key)


def _redis_or_none() -> Any:
    try:
        import redis  # 项目既有依赖（celery 传递）

        return redis.Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    except Exception:  # noqa: BLE001
        return None


def _make_inbound_handler(bot: BotConfig, guard: Any, send: Any) -> Any:
    """入站回调工厂：平台适配器收到消息 → 独立会话跑 gateway 管线。"""

    async def on_inbound(msg: Any) -> None:  # noqa: ANN401
        from app.db.session import SessionLocal as _SL
        from app.modules.bots.gateway import handle_inbound
        from app.modules.bots.service import list_public_names

        sdb = _SL()
        try:
            await handle_inbound(
                sdb,
                bot=bot,
                msg=msg,
                guard=guard,
                send=send,
                list_candidates=lambda tid: list_public_names(sdb, tid),
            )
        finally:
            sdb.close()

    return on_inbound


class BotManager:
    """进程内单例：bot_id → 运行任务句柄。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._gates: dict[str, LeaderGate] = {}
        self._redis = None

    @property
    def disabled(self) -> bool:
        return (
            not settings.bot_manager_enabled
            or os.environ.get("BOT_MANAGER_DISABLED") == "1"
            or settings.app_env == "test"
        )

    def running(self) -> list[str]:
        return sorted(self._tasks)

    async def resume_enabled_bots(self) -> None:
        """startup 钩子：拉起所有 enabled 机器人（桌面/服务器通用）。"""
        if self.disabled:
            log.info("BotManager 未启用（测试环境/显式关闭）")
            return
        self._redis = _redis_or_none()
        db = SessionLocal()
        try:
            bots = list(db.scalars(select(BotConfig).where(BotConfig.enabled.is_(True))))
        finally:
            db.close()
        for bot in bots:
            self.spawn(bot.id)

    def spawn(self, bot_id: str) -> None:
        if bot_id in self._tasks or self.disabled:
            return
        self._tasks[bot_id] = asyncio.create_task(self._supervise(bot_id))

    async def stop(self, bot_id: str) -> None:
        task = self._tasks.pop(bot_id, None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        gate = self._gates.pop(bot_id, None)
        if gate:
            await gate.release()

    async def _supervise(self, bot_id: str) -> None:
        """单机器人监督循环：选主 → 建连 → 断线指数退避重连；熔断即退出。"""
        backoff = 1.0
        while True:
            db = SessionLocal()
            try:
                bot = db.get(BotConfig, bot_id)
                if bot is None or not bot.enabled:
                    return
                # 选主：无 redis（单机）恒为主；多实例只有 leader 建连
                gate: LeaderGate | None = None
                if self._redis is not None:
                    gate = LeaderGate(
                        self._redis,
                        bot_id,
                        ttl=settings.bot_leader_lock_ttl_seconds,
                        renew=settings.bot_leader_renew_seconds,
                    )
                    if not await gate.acquire():
                        await asyncio.sleep(settings.bot_leader_lock_ttl_seconds)
                        continue
                    await gate.start_renew()
                    self._gates[bot_id] = gate
                try:
                    await self._run_connection(db, bot)
                    backoff = 1.0  # 正常结束（配置停用等）重置退避
                finally:
                    if gate:
                        await gate.release()
                        self._gates.pop(bot_id, None)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 任何异常都不能杀死监督协程
                log.warning("bot %s 连接异常：%s", bot_id, e)
            finally:
                db.close()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60.0)

    async def _run_connection(self, db, bot: BotConfig) -> None:  # noqa: ANN001
        """按平台建连（真连接路径；测试不进入）。"""
        creds = service.load_credentials(bot)
        from app.modules.bots.gateway import IdempotencyGuard, handle_inbound

        guard = IdempotencyGuard(settings.bot_idempotency_cache_size)

        async def on_inbound(msg):  # noqa: ANN001
            sdb = SessionLocal()
            try:
                # 出站发送函数按平台绑定（真实现走各自的连接对象）
                async def _send(conv_id: str, text: str) -> None:
                    log.info("bot outbound stub %s %s %s", bot.platform, conv_id, text[:40])

                await handle_inbound(
                    sdb,
                    bot=bot,
                    msg=msg,
                    guard=guard,
                    send=_send,
                    list_candidates=lambda tid: service.list_public_names(sdb, tid),
                )
            finally:
                sdb.close()

        def on_state(status: str, reason: str) -> None:
            sdb = SessionLocal()
            try:
                service.record_state(sdb, bot.id, status, reason)
            finally:
                sdb.close()

        if bot.platform == "wecom_aibot":
            import websockets

            from app.modules.bots.gateway import IdempotencyGuard
            from app.modules.bots.protocol import WECOM_WS_URL, WecomAibotAdapter

            guard = IdempotencyGuard(settings.bot_idempotency_cache_size)
            async with websockets.connect(
                settings.bot_wecom_ws_url or WECOM_WS_URL,
                ping_interval=None,
                max_size=1024 * 1024,
            ) as ws:

                class _W:
                    async def send(self, text: str) -> None:
                        await ws.send(text)

                    async def recv(self) -> str:
                        m = await ws.recv()
                        return m if isinstance(m, str) else m.decode()

                    async def aclose(self) -> None:
                        await ws.close()

                holder: dict[str, Any] = {}

                async def _send_wecom(conv_id: str, text: str) -> None:
                    await holder["adapter"].send(conv_id, text)

                handler = _make_inbound_handler(bot, guard, _send_wecom)
                adapter = WecomAibotAdapter(
                    bot_id=str(creds.get("bot_id", "")),
                    secret=str(creds.get("secret", "")),
                    conn=_W(),
                    on_inbound=handler,
                    on_state=on_state,
                )
                holder["adapter"] = adapter
                await adapter.subscribe()

                async def _beat() -> None:
                    while True:
                        await asyncio.sleep(settings.bot_wecom_ping_seconds)
                        with contextlib.suppress(Exception):
                            await adapter.heartbeat()

                beat = asyncio.create_task(_beat())
                try:
                    await adapter.run()
                finally:
                    beat.cancel()
        elif bot.platform == "dingtalk":
            await self._run_dingtalk(bot, creds, on_inbound, on_state)
        elif bot.platform == "feishu":
            from app.modules.bots.feishu_adapter import MISSING_SDK_REASON, FeishuAdapter

            try:
                f_adapter = FeishuAdapter(
                    app_id=str(creds.get("app_id", "")),
                    app_secret=str(creds.get("app_secret", "")),
                    on_inbound=on_inbound,
                    on_state=on_state,
                )
            except RuntimeError as e:
                service.record_state(db, bot.id, "error", str(e) or MISSING_SDK_REASON)
                return
            await f_adapter.run()

    async def _run_dingtalk(self, bot, creds, on_inbound, on_state) -> None:  # noqa: ANN001
        """钉钉：HTTP open 换 endpoint → websockets 建连 → 帧循环。"""
        import httpx
        import websockets

        from app.modules.bots.dingtalk import DingTalkAdapter

        async with httpx.AsyncClient(timeout=10) as hc:
            adapter_probe = DingTalkAdapter(conn=None, on_inbound=on_inbound, on_state=on_state)  # type: ignore[arg-type]
            ws_url = await adapter_probe.open_connection(
                hc, str(creds.get("client_id", "")), str(creds.get("client_secret", ""))
            )

        class _Wrap:
            def __init__(self, ws) -> None:  # noqa: ANN001
                self._ws = ws

            async def send(self, text: str) -> None:
                await self._ws.send(text)

            async def recv(self) -> str:
                m = await self._ws.recv()
                return m if isinstance(m, str) else m.decode()

            async def aclose(self) -> None:
                await self._ws.close()

        async with websockets.connect(ws_url, ping_interval=None, max_size=1024 * 1024) as ws:
            from app.modules.bots.gateway import IdempotencyGuard as _Guard

            state: dict[str, Any] = {"webhook": None}

            async def _send_dd(_conv_id: str, text: str) -> None:
                wh = state.get("webhook")
                if not wh:
                    log.info("钉钉 sessionWebhook 缺失，消息未送达：%s", text[:40])
                    return
                import httpx

                async with httpx.AsyncClient(timeout=10) as hc2:
                    await hc2.post(wh, json={"msgtype": "text", "text": {"content": text[:2000]}})

            base_handler = _make_inbound_handler(
                bot, _Guard(settings.bot_idempotency_cache_size), _send_dd
            )

            async def _capture(inbound):  # noqa: ANN001
                wh = (inbound.raw or {}).get("session_webhook")
                if wh:
                    state["webhook"] = wh
                await base_handler(inbound)

            adapter = DingTalkAdapter(conn=_Wrap(ws), on_inbound=_capture, on_state=on_state)
            on_state("online", "")
            await adapter.run()


manager = BotManager()
