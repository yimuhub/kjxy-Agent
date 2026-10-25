"""成员邀请与批量导入集成测试（全离线）。"""

import os

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-min-32-chars-for-unit-tests")

from datetime import UTC, datetime, timedelta

from app.core.security import create_access_token
from app.modules.auth.repository import create_user
from app.modules.tenants.models import TenantInvitation
from app.modules.tenants.repository import add_membership, create_tenant


def _hdr(tenant_id: str, roles: tuple[str, ...] = ("tenant_admin",)) -> dict:
    token = create_access_token(user_id="u-admin", tenant_id=tenant_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _invite(client, tenant_id: str, **over) -> dict:
    payload = {"role": "operator", "expires_in_days": 7, "note": "生产部新同事"}
    payload.update(over)
    return client.post("/api/v1/tenants/current/invitations", json=payload, headers=_hdr(tenant_id))


def _token_from(resp) -> str:
    path = resp.json()["data"]["invite_path"]
    return path.rsplit("/", 1)[-1]


def test_invitation_full_flow(client, db_session):
    tenant = create_tenant(db_session, name="邀请测试空间")
    db_session.commit()

    created = _invite(client, tenant.id)
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    assert data["status"] == "pending" and data["role"] == "operator"
    assert data["invite_path"].startswith("/invite/")
    token = _token_from(created)

    preview = client.get(f"/api/v1/invitations/{token}")
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["tenant_name"] == "邀请测试空间"
    assert preview.json()["data"]["role"] == "operator"

    accepted = client.post(
        f"/api/v1/invitations/{token}/accept",
        json={"username": "new_member", "password": "Passw0rd123", "display_name": "新成员"},
    )
    assert accepted.status_code == 200, accepted.text
    body = accepted.json()["data"]
    assert body["access_token"]
    assert body["roles"] == ["operator"]
    assert [t["tenant_id"] for t in body["tenants"]] == [tenant.id]

    # 令牌一次性：重复接受必须失效
    again = client.post(
        f"/api/v1/invitations/{token}/accept",
        json={"username": "new_member2", "password": "Passw0rd123"},
    )
    assert again.status_code == 404
    assert again.json()["error"]["code"] == "INVITATION_INVALID"

    listed = client.get("/api/v1/tenants/current/invitations", headers=_hdr(tenant.id))
    assert listed.status_code == 200
    assert listed.json()["data"][0]["status"] == "accepted"
    # 列表不返回明文令牌/链接
    assert listed.json()["data"][0]["invite_path"] is None


def test_existing_account_can_join_second_tenant_with_password(client, db_session):
    first = create_tenant(db_session, name="空间甲")
    second = create_tenant(db_session, name="空间乙")
    user = create_user(
        db_session, username="veteran", password="Passw0rd123", display_name="老成员"
    )
    add_membership(db_session, tenant_id=first.id, user_id=user.id, role="viewer")
    db_session.commit()

    token = _token_from(_invite(client, second.id, role="expert"))

    wrong = client.post(
        f"/api/v1/invitations/{token}/accept",
        json={"username": "veteran", "password": "wrong-password"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["error"]["code"] == "INVALID_CREDENTIALS"

    ok = client.post(
        f"/api/v1/invitations/{token}/accept",
        json={"username": "veteran", "password": "Passw0rd123"},
    )
    assert ok.status_code == 200, ok.text
    tenants = {t["tenant_id"]: t["roles"] for t in ok.json()["data"]["tenants"]}
    assert tenants == {first.id: ["viewer"], second.id: ["expert"]}


def test_revoke_and_expiry_block_acceptance(client, db_session):
    tenant = create_tenant(db_session, name="撤销测试空间")
    db_session.commit()

    created = _invite(client, tenant.id)
    inv_id = created.json()["data"]["id"]
    token = _token_from(created)

    revoked = client.delete(
        f"/api/v1/tenants/current/invitations/{inv_id}", headers=_hdr(tenant.id)
    )
    assert revoked.status_code == 200
    assert revoked.json()["data"]["status"] == "revoked"
    assert client.get(f"/api/v1/invitations/{token}").status_code == 404

    second = _invite(client, tenant.id)
    second_token = _token_from(second)
    row = db_session.query(TenantInvitation).filter_by(id=second.json()["data"]["id"]).one()
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db_session.commit()
    expired = client.get(f"/api/v1/invitations/{second_token}")
    assert expired.status_code == 404
    listed = client.get("/api/v1/tenants/current/invitations", headers=_hdr(tenant.id))
    statuses = {i["id"]: i["status"] for i in listed.json()["data"]}
    assert statuses[row.id] == "expired"


def test_invitation_requires_member_manage_permission(client, db_session):
    tenant = create_tenant(db_session, name="权限测试空间")
    db_session.commit()
    viewer = _hdr(tenant.id, roles=("viewer",))
    assert client.get("/api/v1/tenants/current/invitations", headers=viewer).status_code == 403
    resp = client.post(
        "/api/v1/tenants/current/invitations",
        json={"role": "expert"},
        headers=viewer,
    )
    assert resp.status_code == 403


def test_batch_add_members_partial_success(client, db_session):
    tenant = create_tenant(db_session, name="批量导入空间")
    create_user(db_session, username="worker_ok", password="Passw0rd123")
    db_session.commit()

    resp = client.post(
        "/api/v1/tenants/current/members/batch",
        json={
            "members": [
                {"username": "worker_ok", "role": "operator"},
                {"username": "ghost_user", "role": "viewer"},
                {"username": "worker_ok", "role": "operator"},
            ]
        },
        headers=_hdr(tenant.id),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert [m["username"] for m in data["added"]] == ["worker_ok"]
    assert {f["error_code"] for f in data["failed"]} == {"NOT_FOUND", "MEMBER_EXISTS"}

    members = client.get("/api/v1/tenants/current/members", headers=_hdr(tenant.id))
    assert members.json()["meta"]["total"] == 1
