"""会话列表管理端点集成测试（0043：置顶/归档/未读/批量/分支 + 会话授权模式）。

覆盖：
1. 三点菜单的局部更新：只改传入的字段，其余保持原值；
2. 列表排序与筛选：置顶恒在最前，进行中/已归档两个视图互斥；
3. 批量操作：一次处理多个，他人/不存在的 id 静默跳过（不泄露存在性）；
4. 分支：只带到指定回答为止的历史，原会话不受影响，附件与群聊名单一并继承；
5. 分支删掉不会弄坏原会话的附件文件（共用同一份存储，按引用计数清理）；
6. 授权模式是权限动作：普通成员请求 auto/full 一律降级为 ask 并如实返回；
7. 私有边界：他人/跨小院的会话在 patch、fork、batch 上统一 404。
"""

import pytest
from app.core.security import create_access_token
from app.modules.llm import providers_fake
from app.modules.yards.repository import create_yard

CHAT = "/api/v1/chat/sessions"


def _hdr(user_id: str = "u-ops", yard_id: str | None = None, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _script_isolation():
    providers_fake.reset_scripted_tool_responses()
    yield
    providers_fake.reset_scripted_tool_responses()


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="会话管理小院")
    db_session.commit()
    return t


@pytest.fixture()
def other_yard(db_session):
    t = create_yard(db_session, name="隔壁小院")
    db_session.commit()
    return t


def _mk_session(client, yard, title: str, user: str = "u-ops", roles=("yard_admin",)) -> str:
    resp = client.post(CHAT, json={"title": title}, headers=_hdr(user, yard.id, roles=roles))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


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
    assert (
        client.post(
            f"/api/v1/agents/declarative/{aid}/publish", headers=_hdr(yard_id=yard.id)
        ).status_code
        == 200
    )
    return aid


def _send(client, yard, sid: str, content: str) -> dict:
    resp = client.post(
        f"{CHAT}/{sid}/messages", json={"content": content}, headers=_hdr(yard_id=yard.id)
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _titles(items: list[dict]) -> list[str]:
    return [i["title"] for i in items]


def test_flags_are_partial_and_list_orders_pinned_first(client, yard):
    a = _mk_session(client, yard, "甲")
    b = _mk_session(client, yard, "乙")
    c = _mk_session(client, yard, "丙")

    # 置顶只动 pinned，标题与其他标记保持原值
    pinned = client.patch(CHAT + f"/{b}", json={"pinned": True}, headers=_hdr(yard_id=yard.id))
    assert pinned.status_code == 200
    body = pinned.json()["data"]
    assert body["pinned"] is True and body["title"] == "乙" and body["archived"] is False
    assert body["approval_mode"] == "ask"

    # 未读 + 归档一次请求改两位
    both = client.patch(
        CHAT + f"/{c}", json={"unread": True, "archived": True}, headers=_hdr(yard_id=yard.id)
    ).json()["data"]
    assert both["unread"] is True and both["archived"] is True

    active = client.get(CHAT, headers=_hdr(yard_id=yard.id)).json()["data"]["items"]
    assert _titles(active) == ["乙", "甲"], "置顶必须恒在最前，已归档必须不出现"
    archived = client.get(CHAT, params={"archived": True}, headers=_hdr(yard_id=yard.id)).json()[
        "data"
    ]["items"]
    assert _titles(archived) == ["丙"]

    # 取消置顶后回到「最近更新在前」：刚才那次 PATCH 本身刷新了 updated_at
    client.patch(CHAT + f"/{b}", json={"pinned": False}, headers=_hdr(yard_id=yard.id))
    after = client.get(CHAT, headers=_hdr(yard_id=yard.id)).json()["data"]["items"]
    assert _titles(after) == ["乙", "甲"]
    assert [i["pinned"] for i in after] == [False, False]
    assert a and len({a, b, c}) == 3


def test_batch_skips_foreign_ids_and_deletes(client, yard, other_yard):
    keep = _mk_session(client, yard, "留下")
    gone = _mk_session(client, yard, "删掉")
    foreign = _mk_session(client, other_yard, "他人会话", user="u-other")

    res = client.post(
        CHAT + "/batch",
        json={"session_ids": [keep, gone, foreign, "no-such-id"], "action": "archive"},
        headers=_hdr(yard_id=yard.id),
    )
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    # 别人的 id 与不存在的 id 一起计入 skipped，不回显具体是谁（防枚举）
    assert sorted(data["affected"]) == sorted([keep, gone])
    assert data["missing"] == 2 and data["deleted"] == 0

    deleted = client.post(
        CHAT + "/batch",
        json={"session_ids": [gone, foreign], "action": "delete"},
        headers=_hdr(yard_id=yard.id),
    ).json()["data"]
    assert deleted["deleted"] == 1 and deleted["affected"] == [gone]
    assert client.get(f"{CHAT}/{gone}", headers=_hdr(yard_id=yard.id)).status_code == 404
    # 别人的会话没有被删
    assert (
        client.get(f"{CHAT}/{foreign}", headers=_hdr("u-other", other_yard.id)).status_code == 200
    )


def test_batch_rejects_unknown_action_and_empty_ids(client, yard):
    sid = _mk_session(client, yard, "甲")
    # 非法 action / 全空白 id 由请求体校验挡下（本 app 把校验失败统一记 400）
    assert client.post(
        CHAT + "/batch",
        json={"session_ids": [sid], "action": "explode"},
        headers=_hdr(yard_id=yard.id),
    ).status_code in (400, 422)
    assert client.post(
        CHAT + "/batch",
        json={"session_ids": ["  "], "action": "pin"},
        headers=_hdr(yard_id=yard.id),
    ).status_code in (400, 422)


def test_fork_carries_history_up_to_that_answer_only(client, yard):
    _publish(client, yard, "fork_c", "分叉顾问", "负责分叉测试")
    sid = _mk_session(client, yard, "原始会话")
    first = _send(client, yard, sid, "第一个问题")
    _send(client, yard, sid, "第二个问题")

    branch = client.post(
        CHAT + f"/{sid}/fork",
        json={"message_id": first["assistant_message"]["id"]},
        headers=_hdr(yard_id=yard.id),
    )
    assert branch.status_code == 201, branch.text
    new = branch.json()["data"]
    assert new["title"] == "原始会话 · 分支"
    assert new["parent_session_id"] == sid
    assert new["parent_message_id"] == first["assistant_message"]["id"]
    assert new["message_count"] == 2 and new["pinned"] is False and new["unread"] is False

    hist = client.get(f"{CHAT}/{new['id']}/messages", headers=_hdr(yard_id=yard.id)).json()["data"][
        "items"
    ]
    assert [m["seq"] for m in hist] == [1, 2]
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[0]["id"] != first["user_message"]["id"], "分支必须是新行，不能共享主键"
    # 路由存证随行复制：不重新执行模型，内容与 routed_by 与原回答逐字一致
    assert hist[1]["content"] == first["assistant_message"]["content"]
    assert hist[1]["routed_by"] == first["assistant_message"]["routed_by"]
    assert hist[1]["token_usage"] == first["assistant_message"]["token_usage"]

    # 原会话完全不受影响
    original = client.get(f"{CHAT}/{sid}", headers=_hdr(yard_id=yard.id)).json()["data"]
    assert original["message_count"] == 4
    assert original["title"] == "原始会话"


def test_fork_copies_referenced_attachment_and_delete_is_safe(client, yard):
    _publish(client, yard, "fork_att", "要点顾问", "负责要点与巡检")
    sid = _mk_session(client, yard, "带附件的会话")
    up = client.post(
        f"{CHAT}/{sid}/attachments",
        files={"file": ("要点.txt", "巡检要点".encode(), "text/plain")},
        headers=_hdr(yard_id=yard.id),
    )
    assert up.status_code == 201, up.text
    aid = up.json()["data"]["id"]
    turn = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "看看这份要点", "attachment_ids": [aid]},
        headers=_hdr(yard_id=yard.id),
    )
    assert turn.status_code == 200, turn.text

    new = client.post(
        CHAT + f"/{sid}/fork",
        json={"message_id": turn.json()["data"]["assistant_message"]["id"]},
        headers=_hdr(yard_id=yard.id),
    ).json()["data"]
    branch_atts = client.get(
        f"{CHAT}/{new['id']}/attachments", headers=_hdr(yard_id=yard.id)
    ).json()["data"]["items"]
    assert len(branch_atts) == 1 and branch_atts[0]["id"] != aid
    assert branch_atts[0]["filename"] == "要点.txt"

    # 分支里的附件可下载（同一份文件、不同行）
    dl = client.get(
        f"{CHAT}/{new['id']}/attachments/{branch_atts[0]['id']}/download",
        headers=_hdr(yard_id=yard.id),
    )
    assert dl.status_code == 200 and dl.content == "巡检要点".encode()

    # 删掉分支不会弄坏原会话的附件文件
    assert client.delete(f"{CHAT}/{new['id']}", headers=_hdr(yard_id=yard.id)).status_code == 200
    origin = client.get(f"{CHAT}/{sid}/attachments/{aid}/download", headers=_hdr(yard_id=yard.id))
    assert origin.status_code == 200 and origin.content == "巡检要点".encode()


def test_approval_mode_is_a_permission_action(client, yard):
    sid = _mk_session(client, yard, "权限会话")

    admin = client.patch(
        CHAT + f"/{sid}", json={"approval_mode": "full"}, headers=_hdr(yard_id=yard.id)
    ).json()["data"]
    assert admin["approval_mode"] == "full"

    member_sid = _mk_session(client, yard, "成员会话", user="u-member", roles=("member",))
    downgraded = client.patch(
        CHAT + f"/{member_sid}",
        json={"approval_mode": "auto"},
        headers=_hdr("u-member", yard.id, roles=("member",)),
    ).json()["data"]
    assert downgraded["approval_mode"] == "ask", "非管理员请求 auto/full 必须静默降级"

    bad = client.patch(
        CHAT + f"/{sid}", json={"approval_mode": "yolo"}, headers=_hdr(yard_id=yard.id)
    )
    assert bad.status_code == 400


def test_private_boundary_on_new_endpoints(client, yard, other_yard):
    sid = _mk_session(client, yard, "私密会话")
    other = _mk_session(client, other_yard, "隔壁会话", user="u-other")

    intruder = _hdr("u-intruder", yard.id)
    assert (
        client.patch(CHAT + f"/{sid}", json={"pinned": True}, headers=intruder).status_code == 404
    )
    assert (
        client.post(
            CHAT + "/batch", json={"session_ids": [sid], "action": "archive"}, headers=intruder
        ).json()["data"]["affected"]
        == []
    )
    assert (
        client.post(CHAT + f"/{sid}/fork", json={"message_id": "m-1"}, headers=intruder).status_code
        == 404
    )
    # 跨小院同样 404
    assert (
        client.patch(
            CHAT + f"/{other}", json={"pinned": True}, headers=_hdr(yard_id=yard.id)
        ).status_code
        == 404
    )


def test_empty_update_body_is_rejected(client, yard):
    sid = _mk_session(client, yard, "甲")
    resp = client.patch(CHAT + f"/{sid}", json={}, headers=_hdr(yard_id=yard.id))
    assert resp.status_code in (400, 422)
    # 会话没有被改动
    assert client.get(f"{CHAT}/{sid}", headers=_hdr(yard_id=yard.id)).status_code == 200


def test_fork_missing_message_returns_404(client, yard):
    sid = _mk_session(client, yard, "甲")
    resp = client.post(
        CHAT + f"/{sid}/fork", json={"message_id": "nope"}, headers=_hdr(yard_id=yard.id)
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CHAT_MESSAGE_NOT_FOUND"
