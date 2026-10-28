"""SSO/OIDC HTTP 接口（v3.18）：IdP 管理 + 登录授权码全链路。

出网 mock 与单测一致：discovery/JWKS 走 ``_fetch_json``，令牌接口走 ``_post_form``，
id_token 用测试内 RSA 私钥真实签名（验签仍走 PyJWT + JWKS 路径）。
"""

from __future__ import annotations

import time

import jwt
import pytest
from app.modules.auth import sso
from app.modules.auth.models import User
from app.modules.auth.repository import create_user
from app.modules.auth.sso_models import SsoIdentity
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import add_membership, create_yard
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PREFIX = "/api/v1"
ISS = "https://idp.example.com"
JWKS_URI = f"{ISS}/jwks"
DISCOVERY = {
    "issuer": ISS,
    "authorization_endpoint": f"{ISS}/authorize",
    "token_endpoint": f"{ISS}/token",
    "jwks_uri": JWKS_URI,
    "id_token_signing_alg_values_supported": ["RS256"],
}

_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_private_pem = _private_key.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
)
_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(_private_key.public_key(), as_dict=True)
_jwk.update({"kid": "kid-1", "alg": "RS256", "use": "sig"})

PROVIDER_BODY = {
    "name": "学校统一认证",
    "issuer": ISS,
    "client_id": "client-123",
    "client_secret": "super-secret-value",
    "redirect_uri": "http://localhost:8000/api/v1/auth/sso/callback",
    "frontend_redirect_uri": "http://localhost:5173/sso/callback",
    "role_mapping": {"lab": "expert"},
    "default_role": "viewer",
}


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="SSO 集成小院")
    owner = create_user(db_session, username="sso_owner", password="Passw0rd123")
    admin = create_user(db_session, username="sso_admin", password="Passw0rd123")
    viewer = create_user(db_session, username="sso_viewer", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    db_session.commit()
    return {"yard": yard, "owner": owner, "admin": admin, "viewer": viewer}


@pytest.fixture(autouse=True)
def _sso_on(monkeypatch):
    monkeypatch.setattr(sso.settings, "sso_enabled", True)
    sso.reset_caches()
    yield
    sso.reset_caches()


@pytest.fixture()
def idp(monkeypatch):
    state: dict[str, str] = {}

    def fake_fetch(url: str, *, context: str):
        if url.endswith("openid-configuration"):
            return DISCOVERY
        if url == JWKS_URI:
            return {"keys": [_jwk]}
        raise AssertionError(f"unexpected url {url} ({context})")

    def fake_post(url: str, data: dict[str, str], *, context: str):
        assert url == f"{ISS}/token"
        return {"id_token": state["token"]}

    monkeypatch.setattr(sso, "_fetch_json", fake_fetch)
    monkeypatch.setattr(sso, "_post_form", fake_post)
    state["claims"] = {
        "sub": "subject-1",
        "email": "alice@example.com",
        "email_verified": True,
        "groups": ["lab"],
    }
    return state


def _login(client, username: str, yard_id: str) -> str:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": "Passw0rd123", "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_provider(client, admin_token: str, **overrides) -> dict:
    body = {**PROVIDER_BODY, **overrides}
    resp = client.post(f"{PREFIX}/settings/sso-providers", json=body, headers=_hdr(admin_token))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _sign_token(state: dict, claims: dict | None = None, nonce: str | None = None) -> None:
    payload = {
        "iss": ISS,
        "aud": "client-123",
        "iat": int(time.time()),
        "exp": int(time.time()) + 300,
        "nonce": nonce or "",
        **(claims or state["claims"]),
    }
    state["token"] = jwt.encode(payload, _private_pem, algorithm="RS256", headers={"kid": "kid-1"})


# ---------------------------------------------------------------- 管理端


def test_admin_crud_never_echoes_secret(client, world):
    token = _login(client, "sso_owner", world["yard"].id)
    created = _create_provider(client, token)
    assert created["client_secret_configured"] is True
    assert "client_secret" not in created and "client_secret_enc" not in created

    listed = client.get(f"{PREFIX}/settings/sso-providers", headers=_hdr(token))
    assert listed.status_code == 200
    assert listed.json()["data"][0]["client_secret_configured"] is True
    assert "super-secret-value" not in listed.text

    # 空串表示沿用已存密钥，不能把原有密钥清掉
    patched = client.patch(
        f"{PREFIX}/settings/sso-providers/{created['id']}",
        json={"client_secret": "", "scopes": "openid email"},
        headers=_hdr(token),
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["data"]["client_secret_configured"] is True
    assert patched.json()["data"]["scopes"] == "openid email"


def test_admin_rejects_duplicate_name_and_privileged_role(client, world):
    token = _login(client, "sso_owner", world["yard"].id)
    _create_provider(client, token)

    dup = client.post(
        f"{PREFIX}/settings/sso-providers",
        json={**PROVIDER_BODY, "name": "学校统一认证"},
        headers=_hdr(token),
    )
    assert dup.status_code == 409
    assert dup.json()["error"]["code"] == "SSO_PROVIDER_EXISTS"

    bad_role = client.post(
        f"{PREFIX}/settings/sso-providers",
        json={**PROVIDER_BODY, "name": "越权映射", "role_mapping": {"boss": "yard_owner"}},
        headers=_hdr(token),
    )
    assert bad_role.status_code == 422
    assert bad_role.json()["error"]["code"] == "INVALID_REQUEST"


def test_viewer_forbidden_and_admin_read_only(client, world):
    viewer = _login(client, "sso_viewer", world["yard"].id)
    assert client.get(f"{PREFIX}/settings/sso-providers", headers=_hdr(viewer)).status_code == 403

    # 管理员可查看配置，但开关身份源属保护型权限（默认仅负责人 / 逐人授权）
    admin = _login(client, "sso_admin", world["yard"].id)
    listed = client.get(f"{PREFIX}/settings/sso-providers", headers=_hdr(admin))
    assert listed.status_code == 200
    denied = client.post(
        f"{PREFIX}/settings/sso-providers", json=PROVIDER_BODY, headers=_hdr(admin)
    )
    assert denied.status_code == 403


def test_provider_test_endpoint_reports_signing_keys(client, world, idp):
    token = _login(client, "sso_owner", world["yard"].id)
    created = _create_provider(client, token)
    resp = client.post(f"{PREFIX}/settings/sso-providers/{created['id']}/test", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["issuer"] == ISS
    assert data["signing_keys"] == 1
    assert "super-secret-value" not in resp.text


def test_delete_provider_keeps_users_but_drops_identities(client, db_session, world, idp):
    token = _login(client, "sso_owner", world["yard"].id)
    created = _create_provider(client, token)

    # 先走一次登录，产生身份绑定
    started = client.get(f"{PREFIX}/auth/sso/{created['id']}/start")
    assert started.status_code == 200, started.text
    state = started.json()["data"]["state"]
    _sign_token(idp, nonce=sso.verify_state(state)["nonce"])
    client.get(
        f"{PREFIX}/auth/sso/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert db_session.query(SsoIdentity).count() == 1

    deleted = client.delete(f"{PREFIX}/settings/sso-providers/{created['id']}", headers=_hdr(token))
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"{PREFIX}/settings/sso-providers", headers=_hdr(token)).json()["data"] == []


# ---------------------------------------------------------------- 公开登录链路


def test_public_providers_hidden_without_config(client, world):
    resp = client.get(f"{PREFIX}/auth/sso/providers")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"enabled": True, "providers": []}


def test_full_login_flow_start_callback_exchange(client, db_session, world, idp):
    token = _login(client, "sso_owner", world["yard"].id)
    created = _create_provider(client, token)

    public = client.get(f"{PREFIX}/auth/sso/providers")
    listed = public.json()["data"]["providers"]
    assert [p["id"] for p in listed] == [created["id"]]
    assert "issuer" not in listed[0]  # 只暴露主机名

    started = client.get(f"{PREFIX}/auth/sso/{created['id']}/start")
    assert started.status_code == 200, started.text
    state = started.json()["data"]["state"]
    assert "sso_pkce" in client.cookies

    _sign_token(idp, nonce=sso.verify_state(state)["nonce"])
    callback = client.get(
        f"{PREFIX}/auth/sso/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert callback.status_code == 302, callback.text
    location = callback.headers["location"]
    assert location.startswith("http://localhost:5173/sso/callback?code=")
    exchange_code = location.split("code=", 1)[1]
    assert "sso_pkce" not in client.cookies  # 回调后清掉 PKCE Cookie

    exchanged = client.post(f"{PREFIX}/auth/sso/exchange", json={"code": exchange_code})
    assert exchanged.status_code == 200, exchanged.text
    data = exchanged.json()["data"]
    assert data["access_token"] and data["refresh_token"]
    assert data["roles"] == ["expert"]
    assert data["user"]["username"] == "alice"

    user = db_session.query(User).filter(User.email == "alice@example.com").one()
    membership = db_session.query(YardMembership).filter_by(user_id=user.id).one()
    assert membership.role == "expert"
    assert db_session.query(SsoIdentity).filter_by(user_id=user.id).count() == 1

    replay = client.post(f"{PREFIX}/auth/sso/exchange", json={"code": exchange_code})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "SSO_CODE_INVALID"


def test_start_rejects_unknown_provider(client, world):
    resp = client.get(f"{PREFIX}/auth/sso/missing-provider/start")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SSO_PROVIDER_NOT_FOUND"


def test_callback_without_pkce_cookie_rejected(client, world, idp):
    token = _login(client, "sso_owner", world["yard"].id)
    created = _create_provider(client, token)
    started = client.get(f"{PREFIX}/auth/sso/{created['id']}/start")
    state = started.json()["data"]["state"]
    client.cookies.clear()
    resp = client.get(
        f"{PREFIX}/auth/sso/callback",
        params={"code": "auth-code", "state": state},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "SSO_PKCE_MISSING"
