"""v3.24 权限与千人千面：/auth/me 有效权限、逐人授权、工作台视图。"""

from __future__ import annotations

import pytest
from app.modules.auth.repository import create_user
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="千人千面测试小院")
    owner = create_user(db_session, username="perm_owner", password="Passw0rd123")
    admin = create_user(db_session, username="perm_admin", password="Passw0rd123")
    viewer = create_user(db_session, username="perm_viewer", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    db_session.commit()
    return {"yard": yard, "owner": owner, "admin": admin, "viewer": viewer}


def _login(client, username: str, yard_id: str) -> str:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": "Passw0rd123", "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_me_returns_effective_permissions_and_owner_flag(client, world):
    viewer_token = _login(client, "perm_viewer", world["yard"].id)
    me = client.get(f"{PREFIX}/auth/me", headers=_hdr(viewer_token))
    assert me.status_code == 200, me.text
    data = me.json()["data"]
    assert data["yard_id"] == world["yard"].id
    assert data["is_owner"] is False
    assert "knowledge:read" in data["permissions"]
    assert "settings:manage" not in data["permissions"]

    owner_token = _login(client, "perm_owner", world["yard"].id)
    owner_me = client.get(f"{PREFIX}/auth/me", headers=_hdr(owner_token)).json()["data"]
    assert owner_me["is_owner"] is True
    assert "owner:transfer" in owner_me["permissions"]


def test_member_permissions_owner_only_with_protected_confirm(client, world):
    owner_token = _login(client, "perm_owner", world["yard"].id)
    admin_token = _login(client, "perm_admin", world["yard"].id)
    viewer_id = world["viewer"].id

    got = client.get(
        f"{PREFIX}/yards/current/members/{viewer_id}/permissions",
        headers=_hdr(admin_token),
    )
    assert got.status_code == 200, got.text
    payload = got.json()["data"]
    assert payload["role"] == "viewer"
    assert payload["grants"] == []
    assert "knowledge:read" in payload["effective_permissions"]

    denied = client.put(
        f"{PREFIX}/yards/current/members/{viewer_id}/permissions",
        json={"grants": ["llm:manage"]},
        headers=_hdr(admin_token),
    )
    assert denied.status_code == 403

    no_confirm = client.put(
        f"{PREFIX}/yards/current/members/{viewer_id}/permissions",
        json={"grants": ["settings:manage_protected"]},
        headers=_hdr(owner_token),
    )
    assert no_confirm.status_code == 400
    assert no_confirm.json()["error"]["code"] == "PROTECTED_CONFIRM_REQUIRED"

    granted = client.put(
        f"{PREFIX}/yards/current/members/{viewer_id}/permissions",
        json={"grants": ["settings:manage_protected", "llm:manage"], "confirm_protected": True},
        headers=_hdr(owner_token),
    )
    assert granted.status_code == 200, granted.text
    data = granted.json()["data"]
    assert "settings:manage_protected" in data["grants"]
    assert "llm:manage" in data["effective_permissions"]

    # 授权即时生效：被授权者的 /auth/me 也拿到新权限
    viewer_token = _login(client, "perm_viewer", world["yard"].id)
    me = client.get(f"{PREFIX}/auth/me", headers=_hdr(viewer_token)).json()["data"]
    assert "llm:manage" in me["permissions"]

    invalid = client.put(
        f"{PREFIX}/yards/current/members/{viewer_id}/permissions",
        json={"grants": ["owner:transfer"]},
        headers=_hdr(owner_token),
    )
    assert invalid.status_code == 400

    owner_id = world["owner"].id
    self_grant = client.put(
        f"{PREFIX}/yards/current/members/{owner_id}/permissions",
        json={"grants": ["llm:manage"]},
        headers=_hdr(owner_token),
    )
    assert self_grant.status_code == 400


def test_menu_config_roundtrip_and_permission(client, world):
    viewer_token = _login(client, "perm_viewer", world["yard"].id)
    admin_token = _login(client, "perm_admin", world["yard"].id)

    default = client.get(f"{PREFIX}/yards/current/menu", headers=_hdr(viewer_token))
    assert default.status_code == 200, default.text
    assert default.json()["data"]["default_mode"] == "standard"

    denied = client.put(
        f"{PREFIX}/yards/current/menu",
        json={"default_mode": "minimal"},
        headers=_hdr(viewer_token),
    )
    assert denied.status_code == 403

    updated = client.put(
        f"{PREFIX}/yards/current/menu",
        json={
            "default_mode": "minimal",
            "role_modes": {"expert": "standard", "viewer": "minimal"},
            "profiles": [
                {
                    "id": "field",
                    "name": "田间视图",
                    "mode": "minimal",
                    "groups": [{"label": "常用", "items": [{"key": "farm", "title": "农业台账"}]}],
                }
            ],
        },
        headers=_hdr(admin_token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["default_mode"] == "minimal"
    assert updated.json()["data"]["profiles"][0]["id"] == "field"

    invalid = client.put(
        f"{PREFIX}/yards/current/menu",
        json={"default_mode": "ultra"},
        headers=_hdr(admin_token),
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "MENU_PROFILE_INVALID"

    again = client.get(f"{PREFIX}/yards/current/menu", headers=_hdr(viewer_token))
    assert again.json()["data"]["default_mode"] == "minimal"
