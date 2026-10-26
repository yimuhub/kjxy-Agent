"""自助资料与改密集成测试：旧令牌失效、唯一校验、审计。"""

from __future__ import annotations

import pytest
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.auth.repository import create_user
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="自助资料测试小院")
    user = create_user(db_session, username="self_user", password="Passw0rd123")
    other = create_user(
        db_session, username="other_user", password="Passw0rd123", email="taken@x.com"
    )
    add_membership(db_session, yard_id=yard.id, user_id=user.id, role="operator")
    db_session.commit()
    return {"yard": yard, "user": user, "other": other}


def _login(client, username: str, password: str, yard_id: str) -> dict:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": password, "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_update_profile_with_uniqueness(client, db_session, world):
    login = _login(client, "self_user", "Passw0rd123", world["yard"].id)
    token = login["access_token"]

    resp = client.patch(
        f"{PREFIX}/auth/me",
        json={
            "display_name": "新名字",
            "email": "self@x.com",
            "phone": "13800001111",
        },
        headers=_hdr(token),
    )
    assert resp.status_code == 200, resp.text
    user = resp.json()["data"]["user"]
    assert user["display_name"] == "新名字"
    assert user["email"] == "self@x.com"
    assert user["phone"] == "13800001111"

    dup = client.patch(f"{PREFIX}/auth/me", json={"email": "taken@x.com"}, headers=_hdr(token))
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "EMAIL_TAKEN"

    bad_phone = client.patch(f"{PREFIX}/auth/me", json={"phone": "123"}, headers=_hdr(token))
    assert bad_phone.status_code == 400

    actions = {row.action for row in db_session.query(AuditLog).all()}
    assert "profile_updated" in actions


def test_change_password_invalidates_old_token(client, db_session, world):
    login = _login(client, "self_user", "Passw0rd123", world["yard"].id)
    old_token = login["access_token"]

    changed = client.post(
        f"{PREFIX}/auth/change-password",
        json={"old_password": "Passw0rd123", "new_password": "BrandNewPass9"},
        headers=_hdr(old_token),
    )
    assert changed.status_code == 200, changed.text
    data = changed.json()["data"]
    assert data["access_token"] and data["refresh_token"]
    assert data["must_change_password"] is False

    # 旧 access token 因 token_version 变化立即失效
    stale = client.get(f"{PREFIX}/auth/me", headers=_hdr(old_token))
    assert stale.status_code == 401
    assert stale.json()["error"]["code"] == "UNAUTHORIZED"

    fresh = client.get(f"{PREFIX}/auth/me", headers=_hdr(data["access_token"]))
    assert fresh.status_code == 200
    assert fresh.json()["data"]["user"]["must_change_password"] is False

    # 旧密码失效，新密码可登录
    old_login = client.post(
        f"{PREFIX}/auth/login",
        json={"username": "self_user", "password": "Passw0rd123"},
    )
    assert old_login.status_code == 401
    new_login = client.post(
        f"{PREFIX}/auth/login",
        json={"username": "self_user", "password": "BrandNewPass9"},
    )
    assert new_login.status_code == 200

    db_session.expire_all()
    assert db_session.get(User, world["user"].id).token_version == 1
    actions = {row.action for row in db_session.query(AuditLog).all()}
    assert "password_changed" in actions


def test_change_password_validation(client, world):
    token = _login(client, "self_user", "Passw0rd123", world["yard"].id)["access_token"]

    wrong_old = client.post(
        f"{PREFIX}/auth/change-password",
        json={"old_password": "wrong-pass", "new_password": "BrandNewPass9"},
        headers=_hdr(token),
    )
    assert wrong_old.status_code == 400
    assert wrong_old.json()["error"]["code"] == "INVALID_OLD_PASSWORD"

    same = client.post(
        f"{PREFIX}/auth/change-password",
        json={"old_password": "Passw0rd123", "new_password": "Passw0rd123"},
        headers=_hdr(token),
    )
    assert same.status_code == 400
    assert same.json()["error"]["code"] == "PASSWORD_UNCHANGED"

    too_short = client.post(
        f"{PREFIX}/auth/change-password",
        json={"old_password": "Passw0rd123", "new_password": "short"},
        headers=_hdr(token),
    )
    assert too_short.status_code == 400
