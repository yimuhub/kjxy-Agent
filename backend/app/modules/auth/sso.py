"""OIDC 授权码登录（v3.18）。

安全边界：
- 授权请求带 state（签名 JWT）+ nonce + PKCE(S256)；code_verifier 只放 HttpOnly Cookie；
- id_token 只接受非对称算法（RS/ES/EdDSA），必须通过 JWKS 验签，并校验
  iss/aud/exp/nonce；HS* 与 none 一律拒绝；
- 所有出网（discovery / token / jwks）都先过 ``check_egress_url``，超时固定、
  响应 1MB 上限，错误体不回传；
- JIT 建号只创建 active 用户与可分配角色（yard_admin/expert/operator/viewer），
  绝不把 platform_admin / yard_owner 交给 IdP 声明；
- 回调成功后签发一次性短码（只存 sha256，60 秒、用后即焚），由前端换正式令牌。
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx
import jwt
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.net_guard import check_egress_url
from app.core.security import hash_password
from app.modules.audit.service import log_audit
from app.modules.auth import service as auth_service
from app.modules.auth.models import User
from app.modules.auth.sso_models import SsoExchangeCode, SsoIdentity, SsoProvider
from app.modules.yards.models import YARD_ASSIGNABLE_ROLES, Yard, YardMembership
from app.modules.yards.repository import add_membership

log = get_logger(__name__)

_ALLOWED_ALGS = {"RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"}
_JSON_TTL = 600.0
_MAX_BYTES = 1_000_000
_STATE_TYPE = "sso_state"
_PKCE_COOKIE = "sso_pkce"

_json_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def reset_caches() -> None:
    """测试用：清空 discovery/JWKS 缓存。"""
    _json_cache.clear()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_issuer(issuer: str) -> str:
    return (issuer or "").strip().rstrip("/")


def _fetch_json(url: str, *, context: str) -> dict[str, Any]:
    now = time.time()
    cached = _json_cache.get(url)
    if cached and cached[0] > now:
        return cached[1]
    # IdP 地址由负责人配置（可信输入），允许校园内网 / 本机 Keycloak 等部署；
    # 元数据与链路本地地址仍被 net_guard 永久封死。
    check_egress_url(url, context=context, allow_local=True)
    try:
        with httpx.Client(
            timeout=settings.sso_http_timeout_seconds, follow_redirects=False
        ) as client:
            resp = client.get(url, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise AppError(
            "SSO_IDP_UNAVAILABLE", "无法连接身份提供方，请检查网络与 Issuer 配置。", http_status=502
        ) from e
    if resp.status_code != 200:
        raise AppError(
            "SSO_IDP_UNAVAILABLE",
            f"身份提供方返回 HTTP {resp.status_code}。",
            http_status=502,
        )
    if len(resp.content) > _MAX_BYTES:
        raise AppError("SSO_IDP_INVALID", "身份提供方响应体过大。", http_status=502)
    try:
        data = resp.json()
    except ValueError as e:
        raise AppError("SSO_IDP_INVALID", "身份提供方返回的不是 JSON。", http_status=502) from e
    if not isinstance(data, dict):
        raise AppError("SSO_IDP_INVALID", "身份提供方响应格式不正确。", http_status=502)
    _json_cache[url] = (now + _JSON_TTL, data)
    return data


def _post_form(url: str, data: dict[str, str], *, context: str) -> dict[str, Any]:
    check_egress_url(url, context=context, allow_local=True)
    try:
        with httpx.Client(
            timeout=settings.sso_http_timeout_seconds, follow_redirects=False
        ) as client:
            resp = client.post(url, data=data, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        raise AppError(
            "SSO_IDP_UNAVAILABLE", "无法连接身份提供方的令牌端点。", http_status=502
        ) from e
    if len(resp.content) > _MAX_BYTES:
        raise AppError("SSO_IDP_INVALID", "令牌端点响应体过大。", http_status=502)
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if resp.status_code != 200:
        # 只透出 OAuth error 码，绝不回传 id_token/access_token 或原始响应体
        error = str(body.get("error") or "") if isinstance(body, dict) else ""
        raise AppError(
            "SSO_TOKEN_EXCHANGE_FAILED",
            f"身份提供方拒绝换取令牌{f'（{error}）' if error else ''}。",
            http_status=401,
        )
    if not isinstance(body, dict):
        raise AppError("SSO_IDP_INVALID", "令牌端点响应格式不正确。", http_status=502)
    return body


def discover(provider: SsoProvider) -> dict[str, Any]:
    url = f"{normalize_issuer(provider.issuer)}/.well-known/openid-configuration"
    data = _fetch_json(url, context=f"sso:{provider.id}:discovery")
    issuer = normalize_issuer(str(data.get("issuer") or ""))
    if issuer != normalize_issuer(provider.issuer):
        raise AppError(
            "SSO_ISSUER_MISMATCH",
            "身份提供方声明的 issuer 与配置不一致，已拒绝登录。",
            http_status=502,
            details={"expected": normalize_issuer(provider.issuer), "actual": issuer},
        )
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint = str(data.get(key) or "")
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise AppError(
                "SSO_IDP_INVALID",
                f"身份提供方的 {key} 不合法。",
                http_status=502,
            )
    return data


def _jwks(provider: SsoProvider, discovery: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(discovery["jwks_uri"])
    data = _fetch_json(url, context=f"sso:{provider.id}:jwks")
    keys = data.get("keys")
    if not isinstance(keys, list) or not keys:
        raise AppError("SSO_IDP_INVALID", "身份提供方的 JWKS 为空。", http_status=502)
    return [k for k in keys if isinstance(k, dict)]


def _signing_key(provider: SsoProvider, discovery: dict[str, Any], kid: str | None) -> Any:
    keys = _jwks(provider, discovery)
    candidates = [k for k in keys if not kid or str(k.get("kid") or "") == kid]
    if not candidates and kid:
        # kid 轮换：清缓存后重取一次
        _json_cache.pop(str(discovery["jwks_uri"]), None)
        keys = _jwks(provider, discovery)
        candidates = [k for k in keys if str(k.get("kid") or "") == kid]
    if not candidates:
        raise AppError("SSO_ID_TOKEN_INVALID", "找不到匹配的签名公钥。", http_status=401)
    try:
        return jwt.PyJWK(candidates[0]).key
    except jwt.PyJWTError as e:
        raise AppError("SSO_IDP_INVALID", "JWKS 公钥格式不正确。", http_status=502) from e


def build_authorize_url(provider: SsoProvider) -> tuple[str, str, str]:
    """返回 (authorize_url, state, pkce_cookie)。pkce_cookie 由路由写入 HttpOnly Cookie。"""
    discovery = discover(provider)
    now = int(time.time())
    nonce = secrets.token_urlsafe(24)
    state = jwt.encode(
        {
            "type": _STATE_TYPE,
            "idp_id": provider.id,
            "nonce": nonce,
            "iat": now,
            "exp": now + settings.sso_state_ttl_seconds,
        },
        settings.app_secret_key,
        algorithm="HS256",
    )
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
    challenge = challenge.rstrip("=")
    params = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": provider.redirect_uri,
        "scope": provider.scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    separator = "&" if "?" in str(discovery["authorization_endpoint"]) else "?"
    url = f"{discovery['authorization_endpoint']}{separator}{urlencode(params)}"
    return url, state, f"{provider.id}.{verifier}"


def verify_state(state: str) -> dict[str, Any]:
    try:
        payload = jwt.decode(
            state,
            settings.app_secret_key,
            algorithms=["HS256"],
            options={"require": ["exp", "iat"]},
        )
    except jwt.PyJWTError as e:
        raise AppError("SSO_STATE_INVALID", "登录请求已失效，请重新发起。", http_status=400) from e
    if payload.get("type") != _STATE_TYPE or not payload.get("idp_id"):
        raise AppError("SSO_STATE_INVALID", "登录请求无效，请重新发起。", http_status=400)
    return payload


def parse_pkce_cookie(cookie: str | None) -> tuple[str, str]:
    if not cookie or "." not in cookie:
        raise AppError(
            "SSO_PKCE_MISSING", "浏览器缺少登录校验信息，请重新发起登录。", http_status=400
        )
    idp_id, verifier = cookie.split(".", 1)
    if not idp_id or len(verifier) < 43:
        raise AppError(
            "SSO_PKCE_MISSING", "浏览器缺少登录校验信息，请重新发起登录。", http_status=400
        )
    return idp_id, verifier


def decode_id_token(
    provider: SsoProvider,
    discovery: dict[str, Any],
    id_token: str,
    nonce: str,
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as e:
        raise AppError("SSO_ID_TOKEN_INVALID", "id_token 格式不正确。", http_status=401) from e
    alg = str(header.get("alg") or "")
    if alg not in _ALLOWED_ALGS:
        raise AppError("SSO_ID_TOKEN_INVALID", f"不接受的签名算法：{alg or '<empty>'}。", 401)
    key = _signing_key(provider, discovery, str(header.get("kid") or "") or None)
    try:
        claims = jwt.decode(
            id_token,
            key,
            algorithms=[alg],
            audience=provider.client_id,
            # discovery 已校验 issuer 与配置等价；这里用 IdP 声明的原值做精确比对，
            # 避免配置结尾斜杠与 IdP 声明不一致造成假阴性。
            issuer=str(discovery.get("issuer") or provider.issuer),
            leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as e:
        raise AppError("SSO_ID_TOKEN_INVALID", "id_token 校验失败。", http_status=401) from e
    if str(claims.get("nonce") or "") != nonce:
        raise AppError("SSO_NONCE_MISMATCH", "登录校验码不匹配，请重新发起。", http_status=401)
    return claims


def _claim_groups(provider: SsoProvider, claims: dict[str, Any]) -> list[str]:
    raw = claims.get(provider.group_claim)
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def role_for_claims(provider: SsoProvider, claims: dict[str, Any]) -> str:
    mapping = provider.role_mapping_json or {}
    for group in _claim_groups(provider, claims):
        role = mapping.get(group)
        if role in YARD_ASSIGNABLE_ROLES:
            return str(role)
    if provider.default_role in YARD_ASSIGNABLE_ROLES:
        return provider.default_role
    return "viewer"


def _email_domain_allowed(provider: SsoProvider, email: str) -> bool:
    domains = [
        str(d).strip().lower().lstrip("@")
        for d in (provider.email_domains_json or [])
        if str(d).strip()
    ]
    if not domains:
        return True
    domain = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    return domain in domains


def _unique_username(db: Session, email: str | None, subject: str) -> str:
    base = (email.split("@", 1)[0] if email and "@" in email else f"sso_{subject[:12]}").lower()
    base = re.sub(r"[^a-z0-9._-]+", "_", base).strip("._-") or "sso_user"
    base = base[:48]
    candidate = base
    for index in range(1, 50):
        exists = db.query(User.id).filter(User.username == candidate).first()
        if exists is None:
            return candidate
        candidate = f"{base}_{index}"
    raise AppError("SSO_USERNAME_CONFLICT", "无法生成唯一用户名，请联系管理员。", http_status=409)


def _resolve_user(
    db: Session,
    provider: SsoProvider,
    claims: dict[str, Any],
    *,
    request_id: str,
) -> tuple[User, bool]:
    subject = str(claims["sub"])
    email = str(claims.get("email") or "").strip() or None
    verified = claims.get("email_verified") is True

    identity = (
        db.query(SsoIdentity)
        .filter(SsoIdentity.idp_id == provider.id, SsoIdentity.subject == subject)
        .first()
    )
    if identity is not None:
        user = db.query(User).filter(User.id == identity.user_id).first()
        if user is None:
            raise AppError("SSO_USER_NOT_PROVISIONED", "绑定的本地账号已不存在。", http_status=403)
        if user.status != "active":
            raise AppError("SSO_USER_DISABLED", "账号已停用，请联系管理员。", http_status=403)
        identity.email = email
        identity.last_login_at = datetime.now(UTC)
        return user, False

    if email and not verified:
        raise AppError(
            "SSO_EMAIL_UNVERIFIED", "身份提供方未验证邮箱，无法建立账号绑定。", http_status=403
        )
    if email and not _email_domain_allowed(provider, email):
        raise AppError("SSO_EMAIL_DOMAIN_DENIED", "邮箱域名不在允许范围内。", http_status=403)

    user = None
    if email and provider.link_by_email:
        user = db.query(User).filter(func.lower(User.email) == email.lower()).first()
    if user is not None:
        if user.status != "active":
            raise AppError("SSO_USER_DISABLED", "账号已停用，请联系管理员。", http_status=403)
    elif not provider.auto_create_users:
        raise AppError(
            "SSO_USER_NOT_PROVISIONED", "该账号尚未开通，请联系小院管理员。", http_status=403
        )
    else:
        display = str(claims.get("name") or claims.get("preferred_username") or email or subject)
        user = User(
            username=_unique_username(db, email, subject),
            email=email,
            display_name=display[:64],
            password_hash=hash_password(secrets.token_urlsafe(32)),
            status="active",
            must_change_password=False,
        )
        db.add(user)
        db.flush()

    db.add(
        SsoIdentity(
            idp_id=provider.id,
            user_id=user.id,
            subject=subject,
            email=email,
            last_login_at=datetime.now(UTC),
        )
    )
    db.flush()
    log_audit(
        db,
        yard_id=provider.yard_id,
        actor_id=user.id,
        action="sso_identity_linked",
        resource_type="user",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={"provider_id": provider.id, "subject_hash": _sha256(subject)[:12]},
    )
    return user, True


def _ensure_membership(
    db: Session,
    provider: SsoProvider,
    user: User,
    role: str,
    *,
    request_id: str,
) -> None:
    membership = (
        db.query(YardMembership)
        .filter(YardMembership.yard_id == provider.yard_id, YardMembership.user_id == user.id)
        .first()
    )
    if membership is None:
        add_membership(db, yard_id=provider.yard_id, user_id=user.id, role=role)
        log_audit(
            db,
            yard_id=provider.yard_id,
            actor_id=user.id,
            action="sso_membership_created",
            resource_type="yard_membership",
            resource_id=user.id,
            result="success",
            request_id=request_id,
            metadata={"provider_id": provider.id, "role": role},
        )
        return
    if membership.status != "active":
        raise AppError("SSO_USER_DISABLED", "你在该小院的成员资格已停用。", http_status=403)
    # 负责人身份不能由 IdP 自动降级；其余角色按 IdP 映射同步。
    if membership.role == "yard_owner" or membership.role == role:
        return
    previous = membership.role
    membership.role = role
    log_audit(
        db,
        yard_id=provider.yard_id,
        actor_id=user.id,
        action="sso_membership_role_synced",
        resource_type="yard_membership",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={"provider_id": provider.id, "from": previous, "to": role},
    )


def _frontend_callback(provider: SsoProvider) -> str:
    if provider.frontend_redirect_uri:
        return provider.frontend_redirect_uri
    parsed = urlparse(provider.redirect_uri)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    return f"{origin}/sso/callback" if origin else "/sso/callback"


def handle_callback(
    db: Session,
    *,
    code: str,
    state: str,
    pkce_cookie: str | None,
    request_id: str = "",
) -> str:
    """完成授权码换取、JIT/绑定、角色同步，返回前端落地地址（带一次性 code）。"""
    payload = verify_state(state)
    provider = db.query(SsoProvider).filter(SsoProvider.id == str(payload["idp_id"])).first()
    if provider is None:
        raise AppError("SSO_PROVIDER_NOT_FOUND", "身份提供方不存在。", http_status=404)
    if not provider.enabled:
        raise AppError("SSO_PROVIDER_DISABLED", "该登录方式已停用。", http_status=403)
    cookie_idp, verifier = parse_pkce_cookie(pkce_cookie)
    if cookie_idp != provider.id:
        raise AppError("SSO_STATE_INVALID", "登录请求与校验信息不匹配。", http_status=400)

    discovery = discover(provider)
    token_body = _post_form(
        str(discovery["token_endpoint"]),
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": provider.redirect_uri,
            "client_id": provider.client_id,
            "client_secret": _client_secret(provider),
            "code_verifier": verifier,
        },
        context=f"sso:{provider.id}:token",
    )
    id_token = str(token_body.get("id_token") or "")
    if not id_token:
        raise AppError("SSO_TOKEN_EXCHANGE_FAILED", "令牌响应缺少 id_token。", http_status=401)
    claims = decode_id_token(provider, discovery, id_token, str(payload.get("nonce") or ""))

    email = str(claims.get("email") or "").strip()
    if provider.require_email_verified and claims.get("email_verified") is not True:
        raise AppError("SSO_EMAIL_UNVERIFIED", "身份提供方未验证邮箱，无法登录。", http_status=403)
    if email and not _email_domain_allowed(provider, email):
        raise AppError("SSO_EMAIL_DOMAIN_DENIED", "邮箱域名不在允许范围内。", http_status=403)

    user, created = _resolve_user(db, provider, claims, request_id=request_id)
    role = role_for_claims(provider, claims)
    _ensure_membership(db, provider, user, role, request_id=request_id)

    # 顺手清理过期短码（走 expires_at 索引），这张表只保存「正在登录」的极短窗口
    db.query(SsoExchangeCode).filter(SsoExchangeCode.expires_at < datetime.now(UTC)).delete(
        synchronize_session=False
    )
    raw_code = secrets.token_urlsafe(32)
    db.add(
        SsoExchangeCode(
            code_hash=_sha256(raw_code),
            user_id=user.id,
            yard_id=provider.yard_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=settings.sso_exchange_ttl_seconds),
        )
    )
    log_audit(
        db,
        yard_id=provider.yard_id,
        actor_id=user.id,
        action="sso_login_success",
        resource_type="auth",
        resource_id=user.id,
        result="success",
        request_id=request_id,
        metadata={
            "provider_id": provider.id,
            "provider_name": provider.name,
            "role": role,
            "created_user": created,
        },
    )
    db.commit()
    return _with_code(_frontend_callback(provider), raw_code)


def _with_code(url: str, code: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}code={code}"


def encrypt_client_secret(secret: str) -> str:
    """OIDC client_secret 入库前加密（复用 Fernet 主密钥）。"""
    from app.modules.llm.supplier_crypto import get_fernet

    return get_fernet().encrypt(secret.encode("utf-8")).decode("utf-8")


def _client_secret(provider: SsoProvider) -> str:
    from app.modules.llm.supplier_crypto import get_fernet

    try:
        plain = get_fernet().decrypt(provider.client_secret_enc.encode("utf-8"))
    except AppError:
        raise
    except Exception as e:
        raise AppError(
            "SSO_SECRET_DECRYPT_FAILED",
            "OIDC 客户端密钥解密失败（主密钥可能已轮换），请重新录入。",
            http_status=500,
        ) from e
    return plain.decode("utf-8")


def exchange_code(db: Session, raw_code: str, *, request_id: str = "") -> dict[str, Any]:
    """用一次性短码换正式登录响应；短码用后即焚，过期/重放一律 401。"""
    code_hash = _sha256(raw_code)
    row = db.query(SsoExchangeCode).filter(SsoExchangeCode.code_hash == code_hash).first()
    if row is None:
        raise AppError("SSO_CODE_INVALID", "登录回调已失效，请重新登录。", http_status=401)
    now = datetime.now(UTC)
    expires = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    if row.used_at is not None or expires <= now:
        raise AppError("SSO_CODE_INVALID", "登录回调已失效，请重新登录。", http_status=401)
    updated = (
        db.query(SsoExchangeCode)
        .filter(SsoExchangeCode.id == row.id, SsoExchangeCode.used_at.is_(None))
        .update({"used_at": now})
    )
    if not updated:
        raise AppError("SSO_CODE_INVALID", "登录回调已失效，请重新登录。", http_status=401)
    user = db.query(User).filter(User.id == row.user_id).first()
    if user is None or user.status != "active":
        raise AppError("SSO_USER_DISABLED", "账号已停用，请联系管理员。", http_status=403)
    if not row.yard_id:
        raise AppError("YARD_REQUIRED", "登录回调缺少小院上下文。", http_status=400)
    response = auth_service.issue_login_response(
        db, user, row.yard_id, request_id=request_id, method="sso"
    )
    data = response.model_dump()
    # 多小院账号可能归属多个小院，前端要按 IdP 归属的那个小院落地
    data["yard_id"] = row.yard_id
    return data


def serialize_provider(provider: SsoProvider, *, yard_name: str | None = None) -> dict[str, Any]:
    return {
        "id": provider.id,
        "yard_id": provider.yard_id,
        "yard_name": yard_name,
        "name": provider.name,
        "issuer": provider.issuer,
        "client_id": provider.client_id,
        "client_secret_configured": bool(provider.client_secret_enc),
        "redirect_uri": provider.redirect_uri,
        "frontend_redirect_uri": provider.frontend_redirect_uri,
        "scopes": provider.scopes,
        "group_claim": provider.group_claim,
        "role_mapping": provider.role_mapping_json or {},
        "default_role": provider.default_role,
        "email_domains": provider.email_domains_json or [],
        "enabled": provider.enabled,
        "auto_create_users": provider.auto_create_users,
        "link_by_email": provider.link_by_email,
        "require_email_verified": provider.require_email_verified,
        "last_test_at": provider.last_test_at,
        "last_error": provider.last_error,
        "created_at": provider.created_at,
    }


def public_providers(db: Session) -> list[dict[str, Any]]:
    if not settings.sso_enabled:
        return []
    rows = (
        db.query(SsoProvider, Yard.name)
        .join(Yard, Yard.id == SsoProvider.yard_id)
        .filter(SsoProvider.enabled.is_(True))
        .order_by(SsoProvider.yard_id, SsoProvider.name)
        .all()
    )
    return [
        {
            "id": provider.id,
            "name": provider.name,
            "yard_id": provider.yard_id,
            "yard_name": yard_name,
            "issuer_host": urlparse(provider.issuer).hostname or "",
        }
        for provider, yard_name in rows
    ]


def provider_for_login(db: Session, provider_id: str) -> SsoProvider:
    if not settings.sso_enabled:
        raise AppError("SSO_DISABLED", "当前部署未启用单点登录。", http_status=404)
    provider = db.query(SsoProvider).filter(SsoProvider.id == provider_id).first()
    if provider is None:
        raise AppError("SSO_PROVIDER_NOT_FOUND", "身份提供方不存在。", http_status=404)
    if not provider.enabled:
        raise AppError("SSO_PROVIDER_DISABLED", "该登录方式已停用。", http_status=403)
    return provider


def test_provider(db: Session, provider: SsoProvider) -> dict[str, Any]:
    """探测 discovery 与 JWKS：只回端点主机与公钥数量，不回密钥。"""
    discovery = discover(provider)
    keys = _jwks(provider, discovery)
    provider.last_test_at = datetime.now(UTC)
    provider.last_error = None
    db.flush()
    return {
        "issuer": discovery.get("issuer"),
        "authorization_endpoint": discovery.get("authorization_endpoint"),
        "token_endpoint": discovery.get("token_endpoint"),
        "jwks_uri": discovery.get("jwks_uri"),
        "signing_keys": len(keys),
        "supported_algorithms": [
            alg
            for alg in (discovery.get("id_token_signing_alg_values_supported") or [])
            if alg in _ALLOWED_ALGS
        ],
    }


def record_provider_error(db: Session, provider: SsoProvider, message: str) -> None:
    provider.last_test_at = datetime.now(UTC)
    provider.last_error = message[:500]
    db.flush()


__all__ = [
    "build_authorize_url",
    "decode_id_token",
    "discover",
    "encrypt_client_secret",
    "exchange_code",
    "handle_callback",
    "normalize_issuer",
    "parse_pkce_cookie",
    "provider_for_login",
    "public_providers",
    "record_provider_error",
    "reset_caches",
    "role_for_claims",
    "serialize_provider",
    "test_provider",
    "verify_state",
]
