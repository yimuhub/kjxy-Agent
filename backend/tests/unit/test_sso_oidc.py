"""SSO/OIDC 单元测试（v3.18）：授权码 + PKCE + nonce + JIT/角色映射。

出网全部 mock：``_fetch_json`` 返回 discovery/JWKS，``_post_form`` 返回 id_token；
id_token 用测试内生成的 RSA 私钥签名，验签走真实的 PyJWT + JWKS 路径。
"""

from __future__ import annotations

import hashlib
import time
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import jwt
import pytest
from app.core.errors import AppError
from app.modules.auth import sso
from app.modules.auth.models import User
from app.modules.auth.sso_models import SsoExchangeCode, SsoIdentity, SsoProvider
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import add_membership, create_yard
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

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


def _user(db, username: str, email: str | None = None) -> User:
    user = User(
        username=username,
        email=email or f"{username}@example.com",
        password_hash="x",
        display_name=username,
        status="active",
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    return user


def _provider(db, yard_id: str, **overrides) -> SsoProvider:
    data = {
        "yard_id": yard_id,
        "name": "Test IdP",
        "issuer": ISS,
        "client_id": "client-123",
        "client_secret_enc": sso.encrypt_client_secret("client-secret"),
        "redirect_uri": "http://localhost:8000/api/v1/auth/sso/callback",
        "frontend_redirect_uri": "http://localhost:5173/sso/callback",
        "scopes": "openid email profile",
        "group_claim": "groups",
        "role_mapping_json": {"lab": "expert", "ops": "operator"},
        "default_role": "viewer",
        "enabled": True,
    }
    data.update(overrides)
    provider = SsoProvider(**data)
    db.add(provider)
    db.flush()
    return provider


def _id_token(claims: dict, *, alg: str = "RS256", key=None) -> str:
    now = int(time.time())
    payload = {"iat": now, "exp": now + 300, "iss": ISS, "aud": "client-123"}
    payload.update(claims)
    return jwt.encode(payload, key or _private_pem, algorithm=alg, headers={"kid": "kid-1"})


@pytest.fixture(autouse=True)
def _sso_on(monkeypatch):
    monkeypatch.setattr(sso.settings, "sso_enabled", True)
    sso.reset_caches()
    yield
    sso.reset_caches()


@pytest.fixture()
def fake_idp(monkeypatch):
    state: dict[str, str] = {}

    def fake_fetch(url: str, *, context: str):
        if url.endswith("openid-configuration"):
            return DISCOVERY
        if url == JWKS_URI:
            return {"keys": [_jwk]}
        raise AssertionError(f"unexpected url {url} ({context})")

    def fake_post(url: str, data: dict[str, str], *, context: str):
        assert url == f"{ISS}/token"
        assert data["grant_type"] == "authorization_code"
        assert data["code_verifier"]
        return {"id_token": state["token"]}

    monkeypatch.setattr(sso, "_fetch_json", fake_fetch)
    monkeypatch.setattr(sso, "_post_form", fake_post)
    return state


def _start(provider, fake_idp, idp_claims):
    _, state, cookie = sso.build_authorize_url(provider)
    nonce = sso.verify_state(state)["nonce"]
    fake_idp["token"] = _id_token({**idp_claims, "nonce": nonce})
    return state, cookie


def _run_callback(db, provider, fake_idp, claims):
    state, cookie = _start(provider, fake_idp, claims)
    return sso.handle_callback(db, code="auth-code", state=state, pkce_cookie=cookie)


# ---------------------------------------------------------------- 授权请求


def test_start_builds_pkce_and_signed_state(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    url, state, cookie = sso.build_authorize_url(provider)
    params = parse_qs(urlparse(url).query)
    assert params["response_type"] == ["code"]
    assert params["client_id"] == ["client-123"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["nonce"][0]
    assert state
    cookie_idp, verifier = cookie.split(".", 1)
    assert cookie_idp == provider.id
    expected = urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert params["code_challenge"] == [expected]
    assert sso.verify_state(state)["idp_id"] == provider.id


def test_tampered_state_rejected(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    _, state, _ = sso.build_authorize_url(provider)
    with pytest.raises(AppError) as e:
        sso.verify_state(state[:-2] + "xx")
    assert e.value.code == "SSO_STATE_INVALID"


def test_discovery_issuer_mismatch_rejected(db_session, monkeypatch):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    monkeypatch.setattr(
        sso, "_fetch_json", lambda url, *, context: {**DISCOVERY, "issuer": "https://evil.test"}
    )
    with pytest.raises(AppError) as e:
        sso.discover(provider)
    assert e.value.code == "SSO_ISSUER_MISMATCH"


# ---------------------------------------------------------------- 回调与 JIT


def test_callback_jit_creates_user_membership_and_exchange(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    redirect = _run_callback(
        db_session,
        provider,
        fake_idp,
        {
            "sub": "subject-1",
            "email": "alice@example.com",
            "email_verified": True,
            "name": "Alice",
            "groups": ["lab"],
        },
    )
    assert redirect.startswith("http://localhost:5173/sso/callback?code=")
    raw_code = redirect.split("code=", 1)[1]

    user = db_session.query(User).filter(User.email == "alice@example.com").one()
    membership = db_session.query(YardMembership).filter_by(user_id=user.id).one()
    assert membership.role == "expert"
    identity = (
        db_session.query(SsoIdentity).filter_by(idp_id=provider.id, subject="subject-1").one()
    )
    assert identity.user_id == user.id

    login = sso.exchange_code(db_session, raw_code)
    assert login["roles"] == ["expert"]
    assert login["access_token"]
    assert login["user"]["username"] == "alice"


def test_callback_links_existing_user_by_email(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    existing = _user(db_session, "bob", "bob@example.com")
    add_membership(db_session, yard_id=yard.id, user_id=existing.id, role="viewer")
    db_session.flush()

    _run_callback(
        db_session,
        provider,
        fake_idp,
        {
            "sub": "subject-bob",
            "email": "BOB@example.com",
            "email_verified": True,
            "groups": [],
        },
    )
    db_session.refresh(existing)
    identities = db_session.query(SsoIdentity).filter_by(idp_id=provider.id).all()
    assert len(identities) == 1
    assert identities[0].user_id == existing.id
    assert db_session.query(User).filter(User.username.like("bob%")).count() == 1


def test_callback_rejects_unverified_email(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    with pytest.raises(AppError) as e:
        _run_callback(
            db_session,
            provider,
            fake_idp,
            {"sub": "s", "email": "x@example.com", "email_verified": False},
        )
    assert e.value.code == "SSO_EMAIL_UNVERIFIED"


def test_callback_rejects_email_domain(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id, email_domains_json=["cau.edu.cn"])
    with pytest.raises(AppError) as e:
        _run_callback(
            db_session,
            provider,
            fake_idp,
            {"sub": "s", "email": "x@other.com", "email_verified": True},
        )
    assert e.value.code == "SSO_EMAIL_DOMAIN_DENIED"


def test_auto_create_disabled_rejects_unknown_user(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id, auto_create_users=False)
    with pytest.raises(AppError) as e:
        _run_callback(
            db_session,
            provider,
            fake_idp,
            {"sub": "s", "email": "new@example.com", "email_verified": True},
        )
    assert e.value.code == "SSO_USER_NOT_PROVISIONED"


def test_unknown_group_falls_back_to_default_role(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id, default_role="viewer")
    _run_callback(
        db_session,
        provider,
        fake_idp,
        {"sub": "s2", "email": "carol@example.com", "email_verified": True, "groups": ["unknown"]},
    )
    user = db_session.query(User).filter(User.email == "carol@example.com").one()
    membership = db_session.query(YardMembership).filter_by(user_id=user.id).one()
    assert membership.role == "viewer"


def test_role_mapping_cannot_grant_owner(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(
        db_session, yard.id, role_mapping_json={"boss": "yard_owner"}, default_role="viewer"
    )
    _run_callback(
        db_session,
        provider,
        fake_idp,
        {"sub": "s3", "email": "dave@example.com", "email_verified": True, "groups": ["boss"]},
    )
    user = db_session.query(User).filter(User.email == "dave@example.com").one()
    membership = db_session.query(YardMembership).filter_by(user_id=user.id).one()
    assert membership.role == "viewer"


# ---------------------------------------------------------------- 令牌与一次性码


def test_nonce_mismatch_rejected(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    _, state, cookie = sso.build_authorize_url(provider)
    fake_idp["token"] = _id_token(
        {
            "sub": "s",
            "email": "x@example.com",
            "email_verified": True,
            "nonce": "not-the-nonce",
        }
    )
    with pytest.raises(AppError) as e:
        sso.handle_callback(db_session, code="c", state=state, pkce_cookie=cookie)
    assert e.value.code == "SSO_NONCE_MISMATCH"


def test_wrong_audience_rejected(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    _, state, cookie = sso.build_authorize_url(provider)
    nonce = sso.verify_state(state)["nonce"]
    fake_idp["token"] = _id_token(
        {"sub": "s", "aud": "other-client", "nonce": nonce, "email_verified": True}
    )
    with pytest.raises(AppError) as e:
        sso.handle_callback(db_session, code="c", state=state, pkce_cookie=cookie)
    assert e.value.code == "SSO_ID_TOKEN_INVALID"


def test_symmetric_algorithm_rejected(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    _, state, cookie = sso.build_authorize_url(provider)
    nonce = sso.verify_state(state)["nonce"]
    fake_idp["token"] = jwt.encode(
        {
            "sub": "s",
            "iss": ISS,
            "aud": "client-123",
            "nonce": nonce,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
        },
        "shared-secret",
        algorithm="HS256",
        headers={"kid": "kid-1"},
    )
    with pytest.raises(AppError) as e:
        sso.handle_callback(db_session, code="c", state=state, pkce_cookie=cookie)
    assert e.value.code == "SSO_ID_TOKEN_INVALID"


def test_exchange_code_is_single_use(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    redirect = _run_callback(
        db_session,
        provider,
        fake_idp,
        {"sub": "s4", "email": "erin@example.com", "email_verified": True},
    )
    raw_code = redirect.split("code=", 1)[1]
    sso.exchange_code(db_session, raw_code)
    with pytest.raises(AppError) as e:
        sso.exchange_code(db_session, raw_code)
    assert e.value.code == "SSO_CODE_INVALID"


def test_expired_exchange_code_rejected(db_session, fake_idp):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    redirect = _run_callback(
        db_session,
        provider,
        fake_idp,
        {"sub": "s5", "email": "frank@example.com", "email_verified": True},
    )
    raw_code = redirect.split("code=", 1)[1]
    row = db_session.query(SsoExchangeCode).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.flush()
    with pytest.raises(AppError) as e:
        sso.exchange_code(db_session, raw_code)
    assert e.value.code == "SSO_CODE_INVALID"


def test_disabled_provider_cannot_login(db_session):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id, enabled=False)
    with pytest.raises(AppError) as e:
        sso.provider_for_login(db_session, provider.id)
    assert e.value.code == "SSO_PROVIDER_DISABLED"


def test_public_providers_hidden_when_global_switch_off(db_session, monkeypatch):
    yard = create_yard(db_session, name="SSO 小院")
    _provider(db_session, yard.id)
    monkeypatch.setattr(sso.settings, "sso_enabled", False)
    assert sso.public_providers(db_session) == []


def test_role_for_claims_skips_unassignable_mapping(db_session):
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(
        db_session,
        yard.id,
        role_mapping_json={"a": "yard_owner", "b": "platform_admin", "c": "expert"},
        default_role="operator",
    )
    assert sso.role_for_claims(provider, {"groups": ["a", "b"]}) == "operator"
    assert sso.role_for_claims(provider, {"groups": ["a", "c"]}) == "expert"


def test_yard_context_not_used_for_roles(db_session, fake_idp):
    """角色只来自 DB membership，绝不把 IdP 的 groups 直接写进 JWT。"""
    yard = create_yard(db_session, name="SSO 小院")
    provider = _provider(db_session, yard.id)
    redirect = _run_callback(
        db_session,
        provider,
        fake_idp,
        {"sub": "s6", "email": "gina@example.com", "email_verified": True, "groups": ["lab"]},
    )
    login = sso.exchange_code(db_session, redirect.split("code=", 1)[1])
    assert login["roles"] == ["expert"]
    assert "platform_admin" not in login["roles"]
    # 账号确实属于该小院
    assert any(t["yard_id"] == yard.id for t in login["yards"])
