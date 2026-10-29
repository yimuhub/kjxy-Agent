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


# ---------- v3.26：模型中心 → 智能体模型（聚合 + 接管旧设置） ----------


def _mk_yard_agent(db_session, yard_id: str, *, code: str, name: str, supplier_id=None):
    from app.modules.agents.models import YardAgent

    agent = YardAgent(
        yard_id=yard_id,
        agent_code=code,
        name=name,
        persona_prompt="你是测试助手。",
        task_prompt_template="{{question}}",
        allowed_tools=[],
        bound_skills=[],
        knowledge_scopes=["yard"],
        status="published",
        visibility="public",
        supplier_id=supplier_id,
        model_name="legacy-sub-model" if supplier_id else None,
    )
    db_session.add(agent)
    db_session.commit()
    return agent


def test_agent_model_overview_lists_platform_and_yard_agents(client, world, db_session):
    token = _login(client, "llm_owner", world["yard"].id)
    _mk_yard_agent(db_session, world["yard"].id, code="growth_helper", name="增长助手")

    resp = client.get(f"{PREFIX}/llm/bindings/agents", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    codes = {item["code"] for item in data["items"]}
    assert "orchestrator" in codes
    assert "growth_helper" in codes
    row = next(item for item in data["items"] if item["code"] == "growth_helper")
    assert row["kind"] == "yard"
    assert row["mode"] == "follow"
    assert row["bindings"] == []
    assert data["max_fallback"] == 3
    purposes = {item["purpose"] for item in data["visual"]}
    assert purposes == {"image_generation", "video_generation"}
    labels = {item["label"] for item in data["visual"]}
    assert {"图像生成", "视频生成"} <= labels


def test_agent_binding_save_clears_legacy_agent_model(client, world, db_session):
    token = _login(client, "llm_owner", world["yard"].id)
    legacy_supplier = _mk_supplier(client, token, "legacy")
    new_supplier = _mk_supplier(client, token, "picked")
    agent = _mk_yard_agent(
        db_session, world["yard"].id, code="legacy_agent", name="旧设置助手",
        supplier_id=legacy_supplier,
    )

    before = client.get(f"{PREFIX}/llm/bindings/agents", headers=_hdr(token)).json()["data"]
    row = next(item for item in before["items"] if item["code"] == "legacy_agent")
    assert row["mode"] == "legacy"
    assert row["legacy"]["supplier_name"] == "legacy"
    assert row["resolved"]["level_label"] == "助手指定模型"

    saved = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={
            "scope_type": "agent",
            "scope_key": "legacy_agent",
            "entries": [{"supplier_id": new_supplier}],
        },
    )
    assert saved.status_code == 200, saved.text

    db_session.expire_all()
    refreshed = db_session.get(type(agent), agent.id)
    assert refreshed.supplier_id is None
    assert refreshed.model_name is None

    after = client.get(f"{PREFIX}/llm/bindings/agents", headers=_hdr(token)).json()["data"]
    row = next(item for item in after["items"] if item["code"] == "legacy_agent")
    assert row["mode"] == "custom"
    assert row["legacy"] is None
    assert row["bindings"][0]["supplier_name"] == "picked"
    assert row["resolved"]["supplier_name"] == "picked"
    assert row["resolved"]["level_label"] == "按智能体绑定"

    # 清空即回到跟随中心
    cleared = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={"scope_type": "agent", "scope_key": "legacy_agent", "entries": []},
    )
    assert cleared.status_code == 200
    final = client.get(f"{PREFIX}/llm/bindings/agents", headers=_hdr(token)).json()["data"]
    row = next(item for item in final["items"] if item["code"] == "legacy_agent")
    assert row["mode"] == "follow"


def test_agent_model_overview_requires_llm_manage(client, world):
    token = _login(client, "llm_viewer", world["yard"].id)
    resp = client.get(f"{PREFIX}/llm/bindings/agents", headers=_hdr(token))
    assert resp.status_code == 403
