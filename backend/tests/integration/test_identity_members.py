"""成员管理集成测试：创建/挂接、角色矩阵、脱敏、停用、重置密码、软删除。"""

from __future__ import annotations

import pytest
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.auth.repository import create_user
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="身份成员测试小院")
    owner = create_user(
        db_session, username="owner1", password="Passw0rd123", display_name="负责人"
    )
    admin = create_user(
        db_session, username="admin1", password="Passw0rd123", display_name="管理员"
    )
    expert = create_user(
        db_session, username="expert1", password="Passw0rd123", display_name="专家"
    )
    viewer = create_user(
        db_session,
        username="viewer1",
        password="Passw0rd123",
        email="viewer@x.com",
        display_name="普通成员",
    )
    viewer.phone = "13812341234"
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    add_membership(db_session, yard_id=yard.id, user_id=expert.id, role="expert")
    add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    db_session.commit()
    return {
        "yard": yard,
        "owner": owner,
        "admin": admin,
        "expert": expert,
        "viewer": viewer,
    }


def _login(client, username: str, password: str, yard_id: str) -> str:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": password, "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _members(client, token: str) -> dict[str, dict]:
    resp = client.get(f"{PREFIX}/yards/current/members", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    return {m["username"]: m for m in resp.json()["data"]}


def test_owner_creates_yard_admin_with_initial_password(client, db_session, world):
    token = _login(client, "owner1", "Passw0rd123", world["yard"].id)
    resp = client.post(
        f"{PREFIX}/yards/current/members",
        json={
            "username": "new_admin",
            "display_name": "新管理员",
            "role": "yard_admin",
            "email": "new_admin@x.com",
        },
        headers=_hdr(token),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["member"]["role"] == "yard_admin"
    assert data["member"]["role_name"] == "小院管理员"
    assert data["member"]["must_change_password"] is True
    assert data["initial_password"] and len(data["initial_password"]) == 12

    user = db_session.query(User).filter_by(username="new_admin").one()
    assert user.status == "active" and user.must_change_password is True
    membership = (
        db_session.query(YardMembership).filter_by(yard_id=world["yard"].id, user_id=user.id).one()
    )
    assert membership.role == "yard_admin"


def test_admin_cannot_create_yard_admin(client, world):
    token = _login(client, "admin1", "Passw0rd123", world["yard"].id)
    resp = client.post(
        f"{PREFIX}/yards/current/members",
        json={"username": "another_admin", "role": "yard_admin"},
        headers=_hdr(token),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_admin_cannot_update_owner(client, world):
    token = _login(client, "admin1", "Passw0rd123", world["yard"].id)
    resp = client.patch(
        f"{PREFIX}/yards/current/members/{world['owner'].id}",
        json={"display_name": "被改名"},
        headers=_hdr(token),
    )
    assert resp.status_code == 403


def test_owner_can_update_member_and_audit(client, db_session, world):
    token = _login(client, "owner1", "Passw0rd123", world["yard"].id)
    resp = client.patch(
        f"{PREFIX}/yards/current/members/{world['viewer'].id}",
        json={"role": "operator", "display_name": "操作员甲", "status": "active"},
        headers=_hdr(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["role"] == "operator"

    db_session.expire_all()
    membership = (
        db_session.query(YardMembership)
        .filter_by(yard_id=world["yard"].id, user_id=world["viewer"].id)
        .one()
    )
    assert membership.role == "operator"
    viewer = db_session.get(User, world["viewer"].id)
    assert viewer.token_version >= 1
    actions = {
        row.action
        for row in db_session.query(AuditLog).filter_by(resource_id=world["viewer"].id).all()
    }
    assert {"member_updated", "member_role_changed"} <= actions


def test_user_cannot_change_own_role(client, world):
    token = _login(client, "admin1", "Passw0rd123", world["yard"].id)
    resp = client.patch(
        f"{PREFIX}/yards/current/members/{world['admin'].id}",
        json={"role": "viewer"},
        headers=_hdr(token),
    )
    assert resp.status_code == 403


def test_expert_role_matrix(client, db_session, world):
    operator = create_user(db_session, username="operator1", password="Passw0rd123")
    add_membership(db_session, yard_id=world["yard"].id, user_id=operator.id, role="operator")
    db_session.commit()
    token = _login(client, "expert1", "Passw0rd123", world["yard"].id)

    ok = client.patch(
        f"{PREFIX}/yards/current/members/{operator.id}",
        json={"display_name": "田间操作员"},
        headers=_hdr(token),
    )
    assert ok.status_code == 200, ok.text

    cannot_admin = client.patch(
        f"{PREFIX}/yards/current/members/{world['admin'].id}",
        json={"display_name": "改名"},
        headers=_hdr(token),
    )
    assert cannot_admin.status_code == 403

    cannot_promote = client.patch(
        f"{PREFIX}/yards/current/members/{operator.id}",
        json={"role": "yard_admin"},
        headers=_hdr(token),
    )
    assert cannot_promote.status_code == 403


def test_contacts_masked_without_members_manage(client, world):
    expert_token = _login(client, "expert1", "Passw0rd123", world["yard"].id)
    masked = _members(client, expert_token)["viewer1"]
    assert masked["email"] == "v***@x.com"
    assert masked["phone"] == "138****1234"
    assert masked["last_login_at"] is None

    admin_token = _login(client, "admin1", "Passw0rd123", world["yard"].id)
    full = _members(client, admin_token)["viewer1"]
    assert full["email"] == "viewer@x.com"
    assert full["phone"] == "13812341234"


def test_duplicate_contact_rejected(client, world):
    token = _login(client, "owner1", "Passw0rd123", world["yard"].id)
    resp = client.post(
        f"{PREFIX}/yards/current/members",
        json={"username": "dup_contact", "email": "viewer@x.com"},
        headers=_hdr(token),
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "EMAIL_TAKEN"


def test_existing_account_attached_without_new_password(client, db_session, world):
    create_user(db_session, username="outsider", password="Passw0rd123", display_name="外部账号")
    db_session.commit()
    token = _login(client, "owner1", "Passw0rd123", world["yard"].id)
    resp = client.post(
        f"{PREFIX}/yards/current/members",
        json={"username": "outsider", "role": "viewer"},
        headers=_hdr(token),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["data"]["initial_password"] is None
    again = client.post(
        f"{PREFIX}/yards/current/members",
        json={"username": "outsider", "role": "viewer"},
        headers=_hdr(token),
    )
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "MEMBER_EXISTS"


def test_reset_password_increments_token_version(client, db_session, world):
    owner_token = _login(client, "owner1", "Passw0rd123", world["yard"].id)
    admin_token = _login(client, "admin1", "Passw0rd123", world["yard"].id)

    # 管理员不能重置负责人
    admin_reset_owner = client.post(
        f"{PREFIX}/yards/current/members/{world['owner'].id}/reset-password",
        headers=_hdr(admin_token),
    )
    assert admin_reset_owner.status_code == 403

    before = db_session.get(User, world["admin"].id).token_version
    resp = client.post(
        f"{PREFIX}/yards/current/members/{world['admin'].id}/reset-password",
        headers=_hdr(owner_token),
    )
    assert resp.status_code == 200, resp.text
    temp = resp.json()["data"]["temporary_password"]
    assert len(temp) == 12

    db_session.expire_all()
    admin = db_session.get(User, world["admin"].id)
    assert admin.token_version == before + 1
    assert admin.must_change_password is True

    # 旧令牌因 ver 不匹配立即失效
    stale = client.get(f"{PREFIX}/auth/me", headers=_hdr(admin_token))
    assert stale.status_code == 401

    # 临时密码可以登录（登录后仍要求改密）
    relogin = client.post(
        f"{PREFIX}/auth/login",
        json={"username": "admin1", "password": temp, "yard_id": world["yard"].id},
    )
    assert relogin.status_code == 200, relogin.text
    assert relogin.json()["data"]["user"]["must_change_password"] is True


def test_soft_delete_disables_membership_and_account(client, db_session, world):
    token = _login(client, "owner1", "Passw0rd123", world["yard"].id)
    before = db_session.get(User, world["viewer"].id).token_version
    resp = client.delete(
        f"{PREFIX}/yards/current/members/{world['viewer'].id}", headers=_hdr(token)
    )
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    membership = (
        db_session.query(YardMembership)
        .filter_by(yard_id=world["yard"].id, user_id=world["viewer"].id)
        .one()
    )
    assert membership.status == "disabled"
    viewer = db_session.get(User, world["viewer"].id)
    assert viewer.status == "disabled"
    assert viewer.token_version == before + 1


def test_delete_guards_owner_self_and_last_admin(client, world):
    owner_token = _login(client, "owner1", "Passw0rd123", world["yard"].id)
    self_delete = client.delete(
        f"{PREFIX}/yards/current/members/{world['owner'].id}", headers=_hdr(owner_token)
    )
    assert self_delete.status_code == 403

    last_admin = client.delete(
        f"{PREFIX}/yards/current/members/{world['admin'].id}", headers=_hdr(owner_token)
    )
    assert last_admin.status_code == 409
    assert last_admin.json()["error"]["code"] == "CONFLICT"


def test_disable_member_then_enable(client, db_session, world):
    token = _login(client, "owner1", "Passw0rd123", world["yard"].id)
    disabled = client.patch(
        f"{PREFIX}/yards/current/members/{world['expert'].id}",
        json={"status": "disabled"},
        headers=_hdr(token),
    )
    assert disabled.status_code == 200, disabled.text
    db_session.expire_all()
    assert db_session.get(User, world["expert"].id).status == "disabled"

    enabled = client.patch(
        f"{PREFIX}/yards/current/members/{world['expert'].id}",
        json={"status": "active"},
        headers=_hdr(token),
    )
    assert enabled.status_code == 200, enabled.text
    db_session.expire_all()
    assert db_session.get(User, world["expert"].id).status == "active"


def test_custom_role_assignment_and_admin_delete_guard(client, db_session, world):
    owner_token = _login(client, "owner1", "Passw0rd123", world["yard"].id)
    created = client.post(
        f"{PREFIX}/yards/current/roles",
        json={
            "code": "field_lead",
            "name": "田间组长",
            "permissions": ["tasks:read", "knowledge:read"],
        },
        headers=_hdr(owner_token),
    )
    assert created.status_code == 201, created.text

    assigned = client.patch(
        f"{PREFIX}/yards/current/members/{world['viewer'].id}",
        json={"role": "field_lead"},
        headers=_hdr(owner_token),
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["data"]["role"] == "field_lead"
    assert assigned.json()["data"]["role_name"] == "田间组长"

    # 管理员不能移除同为管理员的成员（只有负责人可以）
    second_admin = create_user(db_session, username="admin2", password="Passw0rd123")
    add_membership(db_session, yard_id=world["yard"].id, user_id=second_admin.id, role="yard_admin")
    db_session.commit()
    admin_token = _login(client, "admin1", "Passw0rd123", world["yard"].id)
    denied = client.delete(
        f"{PREFIX}/yards/current/members/{second_admin.id}", headers=_hdr(admin_token)
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "FORBIDDEN"
