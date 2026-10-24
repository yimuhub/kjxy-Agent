"""机器人网关 API 集成测试（/bots，全离线）。

覆盖：管理员口径 403、CRUD 与凭据掩码、启停联动、测试端点、状态、
会话批准/吊销流、消息账本（sha12 口径无正文）。
"""

import os

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-chars-for-unit-tests")

import pytest  # noqa: E402
from app.core.security import create_access_token  # noqa: E402
from app.modules.bots.models import BotConversation, BotMessageLog  # noqa: E402
from app.modules.tenants.repository import create_tenant  # noqa: E402

TENANT = "t-bots-a"


@pytest.fixture()
def tenant(db_session):
    t = create_tenant(db_session, name="机器人租户A")
    db_session.commit()
    return t


def _hdr(roles: tuple[str, ...] = ("tenant_admin",)) -> dict:
    token = create_access_token(user_id="u-bots", tenant_id=TENANT, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _create(client, **over) -> dict:  # noqa: ANN003, ANN202
    payload = {
        "platform": "wecom_aibot",
        "name": "大棚助手",
        "credentials": {"bot_id": "bot-123456", "secret": "sup3r-secret-value"},
        "binding_mode": "auto",
        "require_approval": True,
    }
    payload.update(over)
    return client.post("/api/v1/bots", json=payload, headers=_hdr())


# ---------------------------------------------------------------------------


def test_admin_only(client, tenant):
    assert client.get("/api/v1/bots", headers=_hdr(roles=("user",))).status_code == 403
    assert client.get("/api/v1/bots", headers=_hdr()).status_code == 200


def test_create_masks_credentials(client, tenant):
    resp = _create(client)
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    masked = data["credentials_masked"]
    assert "sup3r" not in str(masked)
    assert masked["bot_id"].endswith("56") and "*" in masked["bot_id"]
    assert data["status"] == "offline" and data["enabled"] is True


def test_create_missing_credentials_400(client, tenant):
    resp = _create(client, credentials={})
    assert resp.status_code == 400
    resp2 = _create(client, credentials={"bot_id": "only-id"})
    assert resp2.status_code == 400 and "secret" in resp2.text


def test_update_toggle_and_circuit_clear(client, tenant):
    bot_id = _create(client).json()["data"]["id"]
    # 通过 PATCH 走正常启停路径（启用联动 manager.spawn 在测试环境为 no-op）
    r = client.patch(f"/api/v1/bots/{bot_id}", json={"enabled": False}, headers=_hdr())
    assert r.status_code == 200 and r.json()["data"]["enabled"] is False
    r2 = client.patch(f"/api/v1/bots/{bot_id}", json={"enabled": True}, headers=_hdr())
    assert r2.json()["data"]["enabled"] is True


def test_status_and_delete(client, tenant):
    bot_id = _create(client).json()["data"]["id"]
    st = client.get(f"/api/v1/bots/{bot_id}/status", headers=_hdr())
    assert st.status_code == 200 and st.json()["data"]["status"] == "offline"
    assert client.delete(f"/api/v1/bots/{bot_id}", headers=_hdr()).status_code == 200
    assert client.get(f"/api/v1/bots/{bot_id}/status", headers=_hdr()).status_code == 404


def test_wecom_test_endpoint_local_validation(client, tenant, monkeypatch):
    # wecom 测试为本地校验（真连由 manager 长连接负责），不外呼
    bot_id = _create(client).json()["data"]["id"]
    r = client.post(f"/api/v1/bots/{bot_id}/test", headers=_hdr())
    assert r.status_code == 200 and r.json()["data"]["ok"] is True
    # 缺凭据的企微配置：报错但不外呼
    bot2 = client.post(
        "/api/v1/bots",
        json={
            "platform": "wecom_aibot",
            "name": "缺凭据",
            "credentials": {"bot_id": "bid-abcdef", "secret": "s3cret-abc"},
        },
        headers=_hdr(),
    ).json()["data"]
    # 用临时凭据覆盖测试路径（不查库凭据）
    r2 = client.post(
        f"/api/v1/bots/{bot2['id']}/test",
        json={"credentials": {"note": "无 bot_id"}},
        headers=_hdr(),
    )
    assert r2.json()["data"]["ok"] is False


def test_conversation_approval_flow(client, tenant, db_session):
    bot_id = _create(client).json()["data"]["id"]
    conv = BotConversation(
        tenant_id=TENANT,
        bot_config_id=bot_id,
        platform="wecom_aibot",
        external_conv_id="chat-x",
        external_conv_type="dm",
        display_name="张三",
        context_cache_json=[{"role": "user", "content": "明文上下文"}],
    )
    db_session.add(conv)
    db_session.flush()  # 生成 conv.id 后账本才能引用
    db_session.add(
        BotMessageLog(
            tenant_id=TENANT,
            conversation_id=conv.id,
            direction="in",
            content_sha12="a" * 12,
            length=6,
            routed_by="auto",
            reason_summary="",
            ok=True,
        )
    )
    db_session.commit()

    lst = client.get("/api/v1/bots/conversations", headers=_hdr())
    assert lst.status_code == 200
    row = lst.json()["data"][0]
    assert row["display_name"] == "张三" and row["approved_at"] == ""

    ap = client.post(f"/api/v1/bots/conversations/{conv.id}/approve", headers=_hdr())
    assert ap.status_code == 200 and ap.json()["data"]["approved_at"] != ""

    msgs = client.get(f"/api/v1/bots/conversations/{conv.id}/messages", headers=_hdr())
    m = msgs.json()["data"][0]
    assert m["content_sha12"] == "a" * 12 and "content" not in m  # 账本无正文

    rv = client.post(f"/api/v1/bots/conversations/{conv.id}/revoke", headers=_hdr())
    assert rv.json()["data"]["revoked_at"] != ""
    db_session.refresh(conv)
    assert conv.context_cache_json is None  # 吊销即清上下文明文


def test_cross_tenant_isolation(client, tenant, db_session):
    bot_id = _create(client).json()["data"]["id"]
    other_token = create_access_token(
        user_id="u-evil", tenant_id="t-bots-evil", roles=["tenant_admin"]
    )
    hdr = {"Authorization": f"Bearer {other_token}"}
    assert client.get(f"/api/v1/bots/{bot_id}/status", headers=hdr).status_code == 404
    assert (
        client.patch(f"/api/v1/bots/{bot_id}", json={"enabled": False}, headers=hdr).status_code
        == 404
    )


def test_dingtalk_open_failure_reported(client, tenant, monkeypatch):
    # 钉钉 test 的真外呼分支：注入假 _ding_reachable，验证 API 组装正确
    from app.modules.bots import router as bots_router

    async def fake_reachable(creds):  # noqa: ANN001, ANN001
        return ["无法连接钉钉开放平台（网络出网受限？）"]

    monkeypatch.setattr(bots_router, "_ding_reachable", fake_reachable)
    bot_id = client.post(
        "/api/v1/bots",
        json={
            "platform": "dingtalk",
            "name": "钉钉机器人",
            "credentials": {"client_id": "cid", "client_secret": "cs"},
        },
        headers=_hdr(),
    ).json()["data"]["id"]
    r = client.post(f"/api/v1/bots/{bot_id}/test", headers=_hdr())
    assert r.json()["data"]["ok"] is False
    assert "钉钉" in r.json()["data"]["problems"][0]
