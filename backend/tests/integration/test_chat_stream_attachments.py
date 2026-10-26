"""对话增强链路集成测试：附件上传 + 消息引用 + SSE 步骤流 + 能力卡片。

覆盖：
1. 附件端点：上传（图片/文档）、类型拒收、列举、删除、越权 404；
2. 消息引用附件：用户消息带走附件摘要，助手步骤出现「正在阅读附件」；
3. SSE 流：帧序列 chat-init → step → message → chat-end，最终消息落库；
4. 能力链路：管理员建草稿出卡片，普通成员只出建议卡片（不落库、不旁路权限）；
5. 跨用户附件不可引用（防越权）。
"""

import json

import pytest
from app.core.security import create_access_token
from app.modules.agents.models import CREATED_FROM_CHAT
from app.modules.yards.repository import create_yard

CHAT = "/api/v1/chat/sessions"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _stream_session_factory(db_session):
    """流执行任务自开短连接：注入同一内存库的会话工厂（生产用 SessionLocal）。

    这是流链路与生产一致的关键前提——请求级 Session 在端点返回 Response 时
    就被 FastAPI 关闭，流不能依赖它。
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


def _hdr(user_id: str = "u-chat", yard_id: str | None = None, roles: list[str] | None = None):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=roles or ["yard_admin"])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="对话增强小院")
    db_session.commit()
    return t


def _tid(yard) -> str:
    """小院 id：可传 ORM 对象或已取好的字符串（会话关闭后 ORM 对象会脱管）。"""
    return yard if isinstance(yard, str) else yard.id


def _mk_session(client, yard, user_id: str = "u-chat", roles: list[str] | None = None) -> str:
    resp = client.post(CHAT, json={}, headers=_hdr(user_id, _tid(yard), roles))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _upload(client, yard, sid: str, filename: str, data: bytes, user_id: str = "u-chat"):
    return client.post(
        f"{CHAT}/{sid}/attachments",
        files={"file": (filename, data, "application/octet-stream")},
        headers=_hdr(user_id, _tid(yard)),
    )


def _sse_frames(text: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析成 [(event, data)] 序列（忽略心跳注释帧）。"""
    frames: list[tuple[str, dict]] = []
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
        raw = "\n".join(data_lines)
        try:
            frames.append((event, json.loads(raw)))
        except json.JSONDecodeError:
            frames.append((event, {"_raw": raw}))
    return frames


# ---------------------------------------------------------------------------
# 附件端点
# ---------------------------------------------------------------------------


def test_upload_list_delete_attachment(client, yard):
    sid = _mk_session(client, yard)
    up = _upload(client, yard, sid, "leaf.png", PNG_BYTES)
    assert up.status_code == 201, up.text
    att = up.json()["data"]
    assert att["kind"] == "image" and att["status"] == "ready"
    assert "storage_key" not in att

    lst = client.get(f"{CHAT}/{sid}/attachments", headers=_hdr(yard_id=yard.id))
    assert lst.status_code == 200
    assert [i["id"] for i in lst.json()["data"]["items"]] == [att["id"]]

    dele = client.delete(f"{CHAT}/{sid}/attachments/{att['id']}", headers=_hdr(yard_id=yard.id))
    assert dele.status_code == 200
    lst2 = client.get(f"{CHAT}/{sid}/attachments", headers=_hdr(yard_id=yard.id))
    assert lst2.json()["data"]["items"] == []


def test_upload_rejects_bad_type(client, yard):
    sid = _mk_session(client, yard)
    bad = _upload(client, yard, sid, "payload.exe", b"MZ\x90\x00")
    assert bad.status_code == 415
    assert bad.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"
    # 改名伪装同样拒
    fake = _upload(client, yard, sid, "evil.png", b"MZ\x90\x00\x03")
    assert fake.status_code == 415


def test_attachment_requires_login(client, yard):
    sid = _mk_session(client, yard)
    # 未登录 → 401
    unauth = client.post(f"{CHAT}/{sid}/attachments", files={"file": ("a.png", PNG_BYTES, "")})
    assert unauth.status_code == 401
    # 会话是创建者私有的：他人上传同样 404
    assert _upload(client, yard, sid, "leaf.png", PNG_BYTES, user_id="x").status_code == 404


def test_other_user_cannot_list_or_delete_attachment(client, yard):
    sid = _mk_session(client, yard)
    att = _upload(client, yard, sid, "leaf.png", PNG_BYTES).json()["data"]
    other = _hdr("u-other", yard.id)
    # 同小院他人看不到别人的会话附件
    assert client.get(f"{CHAT}/{sid}/attachments", headers=other).status_code == 404
    assert client.delete(f"{CHAT}/{sid}/attachments/{att['id']}", headers=other).status_code == 404


def test_reference_foreign_attachment_is_rejected(client, yard):
    sid = _mk_session(client, yard)
    att = _upload(client, yard, sid, "leaf.png", PNG_BYTES).json()["data"]
    # 另一个用户的会话引用该附件 → 404（防越权引用）
    sid2 = _mk_session(client, yard, user_id="u-other")
    resp = client.post(
        f"{CHAT}/{sid2}/messages",
        json={"content": "看看这个", "attachment_ids": [att["id"]]},
        headers=_hdr("u-other", yard.id),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CHAT_ATTACHMENT_NOT_FOUND"


# ---------------------------------------------------------------------------
# 消息引用附件
# ---------------------------------------------------------------------------


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


def test_message_with_attachment_records_ref_and_reading_step(client, yard):
    _publish_agent(client, yard, "irrig_c2", "灌溉顾问", "负责灌溉浇水与水肥安排")
    sid = _mk_session(client, yard)
    att = _upload(client, yard, sid, "note.txt", "今天需要灌溉三十亩".encode()).json()["data"]
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "今天地里需要浇水吗", "attachment_ids": [att["id"]]},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    um = data["user_message"]
    assert um["attachments"] and um["attachments"][0]["filename"] == "note.txt"
    am = data["assistant_message"]
    assert am["status"] == "ok"
    phases = [s["phase"] for s in (am["steps"] or [])]
    assert "reading" in phases
    assert am["routed_by"] == "rules"


# ---------------------------------------------------------------------------
# SSE 流
# ---------------------------------------------------------------------------


def test_stream_emits_frames_and_persists_message(client, yard):
    _publish_agent(client, yard, "irrig_c3", "灌溉顾问", "负责灌溉浇水与水肥安排")
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages/stream",
        json={"content": "今天地里需要浇水吗"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    assert "text/event-stream" in resp.headers.get("content-type", "")
    frames = _sse_frames(resp.text)
    events = [e for e, _ in frames]
    assert events[0] == "chat-init"
    assert "step" in events
    assert "message" in events
    assert events[-1] == "chat-end"

    end = frames[-1][1]
    assert end["status"] == "ok"
    msg = next(d for e, d in frames if e == "message")
    assert msg["role"] == "assistant" and msg["seq"] == 2
    step_frames = [d for e, d in frames if e == "step"]
    assert step_frames[-1]["steps"], "步骤快照不应为空"

    hist = client.get(f"{CHAT}/{sid}/messages", headers=_hdr(yard_id=yard.id)).json()["data"][
        "items"
    ]
    assert [m["role"] for m in hist] == ["user", "assistant"]
    assert hist[1]["steps"]


def test_stream_with_attachment_reports_reading_phase(client, yard):
    _publish_agent(client, yard, "irrig_c4", "灌溉顾问", "负责灌溉浇水与水肥安排")
    sid = _mk_session(client, yard)
    att = _upload(client, yard, sid, "note.txt", "灌溉记录".encode()).json()["data"]
    resp = client.post(
        f"{CHAT}/{sid}/messages/stream",
        json={"content": "今天地里需要浇水吗", "attachment_ids": [att["id"]]},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    frames = _sse_frames(resp.text)
    steps = next(d for e, d in frames if e == "step")["steps"]
    assert any(s["phase"] == "reading" for s in steps)


def test_stream_failure_yields_error_frame(client, yard):
    """没有可用助手 → 诚实报错（error 帧 + chat-end=failed），不吞错。"""
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages/stream",
        json={"content": "随便问点什么"},
        headers=_hdr(yard_id=yard.id),
    )
    frames = _sse_frames(resp.text)
    events = [e for e, _ in frames]
    assert "error" in events
    assert frames[-1][1]["status"] == "failed"


def test_stream_survives_request_session_teardown(db_session, yard):
    """回归：FastAPI 在端点返回 Response 时就关闭请求级 Session（yield 依赖
    teardown），而响应体之后才发送——流不得依赖请求级 Session 的 ORM 对象。

    这里用"请求结束即关闭 Session"的覆盖依赖模拟生产时序（普通 client fixture
    不关闭，掩盖了这类缺陷）。
    """
    from app.core.deps import get_db_session
    from app.main import create_app
    from fastapi.testclient import TestClient

    app = create_app()

    def override_db():
        try:
            yield db_session
        finally:
            db_session.close()  # 与生产一致：请求结束即释放连接

    app.dependency_overrides[get_db_session] = override_db
    yard_id = yard.id  # 会话一关 ORM 对象即脱管，先取纯值
    with TestClient(app, raise_server_exceptions=False) as strict:
        _publish_agent(strict, yard_id, "irrig_c5", "灌溉顾问", "负责灌溉浇水与水肥安排")
        sid = _mk_session(strict, yard_id)
        resp = strict.post(
            f"{CHAT}/{sid}/messages/stream",
            json={"content": "今天地里需要浇水吗"},
            headers=_hdr(yard_id=yard_id),
        )
        assert resp.status_code == 200, resp.text
        frames = _sse_frames(resp.text)
        events = [e for e, _ in frames]
        assert events[0] == "chat-init"
        assert "message" in events
        assert events[-1] == "chat-end"
        assert frames[-1][1]["status"] == "ok"


# ---------------------------------------------------------------------------
# 能力链路（卡片）
# ---------------------------------------------------------------------------


def test_chat_create_capability_returns_card_for_admin(client, yard, db_session):
    from app.modules.agents.models import YardAgent
    from sqlalchemy import select

    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "创建一个「农机维修问答」助手"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    am = resp.json()["data"]["assistant_message"]
    payload = am["payload"]
    assert payload["type"] == "capability"
    assert payload["state"] == "created"
    assert payload["agent_id"]
    assert payload["risk_level"] == "low"
    actions = {a["key"] for a in payload["actions"]}
    assert "publish" in actions and "view" in actions

    agent = db_session.scalar(select(YardAgent).where(YardAgent.id == payload["agent_id"]))
    assert agent is not None
    assert agent.created_from == CREATED_FROM_CHAT
    assert agent.status == "draft"


def test_chat_create_capability_for_member_only_suggests(client, yard, db_session):
    from app.modules.agents.models import YardAgent
    from sqlalchemy import select

    sid = _mk_session(client, yard, user_id="u-viewer", roles=["viewer"])
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "创建一个「农机维修问答」助手"},
        headers=_hdr("u-viewer", yard.id, ["viewer"]),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]["assistant_message"]["payload"]
    assert payload["type"] == "capability"
    assert payload["state"] == "requested"
    assert payload["agent_id"] == ""
    assert payload["actions"] == []
    # 不落库（无权限旁路）
    assert db_session.scalars(select(YardAgent)).all() == []


def test_chat_install_market_capability_returns_card(client, yard):
    sid = _mk_session(client, yard)
    resp = client.post(
        f"{CHAT}/{sid}/messages",
        json={"content": "安装水稻病虫害识别助手"},
        headers=_hdr(yard_id=yard.id),
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()["data"]["assistant_message"]["payload"]
    assert payload["type"] == "market"
    assert payload["state"] in ("installed", "requested", "suggested")
