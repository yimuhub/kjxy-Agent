"""负责人驾驶舱 API 集成测试：隔离挂载 owner_router 验证权限与审计。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.deps import get_db_session
from app.core.errors import AppError, app_error_handler, validation_error_handler
from app.core.security import create_access_token
from app.modules.agents.models import STATUS_PUBLISHED, YardAgent
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.chat.models import ChatMessage, ChatSession
from app.modules.yards.models import Yard, YardMembership
from app.modules.yards.owner_router import router as owner_router
from app.modules.yards.repository import add_membership, create_yard
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy import select

BASE = "/api/v1/yards/current/owner"


def _user(db, username: str, **kwargs) -> User:
    user = User(
        username=username,
        email=kwargs.pop("email", f"{username}@example.com"),
        phone=kwargs.pop("phone", None),
        password_hash="x",
        display_name=kwargs.pop("display_name", username),
        status="active",
        token_version=0,
        **kwargs,
    )
    db.add(user)
    db.flush()
    return user


def _hdr(user: User, yard: Yard, role: str) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=[role])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def api_client(db_session):
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(owner_router, prefix="/api/v1")

    def override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.fixture()
def seeded(db_session):
    owner = _user(db_session, "owner", email="owner@example.com", phone="13800138000")
    admin = _user(db_session, "admin", email="admin@example.com", phone="13800138001")
    member = _user(db_session, "member", email="member@example.com", phone="13900139000")
    outsider = _user(db_session, "outsider")

    yard = create_yard(db_session, name="驾驶舱小院")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    add_membership(db_session, yard_id=yard.id, user_id=member.id, role="operator")

    other_yard = create_yard(db_session, name="隔壁小院")
    other_yard.owner_user_id = outsider.id
    add_membership(db_session, yard_id=other_yard.id, user_id=outsider.id, role="yard_owner")

    now = datetime.now(UTC)
    admin.last_login_at = now
    member.last_login_at = now - timedelta(hours=1)

    session = ChatSession(
        yard_id=yard.id,
        created_by=member.id,
        title="灌溉咨询",
        target="auto",
        created_at=now,
        updated_at=now,
    )
    db_session.add(session)
    db_session.flush()
    db_session.add(
        ChatMessage(
            session_id=session.id,
            yard_id=yard.id,
            seq=1,
            role="user",
            content="今天浇多少水",
            status="ok",
            token_usage_json={"input_tokens": 10, "output_tokens": 0, "total_tokens": 10},
            created_at=now,
        )
    )
    db_session.add(
        ChatMessage(
            session_id=session.id,
            yard_id=yard.id,
            seq=2,
            role="assistant",
            content="建议滴灌两小时",
            status="ok",
            token_usage_json={"input_tokens": 0, "output_tokens": 20, "total_tokens": 20},
            choice_json={"kind": "agent", "id": "a1", "code": "irrig", "name": "灌溉顾问"},
            created_at=now,
        )
    )
    other_session = ChatSession(
        yard_id=other_yard.id,
        created_by=outsider.id,
        title="别人家的会话",
        target="auto",
    )
    db_session.add(other_session)
    db_session.add(
        YardAgent(
            yard_id=yard.id,
            agent_code="irrig",
            name="灌溉顾问",
            persona_prompt="你是灌溉顾问",
            task_prompt_template="{{question}}",
            status=STATUS_PUBLISHED,
        )
    )
    db_session.commit()
    return {
        "owner": owner,
        "admin": admin,
        "member": member,
        "outsider": outsider,
        "yard": yard,
        "other_yard": other_yard,
        "session": session,
        "other_session": other_session,
    }


def test_owner_overview(api_client, seeded):
    resp = api_client.get(
        f"{BASE}/overview", headers=_hdr(seeded["owner"], seeded["yard"], "yard_owner")
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["members_total"] == 3
    assert data["agents_count"] == 1
    assert data["sessions_today"] == 1
    assert data["messages_today"] == 2
    assert data["tokens_today"] == 30
    assert data["tokens_7d"] == 30
    assert data["pending_reviews"]["total"] == 0


def test_member_usage_masks_contacts_without_grant(api_client, seeded):
    admin_resp = api_client.get(
        f"{BASE}/member-usage?days=7",
        headers=_hdr(seeded["admin"], seeded["yard"], "yard_admin"),
    )
    assert admin_resp.status_code == 200, admin_resp.text
    data = admin_resp.json()["data"]
    assert data["sensitive"] is False
    items = {item["username"]: item for item in data["items"]}
    assert items["member"]["email"] == "me***@example.com"
    assert items["member"]["phone"] == "139****9000"
    assert items["member"]["sessions"] == 1
    assert items["member"]["messages"] == 2
    assert items["member"]["total_tokens"] == 30

    owner_resp = api_client.get(
        f"{BASE}/member-usage?days=7",
        headers=_hdr(seeded["owner"], seeded["yard"], "yard_owner"),
    )
    owner_items = {item["username"]: item for item in owner_resp.json()["data"]["items"]}
    assert owner_resp.json()["data"]["sensitive"] is True
    assert owner_items["member"]["email"] == "member@example.com"


def test_admin_cannot_list_sessions_until_granted(api_client, seeded):
    admin_hdr = _hdr(seeded["admin"], seeded["yard"], "yard_admin")
    owner_hdr = _hdr(seeded["owner"], seeded["yard"], "yard_owner")

    denied = api_client.get(f"{BASE}/sessions", headers=admin_hdr)
    assert denied.status_code == 403

    granted = api_client.put(
        f"{BASE}/grants/{seeded['admin'].id}",
        json={"grants": ["insights:sensitive"]},
        headers=owner_hdr,
    )
    assert granted.status_code == 200, granted.text
    assert "insights:sensitive" in granted.json()["data"]["effective_permissions"]

    allowed = api_client.get(f"{BASE}/sessions", headers=admin_hdr)
    assert allowed.status_code == 200, allowed.text
    body = allowed.json()
    assert body["meta"]["total"] == 1
    item = body["data"][0]
    assert item["id"] == seeded["session"].id
    assert item["created_by_username"] == "member"
    assert item["total_tokens"] == 30


def test_owner_messages_requires_reason_and_writes_audit(api_client, db_session, seeded):
    owner_hdr = _hdr(seeded["owner"], seeded["yard"], "yard_owner")
    url = f"{BASE}/sessions/{seeded['session'].id}/messages"

    missing = api_client.get(url, headers=owner_hdr)
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "AUDIT_REASON_REQUIRED"

    too_long = api_client.get(url, params={"reason": "长" * 201}, headers=owner_hdr)
    assert too_long.status_code == 400
    assert too_long.json()["error"]["code"] == "AUDIT_REASON_REQUIRED"

    ok_resp = api_client.get(url, params={"reason": "处理成员反馈"}, headers=owner_hdr)
    assert ok_resp.status_code == 200, ok_resp.text
    data = ok_resp.json()["data"]
    assert [item["seq"] for item in data["items"]] == [1, 2]
    assert data["session"]["created_by"] == seeded["member"].id

    db_session.expire_all()
    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.yard_id == seeded["yard"].id,
            AuditLog.action == "sensitive_view",
        )
    )
    assert audit is not None
    assert audit.actor_id == seeded["owner"].id
    assert audit.resource_type == "chat_session"
    assert audit.resource_id == seeded["session"].id
    assert audit.metadata_json["reason"] == "处理成员反馈"
    assert audit.metadata_json["session_id"] == seeded["session"].id
    assert audit.metadata_json["user_id"] == seeded["member"].id

    listing = api_client.get(
        f"{BASE}/audit", params={"action": "sensitive_view"}, headers=owner_hdr
    )
    assert listing.status_code == 200
    assert listing.json()["data"]["items"][0]["action"] == "sensitive_view"


def test_session_messages_cross_yard_404(api_client, seeded):
    resp = api_client.get(
        f"{BASE}/sessions/{seeded['other_session'].id}/messages",
        params={"reason": "排查问题"},
        headers=_hdr(seeded["owner"], seeded["yard"], "yard_owner"),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "CHAT_SESSION_NOT_FOUND"


def test_grants_owner_only_and_grantable_list(api_client, seeded):
    admin_hdr = _hdr(seeded["admin"], seeded["yard"], "yard_admin")
    owner_hdr = _hdr(seeded["owner"], seeded["yard"], "yard_owner")

    assert api_client.get(f"{BASE}/grants", headers=admin_hdr).status_code == 403
    assert (
        api_client.put(
            f"{BASE}/grants/{seeded['admin'].id}",
            json={"grants": ["audit:read"]},
            headers=admin_hdr,
        ).status_code
        == 403
    )

    listed = api_client.get(f"{BASE}/grants", headers=owner_hdr)
    assert listed.status_code == 200
    assert listed.json()["data"]["grantable"] == [
        "audit:read",
        "finance:read",
        "insights:sensitive",
        "skills:publish_any",
    ]
    assert [item["user_id"] for item in listed.json()["data"]["items"]] == [seeded["admin"].id]

    invalid = api_client.put(
        f"{BASE}/grants/{seeded['admin'].id}",
        json={"grants": ["settings:manage_protected"]},
        headers=owner_hdr,
    )
    assert invalid.status_code == 400
    assert invalid.json()["error"]["code"] == "INVALID_GRANTS"

    not_admin = api_client.put(
        f"{BASE}/grants/{seeded['member'].id}",
        json={"grants": ["audit:read"]},
        headers=owner_hdr,
    )
    assert not_admin.status_code == 400
    assert not_admin.json()["error"]["code"] == "TARGET_NOT_YARD_ADMIN"

    updated = api_client.put(
        f"{BASE}/grants/{seeded['admin'].id}",
        json={"grants": ["audit:read", "finance:read", "audit:read"]},
        headers=owner_hdr,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["grants"] == ["audit:read", "finance:read"]
    perms = updated.json()["data"]["effective_permissions"]
    assert "audit:read" in perms and "finance:read" in perms


def test_admin_cannot_transfer_owner(api_client, seeded):
    resp = api_client.post(
        f"{BASE}/transfer",
        json={"user_id": seeded["member"].id},
        headers=_hdr(seeded["admin"], seeded["yard"], "yard_admin"),
    )
    assert resp.status_code == 403


def test_owner_transfer_swaps_roles_and_bumps_token_version(api_client, db_session, seeded):
    owner_hdr = _hdr(seeded["owner"], seeded["yard"], "yard_owner")

    self_transfer = api_client.post(
        f"{BASE}/transfer", json={"user_id": seeded["owner"].id}, headers=owner_hdr
    )
    assert self_transfer.status_code == 400
    assert self_transfer.json()["error"]["code"] == "OWNER_TRANSFER_INVALID"

    not_admin = api_client.post(
        f"{BASE}/transfer", json={"user_id": seeded["member"].id}, headers=owner_hdr
    )
    assert not_admin.status_code == 400
    assert not_admin.json()["error"]["code"] == "TARGET_NOT_YARD_ADMIN"

    resp = api_client.post(
        f"{BASE}/transfer", json={"user_id": seeded["admin"].id}, headers=owner_hdr
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"] == {"ok": True, "owner_user_id": seeded["admin"].id}

    db_session.expire_all()
    yard = db_session.get(Yard, seeded["yard"].id)
    assert yard.owner_user_id == seeded["admin"].id
    roles = {
        row.user_id: row.role
        for row in db_session.scalars(
            select(YardMembership).where(YardMembership.yard_id == seeded["yard"].id)
        )
    }
    assert roles[seeded["admin"].id] == "yard_owner"
    assert roles[seeded["owner"].id] == "yard_admin"
    assert db_session.get(User, seeded["admin"].id).token_version == 1
    assert db_session.get(User, seeded["owner"].id).token_version == 1

    audit = db_session.scalar(
        select(AuditLog).where(
            AuditLog.yard_id == seeded["yard"].id,
            AuditLog.action == "owner_transferred",
        )
    )
    assert audit is not None
    assert audit.metadata_json == {
        "from_user_id": seeded["owner"].id,
        "to_user_id": seeded["admin"].id,
    }
