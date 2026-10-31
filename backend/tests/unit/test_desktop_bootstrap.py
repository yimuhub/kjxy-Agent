"""桌面引导登录：仅回环 + 壳令牌，换本机负责人登录态。"""

from app.modules.auth import repository as auth_repo
from app.modules.yards import repository as yard_repo

TOKEN = "desktop-bootstrap-token-for-test"


def _seed_owner(db_session):
    user = auth_repo.create_user(
        db_session, username="local_owner", password="owner-pass-123", display_name="本机负责人"
    )
    yard = yard_repo.create_yard(db_session, name="本机小院")
    yard.owner_user_id = user.id
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=user.id, role="yard_owner")
    db_session.commit()
    return user, yard


def test_bootstrap_returns_owner_login(client, db_session, monkeypatch):
    user, yard = _seed_owner(db_session)
    monkeypatch.setenv("TECHYARD_DESKTOP_BOOTSTRAP", TOKEN)

    res = client.post("/api/v1/auth/desktop-bootstrap", json={"token": TOKEN})

    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert data["user"]["user_id"] == user.id
    assert data["yards"][0]["yard_id"] == yard.id
    assert "yard_owner" in data["roles"]
    assert data["access_token"] and data["refresh_token"]


def test_bootstrap_rejects_wrong_token(client, db_session, monkeypatch):
    _seed_owner(db_session)
    monkeypatch.setenv("TECHYARD_DESKTOP_BOOTSTRAP", TOKEN)

    res = client.post("/api/v1/auth/desktop-bootstrap", json={"token": "x" * 32})

    assert res.status_code == 401
    assert res.json()["error"]["code"] == "INVALID_BOOTSTRAP_TOKEN"


def test_bootstrap_disabled_without_env(client, db_session, monkeypatch):
    _seed_owner(db_session)
    monkeypatch.delenv("TECHYARD_DESKTOP_BOOTSTRAP", raising=False)

    res = client.post("/api/v1/auth/desktop-bootstrap", json={"token": TOKEN})

    assert res.status_code == 404
    assert res.json()["error"]["code"] == "DESKTOP_DISABLED"


def test_bootstrap_needs_owner_yard(client, db_session, monkeypatch):
    monkeypatch.setenv("TECHYARD_DESKTOP_BOOTSTRAP", TOKEN)

    res = client.post("/api/v1/auth/desktop-bootstrap", json={"token": TOKEN})

    assert res.status_code == 503
    assert res.json()["error"]["code"] == "DESKTOP_NOT_READY"
