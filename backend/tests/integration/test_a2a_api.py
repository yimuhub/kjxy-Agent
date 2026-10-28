"""A2A HTTP 接口（v3.20）：入站令牌、JSON-RPC 全链路、协作方管理与任务。

出站全部 mock：``a2a.service.set_test_transport`` 注入 MockTransport，假扮远端平台。
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.core.config import settings
from app.modules.a2a import service as a2a_service
from app.modules.auth.repository import create_user
from app.modules.farm.models import FarmPlot
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"
REMOTE = "https://peer.example.com"
CARD = {
    "protocolVersion": "0.2.5",
    "name": "邻村小院",
    "url": f"{REMOTE}/api/v1/a2a/rpc",
    "version": "1.0.0",
    "skills": [{"id": "yard.summary", "name": "小院概况"}],
}


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="A2A 集成小院")
    owner = create_user(db_session, username="a2a_owner", password="Passw0rd123")
    admin = create_user(db_session, username="a2a_admin", password="Passw0rd123")
    viewer = create_user(db_session, username="a2a_viewer", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    db_session.add(FarmPlot(yard_id=yard.id, name="东一号田"))
    db_session.commit()
    return {"yard": yard, "owner": owner, "admin": admin, "viewer": viewer}


@pytest.fixture(autouse=True)
def _a2a_on(monkeypatch):
    monkeypatch.setattr(settings, "a2a_enabled", True)
    yield
    a2a_service.set_test_transport(None)


def _login(client, username: str, yard_id: str) -> str:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": "Passw0rd123", "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _token(client, owner_token: str) -> str:
    resp = client.post(f"{PREFIX}/settings/a2a/token", headers=_hdr(owner_token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _peer_body(**overrides) -> dict:
    body = {"name": "邻村小院", "base_url": REMOTE, "token": "peer-secret"}
    body.update(overrides)
    return body


def _create_peer(client, token: str, **overrides) -> dict:
    resp = client.post(
        f"{PREFIX}/settings/a2a/peers", json=_peer_body(**overrides), headers=_hdr(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------- Agent Card


def test_agent_card_is_public_when_enabled(client):
    card = client.get("/.well-known/agent.json")
    assert card.status_code == 200
    data = card.json()
    assert data["protocolVersion"] == a2a_service.PROTOCOL_VERSION
    assert {s["id"] for s in data["skills"]} == {"yard.summary", "knowledge.search"}
    assert data["url"].endswith("/api/v1/a2a/rpc")
    alias = client.get("/.well-known/agent-card.json")
    assert alias.status_code == 200
    assert alias.json()["name"] == data["name"]


# ---------------------------------------------------------------- 令牌与权限


def test_owner_rotates_token_and_status_never_echoes_it(client, world):
    owner = _login(client, "a2a_owner", world["yard"].id)
    raw = _token(client, owner)
    assert raw.startswith("a2a_")

    status = client.get(f"{PREFIX}/settings/a2a", headers=_hdr(owner))
    assert status.status_code == 200
    data = status.json()["data"]
    assert data["enabled"] is True
    assert data["endpoint"]["token_prefix"] == raw[:12]
    assert raw not in status.text
    assert data["card_url"].endswith("/.well-known/agent.json")
    assert {s["id"] for s in data["skills"]} == {"yard.summary", "knowledge.search"}

    rotated = _token(client, owner)
    assert rotated != raw


def test_viewer_forbidden_and_admin_cannot_rotate(client, world):
    viewer = _login(client, "a2a_viewer", world["yard"].id)
    admin = _login(client, "a2a_admin", world["yard"].id)
    assert client.get(f"{PREFIX}/settings/a2a", headers=_hdr(viewer)).status_code == 403
    assert client.get(f"{PREFIX}/settings/a2a", headers=_hdr(admin)).status_code == 200
    assert client.post(f"{PREFIX}/settings/a2a/token", headers=_hdr(admin)).status_code == 403


# ---------------------------------------------------------------- 入站 RPC


def test_inbound_rpc_end_to_end_and_auth(client, world):
    owner = _login(client, "a2a_owner", world["yard"].id)
    raw = _token(client, owner)
    payload = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "概况"}],
                "messageId": "msg-http-1",
                "metadata": {"skill": "yard.summary"},
            }
        },
    }
    missing = client.post(f"{PREFIX}/a2a/rpc", json=payload)
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "A2A_UNAUTHORIZED"

    wrong = client.post(f"{PREFIX}/a2a/rpc", json=payload, headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401

    ok = client.post(f"{PREFIX}/a2a/rpc", json=payload, headers=_hdr(raw))
    assert ok.status_code == 200, ok.text
    task = ok.json()["result"]
    assert task["status"]["state"] == "completed"
    assert "地块 1 块" in task["artifacts"][0]["parts"][0]["text"]

    again = client.post(f"{PREFIX}/a2a/rpc", json=payload, headers=_hdr(raw))
    assert again.json()["result"]["id"] == task["id"]

    listed = client.get(f"{PREFIX}/settings/a2a/tasks", headers=_hdr(owner))
    assert listed.status_code == 200
    assert len(listed.json()["data"]) == 1


def test_inbound_rejects_unknown_skill(client, world):
    owner = _login(client, "a2a_owner", world["yard"].id)
    raw = _token(client, owner)
    resp = client.post(
        f"{PREFIX}/a2a/rpc",
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "message/send",
            "params": {
                "message": {
                    "messageId": "m-x",
                    "parts": [{"kind": "text", "text": "x"}],
                    "metadata": {"skill": "admin.drop"},
                }
            },
        },
        headers=_hdr(raw),
    )
    assert resp.status_code == 200
    assert resp.json()["error"]["code"] == -32602


# ---------------------------------------------------------------- 协作方


def test_peer_crud_masks_token_and_rejects_duplicates(client, world):
    owner = _login(client, "a2a_owner", world["yard"].id)
    peer = _create_peer(client, owner)
    assert peer["token_configured"] is True
    assert "peer-secret" not in json.dumps(peer, ensure_ascii=False)

    dup = client.post(
        f"{PREFIX}/settings/a2a/peers", json=_peer_body(name="邻村小院"), headers=_hdr(owner)
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "A2A_PEER_EXISTS"

    patched = client.patch(
        f"{PREFIX}/settings/a2a/peers/{peer['id']}",
        json={"enabled": False, "clear_token": True},
        headers=_hdr(owner),
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["enabled"] is False
    assert patched.json()["data"]["token_configured"] is False

    deleted = client.delete(f"{PREFIX}/settings/a2a/peers/{peer['id']}", headers=_hdr(owner))
    assert deleted.status_code == 200
    assert client.get(f"{PREFIX}/settings/a2a/peers", headers=_hdr(owner)).json()["data"] == []


def test_peer_create_rejects_metadata_url(client, world):
    owner = _login(client, "a2a_owner", world["yard"].id)
    resp = client.post(
        f"{PREFIX}/settings/a2a/peers",
        json=_peer_body(base_url="http://169.254.169.254"),
        headers=_hdr(owner),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "SSRF_BLOCKED"


def _rpc_handler(calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/.well-known/agent-card.json":
            return httpx.Response(200, json=CARD)
        if request.method == "POST" and request.url.path == "/api/v1/a2a/rpc":
            body = json.loads(request.content)
            calls.append(body)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": body.get("id"),
                    "result": {
                        "kind": "task",
                        "id": "remote-9",
                        "contextId": "ctx-9",
                        "status": {"state": "completed"},
                        "artifacts": [
                            {"parts": [{"kind": "text", "text": "邻村已收到，地块 12 块。"}]}
                        ],
                    },
                },
            )
        return httpx.Response(404)

    return handler


def test_probe_then_send_task_end_to_end(client, world):
    owner = _login(client, "a2a_owner", world["yard"].id)
    peer = _create_peer(client, owner)
    calls: list[dict] = []
    a2a_service.set_test_transport(httpx.MockTransport(_rpc_handler(calls)))

    probed = client.post(f"{PREFIX}/settings/a2a/peers/{peer['id']}/probe", headers=_hdr(owner))
    assert probed.status_code == 200, probed.text
    assert probed.json()["data"]["card"]["name"] == "邻村小院"

    sent = client.post(
        f"{PREFIX}/settings/a2a/peers/{peer['id']}/tasks",
        json={"skill_id": "yard.summary", "input_text": "请给出概况"},
        headers=_hdr(owner),
    )
    assert sent.status_code == 201, sent.text
    task = sent.json()["data"]
    assert task["status"] == "completed"
    assert "邻村已收到" in task["result_text"]
    assert calls[0]["params"]["message"]["metadata"]["skill"] == "yard.summary"

    listed = client.get(f"{PREFIX}/settings/a2a/tasks?direction=outbound", headers=_hdr(owner))
    assert len(listed.json()["data"]) == 1


def test_probe_failure_records_last_error(client, world):
    owner = _login(client, "a2a_owner", world["yard"].id)
    peer = _create_peer(client, owner)
    a2a_service.set_test_transport(httpx.MockTransport(lambda request: httpx.Response(404)))
    resp = client.post(f"{PREFIX}/settings/a2a/peers/{peer['id']}/probe", headers=_hdr(owner))
    assert resp.status_code == 502
    assert resp.json()["error"]["code"] == "A2A_CARD_INVALID"
    listed = client.get(f"{PREFIX}/settings/a2a/peers", headers=_hdr(owner)).json()["data"]
    assert listed[0]["last_error"]


# ---------------------------------------------------------------- 开关


def test_card_and_inbound_404_when_platform_switch_off(client, world, monkeypatch):
    owner = _login(client, "a2a_owner", world["yard"].id)
    raw = _token(client, owner)
    monkeypatch.setattr(settings, "a2a_enabled", False)

    card = client.get("/.well-known/agent.json")
    assert card.status_code == 404
    assert card.json()["error"]["code"] == "A2A_DISABLED"

    rpc = client.post(
        f"{PREFIX}/a2a/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {"id": "x"}},
        headers=_hdr(raw),
    )
    assert rpc.status_code == 404
    assert rpc.json()["error"]["code"] == "A2A_DISABLED"
