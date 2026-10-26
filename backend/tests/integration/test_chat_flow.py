"""对话模式集成测试（HTTP 端到端，fake provider 全离线）。

覆盖：
1. 主流程：建会话→发消息（规则路由命中已发布助手）→历史 seq 升序、
   assistant 行带走由存证（routed_by/choice）、空标题自动继承首句；
2. 失败诚实：无可路由候选 → HTTP 404，但历史里 user(ok)+assistant(failed)
   两行俱在（error_code 可查）；
3. 私有边界：跨小院、SAME 小院他人一律 404；列表只看自己的；
4. 校验与鉴权：空/空白 content 400、不存在会话 404、未登录 401；
5. 改名与删除（删除连带消息）；
6. 审计：chat_session_created / chat_message_send 落审计。
"""

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.llm import providers_fake
from app.modules.yards.repository import create_yard

CHAT = "/api/v1/chat/sessions"


def _hdr(user_id: str = "u-chat", yard_id: str | None = None):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=["yard_admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _script_isolation():
    providers_fake.reset_scripted_tool_responses()
    yield
    providers_fake.reset_scripted_tool_responses()


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="对话小院")
    db_session.commit()
    return t


@pytest.fixture()
def other_yard(db_session):
    t = create_yard(db_session, name="隔壁对话小院")
    db_session.commit()
    return t


def _publish(client, yard, code: str, name: str, description: str) -> str:
    created = client.post(
        "/api/v1/agents/declarative",
        json={
            "agent_code": code,
            "name": name,
            "description": description,
            "persona_prompt": f"你是{name}。",
            "task_prompt_template": "回答：{{question}}",
            "knowledge_scopes": ["yard"],
            "allowed_tools": [],
        },
        headers=_hdr(yard_id=yard.id),
    )
    assert created.status_code == 200, created.text
    aid = created.json()["data"]["id"]
    pub = client.post(f"/api/v1/agents/declarative/{aid}/publish", headers=_hdr(yard_id=yard.id))
    assert pub.status_code == 200, pub.text
    return aid


def _mk_session(client, yard, title: str | None = None) -> str:
    body = {"title": title} if title else {}
    resp = client.post(CHAT, json=body, headers=_hdr(yard_id=yard.id))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def test_full_flow_send_history_and_auto_title(client, yard):
    _publish(client, yard, "irrig_c", "灌溉顾问", "负责灌溉浇水与水肥安排")
    sid = _mk_session(client, yard)

    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "今天地里需要浇水吗"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["user_message"]["seq"] == 1 and data["user_message"]["role"] == "user"
    a = data["assistant_message"]
    assert a["seq"] == 2 and a["role"] == "assistant" and a["status"] == "ok"
    assert a["routed_by"] == "rules"  # 关键词命中灌溉候选
    assert a["choice"]["name"] == "灌溉顾问"
    assert isinstance(a["content"], str)
    # 空标题自动继承首句
    assert data["session"]["title"] == "今天地里需要浇水吗"

    # 第二轮：seq 续接，标题不被覆盖
    resp2 = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "那肥料什么时候追？"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp2.status_code == 200, resp2.text
    d2 = resp2.json()["data"]
    assert d2["user_message"]["seq"] == 3 and d2["assistant_message"]["seq"] == 4
    assert d2["session"]["title"] == "今天地里需要浇水吗"

    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id))
    assert hist.status_code == 200
    msgs = hist.json()["data"]["items"]
    assert [m["seq"] for m in msgs] == [1, 2, 3, 4]
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]

    lst = client.get(CHAT, headers=_hdr(yard_id=yard.id)).json()["data"]["items"]
    assert len(lst) == 1 and lst[0]["message_count"] == 4


def test_failure_persists_honest_history(client, yard):
    # 无任何已发布助手/班组：supervisor 报 NOT_FOUND(404)
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "随便问点什么"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"

    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id)).json()["data"][
        "items"
    ]
    assert len(hist) == 2
    assert hist[0]["role"] == "user" and hist[0]["status"] == "ok"
    assert hist[1]["role"] == "assistant" and hist[1]["status"] == "failed"
    assert hist[1]["error_code"] == "NOT_FOUND"


def test_cross_yard_isolation(client, yard, other_yard):
    sid = _mk_session(client, yard)
    # 别的小院读取/发消息一律 404（防枚举口径），且不产生消息
    assert client.get(f"{CHAT}/{sid}", headers=_hdr("u-x", other_yard.id)).status_code == 404
    assert (
        client.post(
            f"{CHAT}/{sid}/messages",
            json={"content": "偷看"},
            headers=_hdr("u-x", other_yard.id),
        ).status_code
        == 404
    )
    assert (
        client.get(f"{CHAT}/{sid}/messages", headers=_hdr("u-x", other_yard.id)).status_code == 404
    )
    # 自己小院列表为空
    assert client.get(CHAT, headers=_hdr("u-x", other_yard.id)).json()["data"]["items"] == []
    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id)).json()["data"][
        "items"
    ]
    assert hist == []


def test_session_private_within_same_yard(client, yard):
    sid = _mk_session(client, yard)
    assert client.get(f"{CHAT}/{sid}", headers=_hdr("u-peer", yard.id)).status_code == 404
    assert (
        client.post(
            f"{CHAT}/{sid}/messages",
            json={"content": "同事偷看"},
            headers=_hdr("u-peer", yard.id),
        ).status_code
        == 404
    )
    assert client.get(CHAT, headers=_hdr("u-peer", yard.id)).json()["data"]["items"] == []


def test_validation_and_auth(client, yard):
    sid = _mk_session(client, yard)
    # 空白内容（pydantic 校验 400）
    bad = client.post(
        f"{CHAT}/{sid}/messages", json={"content": "   "}, headers=_hdr(yard_id=yard.id)
    )
    assert bad.status_code == 400
    # 未知字段拒绝
    assert (
        client.post(
            f"{CHAT}/{sid}/messages",
            json={"content": "好", "extra": 1},
            headers=_hdr(yard_id=yard.id),
        ).status_code
        == 400
    )
    # 不存在的会话
    assert client.get(f"{CHAT}/no-such-session", headers=_hdr(yard_id=yard.id)).status_code == 404
    assert (
        client.post(f"{CHAT}/no-such/messages", json={"content": "hi"}).status_code
        in (401, 404)  # 未登录先 401
    )
    assert client.get(CHAT).status_code == 401


def test_rename_and_delete(client, yard):
    sid = _mk_session(client, yard)
    ren = client.patch(f"{CHAT}/{sid}", json={"title": "灌溉咨询"}, headers=_hdr(yard_id=yard.id))
    assert ren.status_code == 200
    assert ren.json()["data"]["title"] == "灌溉咨询"
    assert (
        client.patch(
            f"{CHAT}/{sid}", json={"title": "  "}, headers=_hdr(yard_id=yard.id)
        ).status_code
        == 400
    )

    dele = client.delete(f"{CHAT}/{sid}", headers=_hdr(yard_id=yard.id))
    assert dele.status_code == 200
    assert client.get(f"{CHAT}/{sid}", headers=_hdr(yard_id=yard.id)).status_code == 404
    assert client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id)).status_code == 404
    assert client.get(CHAT, headers=_hdr(yard_id=yard.id)).json()["data"]["items"] == []


def test_audit_written(client, yard, db_session):
    from sqlalchemy import select

    _publish(client, yard, "green_c", "棚室管家", "棚室通风保温与光照管理")
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "棚室今晚要保温吗"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    actions = {
        a.action
        for a in db_session.scalars(
            select(AuditLog).where(
                AuditLog.yard_id == yard.id,
                AuditLog.resource_type == "chat_session",
                AuditLog.resource_id == sid,
            )
        )
    }
    assert {"chat_session_created", "chat_message_send"} <= actions
