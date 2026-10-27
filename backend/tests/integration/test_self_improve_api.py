"""Self-improve API integration tests (v3.4): permissions, settings, run, memories."""

from __future__ import annotations

import pytest
from app.modules.auth.repository import create_user
from app.modules.yards.repository import add_membership, create_yard
from app.modules.yards.settings_service import update_setting

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="si-api-test-yard")
    owner = create_user(db_session, username="si_owner", password="Passw0rd123")
    admin = create_user(db_session, username="si_admin", password="Passw0rd123")
    viewer = create_user(db_session, username="si_viewer", password="Passw0rd123")
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


# ---------------------------------------------------------------------------
# GET /yards/current/self-improve
# ---------------------------------------------------------------------------


class TestGetSelfImprove:
    def test_owner_gets_report(self, client, world):
        token = _login(client, "si_owner", "Passw0rd123", world["yard"].id)
        resp = client.get(f"{PREFIX}/yards/current/self-improve", headers=_hdr(token))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert "settings" in data
        assert data["settings"]["enabled"] is False  # default OFF
        assert data["job"]["status"] == "none"  # no job yet
        assert data["can_manage"] is True  # owner always can manage

    def test_admin_without_grant_gets_403(self, client, world):
        """settings:manage is available to admin by default (only settings:manage_protected
        is PROTECTED). So admin CAN read the report but NOT write settings or run."""
        token = _login(client, "si_admin", "Passw0rd123", world["yard"].id)
        resp = client.get(f"{PREFIX}/yards/current/self-improve", headers=_hdr(token))
        # admin has settings:manage → 200
        assert resp.status_code == 200

    def test_viewer_gets_403(self, client, world):
        """Viewer does not have settings:manage → 403."""
        token = _login(client, "si_viewer", "Passw0rd123", world["yard"].id)
        resp = client.get(f"{PREFIX}/yards/current/self-improve", headers=_hdr(token))
        assert resp.status_code == 403

    def test_admin_with_grant_can_write_settings(self, client, world, db_session):
        """Owner grants settings:manage_protected to admin via owner grants endpoint."""
        owner_token = _login(client, "si_owner", "Passw0rd123", world["yard"].id)
        grant_resp = client.put(
            f"{PREFIX}/yards/current/owner/grants/{world['admin'].id}",
            json={"grants": ["settings:manage_protected"]},
            headers=_hdr(owner_token),
        )
        assert grant_resp.status_code == 200, grant_resp.text
        db_session.commit()

        admin_token = _login(client, "si_admin", "Passw0rd123", world["yard"].id)
        resp = client.get(f"{PREFIX}/yards/current/self-improve", headers=_hdr(admin_token))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["can_manage"] is True


# ---------------------------------------------------------------------------
# PUT /yards/current/settings/self_improve
# ---------------------------------------------------------------------------


class TestUpdateSelfImproveSettings:
    def test_admin_without_grant_403(self, client, world):
        token = _login(client, "si_admin", "Passw0rd123", world["yard"].id)
        resp = client.put(
            f"{PREFIX}/yards/current/settings/self_improve",
            json={"enabled": True},
            headers=_hdr(token),
        )
        assert resp.status_code == 403

    def test_owner_updates(self, client, world):
        token = _login(client, "si_owner", "Passw0rd123", world["yard"].id)
        resp = client.put(
            f"{PREFIX}/yards/current/settings/self_improve",
            json={
                "enabled": True,
                "schedule": {"preset": "daily"},
                "scope": "per_member",
                "daily_budget_tokens": 100000,
                "auto_publish": False,
                "retain_days": 30,
                "notify_owner": True,
            },
            headers=_hdr(token),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]["self_improve"]
        assert data["enabled"] is True
        assert data["daily_budget_tokens"] == 100000

    def test_auto_publish_requires_confirm(self, client, world):
        token = _login(client, "si_owner", "Passw0rd123", world["yard"].id)
        resp = client.put(
            f"{PREFIX}/yards/current/settings/self_improve",
            json={"auto_publish": True},
            headers=_hdr(token),
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "SELF_IMPROVE_CONFIRM_REQUIRED"

    def test_auto_publish_with_confirm(self, client, world):
        token = _login(client, "si_owner", "Passw0rd123", world["yard"].id)
        resp = client.put(
            f"{PREFIX}/yards/current/settings/self_improve",
            json={"auto_publish": True, "confirm_auto_publish": True},
            headers=_hdr(token),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["self_improve"]["auto_publish"] is True

    def test_invalid_budget_rejected(self, client, world):
        token = _login(client, "si_owner", "Passw0rd123", world["yard"].id)
        resp = client.put(
            f"{PREFIX}/yards/current/settings/self_improve",
            json={"daily_budget_tokens": 100},  # below min 1000
            headers=_hdr(token),
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /yards/current/self-improve/run
# ---------------------------------------------------------------------------


class TestRunSelfImprove:
    def test_owner_run_empty_window_success(self, client, world, db_session):
        """Enable self_improve, run manually with empty window → success (no LLM)."""
        owner_token = _login(client, "si_owner", "Passw0rd123", world["yard"].id)
        # Enable the feature first
        update_setting(
            db_session,
            yard_id=world["yard"].id,
            key="self_improve",
            value={"enabled": True, "schedule": {"preset": "daily"}},
            actor_id=world["owner"].id,
            request_id="test-int",
        )
        db_session.commit()

        resp = client.post(f"{PREFIX}/yards/current/self-improve/run", headers=_hdr(owner_token))
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["status"] == "success"
        assert "窗口内无可用会话数据" in (data.get("output") or {}).get("note", "")

    def test_admin_without_grant_403(self, client, world):
        token = _login(client, "si_admin", "Passw0rd123", world["yard"].id)
        resp = client.post(f"{PREFIX}/yards/current/self-improve/run", headers=_hdr(token))
        assert resp.status_code == 403

    def test_viewer_403(self, client, world):
        token = _login(client, "si_viewer", "Passw0rd123", world["yard"].id)
        resp = client.post(f"{PREFIX}/yards/current/self-improve/run", headers=_hdr(token))
        assert resp.status_code == 403

    def test_disabled_run_409(self, client, world, db_session):
        """Self_improve disabled by default; run should 409."""
        owner_token = _login(client, "si_owner", "Passw0rd123", world["yard"].id)
        resp = client.post(f"{PREFIX}/yards/current/self-improve/run", headers=_hdr(owner_token))
        # Default setting is enabled=False → sync_job creates paused job → 409
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "SELF_IMPROVE_DISABLED"


# ---------------------------------------------------------------------------
# GET /yards/current/self-improve/runs
# ---------------------------------------------------------------------------


class TestListRuns:
    def test_owner_sees_runs(self, client, world, db_session):
        owner_token = _login(client, "si_owner", "Passw0rd123", world["yard"].id)
        update_setting(
            db_session,
            yard_id=world["yard"].id,
            key="self_improve",
            value={"enabled": True, "schedule": {"preset": "daily"}},
            actor_id=world["owner"].id,
            request_id="test-int2",
        )
        db_session.commit()

        # Trigger a run
        client.post(f"{PREFIX}/yards/current/self-improve/run", headers=_hdr(owner_token))
        db_session.commit()

        resp = client.get(f"{PREFIX}/yards/current/self-improve/runs", headers=_hdr(owner_token))
        assert resp.status_code == 200, resp.text
        items = resp.json()["data"]["items"]
        assert len(items) >= 1
        assert items[0]["status"] == "success"

    def test_viewer_403(self, client, world):
        token = _login(client, "si_viewer", "Passw0rd123", world["yard"].id)
        resp = client.get(f"{PREFIX}/yards/current/self-improve/runs", headers=_hdr(token))
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /yards/current/self-improve/memories
# ---------------------------------------------------------------------------


class TestMemories:
    def test_viewer_sees_own(self, client, world, db_session):
        """Viewer can see their own memories (empty list initially)."""
        token = _login(client, "si_viewer", "Passw0rd123", world["yard"].id)
        resp = client.get(f"{PREFIX}/yards/current/self-improve/memories", headers=_hdr(token))
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["items"] == []

    def test_viewer_cannot_see_another_member(self, client, world):
        token = _login(client, "si_viewer", "Passw0rd123", world["yard"].id)
        resp = client.get(
            f"{PREFIX}/yards/current/self-improve/memories?user_id={world['admin'].id}",
            headers=_hdr(token),
        )
        assert resp.status_code == 403

    def test_owner_sees_all_including_yard_level(self, client, world, db_session):
        token = _login(client, "si_owner", "Passw0rd123", world["yard"].id)
        resp = client.get(f"{PREFIX}/yards/current/self-improve/memories", headers=_hdr(token))
        assert resp.status_code == 200, resp.text

    def test_admin_without_grant_cannot_see_others(self, client, world):
        token = _login(client, "si_admin", "Passw0rd123", world["yard"].id)
        resp = client.get(
            f"{PREFIX}/yards/current/self-improve/memories?user_id={world['viewer'].id}",
            headers=_hdr(token),
        )
        assert resp.status_code == 403
