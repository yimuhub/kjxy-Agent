"""可用工具接口集成测试（v3.25 B-b）：目录、草稿→启用、详情与权限闸门。"""

from __future__ import annotations

import pytest
from app.modules.auth.repository import create_user
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"

SPEC = {
    "code": "pest.alert_read",
    "name": "告警速读",
    "description": "把今天的设备告警读成人话",
    "input_schema": {
        "type": "object",
        "properties": {"crop": {"type": "string", "description": "作物名"}},
        "required": ["crop"],
    },
    "impl_kind": "prompt",
    "impl": {"prompt_template": "请用三句话总结 {crop} 的今日告警"},
    "risk_level": "read_only",
    "side_effect": False,
}


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="工具目录集成院")
    owner = create_user(db_session, username="tool_owner", password="Passw0rd123")
    admin = create_user(db_session, username="tool_admin", password="Passw0rd123")
    viewer = create_user(db_session, username="tool_viewer", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    # 内置工具元数据行：正常由启动/CLI seed 写入，集成用例里显式补齐
    from app.modules.tools.registry import seed_builtin_tools

    seed_builtin_tools(db_session)
    db_session.commit()
    return {"yard": yard, "owner": owner, "admin": admin, "viewer": viewer}


def _login(client, username: str, yard_id: str) -> dict:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": "Passw0rd123", "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


def test_catalog_lists_sources_and_labels(client, world):
    hdr = _login(client, "tool_admin", world["yard"].id)
    resp = client.get(f"{PREFIX}/tools/catalog", headers=hdr)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["source_labels"]["builtin"] == "内置"
    assert data["risk_labels"]["external_side_effect"].startswith("外部副作用")
    assert data["counts"]["builtin"] >= 1
    assert all(item["readonly"] is True for item in data["items"] if item["source"] == "builtin")


def test_draft_is_hidden_until_enabled(client, world):
    hdr = _login(client, "tool_admin", world["yard"].id)

    created = client.post(f"{PREFIX}/tools", headers=hdr, json={**SPEC, "enabled": False})
    assert created.status_code == 200, created.text
    item = created.json()["data"]
    assert item["enabled"] is False and item["source"] == "custom"

    runtime_list = client.get(f"{PREFIX}/tools", headers=hdr).json()["data"]
    assert item["code"] not in [t["code"] for t in runtime_list]

    catalog = client.get(f"{PREFIX}/tools/catalog", headers=hdr).json()["data"]
    assert item["code"] in [t["code"] for t in catalog["items"]]

    enabled = client.post(f"{PREFIX}/tools/{item['code']}/enable", headers=hdr)
    assert enabled.status_code == 200, enabled.text
    runtime_list = client.get(f"{PREFIX}/tools", headers=hdr).json()["data"]
    assert item["code"] in [t["code"] for t in runtime_list]

    # 停用后再次从可调用清单消失（元数据下线 = 运行不可用）
    client.post(f"{PREFIX}/tools/{item['code']}/disable", headers=hdr)
    after = client.get(f"{PREFIX}/tools", headers=hdr).json()["data"]
    assert item["code"] not in [t["code"] for t in after]


def test_detail_shows_params_example_and_risk(client, world):
    hdr = _login(client, "tool_admin", world["yard"].id)
    code = client.post(f"{PREFIX}/tools", headers=hdr, json={**SPEC, "enabled": True}).json()[
        "data"
    ]["code"]

    detail = client.get(f"{PREFIX}/tools/{code}", headers=hdr)
    assert detail.status_code == 200, detail.text
    data = detail.json()["data"]
    assert data["risk_level"] == "read_only"
    assert "作物名" in data["definition_markdown"]
    assert '"crop"' in data["call_example"]
    assert data["recent_calls"] == []


def test_invalid_schema_and_duplicate_code_rejected(client, world):
    hdr = _login(client, "tool_admin", world["yard"].id)
    bad = client.post(
        f"{PREFIX}/tools",
        headers=hdr,
        json={**SPEC, "code": "bad.tool", "input_schema": {"type": "object", "required": ["zz"]}},
    )
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "TOOL_SPEC_INVALID"

    first = client.post(f"{PREFIX}/tools", headers=hdr, json={**SPEC, "enabled": True})
    assert first.status_code == 200
    dup = client.post(f"{PREFIX}/tools", headers=hdr, json=SPEC)
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "DUPLICATE_TOOL_CODE"


def test_generate_returns_confirmable_draft_without_persisting(client, world):
    """一句话生成：fake 模型产不出合法 JSON 时走确定性兜底草稿，且**不落库**。"""
    hdr = _login(client, "tool_admin", world["yard"].id)
    resp = client.post(
        f"{PREFIX}/tools/generate", headers=hdr, json={"utterance": "帮我把今日告警总结成三句话"}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["source"] in ("model", "fallback")
    assert data["spec"]["impl_kind"] in ("prompt", "http")
    assert "草稿" in data["note"]

    catalog = client.get(f"{PREFIX}/tools/catalog", headers=hdr).json()["data"]
    assert data["spec"]["code"] not in [item["code"] for item in catalog["items"]]

    # 预览确认后才能真正建草稿
    confirmed = client.post(f"{PREFIX}/tools", headers=hdr, json=data["spec"])
    assert confirmed.status_code == 200, confirmed.text


def test_member_without_permission_cannot_manage_tools(client, world):
    hdr = _login(client, "tool_viewer", world["yard"].id)
    assert client.get(f"{PREFIX}/tools/catalog", headers=hdr).status_code == 403
    denied = client.post(f"{PREFIX}/tools", headers=hdr, json=SPEC)
    assert denied.status_code == 403
    assert "tools:manage" in denied.json()["error"]["message"]
    # 但可读「当前可调用」清单（对话页工具选择要用）
    assert client.get(f"{PREFIX}/tools", headers=hdr).status_code == 200


def test_builtin_tools_are_not_deletable(client, world):
    hdr = _login(client, "tool_admin", world["yard"].id)
    catalog = client.get(f"{PREFIX}/tools/catalog", headers=hdr).json()["data"]
    builtin_code = next(i["code"] for i in catalog["items"] if i["source"] == "builtin")
    resp = client.delete(f"{PREFIX}/tools/{builtin_code}", headers=hdr)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "TOOL_READONLY"
