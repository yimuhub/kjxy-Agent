"""系统设置与角色矩阵集成测试（路由已由 app/api/v1/router.py 统一挂载）。"""

from __future__ import annotations

import pytest
from app.modules.auth.repository import create_user
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="设置角色测试小院")
    owner = create_user(db_session, username="set_owner", password="Passw0rd123")
    admin = create_user(db_session, username="set_admin", password="Passw0rd123")
    viewer = create_user(db_session, username="set_viewer", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    db_session.commit()
    return {"yard": yard, "owner": owner, "admin": admin, "viewer": viewer}


def _login(client, username: str, password: str, yard_id: str) -> str:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": password, "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_get_settings_owner_admin_only(client, world):
    admin_token = _login(client, "set_admin", "Passw0rd123", world["yard"].id)
    resp = client.get(f"{PREFIX}/yards/current/settings", headers=_hdr(admin_token))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["registration"]["mode"] == "approval"
    assert "search" in data and "security" in data
    assert data["roles"] and data["permission_groups"]
    assert data["role_labels"]["yard_owner"] == "小院负责人"
    # 搜索密钥不回显明文
    assert "tavily_api_key" not in data["search"]

    viewer_token = _login(client, "set_viewer", "Passw0rd123", world["yard"].id)
    denied = client.get(f"{PREFIX}/yards/current/settings", headers=_hdr(viewer_token))
    assert denied.status_code == 403


def test_update_registration_and_search_settings(client, world):
    owner_token = _login(client, "set_owner", "Passw0rd123", world["yard"].id)
    admin_token = _login(client, "set_admin", "Passw0rd123", world["yard"].id)

    updated = client.put(
        f"{PREFIX}/yards/current/settings/registration",
        json={"mode": "open"},
        headers=_hdr(owner_token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["registration"]["mode"] == "open"

    invalid = client.put(
        f"{PREFIX}/yards/current/settings/registration",
        json={"mode": "weird"},
        headers=_hdr(owner_token),
    )
    assert invalid.status_code == 400

    search = client.put(
        f"{PREFIX}/yards/current/settings/search",
        json={"daily_limit": 100, "provider": "tavily", "tavily_api_key": "tvly-secret"},
        headers=_hdr(admin_token),
    )
    assert search.status_code == 200, search.text
    assert search.json()["data"]["search"]["daily_limit"] == 100
    assert search.json()["data"]["search"]["tavily_configured"] is True

    # 管理员不能把默认角色设为管理员（仅负责人可设置）
    elevated = client.put(
        f"{PREFIX}/yards/current/settings/registration",
        json={"default_role": "yard_admin"},
        headers=_hdr(admin_token),
    )
    assert elevated.status_code == 403

    unknown = client.put(
        f"{PREFIX}/yards/current/settings/unknown",
        json={"x": 1},
        headers=_hdr(owner_token),
    )
    assert unknown.status_code == 400


def test_update_skill_lab_settings(client, world):
    owner_token = _login(client, "set_owner", "Passw0rd123", world["yard"].id)
    admin_token = _login(client, "set_admin", "Passw0rd123", world["yard"].id)
    viewer_token = _login(client, "set_viewer", "Passw0rd123", world["yard"].id)

    # 读取：skill_lab 随默认设置一并下发
    read = client.get(f"{PREFIX}/yards/current/settings", headers=_hdr(admin_token))
    assert read.status_code == 200, read.text
    assert read.json()["data"]["skill_lab"]["enabled"] is True
    assert read.json()["data"]["skill_lab"]["daily_limit"] == 20

    # 写入：管理员具备 settings:manage
    updated = client.put(
        f"{PREFIX}/yards/current/settings/skill_lab",
        json={"enabled": False, "daily_limit": 5, "use_web": False, "max_sources": 3},
        headers=_hdr(admin_token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["skill_lab"]["enabled"] is False
    assert updated.json()["data"]["skill_lab"]["daily_limit"] == 5
    assert updated.json()["data"]["skill_lab"]["max_sources"] == 3

    # 负责人同样可写
    owner = client.put(
        f"{PREFIX}/yards/current/settings/skill_lab",
        json={"enabled": True, "daily_limit": 20, "use_web": True, "max_sources": 6},
        headers=_hdr(owner_token),
    )
    assert owner.status_code == 200, owner.text

    # 缺少 settings:manage 的普通成员被拒绝
    denied = client.put(
        f"{PREFIX}/yards/current/settings/skill_lab",
        json={"enabled": False},
        headers=_hdr(viewer_token),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    # 越界配额被校验拦截
    invalid = client.put(
        f"{PREFIX}/yards/current/settings/skill_lab",
        json={"daily_limit": 5000},
        headers=_hdr(owner_token),
    )
    assert invalid.status_code == 400

    invalid_sources = client.put(
        f"{PREFIX}/yards/current/settings/skill_lab",
        json={"max_sources": 50},
        headers=_hdr(owner_token),
    )
    assert invalid_sources.status_code == 400


def test_roles_read_and_system_role_write_matrix(client, world):
    admin_token = _login(client, "set_admin", "Passw0rd123", world["yard"].id)
    owner_token = _login(client, "set_owner", "Passw0rd123", world["yard"].id)
    viewer_token = _login(client, "set_viewer", "Passw0rd123", world["yard"].id)

    listed = client.get(f"{PREFIX}/yards/current/roles", headers=_hdr(admin_token))
    assert listed.status_code == 200, listed.text
    codes = {r["code"] for r in listed.json()["data"]["roles"]}
    assert {"yard_owner", "yard_admin", "expert", "operator", "viewer"} <= codes

    viewer_denied = client.get(f"{PREFIX}/yards/current/roles", headers=_hdr(viewer_token))
    assert viewer_denied.status_code == 403

    # v3.24：管理员可以维护专家/操作员/普通成员角色，但不能碰管理员与保护型权限
    admin_system_write = client.put(
        f"{PREFIX}/yards/current/roles/expert",
        json={"name": "高级专家"},
        headers=_hdr(admin_token),
    )
    assert admin_system_write.status_code == 200, admin_system_write.text
    assert admin_system_write.json()["data"]["name"] == "高级专家"

    admin_admin_write = client.put(
        f"{PREFIX}/yards/current/roles/yard_admin",
        json={"name": "越权改名"},
        headers=_hdr(admin_token),
    )
    assert admin_admin_write.status_code == 403
    assert admin_admin_write.json()["error"]["code"] == "ROLE_EDIT_FORBIDDEN"

    admin_protected = client.put(
        f"{PREFIX}/yards/current/roles/expert",
        json={"permissions": ["knowledge:read", "insights:sensitive"]},
        headers=_hdr(admin_token),
    )
    assert admin_protected.status_code == 403
    assert admin_protected.json()["error"]["code"] == "ROLE_EDIT_FORBIDDEN"

    owner_protected_no_confirm = client.put(
        f"{PREFIX}/yards/current/roles/expert",
        json={"permissions": ["knowledge:read", "insights:sensitive"]},
        headers=_hdr(owner_token),
    )
    assert owner_protected_no_confirm.status_code == 400
    assert owner_protected_no_confirm.json()["error"]["code"] == "PROTECTED_CONFIRM_REQUIRED"

    owner_write = client.put(
        f"{PREFIX}/yards/current/roles/expert",
        json={"name": "高级专家", "permissions": ["knowledge:read", "tasks:read"]},
        headers=_hdr(owner_token),
    )
    assert owner_write.status_code == 200, owner_write.text
    assert owner_write.json()["data"]["name"] == "高级专家"

    owner_confirmed = client.put(
        f"{PREFIX}/yards/current/roles/expert",
        json={
            "permissions": ["knowledge:read", "insights:sensitive"],
            "confirm_protected": True,
        },
        headers=_hdr(owner_token),
    )
    assert owner_confirmed.status_code == 200, owner_confirmed.text
    assert "insights:sensitive" in owner_confirmed.json()["data"]["permissions"]

    owner_role_write = client.put(
        f"{PREFIX}/yards/current/roles/yard_owner",
        json={"permissions": ["members:manage"]},
        headers=_hdr(owner_token),
    )
    assert owner_role_write.status_code == 403


def test_custom_role_lifecycle(client, db_session, world):
    admin_token = _login(client, "set_admin", "Passw0rd123", world["yard"].id)
    owner_token = _login(client, "set_owner", "Passw0rd123", world["yard"].id)

    created = client.post(
        f"{PREFIX}/yards/current/roles",
        json={"code": "field_tech", "name": "田间技术员", "permissions": ["knowledge:read"]},
        headers=_hdr(admin_token),
    )
    assert created.status_code == 201, created.text
    assert created.json()["data"]["is_system"] is False

    updated = client.put(
        f"{PREFIX}/yards/current/roles/field_tech",
        json={"name": "田间技术专家", "permissions": ["knowledge:read", "tasks:read"]},
        headers=_hdr(admin_token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["name"] == "田间技术专家"
    assert "tasks:read" in updated.json()["data"]["permissions"]

    protected = client.post(
        f"{PREFIX}/yards/current/roles",
        json={"code": "finance_guy", "name": "财务", "permissions": ["finance:read"]},
        headers=_hdr(admin_token),
    )
    assert protected.status_code == 403

    # 自定义角色被成员使用时禁止删除
    custom_user = create_user(db_session, username="custom_member", password="Passw0rd123")
    db_session.commit()
    add_membership(db_session, yard_id=world["yard"].id, user_id=custom_user.id, role="field_tech")
    db_session.commit()

    in_use = client.delete(f"{PREFIX}/yards/current/roles/field_tech", headers=_hdr(admin_token))
    assert in_use.status_code == 409
    assert in_use.json()["error"]["code"] == "ROLE_IN_USE"

    membership = (
        db_session.query(YardMembership)
        .filter_by(yard_id=world["yard"].id, user_id=custom_user.id)
        .one()
    )
    db_session.delete(membership)
    db_session.commit()

    deleted = client.delete(f"{PREFIX}/yards/current/roles/field_tech", headers=_hdr(admin_token))
    assert deleted.status_code == 200, deleted.text

    system_delete = client.delete(f"{PREFIX}/yards/current/roles/expert", headers=_hdr(owner_token))
    assert system_delete.status_code == 404
