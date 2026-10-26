"""多助手群聊 API 集成测试：名单端点 + 三种策略的同步/SSE 链路。

覆盖：
1. 名单：PUT 覆盖 + GET 回读（顺序、名称快照）、非本小院助手 400、越权 404；
   private 助手按请求者可见性放行——创建者能拉进自己的名单，他人一律 400；
2. 策略 all：N 位成员 = N 条 member-event + N 条 message 帧 + N 行助手消息；
3. 策略 named：恰好 1 条回答，点名的另一位不打模型；
4. 策略 auto：对口者开口、判退者留下"为什么不答"的步骤与说明消息；
5. 部分失败：一位成员助手已下架 → 另一位照常回答 + "某位未回答：<原因>"；
6. 会话生命周期：请求级 Session 在返回响应时即关闭（生产时序），群聊流照跑通。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.core.security import create_access_token
from app.modules.chat import group
from app.modules.yards.repository import create_yard
from sqlalchemy import select

CHAT = "/api/v1/chat/sessions"


@pytest.fixture(autouse=True)
def _stream_session_factory(db_session):
    """流与群聊共用同一会话工厂注入点（生产为 SessionLocal，这里换成内存库）。"""
    from app.modules.chat import stream as chat_stream
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
    original = chat_stream._session_factory
    chat_stream._session_factory = factory
    try:
        yield factory
    finally:
        chat_stream._session_factory = original


def _hdr(user_id: str = "u-group", yard_id: str | None = None, roles: list[str] | None = None):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=roles or ["yard_admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    y = create_yard(db_session, name="群聊集成小院")
    db_session.commit()
    return y


def _tid(y) -> str:
    return y if isinstance(y, str) else y.id


def _mk_session(client, yard, user_id: str = "u-group") -> str:
    resp = client.post(CHAT, json={}, headers=_hdr(user_id, _tid(yard)))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _publish_agent(client, yard, code: str, name: str, desc: str) -> str:
    created = client.post(
        "/api/v1/agents/declarative",
        json={
            "agent_code": code,
            "name": name,
            "description": desc,
            "persona_prompt": f"你是{name}。",
            "task_prompt_template": f"{name}请回答：{{{{question}}}}",
            "knowledge_scopes": ["yard"],
            "allowed_tools": [],
        },
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert created.status_code == 200, created.text
    aid = created.json()["data"]["id"]
    pub = client.post(f"/api/v1/agents/declarative/{aid}/publish", headers=_hdr(yard_id=_tid(yard)))
    assert pub.status_code == 200, pub.text
    return aid


def _set_participants(client, yard, sid: str, agent_ids: list[str]) -> list[dict[str, Any]]:
    resp = client.put(
        f"{CHAT}/{sid}/participants",
        json={"agent_ids": agent_ids},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["items"]


def _sse_frames(text: str) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in text.split("\n\n"):
        event = ""
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].strip())
        if not event or not data_lines:
            continue
        try:
            frames.append((event, json.loads("\n".join(data_lines))))
        except json.JSONDecodeError:
            frames.append((event, {"_raw": "\n".join(data_lines)}))
    return frames


def _two_agents(client, yard) -> tuple[str, str]:
    a1 = _publish_agent(client, yard, "grp_irrig", "灌溉顾问", "负责灌溉浇水与水肥安排")
    a2 = _publish_agent(client, yard, "grp_market", "行情助手", "负责价格行情与批发")
    return a1, a2


# ---------------------------------------------------------------------------
# 名单端点
# ---------------------------------------------------------------------------


def test_participants_put_get_keeps_order_and_name_snapshot(client, yard):
    sid = _mk_session(client, yard)
    a1, a2 = _two_agents(client, yard)
    items = _set_participants(client, yard, sid, [a2, a1])
    assert [p["agent_name"] for p in items] == ["行情助手", "灌溉顾问"]
    assert [p["position"] for p in items] == [1, 2]

    got = client.get(f"{CHAT}/{sid}/participants", headers=_hdr(yard_id=yard.id))
    assert got.status_code == 200
    assert [p["agent_id"] for p in got.json()["data"]["items"]] == [a2, a1]

    # 覆盖式更新：只留一位
    items2 = _set_participants(client, yard, sid, [a1])
    assert [p["agent_id"] for p in items2] == [a1]


def test_participants_reject_foreign_yard_agent(client, yard, db_session):
    other = create_yard(db_session, name="隔壁小院")
    db_session.commit()
    foreign = _publish_agent(client, other, "grp_other", "隔壁助手", "通用问答")
    sid = _mk_session(client, yard)
    resp = client.put(
        f"{CHAT}/{sid}/participants",
        json={"agent_ids": [foreign]},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "GROUP_PARTICIPANT_INVALID"


def test_participants_of_foreign_yard_session_are_unreachable(client, yard, db_session):
    """跨小院会话不可达：另一小院同名用户拿到的是 404（防枚举）。"""
    other = create_yard(db_session, name="对手小院")
    db_session.commit()
    sid = _mk_session(client, yard)
    a1, _ = _two_agents(client, yard)

    hdr_other = _hdr("u-group", other.id)
    got = client.get(f"{CHAT}/{sid}/participants", headers=hdr_other)
    assert got.status_code == 404
    assert got.json()["error"]["code"] == "CHAT_SESSION_NOT_FOUND"
    put = client.put(f"{CHAT}/{sid}/participants", json={"agent_ids": [a1]}, headers=hdr_other)
    assert put.status_code == 404
    turn = client.post(
        f"{CHAT}/{sid}/messages/group",
        json={"content": "今天浇水吗", "policy": "all"},
        headers=hdr_other,
    )
    assert turn.status_code == 404
    stream = client.post(
        f"{CHAT}/{sid}/messages/group/stream",
        json={"content": "今天浇水吗", "policy": "all"},
        headers=hdr_other,
    )
    assert stream.status_code == 404


def test_participants_are_private_to_creator_even_in_same_yard(client, yard):
    sid = _mk_session(client, yard)
    a1, _ = _two_agents(client, yard)
    other = _hdr("u-peer", yard.id)
    assert client.get(f"{CHAT}/{sid}/participants", headers=other).status_code == 404
    assert (
        client.put(
            f"{CHAT}/{sid}/participants", json={"agent_ids": [a1]}, headers=other
        ).status_code
        == 404
    )


def test_participants_accept_the_creators_own_private_agent(client, yard):
    """创建者把自己"只能自己使用"的已发布助手拉进群聊名单：可见即可路由。

    执行侧 ask_directed 按请求者 user_id 取候选，名单校验若不带身份会出现
    "自己建的小助手永远进不了群聊"——这里把两侧口径锁死。
    """
    member = "u-group-owner"
    hdr = _hdr(member, yard.id, roles=["viewer"])
    created = client.post(
        "/api/v1/agents/declarative",
        json={
            "agent_code": "grp_private",
            "name": "我的私有助手",
            "description": "只给自己用的群聊成员",
            "persona_prompt": "你是私有测试助手。",
            "task_prompt_template": "请回答：{{question}}",
            "knowledge_scopes": ["yard"],
            "allowed_tools": ["knowledge.search"],
        },
        headers=hdr,
    )
    assert created.status_code == 200, created.text
    body = created.json()["data"]
    assert body["visibility"] == "private"
    aid = body["id"]
    assert client.post(f"/api/v1/agents/declarative/{aid}/publish", headers=hdr).status_code == 200

    sid = _mk_session(client, yard, member)
    put = client.put(f"{CHAT}/{sid}/participants", json={"agent_ids": [aid]}, headers=hdr)
    assert put.status_code == 200, put.text
    assert [p["agent_id"] for p in put.json()["data"]["items"]] == [aid]

    got = client.get(f"{CHAT}/{sid}/participants", headers=hdr)
    assert [p["agent_id"] for p in got.json()["data"]["items"]] == [aid]


def test_participants_reject_another_members_private_agent(client, yard):
    """他人（含管理员）看不见 private 助手 → 拉进群聊名单一律 400。"""
    owner = "u-group-priv-owner"
    owner_hdr = _hdr(owner, yard.id, roles=["viewer"])
    created = client.post(
        "/api/v1/agents/declarative",
        json={
            "agent_code": "grp_hidden",
            "name": "别人的私有助手",
            "description": "通用问答",
            "persona_prompt": "你是私有测试助手。",
            "task_prompt_template": "请回答：{{question}}",
            "knowledge_scopes": ["yard"],
            "allowed_tools": [],
        },
        headers=owner_hdr,
    )
    aid = created.json()["data"]["id"]
    assert (
        client.post(f"/api/v1/agents/declarative/{aid}/publish", headers=owner_hdr).status_code
        == 200
    )

    sid = _mk_session(client, yard)
    resp = client.put(
        f"{CHAT}/{sid}/participants", json={"agent_ids": [aid]}, headers=_hdr(yard_id=yard.id)
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "GROUP_PARTICIPANT_INVALID"


# ---------------------------------------------------------------------------
# 同步端点：策略 all / named
# ---------------------------------------------------------------------------


def test_group_sync_policy_all_returns_one_message_per_member(client, yard):
    sid = _mk_session(client, yard)
    a1, a2 = _two_agents(client, yard)
    _set_participants(client, yard, sid, [a1, a2])

    resp = client.post(
        f"{CHAT}/{sid}/messages/group",
        json={"content": "今天地里需要浇水吗", "policy": "all"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["member_count"] == 2
    assert len(data["messages"]) == 2 and data["not_answered"] == [] and data["notice"] is None
    assert {m["choice"]["id"] for m in data["messages"]} == {a1, a2}
    assert all(m["routed_by"] == "bound" and m["status"] == "ok" for m in data["messages"])

    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id)).json()["data"][
        "items"
    ]
    assert [h["role"] for h in hist] == ["user", "assistant", "assistant"]
    assert [h["seq"] for h in hist] == [1, 2, 3]


def test_group_sync_policy_named_answers_exactly_once(client, yard):
    sid = _mk_session(client, yard)
    a1, a2 = _two_agents(client, yard)
    _set_participants(client, yard, sid, [a1, a2])

    resp = client.post(
        f"{CHAT}/{sid}/messages/group",
        json={"content": "最近玉米什么价", "policy": "named", "target": "行情助手"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data["messages"]) == 1
    assert data["messages"][0]["choice"]["id"] == a2
    assert data["notice"] is None


def test_group_sync_named_target_not_in_roster_is_404(client, yard):
    sid = _mk_session(client, yard)
    a1, a2 = _two_agents(client, yard)
    _set_participants(client, yard, sid, [a1])
    resp = client.post(
        f"{CHAT}/{sid}/messages/group",
        json={"content": "最近玉米什么价", "policy": "named", "target": a2},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "GROUP_TARGET_NOT_PARTICIPANT"


def test_group_turn_without_roster_is_400_and_leaves_no_user_message(client, yard):
    """策略/名单先校验再落发言：不合法请求不该留下一条没人回答的用户消息。"""
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages/group",
        json={"content": "今天浇水吗", "policy": "all"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "GROUP_NO_PARTICIPANTS"
    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id)).json()["data"][
        "items"
    ]
    assert hist == []


def test_group_named_without_target_is_400(client, yard):
    sid = _mk_session(client, yard)
    a1, _ = _two_agents(client, yard)
    _set_participants(client, yard, sid, [a1])
    resp = client.post(
        f"{CHAT}/{sid}/messages/group",
        json={"content": "今天浇水吗", "policy": "named"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 400  # Pydantic 校验 → 统一 INVALID_REQUEST


# ---------------------------------------------------------------------------
# SSE 流：三种策略 + 部分失败
# ---------------------------------------------------------------------------


def test_group_stream_policy_all_emits_member_events_and_messages(client, yard):
    sid = _mk_session(client, yard)
    a1, a2 = _two_agents(client, yard)
    _set_participants(client, yard, sid, [a1, a2])

    resp = client.post(
        f"{CHAT}/{sid}/messages/group/stream",
        json={"content": "今天地里需要浇水吗", "policy": "all"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    frames = _sse_frames(resp.text)
    events = [e for e, _ in frames]
    assert events[0] == "chat-init"
    assert events[-1] == "chat-end" and frames[-1][1]["status"] == "ok"
    ok_events = [d for e, d in frames if e == "member-event" and d["status"] == group.MEMBER_OK]
    assert len(ok_events) == 2
    assert {d["agent_id"] for d in ok_events} == {a1, a2}
    messages = [d for e, d in frames if e == "message"]
    assert len(messages) == 2
    assert {m["choice"]["id"] for m in messages} == {a1, a2}
    step_frames = [d for e, d in frames if e == "step"]
    assert step_frames and step_frames[-1]["steps"]

    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id)).json()["data"][
        "items"
    ]
    assert [h["role"] for h in hist] == ["user", "assistant", "assistant"]


def test_group_stream_policy_named_single_message(client, yard):
    sid = _mk_session(client, yard)
    a1, a2 = _two_agents(client, yard)
    _set_participants(client, yard, sid, [a1, a2])
    resp = client.post(
        f"{CHAT}/{sid}/messages/group/stream",
        json={"content": "最近玉米什么价", "policy": "named", "target": a1},
        headers=_hdr(yard_id=yard.id),
    )
    frames = _sse_frames(resp.text)
    assert len([d for e, d in frames if e == "message"]) == 1
    assert len([d for e, d in frames if e == "member-event" and d["status"] == "ok"]) == 1


def test_group_stream_auto_records_decline_reason(client, yard):
    """auto：fake 模型给不出可解析的自判 JSON → 走关键词兜底，判退者必须留原因。"""
    sid = _mk_session(client, yard)
    a1, a2 = _two_agents(client, yard)
    _set_participants(client, yard, sid, [a1, a2])
    resp = client.post(
        f"{CHAT}/{sid}/messages/group/stream",
        json={"content": "今天地里需要浇水吗", "policy": "auto"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    frames = _sse_frames(resp.text)
    messages = [d for e, d in frames if e == "message"]
    assert len(messages) >= 1  # 灌溉顾问开口，行情助手判退
    assert {m["choice"]["id"] for m in messages if m["routed_by"] == "bound"} == {a1}
    notice = next(m for m in messages if m["routed_by"] == group.ROUTED_GROUP)
    assert "「行情助手」未回答" in notice["content"]
    steps_text = "；".join(str(s["detail"]) for s in notice["steps"])
    assert "自判：不回答" in steps_text and "简介与问题不相关" in steps_text


def test_group_stream_partial_provider_failure(client, yard, db_session):
    """一位成员的助手下架 → 另一位照常回答，缺席者有诚实说明，不 500 不静默。"""
    sid = _mk_session(client, yard)
    a1, a2 = _two_agents(client, yard)
    _set_participants(client, yard, sid, [a1, a2])

    from app.modules.agents.models import STATUS_DEPRECATED, YardAgent

    dead = db_session.scalar(select(YardAgent).where(YardAgent.id == a2))
    dead.status = STATUS_DEPRECATED
    db_session.commit()

    resp = client.post(
        f"{CHAT}/{sid}/messages/group/stream",
        json={"content": "今天地里需要浇水吗", "policy": "all"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    frames = _sse_frames(resp.text)
    assert len([d for e, d in frames if e == "message" and d["routed_by"] == "bound"]) == 1
    err = [d for e, d in frames if e == "member-event" and d["status"] == group.MEMBER_ERROR]
    assert len(err) == 1 and err[0]["agent_id"] == a2
    notice = next(d for e, d in frames if e == "message" and d["routed_by"] == group.ROUTED_GROUP)
    assert "「行情助手」未回答：" in notice["content"]
    assert frames[-1] == ("chat-end", {"status": "ok"})


def test_group_stream_survives_request_session_teardown(db_session, yard):
    """回归 §12.2 第 3 条：请求级 Session 在返回 Response 时即关闭，群聊流必须自持连接。"""
    from app.core.deps import get_db_session
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()

    def override_db():
        try:
            yield db_session
        finally:
            db_session.close()

    app.dependency_overrides[get_db_session] = override_db
    yard_id = _tid(yard)
    with TestClient(app, raise_server_exceptions=False) as strict:
        a1, a2 = _two_agents(strict, yard_id)
        sid = _mk_session(strict, yard_id)
        _set_participants(strict, yard_id, sid, [a1, a2])
        resp = strict.post(
            f"{CHAT}/{sid}/messages/group/stream",
            json={"content": "今天地里需要浇水吗", "policy": "all"},
            headers=_hdr(yard_id=yard_id),
        )
        assert resp.status_code == 200, resp.text
        frames = _sse_frames(resp.text)
        assert frames[0][0] == "chat-init"
        assert len([d for e, d in frames if e == "message"]) == 2
        assert frames[-1][1]["status"] == "ok"


# ---------------------------------------------------------------------------
# 会话类型标识（迁移 0028）：普通对话 / 锁定智能体 / 群聊
# ---------------------------------------------------------------------------


def test_session_identity_for_plain_agent_and_group(client, yard):
    # 普通对话：auto，无锁定对象、无成员
    plain = client.post(CHAT, json={"title": "随便聊聊"}, headers=_hdr(yard_id=_tid(yard)))
    assert plain.status_code == 200, plain.text
    p = plain.json()["data"]
    assert p["target"] == "auto"
    assert p["target_code"] is None
    assert p["target_name"] is None
    assert p["participant_count"] == 0

    # 锁定某个智能体：列表要能直接显示助手展示名（而不是编码）
    _publish_agent(client, yard, "kind_agent", "标识助手", "用于验证会话标识")
    agent = client.post(
        CHAT,
        json={"title": "与标识助手对话", "target": "agent", "target_code": "kind_agent"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert agent.status_code == 200, agent.text
    a = agent.json()["data"]
    assert a["target"] == "agent"
    assert a["target_code"] == "kind_agent"
    assert a["target_name"] == "标识助手"

    # 群聊：名单有成员即群聊（与 target 取值正交），回读列表带成员数
    sid = _mk_session(client, yard)
    a1 = _publish_agent(client, yard, "kind_g1", "群聊甲", "甲")
    a2 = _publish_agent(client, yard, "kind_g2", "群聊乙", "乙")
    _set_participants(client, yard, sid, [a1, a2])
    rows = client.get(CHAT, headers=_hdr(yard_id=_tid(yard))).json()["data"]["items"]
    row = next(r for r in rows if r["id"] == sid)
    assert row["participant_count"] == 2


def test_session_identity_rejects_unknown_target(client, yard):
    resp = client.post(
        CHAT,
        json={"title": "x", "target": "group", "target_code": "a"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    # 统一校验错误处理器把 422 归一为 400（错误体仍是标准 envelope）
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"
