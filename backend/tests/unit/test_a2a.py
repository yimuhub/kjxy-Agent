"""A2A 单元测试（v3.20）：Agent Card、入站令牌/幂等/技能、协作方探测与出站任务。

出网全部 mock：``service.set_test_transport(httpx.MockTransport(...))`` 假扮远端平台，
真实走 httpx 的请求/解析路径。
"""

from __future__ import annotations

import json

import httpx
import pytest
from app.core.config import settings
from app.core.errors import AppError
from app.modules.a2a import service
from app.modules.a2a.models import (
    DIRECTION_INBOUND,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_WORKING,
    A2aEndpoint,
    A2aPeer,
    A2aTask,
)
from app.modules.farm.models import FarmPlot
from app.modules.yards.repository import create_yard

REMOTE = "https://peer.example.com"
REMOTE_RPC = f"{REMOTE}/api/v1/a2a/rpc"
CARD = {
    "protocolVersion": "0.2.5",
    "name": "邻村小院",
    "description": "演示协作方",
    "url": REMOTE_RPC,
    "version": "1.0.0",
    "skills": [{"id": "yard.summary", "name": "小院概况"}],
}


@pytest.fixture(autouse=True)
def _a2a_on(monkeypatch):
    monkeypatch.setattr(settings, "a2a_enabled", True)
    yield
    service.set_test_transport(None)


def _endpoint(db, yard_id: str) -> tuple[A2aEndpoint, str]:
    raw, prefix = service.new_inbound_token()
    endpoint = A2aEndpoint(
        yard_id=yard_id,
        token_hash=service.hash_token(raw),
        token_prefix=prefix,
        enabled=True,
    )
    db.add(endpoint)
    db.flush()
    return endpoint, raw


def _send_payload(message_id: str, skill: str = service.SKILL_YARD_SUMMARY, text: str = "概况"):
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": text}],
                "messageId": message_id,
                "metadata": {"skill": skill},
            }
        },
    }


def _peer(db, yard_id: str, **overrides) -> A2aPeer:
    data = {
        "yard_id": yard_id,
        "name": "邻村小院",
        "base_url": REMOTE,
        "enabled": True,
        "card_json": CARD,
    }
    data.update(overrides)
    peer = A2aPeer(**data)
    db.add(peer)
    db.flush()
    return peer


# ---------------------------------------------------------------- Agent Card


def test_agent_card_lists_whitelisted_skills():
    card = service.build_agent_card("https://yard.example.com/", "3.20.0")
    assert card["protocolVersion"] == service.PROTOCOL_VERSION
    assert card["url"] == "https://yard.example.com/api/v1/a2a/rpc"
    assert card["version"] == "3.20.0"
    ids = {skill["id"] for skill in card["skills"]}
    assert ids == {service.SKILL_YARD_SUMMARY, service.SKILL_KNOWLEDGE_SEARCH}
    assert card["capabilities"]["streaming"] is False


def test_card_validation_rejects_malformed_cards():
    for bad in (
        None,
        [],
        {"skills": []},
        {"name": "", "skills": []},
        {"name": "x"},
        {"name": "x", "skills": [{"name": "缺 id"}]},
        {"name": "x", "skills": [], "url": "ftp://example.com"},
    ):
        with pytest.raises(AppError) as e:
            service.validate_remote_card(bad)
        assert e.value.code == "A2A_CARD_INVALID"


def test_card_summary_is_bounded():
    summary = service.card_summary(CARD)
    assert summary is not None
    assert summary["name"] == "邻村小院"
    assert summary["skill_count"] == 1


# ---------------------------------------------------------------- 入站认证


def test_inbound_requires_valid_enabled_token(db_session):
    yard = create_yard(db_session, name="A2A 小院")
    endpoint, raw = _endpoint(db_session, yard.id)
    assert service.resolve_endpoint(db_session, raw).id == endpoint.id

    with pytest.raises(AppError) as e:
        service.resolve_endpoint(db_session, "a2a_wrong")
    assert e.value.code == "A2A_UNAUTHORIZED"

    endpoint.enabled = False
    db_session.flush()
    with pytest.raises(AppError) as e:
        service.resolve_endpoint(db_session, raw)
    assert e.value.code == "A2A_UNAUTHORIZED"


def test_inbound_disabled_by_platform_switch(db_session, monkeypatch):
    monkeypatch.setattr(settings, "a2a_enabled", False)
    with pytest.raises(AppError) as e:
        service.resolve_endpoint(db_session, "any")
    assert e.value.code == "A2A_DISABLED"


# ---------------------------------------------------------------- 入站任务


def test_message_send_summary_is_scoped_to_endpoint_yard(db_session):
    yard_a = create_yard(db_session, name="A 小院")
    yard_b = create_yard(db_session, name="B 小院")
    db_session.add(FarmPlot(yard_id=yard_a.id, name="东一号田"))
    db_session.add(FarmPlot(yard_id=yard_a.id, name="东二号田"))
    db_session.add(FarmPlot(yard_id=yard_b.id, name="西一号田"))
    db_session.flush()
    endpoint, _ = _endpoint(db_session, yard_a.id)

    resp = service.handle_rpc(db_session, endpoint, _send_payload("msg-1"), request_id="req-1")
    task = resp["result"]
    assert task["status"]["state"] == STATE_COMPLETED
    text = task["artifacts"][0]["parts"][0]["text"]
    assert "地块 2 块" in text


def test_message_send_is_idempotent_by_message_id(db_session):
    yard = create_yard(db_session, name="幂等小院")
    endpoint, _ = _endpoint(db_session, yard.id)
    first = service.handle_rpc(db_session, endpoint, _send_payload("msg-same"), request_id="req-1")[
        "result"
    ]
    second = service.handle_rpc(
        db_session, endpoint, _send_payload("msg-same"), request_id="req-2"
    )["result"]
    assert first["id"] == second["id"]
    assert db_session.query(A2aTask).filter_by(yard_id=yard.id).count() == 1


def test_unknown_skill_is_jsonrpc_error(db_session):
    yard = create_yard(db_session, name="未知技能小院")
    endpoint, _ = _endpoint(db_session, yard.id)
    resp = service.handle_rpc(
        db_session, endpoint, _send_payload("msg-2", skill="farm.delete_all"), request_id="req"
    )
    assert resp["error"]["code"] == -32602
    assert "yard.summary" in resp["error"]["message"]
    assert db_session.query(A2aTask).count() == 0


def test_knowledge_search_skill_uses_injected_hits(db_session, monkeypatch):
    from app.modules.knowledge import service as knowledge_service

    yard = create_yard(db_session, name="知识小院")
    endpoint, _ = _endpoint(db_session, yard.id)

    def fake_search(db, ctx, query, top_k=5, mode=None):
        assert ctx.yard_id == yard.id
        assert query == "分蘖期追肥"
        return [
            knowledge_service.ChunkHit(
                chunk_id="c1",
                document_id="d1",
                title="水稻施肥手册",
                source="manual.md",
                page_number=3,
                quote="分蘖期亩施尿素 5-8 公斤。",
                score=0.9,
                trust_level="high",
            )
        ]

    monkeypatch.setattr(knowledge_service, "search", fake_search)
    resp = service.handle_rpc(
        db_session,
        endpoint,
        _send_payload("msg-3", skill=service.SKILL_KNOWLEDGE_SEARCH, text="分蘖期追肥"),
        request_id="req",
    )
    text = resp["result"]["artifacts"][0]["parts"][0]["text"]
    assert "水稻施肥手册" in text and "尿素" in text


def test_knowledge_search_failure_marks_task_failed(db_session):
    yard = create_yard(db_session, name="空查询小院")
    endpoint, _ = _endpoint(db_session, yard.id)
    resp = service.handle_rpc(
        db_session,
        endpoint,
        _send_payload("msg-4", skill=service.SKILL_KNOWLEDGE_SEARCH, text="   "),
        request_id="req",
    )
    assert resp["result"]["status"]["state"] == STATE_FAILED
    assert resp["result"]["metadata"]["errorCode"] == "INVALID_REQUEST"


def test_tasks_get_and_cancel_state_machine(db_session):
    yard = create_yard(db_session, name="状态机小院")
    endpoint, _ = _endpoint(db_session, yard.id)
    task = A2aTask(
        yard_id=yard.id,
        direction=DIRECTION_INBOUND,
        skill_id=service.SKILL_YARD_SUMMARY,
        status=STATE_WORKING,
        op_key="in-manual",
    )
    db_session.add(task)
    db_session.flush()

    got = service.handle_rpc(
        db_session,
        endpoint,
        {"jsonrpc": "2.0", "id": 2, "method": "tasks/get", "params": {"id": task.id}},
        request_id="req",
    )
    assert got["result"]["status"]["state"] == STATE_WORKING

    canceled = service.handle_rpc(
        db_session,
        endpoint,
        {"jsonrpc": "2.0", "id": 3, "method": "tasks/cancel", "params": {"id": task.id}},
        request_id="req",
    )
    assert canceled["result"]["status"]["state"] == "canceled"

    again = service.handle_rpc(
        db_session,
        endpoint,
        {"jsonrpc": "2.0", "id": 4, "method": "tasks/cancel", "params": {"id": task.id}},
        request_id="req",
    )
    assert again["error"]["code"] == -32002

    missing = service.handle_rpc(
        db_session,
        endpoint,
        {"jsonrpc": "2.0", "id": 5, "method": "tasks/get", "params": {"id": "nope"}},
        request_id="req",
    )
    assert missing["error"]["code"] == -32001


def test_unsupported_method_and_bad_version(db_session):
    yard = create_yard(db_session, name="方法小院")
    endpoint, _ = _endpoint(db_session, yard.id)
    bad_version = service.handle_rpc(
        db_session, endpoint, {"jsonrpc": "1.0", "id": 1, "method": "tasks/get"}, request_id="r"
    )
    assert bad_version["error"]["code"] == -32600
    unknown = service.handle_rpc(
        db_session, endpoint, {"jsonrpc": "2.0", "id": 1, "method": "tasks/list"}, request_id="r"
    )
    assert unknown["error"]["code"] == -32601


# ---------------------------------------------------------------- 出站


def test_base_url_rejects_metadata_address():
    with pytest.raises(AppError) as e:
        service.normalize_base_url("http://169.254.169.254")
    assert e.value.code == "SSRF_BLOCKED"
    with pytest.raises(AppError) as e:
        service.normalize_base_url("ftp://peer.example.com")
    assert e.value.code == "INVALID_REQUEST"


def test_peer_token_is_encrypted_and_masked(db_session):
    yard = create_yard(db_session, name="令牌小院")
    peer = _peer(db_session, yard.id, token_enc=service.encrypt_peer_token("secret-token"))
    assert peer.token_enc and "secret-token" not in peer.token_enc
    out = service.peer_to_out(peer)
    assert out["token_configured"] is True
    assert "secret-token" not in str(out)


def test_probe_fetches_and_caches_card(db_session):
    yard = create_yard(db_session, name="探测小院")
    peer = _peer(db_session, yard.id, card_json=None)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/agent-card.json":
            return httpx.Response(200, json=CARD)
        return httpx.Response(404)

    service.set_test_transport(httpx.MockTransport(handler))
    result = service.probe_peer(db_session, peer, request_id="req")
    assert result["ok"] is True
    assert peer.card_json == CARD
    assert peer.last_error is None


def test_probe_404_on_both_paths_is_card_invalid(db_session):
    yard = create_yard(db_session, name="无卡片小院")
    peer = _peer(db_session, yard.id, card_json=None)
    service.set_test_transport(httpx.MockTransport(lambda request: httpx.Response(404)))
    with pytest.raises(AppError) as e:
        service.probe_peer(db_session, peer, request_id="req")
    assert e.value.code == "A2A_CARD_INVALID"


def _remote_rpc(calls: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(
            {
                "path": request.url.path,
                "body": body,
                "auth": request.headers.get("authorization"),
            }
        )
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body.get("id"),
                "result": {
                    "kind": "task",
                    "id": "remote-task-1",
                    "contextId": "ctx-1",
                    "status": {"state": "completed"},
                    "artifacts": [
                        {
                            "parts": [
                                {
                                    "kind": "text",
                                    "text": "远端收到："
                                    + body["params"]["message"]["parts"][0]["text"],
                                }
                            ]
                        }
                    ],
                },
            },
        )

    return handler


def test_send_task_records_result_and_uses_bearer(db_session):
    yard = create_yard(db_session, name="出站小院")
    peer = _peer(db_session, yard.id, token_enc=service.encrypt_peer_token("peer-secret"))
    calls: list[dict] = []
    service.set_test_transport(httpx.MockTransport(_remote_rpc(calls)))
    task = service.send_task(
        db_session,
        peer,
        skill_id=service.SKILL_YARD_SUMMARY,
        input_text="给我概况",
        user_id="u1",
        request_id="req",
    )
    assert task.status == STATE_COMPLETED
    assert task.direction == "outbound"
    assert task.remote_task_id == "remote-task-1"
    assert "远端收到：给我概况" in (task.result_json or {}).get("text", "")
    assert calls[0]["path"] == "/api/v1/a2a/rpc"
    assert calls[0]["auth"] == "Bearer peer-secret"
    assert calls[0]["body"]["method"] == "message/send"
    assert calls[0]["body"]["params"]["message"]["metadata"]["skill"] == "yard.summary"


def test_send_task_remote_500_marks_failed(db_session):
    yard = create_yard(db_session, name="失败小院")
    peer = _peer(db_session, yard.id)
    service.set_test_transport(httpx.MockTransport(lambda request: httpx.Response(500)))
    task = service.send_task(
        db_session,
        peer,
        skill_id=service.SKILL_YARD_SUMMARY,
        input_text="概况",
        user_id="u1",
        request_id="req",
    )
    assert task.status == STATE_FAILED
    assert task.error_code == "A2A_REMOTE_ERROR"
    assert peer.last_error


def test_send_task_rejects_unknown_skill_and_disabled_peer(db_session, monkeypatch):
    yard = create_yard(db_session, name="校验小院")
    peer = _peer(db_session, yard.id)
    with pytest.raises(AppError) as e:
        service.send_task(
            db_session,
            peer,
            skill_id="nope",
            input_text="",
            user_id="u1",
            request_id="req",
        )
    assert e.value.code == "A2A_SKILL_UNKNOWN"

    peer.enabled = False
    db_session.flush()
    with pytest.raises(AppError) as e:
        service.send_task(
            db_session,
            peer,
            skill_id=service.SKILL_YARD_SUMMARY,
            input_text="",
            user_id="u1",
            request_id="req",
        )
    assert e.value.code == "A2A_PEER_DISABLED"

    monkeypatch.setattr(settings, "a2a_enabled", False)
    with pytest.raises(AppError) as e:
        service.send_task(
            db_session,
            peer,
            skill_id=service.SKILL_YARD_SUMMARY,
            input_text="",
            user_id="u1",
            request_id="req",
        )
    assert e.value.code == "A2A_DISABLED"
