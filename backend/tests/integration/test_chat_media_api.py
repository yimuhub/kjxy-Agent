"""对话内媒体 HTTP 链路（v3.73）：意图 → 出图落库 → 消息卡片 → 签名取流。"""

from __future__ import annotations

import pytest
from app.core.deps import YardContext
from app.core.security import create_access_token
from app.modules.chat import group as chat_group
from app.modules.chat import media as chat_media
from app.modules.growth.toolseed import seed_growth_tools
from app.modules.yards.repository import create_yard

CHAT = "/api/v1/chat/sessions"


def _hdr(user_id: str, yard_id: str, roles: list[str]) -> dict[str, str]:
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=roles)
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_image_message_returns_media_card_and_signed_url(client, db_session, monkeypatch):
    yard = create_yard(db_session, name="对话媒体小院")
    seed_growth_tools(db_session)
    db_session.commit()
    headers = _hdr("u-media", yard.id, ["yard_admin"])

    async def fake_spec(db, ctx, intent, request_text):
        return chat_media._fallback_spec(intent), None

    monkeypatch.setattr(chat_media, "build_spec", fake_spec)

    session_id = client.post(CHAT, json={}, headers=headers).json()["data"]["id"]
    resp = client.post(
        f"{CHAT}/{session_id}/messages",
        json={"content": "我需要一个关于牡丹特色小院的宣传海报"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assistant = resp.json()["data"]["assistant_message"]
    payload = assistant["payload"]
    assert payload["type"] == "media"
    assert payload["kind"] == "image"
    assert payload["status"] == "ready"
    asset = payload["assets"][0]
    assert asset["filename"]
    # 供应商出图沿用海报签名目录；转存的视频走 /media/，两者都是 capability URL
    assert asset["url"].startswith(("/api/v1/media/", "/api/v1/growth/posters/"))
    assert "sig=" in asset["url"]

    status = client.get(f"/api/v1/media/assets/{asset['id']}", headers=headers)
    assert status.status_code == 200, status.text
    assert status.json()["data"]["status"] == "ready"

    usage = client.get("/api/v1/media/usage", headers=headers)
    assert usage.status_code == 200, usage.text
    assert usage.json()["data"]["image"]["used"] >= 1

    # 签名能力地址：不带登录态也能取，篡改签名 404
    fetched = client.get(asset["url"])
    assert fetched.status_code == 200, fetched.text
    assert fetched.content
    assert client.get(asset["url"].replace("sig=", "sig=0")).status_code == 404


@pytest.mark.asyncio
async def test_member_video_is_blocked_until_yard_opens_access(client, db_session, monkeypatch):
    yard = create_yard(db_session, name="视频未授权小院")
    seed_growth_tools(db_session)
    db_session.commit()
    headers = _hdr("u-member-video", yard.id, ["member"])

    async def fake_spec(db, ctx, intent, request_text):
        return chat_media._fallback_spec(intent), None

    monkeypatch.setattr(chat_media, "build_spec", fake_spec)

    session_id = client.post(CHAT, json={}, headers=headers).json()["data"]["id"]
    resp = client.post(
        f"{CHAT}/{session_id}/messages",
        json={"content": "给我来一个科技小院 5 秒视频"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assistant = resp.json()["data"]["assistant_message"]
    assert assistant["payload"] is None
    assert "管理员" in assistant["content"]
    assert "视频" in assistant["content"]


@pytest.mark.asyncio
async def test_group_media_runs_once_with_one_card(client, db_session, monkeypatch):
    yard = create_yard(db_session, name="群聊媒体小院")
    seed_growth_tools(db_session)
    db_session.commit()
    headers = _hdr("u-group-media", yard.id, ["yard_admin"])

    async def fake_spec(db, ctx, intent, request_text):
        return chat_media._fallback_spec(intent), None

    monkeypatch.setattr(chat_media, "build_spec", fake_spec)

    session_id = client.post(CHAT, json={}, headers=headers).json()["data"]["id"]
    ctx = YardContext(
        yard_id=yard.id,
        user_id="u-group-media",
        roles=["yard_admin"],
        request_id="req-group-media",
    )
    participants = [
        {"id": "p1", "position": 1, "agent_id": "a1", "agent_name": "牡丹顾问"},
        {"id": "p2", "position": 2, "agent_id": "a2", "agent_name": "设计顾问"},
    ]
    result = await chat_group.run_group_turn(
        db_session,
        ctx=ctx,
        session_id=session_id,
        content="我需要一个关于牡丹特色小院的宣传海报",
        attachment_ids=[],
        policy="all",
        participants=participants,
    )
    # 群聊不按成员数重复出图：只产生一条媒体消息，其他成员不再各自调用模型
    assert result["member_count"] == 1
    assert len(result["messages"]) == 1
    assert result["messages"][0]["payload"]["type"] == "media"
    assert result["not_answered"] == []
