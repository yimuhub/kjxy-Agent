"""v3.25 模型中心接口：能力清单、三层绑定、解析预览与权限闸门。"""

from __future__ import annotations

import pytest
from app.modules.auth.repository import create_user
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="模型中心测试小院")
    owner = create_user(db_session, username="llm_owner", password="Passw0rd123")
    viewer = create_user(db_session, username="llm_viewer", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    db_session.commit()
    return {"yard": yard, "owner": owner, "viewer": viewer}


def _login(client, username: str, yard_id: str) -> str:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": "Passw0rd123", "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _mk_supplier(client, token: str, name: str) -> str:
    resp = client.post(
        f"{PREFIX}/llm/providers",
        headers=_hdr(token),
        json={
            "name": name,
            "provider_type": "deepseek",
            "base_url": "https://api.example.test",
            "model_id": f"{name}-model",
            "api_key": "sk-integration-test-key",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def test_capabilities_lists_every_binding_target_in_chinese(client, world):
    token = _login(client, "llm_owner", world["yard"].id)
    data = client.get(f"{PREFIX}/llm/capabilities", headers=_hdr(token)).json()["data"]
    labels = {item["label"] for item in data["capabilities"]}
    assert {"专业助手对话", "对话内方案生成", "知识向量化", "设备数据解读"} <= labels
    assert [s["type"] for s in data["scope_types"]] == ["global", "purpose", "agent"]
    # 解析档位按真实优先级顺序下发，前端直接照排展示
    assert data["levels"][0]["level"] == "agent_bound"
    assert data["max_fallback"] == 3


def test_bindings_roundtrip_and_resolution_preview(client, world):
    token = _login(client, "llm_owner", world["yard"].id)
    primary = _mk_supplier(client, token, "primary")
    backup = _mk_supplier(client, token, "backup")

    saved = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={
            "scope_type": "global",
            "scope_key": "",
            "entries": [
                {"supplier_id": primary},
                {"supplier_id": backup, "model_name": "backup-72b"},
            ],
        },
    )
    assert saved.status_code == 200, saved.text
    items = saved.json()["data"]["items"]
    assert [i["supplier_id"] for i in items] == [primary, backup]
    assert items[0]["priority"] > items[1]["priority"]  # 提交顺序即兜底顺序
    assert items[1]["model_name"] == "backup-72b"
    assert items[0]["supplier_name"] == "primary"

    listing = client.get(
        f"{PREFIX}/llm/bindings", headers=_hdr(token), params={"scope_type": "global"}
    ).json()["data"]
    assert len(listing["items"]) == 2

    preview = client.get(
        f"{PREFIX}/llm/bindings/resolution", headers=_hdr(token), params={"purpose": "chat_plan"}
    ).json()["data"]
    assert preview["resolved_supplier_id"] == primary
    assert preview["purpose_label"] == "对话内方案生成"
    assert [h["supplier_id"] for h in preview["hops"]] == [primary, backup]

    deleted = client.delete(f"{PREFIX}/llm/bindings/{items[1]['id']}", headers=_hdr(token))
    assert deleted.status_code == 200, deleted.text
    after = client.get(
        f"{PREFIX}/llm/bindings", headers=_hdr(token), params={"scope_type": "global"}
    ).json()["data"]
    assert [i["supplier_id"] for i in after["items"]] == [primary]


def test_purpose_binding_wins_over_global_in_preview(client, world):
    token = _login(client, "llm_owner", world["yard"].id)
    general = _mk_supplier(client, token, "general")
    doc = _mk_supplier(client, token, "doc-only")
    for scope, key, supplier in (("global", "", general), ("purpose", "chat_document", doc)):
        resp = client.put(
            f"{PREFIX}/llm/bindings",
            headers=_hdr(token),
            json={"scope_type": scope, "scope_key": key, "entries": [{"supplier_id": supplier}]},
        )
        assert resp.status_code == 200, resp.text

    data = client.get(
        f"{PREFIX}/llm/bindings/resolution",
        headers=_hdr(token),
        params={"purpose": "chat_document"},
    ).json()["data"]
    assert data["resolved_supplier_id"] == doc
    assert data["hops"][0]["level"] == "purpose_binding"
    assert data["hops"][1]["level"] == "yard_default"

    # 未绑定的能力仍走默认档
    other = client.get(
        f"{PREFIX}/llm/bindings/resolution",
        headers=_hdr(token),
        params={"purpose": "embedding"},
    ).json()["data"]
    assert other["resolved_supplier_id"] == general


def test_binding_validation_errors_are_explicit(client, world):
    token = _login(client, "llm_owner", world["yard"].id)
    ids = [_mk_supplier(client, token, f"s{i}") for i in range(4)]

    too_many = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={
            "scope_type": "global",
            "entries": [{"supplier_id": sid} for sid in ids],
        },
    )
    assert too_many.status_code == 400
    assert too_many.json()["error"]["code"] == "BINDING_TOO_MANY"

    unknown = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={"scope_type": "purpose", "scope_key": "not_a_capability", "entries": []},
    )
    assert unknown.status_code == 400
    assert "未知能力码" in unknown.json()["error"]["message"]

    missing_key = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={"scope_type": "agent", "entries": [{"supplier_id": ids[0]}]},
    )
    assert missing_key.status_code == 400


def test_member_without_llm_permission_is_rejected(client, world):
    token = _login(client, "llm_viewer", world["yard"].id)
    assert client.get(f"{PREFIX}/llm/capabilities", headers=_hdr(token)).status_code == 403
    denied = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={"scope_type": "global", "entries": []},
    )
    assert denied.status_code == 403
    assert "llm:manage" in denied.json()["error"]["message"]
