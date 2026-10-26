"""注册与入小院审核集成测试：模式、待审、批准/拒绝、登录门槛。"""

from __future__ import annotations

import pytest
from app.modules.auth.models import User
from app.modules.auth.repository import create_user
from app.modules.yards import settings_service
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import add_membership, create_yard
from app.modules.yards.signup_router import signup_limiter

PREFIX = "/api/v1"


@pytest.fixture(autouse=True)
def _reset_signup_limiter():
    """注册限流是进程级单例：测试间清空，避免相互耗尽配额。"""
    with signup_limiter._lock:
        signup_limiter._failures.clear()
    yield


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="注册审核测试小院")
    owner = create_user(db_session, username="reg_owner", password="Passw0rd123")
    admin = create_user(
        db_session, username="reg_admin", password="Passw0rd123", email="reg_admin@x.com"
    )
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    db_session.commit()
    return {"yard": yard, "owner": owner, "admin": admin}


def _login(client, username: str, password: str, yard_id: str) -> str:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": password, "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _register(yard_id: str, **over) -> dict:
    payload = {
        "username": "newbie1",
        "password": "Passw0rd123",
        "email": "newbie1@x.com",
        "phone": "13912341234",
        "display_name": "新成员",
        "yard_id": yard_id,
        "note": "希望加入",
    }
    payload.update(over)
    return payload


def test_pending_approve_then_login(client, db_session, world):
    yard_id = world["yard"].id
    resp = client.post(f"{PREFIX}/auth/register", json=_register(yard_id))
    assert resp.status_code == 201, resp.text
    body = resp.json()["data"]
    assert body["status"] == "pending"
    request_id = body["request_id"]

    pending = db_session.query(User).filter_by(username="newbie1").one()
    assert pending.status == "pending"

    blocked = client.post(
        f"{PREFIX}/auth/login",
        json={"username": "newbie1", "password": "Passw0rd123"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "ACCOUNT_PENDING"

    owner_token = _login(client, "reg_owner", "Passw0rd123", yard_id)
    listed = client.get(
        f"{PREFIX}/yards/current/registrations?status=pending",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert listed.status_code == 200, listed.text
    assert [i["id"] for i in listed.json()["data"]] == [request_id]

    approved = client.post(
        f"{PREFIX}/yards/current/registrations/{request_id}/approve",
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert approved.status_code == 200, approved.text
    data = approved.json()["data"]
    assert data["request"]["status"] == "approved"
    assert data["member"]["role"] == "viewer"

    db_session.expire_all()
    assert db_session.query(User).filter_by(username="newbie1").one().status == "active"
    membership = (
        db_session.query(YardMembership).filter_by(yard_id=yard_id, user_id=pending.id).one()
    )
    assert membership.role == "viewer" and membership.status == "active"

    token = _login(client, "newbie1", "Passw0rd123", yard_id)
    me = client.get(f"{PREFIX}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["data"]["roles"] == ["viewer"]


def test_reject_disables_account(client, db_session, world):
    yard_id = world["yard"].id
    resp = client.post(f"{PREFIX}/auth/register", json=_register(yard_id, username="rejected1"))
    request_id = resp.json()["data"]["request_id"]
    owner_token = _login(client, "reg_owner", "Passw0rd123", yard_id)

    rejected = client.post(
        f"{PREFIX}/yards/current/registrations/{request_id}/reject",
        json={"reason": "信息不完整"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["data"]["status"] == "rejected"
    assert rejected.json()["data"]["reject_reason"] == "信息不完整"

    blocked = client.post(
        f"{PREFIX}/auth/login", json={"username": "rejected1", "password": "Passw0rd123"}
    )
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "ACCOUNT_DISABLED"


def test_registration_closed_rejected(client, db_session, world):
    yard_id = world["yard"].id
    settings_service.update_setting(
        db_session,
        yard_id=yard_id,
        key="registration",
        value={"mode": "closed"},
        actor_id=world["owner"].id,
        request_id="t",
    )
    db_session.commit()

    resp = client.post(f"{PREFIX}/auth/register", json=_register(yard_id))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "REGISTRATION_CLOSED"


def test_registration_open_joins_immediately(client, db_session, world):
    yard_id = world["yard"].id
    settings_service.update_setting(
        db_session,
        yard_id=yard_id,
        key="registration",
        value={"mode": "open", "default_role": "operator"},
        actor_id=world["owner"].id,
        request_id="t",
    )
    db_session.commit()

    resp = client.post(f"{PREFIX}/auth/register", json=_register(yard_id, username="open_user"))
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["status"] == "active"

    token = _login(client, "open_user", "Passw0rd123", yard_id)
    me = client.get(f"{PREFIX}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["data"]["roles"] == ["operator"]


def test_duplicate_username_and_contact_rejected(client, db_session, world):
    yard_id = world["yard"].id
    resp = client.post(f"{PREFIX}/auth/register", json=_register(yard_id, username="reg_owner"))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "USERNAME_TAKEN"

    resp = client.post(
        f"{PREFIX}/auth/register",
        json=_register(yard_id, username="other_user", email="reg_admin@x.com"),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_TAKEN"


def test_admin_cannot_approve_into_yard_admin_role(client, db_session, world):
    yard_id = world["yard"].id
    settings_service.update_setting(
        db_session,
        yard_id=yard_id,
        key="registration",
        value={"default_role": "yard_admin"},
        actor_id=world["owner"].id,
        request_id="t",
    )
    db_session.commit()

    resp = client.post(f"{PREFIX}/auth/register", json=_register(yard_id, username="wannabe_admin"))
    request_id = resp.json()["data"]["request_id"]

    admin_token = _login(client, "reg_admin", "Passw0rd123", yard_id)
    approved = client.post(
        f"{PREFIX}/yards/current/registrations/{request_id}/approve",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert approved.status_code == 403


def test_registration_requires_approve_permission(client, db_session, world):
    yard_id = world["yard"].id
    viewer = create_user(db_session, username="plain_viewer", password="Passw0rd123")
    add_membership(db_session, yard_id=yard_id, user_id=viewer.id, role="viewer")
    db_session.commit()
    token = _login(client, "plain_viewer", "Passw0rd123", yard_id)
    resp = client.get(
        f"{PREFIX}/yards/current/registrations",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
