"""机器人网关离线单测：协议帧序 / gateway 编排 / 服务熔断 / 选主锁。

全离线：连接=假连接对象回放 JSON 帧；supervisor=monkeypatch；redis=内存假锁。
"""

import asyncio
import json

import pytest
from app.core.config import settings
from app.modules.bots import gateway as gw
from app.modules.bots import service
from app.modules.bots.dingtalk import DINGTALK_ROBOT_TOPIC, DingTalkAdapter
from app.modules.bots.manager import LeaderGate
from app.modules.bots.models import BotConfig, BotConversation
from app.modules.bots.protocol import InboundMessage, WecomAibotAdapter


class FakeConn:
    """假连接：sent 记录出站帧；脚本化 recv 序列，耗尽后抛 ConnectionError。"""

    def __init__(self, script: list[dict] | None = None) -> None:
        self.sent: list[str] = []
        self._inbox = asyncio.Queue()
        self._script = list(script or [])
        self.closed = False

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def recv(self) -> str:
        if self._script:
            return json.dumps(self._script.pop(0))
        raise ConnectionError("脚本耗尽")

    async def aclose(self) -> None:
        self.closed = True


def _msg_frame(conv="chat1", text="@小助手 棚室要通风吗", mid="m1", ctype="group"):
    return {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgid": mid,
            "chatid": conv,
            "chattype": ctype,
            "chatname": "生产群",
            "msgtype": "text",
            "text": {"content": text},
            "from": {"userid": "u1"},
        },
    }


def _make_bot(**kw) -> BotConfig:  # noqa: ANN001, ANN201
    defaults = dict(
        yard_id="t1",
        platform="wecom_aibot",
        name="测试机器人",
        enabled=True,
        status="offline",
        credentials_enc="x",
        binding_mode="auto",
        require_approval=True,
        consecutive_failures=0,
    )
    defaults.update(kw)
    return BotConfig(**defaults)


# ---------------- 企微 aibot 协议 ----------------


async def test_wecom_subscribe_frames():
    conn = FakeConn([{"cmd": "sub_ack", "code": 0}])
    states: list[tuple] = []
    a = WecomAibotAdapter(
        bot_id="b1",
        secret="s1",
        conn=conn,
        on_inbound=lambda m: asyncio.sleep(0),
        on_state=lambda s, r: states.append((s, r)),
    )
    await a.subscribe()
    sub = json.loads(conn.sent[0])
    assert sub["cmd"] == "aibot_subscribe"
    assert sub["body"] == {"bot_id": "b1", "secret": "s1"}
    assert states == [("online", "")]


async def test_wecom_subscribe_rejected_raises():
    conn = FakeConn([{"cmd": "sub_ack", "code": 40001, "message": "secret 错误"}])
    a = WecomAibotAdapter(
        bot_id="b",
        secret="s",
        conn=conn,
        on_inbound=lambda m: asyncio.sleep(0),
        on_state=lambda s, r: None,
    )
    with pytest.raises(RuntimeError, match="secret 错误"):
        await a.subscribe()


async def test_wecom_msg_callback_to_inbound():
    got: list[InboundMessage] = []

    async def on_in(m):  # noqa: ANN001
        got.append(m)

    conn = FakeConn([_msg_frame()])
    a = WecomAibotAdapter(
        bot_id="b", secret="s", conn=conn, on_inbound=on_in, on_state=lambda s, r: None
    )
    await a.run()
    assert len(got) == 1
    assert got[0].external_conv_id == "chat1"
    assert got[0].conv_type == "group"
    assert got[0].external_msg_id == "m1"


async def test_wecom_disconnected_event_stops():
    states: list[str] = []
    conn = FakeConn([{"cmd": "aibot_event_callback", "body": {"event": {"type": "disconnected"}}}])
    a = WecomAibotAdapter(
        bot_id="b",
        secret="s",
        conn=conn,
        on_inbound=lambda m: asyncio.sleep(0),
        on_state=lambda s, r: states.append(s),
    )
    await a.run()
    assert states == ["error"]


async def test_wecom_send_stream_finish():
    conn = FakeConn([])
    a = WecomAibotAdapter(
        bot_id="b",
        secret="s",
        conn=conn,
        on_inbound=lambda m: asyncio.sleep(0),
        on_state=lambda s, r: None,
    )
    await a.send("chat1", "答案")
    f = json.loads(conn.sent[0])
    assert f["cmd"] == "aibot_respond_msg"
    assert f["body"]["stream"]["finish"] is True
    assert f["body"]["stream"]["content"] == "答案"


async def test_wecom_proactive_send_cmd():
    conn = FakeConn([])
    a = WecomAibotAdapter(
        bot_id="b",
        secret="s",
        conn=conn,
        on_inbound=lambda m: asyncio.sleep(0),
        on_state=lambda s, r: None,
    )
    await a.proactive_send("chat1", "告警")
    assert json.loads(conn.sent[0])["cmd"] == "aibot_send_msg"


# ---------------- 钉钉 Stream ----------------


async def test_ding_open_connection_url(httpx_mock_unused=None):  # noqa: ANN001, ANN001
    import httpx

    async def handler(request):  # noqa: ANN001
        body = json.loads(request.content)
        assert body["subscriptions"][0]["topic"] == DINGTALK_ROBOT_TOPIC
        return httpx.Response(200, json={"endpoint": "wss://ws.ding/long", "ticket": "tk1"})

    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    url = await DingTalkAdapter(
        conn=FakeConn(), on_inbound=lambda m: asyncio.sleep(0), on_state=lambda s, r: None
    ).open_connection(hc, "cid", "cs")
    assert url == "wss://ws.ding/long?ticket=tk1"
    await hc.aclose()


async def test_ding_ping_pong_and_callback_ack():
    frames = [
        {
            "specVersion": "1",
            "type": "SYSTEM",
            "headers": {"topic": "ping", "messageId": "p1"},
            "data": "x",
        },
        {
            "specVersion": "1",
            "type": "CALLBACK",
            "headers": {
                "topic": DINGTALK_ROBOT_TOPIC,
                "messageId": "c1",
                "contentType": "application/json",
            },
            "data": json.dumps(
                {
                    "text": {"content": "湿度多少"},
                    "conversationId": "cid1",
                    "conversationType": "2",
                    "msgId": "dm1",
                    "conversationTitle": "设备群",
                    "sessionWebhook": "https://wh/1",
                }
            ),
        },
    ]

    async def on_in(m):  # noqa: ANN001
        assert m.text == "湿度多少" and m.conv_type == "group"
        assert m.raw["session_webhook"] == "https://wh/1"

    conn = FakeConn(frames)
    a = DingTalkAdapter(conn=conn, on_inbound=on_in, on_state=lambda s, r: None)
    await a.run()
    replies = [json.loads(s) for s in conn.sent]
    assert replies[0]["code"] == 200 and replies[0]["headers"]["messageId"] == "p1"
    assert replies[1]["headers"]["messageId"] == "c1"


# ---------------- LeaderGate 选主（假 redis） ----------------


class FakeRedis:
    def __init__(self):
        self.store: dict = {}

    def set(self, k, v, nx=False, ex=None):  # noqa: ANN001, ANN002, FBT002
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    def get(self, k):  # noqa: ANN001
        return self.store.get(k)

    def expire(self, k, s):  # noqa: ANN001, ANN001
        return True

    def delete(self, k):  # noqa: ANN001
        self.store.pop(k, None)


async def test_leader_gate_single_leader():
    r = FakeRedis()
    g1 = LeaderGate(r, "bot1", ttl=20, renew=15)
    g2 = LeaderGate(r, "bot1", ttl=20, renew=15)
    assert await g1.acquire() is True
    assert await g2.acquire() is False
    await g1.release()
    assert await g2.acquire() is True


# ---------------- gateway 入站编排 ----------------


def _mk_conv_ready(db, bot):  # noqa: ANN001, ANN201
    from datetime import UTC, datetime

    conv = BotConversation(
        yard_id=bot.yard_id,
        bot_config_id=bot.id,
        platform="wecom_aibot",
        external_conv_id="chat1",
        external_conv_type="group",
        approved_at=datetime.now(UTC),
        msg_count=0,
    )
    db.add(conv)
    db.commit()
    return conv


def _inbound(text="棚室要通风吗", mid="m1", conv="chat1"):  # noqa: ANN201, ANN001
    return InboundMessage(
        platform="wecom_aibot",
        external_conv_id=conv,
        conv_type="group",
        external_msg_id=mid,
        text=text,
        display_name="生产群",
    )


def _mk_bot(db, **kw):  # noqa: ANN001, ANN201
    defaults = dict(
        yard_id="t1",
        platform="wecom_aibot",
        name="小助手",
        credentials_enc="x",
        binding_mode="auto",
        require_approval=True,
    )
    defaults.update(kw)
    bot = BotConfig(**defaults)
    db.add(bot)
    db.commit()
    return bot


async def test_gateway_idempotent_drop(db_session):
    bot = _mk_bot(db_session)
    guard = gw.IdempotencyGuard(8)
    sent: list[str] = []

    async def send(cid, text):  # noqa: ANN001
        sent.append(text)

    r1 = await gw.handle_inbound(db_session, bot=bot, msg=_inbound(), guard=guard, send=send)
    r2 = await gw.handle_inbound(db_session, bot=bot, msg=_inbound(), guard=guard, send=send)
    assert r1["status"] == "pending_approval" and r2["status"] == "duplicated"
    assert len(sent) == 1  # 欢迎语只回一次


async def test_gateway_pending_approval_welcome(db_session):
    bot = _mk_bot(db_session)
    sent: list[str] = []

    async def send(cid, text):  # noqa: ANN001
        sent.append(text)

    r = await gw.handle_inbound(
        db_session, bot=bot, msg=_inbound(), guard=gw.IdempotencyGuard(8), send=send
    )
    assert r["status"] == "pending_approval"
    assert "批准" in sent[0]
    # 未批准不进路由：会话无上下文缓存
    conv = db_session.query(BotConversation).one()
    assert not conv.context_cache_json


async def test_gateway_routed_answer_and_ledger(db_session, monkeypatch):
    from app.modules.agents import supervisor

    bot = _mk_bot(db_session, require_approval=False)
    calls: list[str] = []

    async def fake_ask(db, *, ctx, question):  # noqa: ANN001
        calls.append(question)
        return {"result": {"answer": "今日宜通风"}, "routed_by": "auto:助手A", "reason": "关键词"}

    monkeypatch.setattr(supervisor, "ask", fake_ask)
    sent: list[str] = []

    async def send(cid, text):  # noqa: ANN001
        sent.append(text)

    r = await gw.handle_inbound(
        db_session,
        bot=bot,
        msg=_inbound(text="@小助手 棚室要通风吗"),
        guard=gw.IdempotencyGuard(8),
        send=send,
    )
    assert r["status"] == "ok" and sent == ["今日宜通风"]
    # @提及剥离后才进路由
    assert calls == ["棚室要通风吗"]
    logs = db_session.query(gw.BotMessageLog).order_by(gw.BotMessageLog.direction).all()
    assert [m.direction for m in logs] == ["in", "out"]
    import hashlib

    assert logs[0].content_sha12 == hashlib.sha12 if False else len(logs[0].content_sha12) == 12


async def test_gateway_use_list_and_switch(db_session, monkeypatch):
    from app.modules.agents import supervisor

    bot = _mk_bot(db_session, require_approval=False)

    async def fake_ask(db, *, ctx, question):  # noqa: ANN001
        return {"result": {"answer": "A"}, "routed_by": "auto", "reason": ""}

    directed: list = []

    async def fake_directed(db, *, ctx, question, target, prefer_team):  # noqa: ANN001
        directed.append((target, question))
        return {"result": {"answer": "B"}, "routed_by": "bound", "reason": ""}

    monkeypatch.setattr(supervisor, "ask", fake_ask)
    monkeypatch.setattr(supervisor, "ask_directed", fake_directed)
    sent: list[str] = []

    async def send(cid, text):  # noqa: ANN001
        sent.append(text)

    r = await gw.handle_inbound(
        db_session,
        bot=bot,
        msg=_inbound(mid="c1", text="/use"),
        guard=gw.IdempotencyGuard(8),
        send=send,
        list_candidates=lambda t: [{"id": "a1", "name": "种植助手", "kind": "assistant"}],
    )
    assert r["status"] == "use_list" and "种植助手" in sent[0]
    r2 = await gw.handle_inbound(
        db_session,
        bot=bot,
        msg=_inbound(mid="c2", text="/use 种植助手 湿度多少"),
        guard=gw.IdempotencyGuard(8),
        send=send,
    )
    assert r2["status"] == "ok" and directed == [("种植助手", "湿度多少")]


async def test_gateway_context_window_trims(db_session, monkeypatch):
    from app.modules.agents import supervisor

    bot = _mk_bot(db_session, require_approval=False)

    async def fake_ask(db, *, ctx, question):  # noqa: ANN001
        return {"result": {"answer": f"答:{question}"}, "routed_by": "auto", "reason": ""}

    monkeypatch.setattr(supervisor, "ask", fake_ask)

    async def send(cid, text):  # noqa: ANN001
        return None

    guard = gw.IdempotencyGuard(64)
    for i in range(10):
        await gw.handle_inbound(
            db_session, bot=bot, msg=_inbound(mid=f"x{i}", text=f"问{i}"), guard=guard, send=send
        )
    conv = db_session.query(BotConversation).one()
    turns = conv.context_cache_json or []
    # 默认窗口 6 轮 → 最多 12 条消息
    assert len(turns) <= settings.bot_context_window_turns * 2
    assert conv.msg_count == 10


# ---------------- service 熔断 / 凭据 ----------------


def test_record_failure_circuit_opens(db_session, monkeypatch):
    monkeypatch.setattr(settings, "bot_consecutive_failure_limit", 3)
    bot = _mk_bot(db_session)
    for _ in range(3):
        service.record_failure(db_session, bot, "握手超时")
    assert bot.enabled is False and bot.status == "error"
    from app.modules.audit.models import AuditLog

    audit = db_session.query(AuditLog).filter(AuditLog.action == "bot_circuit_open").count()
    assert audit == 1


def test_credentials_roundtrip_and_mask():
    enc = service._dump_credentials({"bot_id": "bid-123456", "secret": "s3cr3t-value"})
    assert "s3cr3t" not in enc
    bot = BotConfig(yard_id="t", platform="wecom_aibot", name="n", credentials_enc=enc)
    assert service.load_credentials(bot) == {"bot_id": "bid-123456", "secret": "s3cr3t-value"}
    masked = service.mask_credentials({"bot_id": "bid-123456", "secret": "s3cr3t-value"})
    assert "*" in masked["secret"] and "s3cr3t" not in masked["secret"]


async def test_wecom_send_stream_increment_finish():
    conn = FakeConn([])
    a = WecomAibotAdapter(
        bot_id="b",
        secret="s",
        conn=conn,
        on_inbound=lambda m: asyncio.sleep(0),
        on_state=lambda s, r: None,
    )
    await a.send_stream("chat1", "今天", stream_id="st1", finish=False)
    await a.send_stream("chat1", "晴", stream_id="st1", finish=True)
    f1, f2 = (json.loads(s) for s in conn.sent)
    assert f1["body"]["stream"] == {"id": "st1", "finish": False, "content": "今天"}
    assert f2["body"]["stream"]["finish"] is True


async def test_ding_open_non_200_raises():
    import httpx

    async def handler(request):  # noqa: ANN001
        return httpx.Response(403, json={"code": "Forbidden"})

    hc = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    a = DingTalkAdapter(
        conn=FakeConn(), on_inbound=lambda m: asyncio.sleep(0), on_state=lambda s, r: None
    )
    with pytest.raises(RuntimeError):
        await a.open_connection(hc, "bad", "creds")
    await hc.aclose()


def test_conversation_unique_constraint(db_session):
    from sqlalchemy.exc import IntegrityError

    bot = _mk_bot(db_session)
    for _ in range(2):
        db_session.add(
            BotConversation(
                yard_id="t1",
                bot_config_id=bot.id,
                platform="wecom_aibot",
                external_conv_id="dup1",
                external_conv_type="dm",
            )
        )
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_prune_stale_context(db_session):
    from datetime import UTC, datetime, timedelta

    bot = _mk_bot(db_session)
    old = BotConversation(
        yard_id="t1",
        bot_config_id=bot.id,
        platform="wecom_aibot",
        external_conv_id="old1",
        external_conv_type="dm",
        context_cache_json=[{"role": "user", "content": "陈旧明文"}],
        last_msg_at=datetime.now(UTC) - timedelta(days=40),
    )
    fresh = BotConversation(
        yard_id="t1",
        bot_config_id=bot.id,
        platform="wecom_aibot",
        external_conv_id="new1",
        external_conv_type="dm",
        context_cache_json=[{"role": "user", "content": "新鲜"}],
        last_msg_at=datetime.now(UTC),
    )
    db_session.add_all([old, fresh])
    db_session.commit()
    assert gw.prune_stale_context(db_session, days=30) == 1
    db_session.refresh(old)
    db_session.refresh(fresh)
    assert old.context_cache_json is None and fresh.context_cache_json


async def test_manager_resume_skipped_in_test_env(db_session):
    from app.modules.bots.manager import BotManager

    m = BotManager()
    assert m.disabled is True  # app_env=test
    await m.resume_enabled_bots()  # 不应触碰 redis/db
    assert m.running() == []


def test_feishu_adapter_requires_sdk():
    from app.modules.bots.feishu_adapter import FeishuAdapter, feishu_available

    if not feishu_available():
        with pytest.raises(RuntimeError, match="lark-oapi"):
            FeishuAdapter(
                app_id="a",
                app_secret="s",
                on_inbound=lambda m: asyncio.sleep(0),
                on_state=lambda s, r: None,
            )
