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


def _mk_image_supplier(client, token: str, name: str) -> str:
    resp = client.post(
        f"{PREFIX}/llm/providers",
        headers=_hdr(token),
        json={
            "name": name,
            "provider_type": "openai_images",
            "base_url": "https://img.example.test/v1",
            "model_id": f"{name}-image",
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


def test_visual_capability_ignores_chat_supplier(client, world):
    """图像/视频的「当前生效」只认对应类型供应商，不能被对话模型的小院兜底顶替。"""
    token = _login(client, "llm_owner", world["yard"].id)
    _mk_supplier(client, token, "chat-only")

    data = client.get(f"{PREFIX}/llm/bindings/agents", headers=_hdr(token)).json()["data"]
    image = next(i for i in data["visual"] if i["purpose"] == "image_generation")
    video = next(i for i in data["visual"] if i["purpose"] == "video_generation")
    assert image["bindings"] == []
    assert image["resolved"] is None
    assert video["resolved"] is None

    image_id = _mk_image_supplier(client, token, "img-main")
    saved = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={
            "scope_type": "purpose",
            "scope_key": "image_generation",
            "entries": [{"supplier_id": image_id}],
        },
    )
    assert saved.status_code == 200, saved.text
    data = client.get(f"{PREFIX}/llm/bindings/agents", headers=_hdr(token)).json()["data"]
    image = next(i for i in data["visual"] if i["purpose"] == "image_generation")
    assert image["resolved"]["supplier_id"] == image_id
    assert image["resolved"]["supplier_name"] == "img-main"
    assert image["resolved"]["level_label"]


def test_visual_purpose_rejects_chat_supplier(client, world):
    """出图/出视频的绑定只收对应类型供应商，保存时就拒绝，不留"配了也用不了"的状态。"""
    token = _login(client, "llm_owner", world["yard"].id)
    chat_id = _mk_supplier(client, token, "chat-only")
    resp = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={
            "scope_type": "purpose",
            "scope_key": "image_generation",
            "entries": [{"supplier_id": chat_id}],
        },
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"
    assert "对应类型" in resp.json()["error"]["message"]


def test_agent_binding_save_clears_legacy_agent_model(client, world, db_session):
    token = _login(client, "llm_owner", world["yard"].id)
    legacy_supplier = _mk_supplier(client, token, "legacy")
    new_supplier = _mk_supplier(client, token, "picked")
    agent = _mk_yard_agent(
        db_session,
        world["yard"].id,
        code="legacy_agent",
        name="旧设置助手",
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


# ---------- v3.37：模态优先 + 助手专属能力 ----------


def _mk_model_row(
    db_session,
    supplier_id: str,
    *,
    key: str,
    modality: str,
    protocol: str = "openai_chat",
):
    from app.modules.llm.supplier_models import SupplierModel

    row = SupplierModel(
        supplier_id=supplier_id,
        model_key=key,
        upstream_model_id=key,
        display_name=key,
        modality=modality,
        protocol=protocol,
        status=1,
        source="manual",
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    return row


def test_visual_binding_accepts_generic_supplier_by_modality(client, world, db_session):
    """聚合网关的图片模型协议常仍是 openai_chat：按模态也要能绑进图像能力。"""
    token = _login(client, "llm_owner", world["yard"].id)
    supplier_id = _mk_generic_supplier(client, token, "gateway-media")
    row = _mk_model_row(db_session, supplier_id, key="agnes-image-2.5", modality="image")

    resp = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={
            "scope_type": "purpose",
            "scope_key": "image_generation",
            "entries": [{"supplier_id": supplier_id, "supplier_model_id": row.id, "priority": 10}],
        },
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["data"]["items"]
    assert items[0]["supplier_model_id"] == row.id
    assert items[0]["modality"] == "image"


def test_visual_binding_rejects_chat_only_supplier(client, world):
    token = _login(client, "llm_owner", world["yard"].id)
    supplier_id = _mk_supplier(client, token, "chat-only")
    resp = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={
            "scope_type": "purpose",
            "scope_key": "image_generation",
            "entries": [{"supplier_id": supplier_id, "priority": 10}],
        },
    )
    assert resp.status_code == 400
    assert "图像生成" in resp.json()["error"]["message"]


def test_agent_purpose_binding_resolves_before_visual_default(client, world, db_session):
    """助手专属能力：同一助手单独指定图像模型，且优先于全局视觉能力绑定。"""
    from app.modules.llm import binding_service

    token = _login(client, "llm_owner", world["yard"].id)
    shared = _mk_generic_supplier(client, token, "gateway-shared")
    shared_model = _mk_model_row(db_session, shared, key="shared-image", modality="image")
    own = _mk_generic_supplier(client, token, "gateway-own")
    own_model = _mk_model_row(db_session, own, key="own-image", modality="image")

    for scope_type, scope_key, supplier, model in (
        ("purpose", "image_generation", shared, shared_model),
        ("agent_purpose", "agent-x::image_generation", own, own_model),
    ):
        resp = client.put(
            f"{PREFIX}/llm/bindings",
            headers=_hdr(token),
            json={
                "scope_type": scope_type,
                "scope_key": scope_key,
                "entries": [
                    {"supplier_id": supplier, "supplier_model_id": model.id, "priority": 10}
                ],
            },
        )
        assert resp.status_code == 200, resp.text

    db_session.expire_all()
    summary = binding_service.resolution_summary(
        db_session, world["yard"].id, purpose="image_generation", agent_code="agent-x"
    )
    assert summary["hops"][0]["level"] == "agent_purpose_binding"
    assert summary["hops"][0]["supplier_id"] == own

    # 别的助手仍然用全局图像模型
    other = binding_service.resolution_summary(
        db_session, world["yard"].id, purpose="image_generation", agent_code="agent-y"
    )
    head = next(hop for hop in other["hops"] if hop.get("supplier_id"))
    assert head["supplier_id"] == shared


def test_agent_overview_exposes_capability_bindings(client, world, db_session):
    from app.modules.agents.definitions import platform_agent_definitions

    code = str(platform_agent_definitions()[0]["code"])
    token = _login(client, "llm_owner", world["yard"].id)
    own = _mk_generic_supplier(client, token, "gateway-cap")
    model = _mk_model_row(db_session, own, key="cap-video", modality="video")
    client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={
            "scope_type": "agent_purpose",
            "scope_key": f"{code}::video_generation",
            "entries": [{"supplier_id": own, "supplier_model_id": model.id, "priority": 10}],
        },
    )
    data = client.get(f"{PREFIX}/llm/bindings/agents", headers=_hdr(token)).json()["data"]
    row = next(item for item in data["items"] if item["code"] == code)
    caps = {cap["purpose"]: cap for cap in row["capabilities"]}
    assert caps["video_generation"]["bindings"][0]["supplier_model_id"] == model.id
    assert caps["image_generation"]["bindings"] == []


def test_agent_purpose_binding_rejects_unknown_purpose(client, world):
    token = _login(client, "llm_owner", world["yard"].id)
    supplier_id = _mk_supplier(client, token, "agent-purpose-bad")
    resp = client.put(
        f"{PREFIX}/llm/bindings",
        headers=_hdr(token),
        json={
            "scope_type": "agent_purpose",
            "scope_key": "agent-x::not_a_purpose",
            "entries": [{"supplier_id": supplier_id, "priority": 10}],
        },
    )
    assert resp.status_code == 400


def _mk_generic_supplier(client, token: str, name: str) -> str:
    resp = client.post(
        f"{PREFIX}/llm/providers",
        headers=_hdr(token),
        json={
            "name": name,
            "provider_type": "openai_compatible",
            "base_url": "https://gateway.example.test/v1",
            "model_id": f"{name}-default",
            "api_key": "sk-integration-test-key",
        },
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]
