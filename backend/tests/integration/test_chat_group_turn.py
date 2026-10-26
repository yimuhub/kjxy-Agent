"""群聊**接线**集成测试：主消息端点带 policy 即走群聊编排。

与 ``tests/integration/test_yard_group_chat_api.py`` 的分工：那边验群聊专用端点
与三种策略本身的编排；这里验**接线**——同一条 ``POST /{id}/messages``（及其
stream 版）在带/不带 ``policy`` 时分别落到群聊与单聊两条链路，且不带时的响应
形状与历史逐字一致（零回归是硬要求）。

覆盖：
1. 名单 PUT/GET：幂等覆盖、position 排序回读、他人会话 404（不泄露存在性）；
2. ``policy=all``：每位成员一条助手消息，全部 ``routed_by='bound'``；
3. ``policy=named``：只点中那位答；target 缺失由 schema 400；点名不在名单 404
   ``GROUP_TARGET_NOT_PARTICIPANT`` 且**不留**一条没人回答的用户发言；
   ``target_code`` 可代 ``target`` 完成点名（前端 @ 面板只有一套字段）；
4. ``policy=auto``：简介不对口的成员判退，判退理由进 **步骤** 也进"未回答"
   说明消息（绝不静默缺人）；
5. 群聊流帧序：``chat-init`` → ``step``/``member-event`` → ``message``* → ``chat-end``。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from app.core.security import create_access_token
from app.modules.chat import group
from app.modules.yards.repository import create_yard

CHAT = "/api/v1/chat/sessions"


@pytest.fixture(autouse=True)
def _stream_session_factory(db_session):
    """群聊成员任务自开短连接：注入同一内存库的会话工厂（与生产同机制）。

    ``group._get_session_factory`` 缺省回落到 ``chat.stream`` 的注入点，因此换
    这一处即可让同步/流式两条链路都跑在测试库上。
    """
    from app.modules.chat import stream as chat_stream
    from sqlalchemy.orm import sessionmaker

    factory = sessionmaker(bind=db_session.get_bind(), autoflush=False, autocommit=False)
    original = chat_stream._session_factory
    chat_stream._session_factory = factory
    try:
        yield factory
    finally:
        chat_stream._session_factory = original


def _hdr(user_id: str = "u-grp", yard_id: str | None = None, roles: list[str] | None = None):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=roles or ["yard_admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    y = create_yard(db_session, name="群聊接线小院")
    db_session.commit()
    return y


def _tid(yard) -> str:
    return yard if isinstance(yard, str) else yard.id


def _mk_session(client, yard, user_id: str = "u-grp") -> str:
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
            "task_prompt_template": "回答：{{question}}",
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


@pytest.fixture()
def roster(client, yard) -> dict[str, str]:
    """两位已发布助手：一位灌溉对口、一位行情不对口（auto 判退用后者）。"""
    return {
        "irrig": _publish_agent(client, yard, "grp_irrig", "灌溉顾问", "负责灌溉浇水与水肥安排"),
        "market": _publish_agent(client, yard, "grp_market", "行情助手", "负责价格批发行情"),
    }


def _put_roster(client, yard, sid: str, ids: list[str], user_id: str = "u-grp"):
    return client.put(
        f"{CHAT}/{sid}/participants", json={"agent_ids": ids}, headers=_hdr(user_id, _tid(yard))
    )


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


# ---------------------------------------------------------------------------
# 名单接口：幂等 + 排序 + 私有边界
# ---------------------------------------------------------------------------


def test_participants_put_is_idempotent_and_ordered(client, yard, roster):
    sid = _mk_session(client, yard)
    first = _put_roster(client, yard, sid, [roster["market"], roster["irrig"]])
    assert first.status_code == 200, first.text
    items = first.json()["data"]["items"]
    assert [p["agent_name"] for p in items] == ["行情助手", "灌溉顾问"]
    assert [p["position"] for p in items] == [1, 2]

    # 同一份名单再 PUT 一次：结果一致（覆盖语义，不追加、不重复）
    again = _put_roster(client, yard, sid, [roster["market"], roster["irrig"]])
    assert [p["agent_id"] for p in again.json()["data"]["items"]] == [
        roster["market"],
        roster["irrig"],
    ]
    got = client.get(f"{CHAT}/{sid}/participants", headers=_hdr(yard_id=_tid(yard)))
    assert got.status_code == 200
    assert got.json()["data"]["items"] == again.json()["data"]["items"]

    # 换顺序 = 改排序；重复 id 去重
    reordered = _put_roster(client, yard, sid, [roster["irrig"], roster["irrig"], roster["market"]])
    assert [p["agent_id"] for p in reordered.json()["data"]["items"]] == [
        roster["irrig"],
        roster["market"],
    ]
    # 空数组 = 清空名单（PUT 语义的自然结果）
    emptied = _put_roster(client, yard, sid, [])
    assert emptied.json()["data"]["items"] == []


def test_participants_reject_unpublished_and_foreign_yard(client, yard, roster):
    sid = _mk_session(client, yard)
    resp = _put_roster(client, yard, sid, [roster["irrig"], "not-an-agent"])
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "GROUP_PARTICIPANT_INVALID"
    # 校验失败不写坏名单
    assert (
        client.get(f"{CHAT}/{sid}/participants", headers=_hdr(yard_id=_tid(yard))).json()["data"][
            "items"
        ]
        == []
    )


def test_participants_of_others_session_are_404(client, yard, roster):
    sid = _mk_session(client, yard, user_id="u-grp")
    other = _hdr("u-other", _tid(yard), ["yard_admin"])
    assert client.get(f"{CHAT}/{sid}/participants", headers=other).status_code == 404
    assert _put_roster(client, yard, sid, [roster["irrig"]], user_id="u-other").status_code == 404
    # 404 口径统一 CHAT_SESSION_NOT_FOUND：不泄露"这个会话存在"
    assert (
        client.get(f"{CHAT}/{sid}/participants", headers=other).json()["error"]["code"]
        == "CHAT_SESSION_NOT_FOUND"
    )


# ---------------------------------------------------------------------------
# /messages 带 policy → 群聊编排
# ---------------------------------------------------------------------------


def test_messages_without_policy_keeps_single_chat_shape(client, yard, roster):
    """零回归：不带 policy 的请求形状与响应字段和单聊时代一致。"""
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "今天地里需要浇水吗"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert set(data) == {"session", "user_message", "assistant_message"}
    assert data["assistant_message"]["routed_by"] in ("rules", "llm", "fallback")


def test_messages_with_policy_all_writes_one_message_per_member(client, yard, roster):
    from app.modules.agents import supervisor

    sid = _mk_session(client, yard)
    assert _put_roster(client, yard, sid, [roster["irrig"], roster["market"]]).status_code == 200
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "今天地里需要浇水吗", "policy": "all"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["policy"] == "all" and data["member_count"] == 2
    assert len(data["messages"]) == 2 and data["not_answered"] == [] and data["notice"] is None
    assert {m["routed_by"] for m in data["messages"]} == {"bound"}
    # 徽章由后端下发：每位成员都拿得到文案与色调
    assert {m["routed_label"] for m in data["messages"]} == {supervisor.routed_label("bound")}
    assert {m["routed_tone"] for m in data["messages"]} == {supervisor.routed_tone("bound")}

    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=_tid(yard))).json()["data"][
        "items"
    ]
    assert [m["role"] for m in hist] == ["user", "assistant", "assistant"]
    assert [m["seq"] for m in hist] == [1, 2, 3]


def test_messages_with_policy_named_only_target_answers(client, yard, roster):
    sid = _mk_session(client, yard)
    _put_roster(client, yard, sid, [roster["irrig"], roster["market"]])
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "看看虫子", "policy": "named", "target": roster["market"]},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert len(data["messages"]) == 1
    assert data["messages"][0]["choice"]["name"] == "行情助手"


def test_messages_named_can_use_target_code_as_the_mention(client, yard, roster):
    """@ 字段与群聊点名字段说的是同一件事：target 缺席时用 target_code 点名。"""
    sid = _mk_session(client, yard)
    _put_roster(client, yard, sid, [roster["irrig"], roster["market"]])
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "最近什么价", "policy": "named", "target_code": "grp_market"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 200, resp.text
    assert [m["choice"]["name"] for m in resp.json()["data"]["messages"]] == ["行情助手"]


def test_messages_named_without_target_is_400(client, yard, roster):
    sid = _mk_session(client, yard)
    _put_roster(client, yard, sid, [roster["irrig"]])
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "随便", "policy": "named"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


def test_messages_named_target_outsider_is_404_and_leaves_no_user_message(client, yard, roster):
    """点名不在名单：404 且**不留**一条没人回答的用户发言（校验先于落库）。"""
    sid = _mk_session(client, yard)
    _put_roster(client, yard, sid, [roster["irrig"]])
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "随便", "policy": "named", "target": "没这个人"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == group_pick_error(resp)
    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=_tid(yard))).json()["data"][
        "items"
    ]
    assert hist == []


def group_pick_error(resp) -> str:
    assert resp.json()["error"]["code"] == "GROUP_TARGET_NOT_PARTICIPANT"
    return "GROUP_TARGET_NOT_PARTICIPANT"


def test_messages_group_without_roster_is_400(client, yard):
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "随便", "policy": "all"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "GROUP_NO_PARTICIPANTS"


def test_messages_with_policy_auto_declines_explainly(client, yard, roster):
    """auto：对口者开口，判退者必须留下可读理由（步骤 + 说明消息），不静默缺人。"""
    sid = _mk_session(client, yard)
    _put_roster(client, yard, sid, [roster["irrig"], roster["market"]])
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "今天地里需要浇水吗", "policy": "auto"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["answered_names"] == ["灌溉顾问"]
    assert [x["name"] for x in data["not_answered"]] == ["行情助手"]
    assert data["not_answered"][0]["status"] == "declined"
    assert "简介与问题不相关" in data["not_answered"][0]["reason"]

    notice = data["notice"]
    assert notice and "「行情助手」未回答" in notice["content"]
    assert notice["routed_by"] == group.ROUTED_GROUP
    assert notice["routed_label"]  # 群聊说明行也有徽章文案（词表已登记）
    # 判退理由进了过程步骤（历史回放看得到"为什么没人答"）
    steps_text = " ".join(str(s.get("detail") or "") for s in notice["steps"])
    assert "行情助手" in steps_text and "不回答" in steps_text


# ---------------------------------------------------------------------------
# /messages/stream 带 policy → 群聊帧序
# ---------------------------------------------------------------------------


def test_messages_stream_with_policy_emits_group_frames(client, yard, roster):
    sid = _mk_session(client, yard)
    _put_roster(client, yard, sid, [roster["irrig"], roster["market"]])
    resp = client.post(
        f"{CHAT}/{sid}/messages/stream",
        json={"content": "今天地里需要浇水吗", "policy": "all"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 200, resp.text
    assert "text/event-stream" in resp.headers.get("content-type", "")
    frames = _sse_frames(resp.text)
    events = [e for e, _ in frames]
    assert events[0] == "chat-init"
    assert events[-1] == "chat-end"
    assert frames[-1][1]["status"] == "ok"
    assert "step" in events and "member-event" in events
    assert events.count("message") == 2

    member_events = [d for e, d in frames if e == "member-event"]
    assert {d["status"] for d in member_events} >= {"running", "ok"}
    first = frames[0][1]
    assert first["session_id"] == sid and first["user_message"]["seq"] == 1
    # 成员事件形状（前端按 member_index 归位头像条）
    ok_events = [d for d in member_events if d["status"] == "ok"]
    assert sorted(d["member_index"] for d in ok_events) == [1, 2]
    assert {d["role_name"] for d in ok_events} == {"灌溉顾问", "行情助手"}
    assert {d["agent_id"] for d in ok_events} == {roster["irrig"], roster["market"]}

    messages = [d for e, d in frames if e == "message"]
    assert all(m["routed_label"] and m["routed_tone"] for m in messages)

    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=_tid(yard))).json()["data"][
        "items"
    ]
    assert [m["role"] for m in hist] == ["user", "assistant", "assistant"]


def test_messages_stream_without_policy_keeps_single_chat_frames(client, yard, roster):
    """零回归：不带 policy 的流不出现 member-event，帧序与历史一致。"""
    sid = _mk_session(client, yard)
    _put_roster(client, yard, sid, [roster["irrig"]])
    resp = client.post(
        f"{CHAT}/{sid}/messages/stream",
        json={"content": "今天地里需要浇水吗"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    frames = _sse_frames(resp.text)
    events = [e for e, _ in frames]
    assert events[0] == "chat-init" and events[-1] == "chat-end"
    assert "member-event" not in events
    assert events.count("message") == 1
    assert frames[-1][1]["status"] == "ok"


def test_messages_group_stream_bad_policy_is_400(client, yard, roster):
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages/stream",
        json={"content": "随便", "policy": "everything"},
        headers=_hdr(yard_id=_tid(yard)),
    )
    assert resp.status_code == 400
