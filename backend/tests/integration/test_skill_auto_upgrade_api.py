"""自动升级 HTTP 接口（v3.21）：开关、概览、手动升级与权限。"""

from __future__ import annotations

import json

import pytest
from app.modules.auth.repository import create_user
from app.modules.evolution import auto_upgrade as auto_service
from app.modules.evolution.models import Skill
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="自动升级集成小院")
    owner = create_user(db_session, username="up_owner", password="Passw0rd123")
    admin = create_user(db_session, username="up_admin", password="Passw0rd123")
    viewer = create_user(db_session, username="up_viewer", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    skill = Skill(
        yard_id=yard.id,
        skill_code="rice.fertilizer",
        version="1.0.0",
        definition_json={"title": "水稻施肥", "description": "给出用量", "prompt": "顾问"},
        status="published",
        created_from="manual",
        visibility="yard",
    )
    db_session.add(skill)
    db_session.commit()
    return {"yard": yard, "owner": owner, "admin": admin, "viewer": viewer, "skill": skill}


def _login(client, username: str, yard_id: str) -> str:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": "Passw0rd123", "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content
        self.input_tokens = 5
        self.output_tokens = 10


def _fake_llm(monkeypatch, payload: dict):
    async def fake_complete(db, request):
        return _Resp(json.dumps(payload, ensure_ascii=False))

    monkeypatch.setattr(auto_service, "complete_with_logging", fake_complete)


def test_owner_can_toggle_and_run_upgrade(client, world, db_session, monkeypatch):
    owner = _login(client, "up_owner", world["yard"].id)
    skill_id = world["skill"].id

    overview = client.get(f"{PREFIX}/skills/auto-upgrade/overview", headers=_hdr(owner))
    assert overview.status_code == 200, overview.text
    assert overview.json()["data"] == {}

    patched = client.patch(
        f"{PREFIX}/skills/{skill_id}/auto-upgrade",
        json={"enabled": True, "min_calls": 1, "cooldown_hours": 24},
        headers=_hdr(owner),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["enabled"] is True

    auto_service.record_usage(
        db_session,
        yard_id=world["yard"].id,
        codes=["rice.fertilizer"],
        source="plan",
        session_id="s1",
    )
    db_session.commit()

    _fake_llm(
        monkeypatch,
        {
            "needs_update": True,
            "reason": "补充分蘖期用量",
            "description": "给出分蘖期亩用量",
            "prompt": "先问生育期，再给用量。",
            "steps": ["问生育期", "给用量"],
        },
    )
    run = client.post(f"{PREFIX}/skills/{skill_id}/auto-upgrade/run", headers=_hdr(owner))
    assert run.status_code == 200, run.text
    assert run.json()["data"]["status"] == "success"
    assert run.json()["data"]["draft_skill_id"]

    detail = client.get(f"{PREFIX}/skills/{skill_id}/auto-upgrade", headers=_hdr(owner))
    assert detail.status_code == 200
    data = detail.json()["data"]
    assert data["enabled"] is True
    assert data["usage"]["calls"] >= 1
    assert data["runs"][0]["status"] == "success"


def test_viewer_cannot_manage_auto_upgrade(client, world):
    viewer = _login(client, "up_viewer", world["yard"].id)
    skill_id = world["skill"].id
    assert (
        client.get(f"{PREFIX}/skills/auto-upgrade/overview", headers=_hdr(viewer)).status_code
        == 403
    )
    assert (
        client.patch(
            f"{PREFIX}/skills/{skill_id}/auto-upgrade",
            json={"enabled": True},
            headers=_hdr(viewer),
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{PREFIX}/skills/{skill_id}/auto-upgrade/run", headers=_hdr(viewer)
        ).status_code
        == 403
    )


def test_run_without_evidence_reports_clear_error(client, world):
    owner = _login(client, "up_owner", world["yard"].id)
    skill_id = world["skill"].id
    client.patch(
        f"{PREFIX}/skills/{skill_id}/auto-upgrade",
        json={"enabled": True, "min_calls": 5},
        headers=_hdr(owner),
    )
    resp = client.post(f"{PREFIX}/skills/{skill_id}/auto-upgrade/run", headers=_hdr(owner))
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SKILL_UPGRADE_NO_EVIDENCE"
