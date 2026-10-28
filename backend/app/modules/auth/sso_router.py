"""SSO/OIDC 路由（v3.18）。

公开端点（无需登录）：``/auth/sso/providers|start|callback|exchange``；
管理员端点：``/settings/sso-providers``（写操作需 settings:manage_protected）。

安全口径：
- client_secret 只写不回显，列表仅返回 ``client_secret_configured``；
- 角色映射只允许可分配角色（yard_admin/expert/operator/viewer），
  platform_admin 与 yard_owner 绝不由 IdP 授予；
- 公开端点按 IP 限流；callback 失败只回错误码，不回 IdP 原始响应。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Body, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.auth import sso
from app.modules.auth.sso_models import SsoIdentity, SsoProvider
from app.modules.auth.sso_schemas import (
    SsoExchangeRequest,
    SsoProviderCreate,
    SsoProviderUpdate,
)
from app.modules.yards.models import YARD_ASSIGNABLE_ROLES

router = APIRouter(tags=["sso"])

_read_gate = require_yard_permission("settings:manage")
_write_gate = require_yard_permission("settings:manage_protected")

_start_limiter = RateLimiter(max_attempts=60, window_seconds=60)
_callback_limiter = RateLimiter(max_attempts=60, window_seconds=60)
_exchange_limiter = RateLimiter(max_attempts=30, window_seconds=60)

_PKCE_COOKIE = "sso_pkce"
_PKCE_PATH = "/api/v1/auth/sso"


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _check_limit(limiter: RateLimiter, key: str) -> None:
    if not limiter.is_allowed(key):
        raise AppError("RATE_LIMITED", "请求过于频繁，请稍后再试。", http_status=429)


def _validate_provider(payload: dict[str, Any]) -> None:
    for field in ("issuer", "redirect_uri", "frontend_redirect_uri"):
        value = payload.get(field)
        if not value:
            continue
        parsed = urlparse(str(value))
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise AppError("INVALID_REQUEST", f"{field} 必须是 http(s) 绝对地址。", http_status=422)
    mapping = payload.get("role_mapping") or {}
    if not isinstance(mapping, dict):
        raise AppError("INVALID_REQUEST", "role_mapping 必须是对象。", http_status=422)
    roles = [*mapping.values(), payload.get("default_role") or "viewer"]
    illegal = sorted({str(r) for r in roles if str(r) not in YARD_ASSIGNABLE_ROLES})
    if illegal:
        raise AppError(
            "INVALID_REQUEST",
            f"角色 {', '.join(illegal)} 不可由 SSO 授予；只允许 "
            f"{', '.join(YARD_ASSIGNABLE_ROLES)}。",
            http_status=422,
        )
    domains = payload.get("email_domains") or []
    if not isinstance(domains, list):
        raise AppError("INVALID_REQUEST", "email_domains 必须是数组。", http_status=422)


def _normalize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = dict(payload)
    if data.get("issuer"):
        data["issuer"] = sso.normalize_issuer(str(data["issuer"]))
    if "email_domains" in data:
        raw_domains = data.pop("email_domains") or []
        data["email_domains_json"] = [
            str(d).strip().lower().lstrip("@") for d in raw_domains if str(d).strip()
        ]
    if "role_mapping" in data:
        data["role_mapping_json"] = data.pop("role_mapping")
    if data.get("client_secret"):
        data["client_secret_enc"] = sso.encrypt_client_secret(str(data.pop("client_secret")))
    elif "client_secret" in data:
        data.pop("client_secret")
    return data


def _get_provider(db: Session, yard_id: str, provider_id: str) -> SsoProvider:
    provider = (
        db.query(SsoProvider)
        .filter(SsoProvider.id == provider_id, SsoProvider.yard_id == yard_id)
        .first()
    )
    if provider is None:
        raise AppError("SSO_PROVIDER_NOT_FOUND", "身份提供方不存在。", http_status=404)
    return provider


# ---------------------------------------------------------------- 公开端点


@router.get("/auth/sso/providers")
def list_public_providers(request: Request, db: Session = Depends(get_db_session)):
    """登录页可用的第三方登录方式（不含任何密钥与端点细节）。"""
    return ok(
        {"enabled": bool(settings.sso_enabled), "providers": sso.public_providers(db)},
        request,
    )


@router.get("/auth/sso/{provider_id}/start")
def start_sso(
    provider_id: str,
    request: Request,
    db: Session = Depends(get_db_session),
):
    """生成授权地址与 state，并把 code_verifier 写入 HttpOnly Cookie。"""
    _check_limit(_start_limiter, _client_ip(request))
    provider = sso.provider_for_login(db, provider_id)
    authorize_url, state, pkce_cookie = sso.build_authorize_url(provider)
    response = JSONResponse(ok({"authorize_url": authorize_url, "state": state}, request))
    response.set_cookie(
        _PKCE_COOKIE,
        pkce_cookie,
        max_age=settings.sso_state_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path=_PKCE_PATH,
    )
    return response


@router.get("/auth/sso/callback")
def sso_callback(
    request: Request,
    code: str,
    state: str,
    db: Session = Depends(get_db_session),
):
    """IdP 回调：换取令牌、JIT/绑定、角色同步，302 回前端并带一次性 code。"""
    _check_limit(_callback_limiter, _client_ip(request))
    redirect_url = sso.handle_callback(
        db,
        code=code,
        state=state,
        pkce_cookie=request.cookies.get(_PKCE_COOKIE),
        request_id=getattr(request.state, "request_id", ""),
    )
    response = RedirectResponse(redirect_url, status_code=302)
    response.delete_cookie(_PKCE_COOKIE, path=_PKCE_PATH)
    return response


@router.post("/auth/sso/exchange")
def exchange_sso(
    request: Request,
    body: SsoExchangeRequest,
    db: Session = Depends(get_db_session),
):
    """一次性 code 换正式登录响应（access/refresh + 用户 + 小院列表）。"""
    ip = _client_ip(request)
    _check_limit(_exchange_limiter, ip)
    data = sso.exchange_code(db, body.code, request_id=getattr(request.state, "request_id", ""))
    _exchange_limiter.reset(ip)
    return ok(data, request)


# ---------------------------------------------------------------- 管理员端点


@router.get("/settings/sso-providers")
def list_sso_providers(
    request: Request,
    user: CurrentUser = Depends(_read_gate),
    db: Session = Depends(get_db_session),
):
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    rows = (
        db.query(SsoProvider)
        .filter(SsoProvider.yard_id == user.yard_id)
        .order_by(SsoProvider.created_at.asc())
        .all()
    )
    return ok([sso.serialize_provider(row) for row in rows], request)


@router.post("/settings/sso-providers", status_code=201)
def create_sso_provider(
    request: Request,
    body: SsoProviderCreate,
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    payload = body.model_dump()
    _validate_provider(payload)
    exists = (
        db.query(SsoProvider)
        .filter(
            SsoProvider.yard_id == user.yard_id,
            SsoProvider.name == body.name,
        )
        .first()
    )
    if exists is not None:
        raise AppError("SSO_PROVIDER_EXISTS", "同名身份提供方已存在。", http_status=409)
    data = _normalize_payload(payload)
    provider = SsoProvider(
        yard_id=user.yard_id,
        created_by=user.user_id,
        **data,
    )
    db.add(provider)
    db.flush()
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action="sso_provider_created",
        resource_type="sso_provider",
        resource_id=provider.id,
        result="success",
        request_id=getattr(request.state, "request_id", ""),
        metadata={"name": provider.name, "issuer": provider.issuer},
    )
    db.commit()
    return JSONResponse(
        status_code=201,
        content=jsonable_encoder(ok(sso.serialize_provider(provider), request)),
    )


@router.patch("/settings/sso-providers/{provider_id}")
def update_sso_provider(
    provider_id: str,
    request: Request,
    body: SsoProviderUpdate = Body(...),
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    provider = _get_provider(db, user.yard_id, provider_id)
    payload = body.model_dump(exclude_unset=True)
    _validate_provider(payload)
    if payload.get("name") and payload["name"] != provider.name:
        exists = (
            db.query(SsoProvider)
            .filter(
                SsoProvider.yard_id == user.yard_id,
                SsoProvider.name == payload["name"],
                SsoProvider.id != provider.id,
            )
            .first()
        )
        if exists is not None:
            raise AppError("SSO_PROVIDER_EXISTS", "同名身份提供方已存在。", http_status=409)
    data = _normalize_payload(payload)
    for key, value in data.items():
        setattr(provider, key, value)
    db.flush()
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action="sso_provider_updated",
        resource_type="sso_provider",
        resource_id=provider.id,
        result="success",
        request_id=getattr(request.state, "request_id", ""),
        metadata={"name": provider.name, "fields": sorted(data.keys())},
    )
    db.commit()
    return ok(sso.serialize_provider(provider), request)


@router.delete("/settings/sso-providers/{provider_id}")
def delete_sso_provider(
    provider_id: str,
    request: Request,
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    provider = _get_provider(db, user.yard_id, provider_id)
    # 身份绑定随配置删除；本地账号保留，不会误删用户。
    db.query(SsoIdentity).filter(SsoIdentity.idp_id == provider.id).delete(
        synchronize_session=False
    )
    db.delete(provider)
    db.flush()
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action="sso_provider_deleted",
        resource_type="sso_provider",
        resource_id=provider_id,
        result="success",
        request_id=getattr(request.state, "request_id", ""),
        metadata={"name": provider.name},
    )
    db.commit()
    return ok({"deleted": provider_id}, request)


@router.post("/settings/sso-providers/{provider_id}/test")
def test_sso_provider(
    provider_id: str,
    request: Request,
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    provider = _get_provider(db, user.yard_id, provider_id)
    try:
        result = sso.test_provider(db, provider)
    except AppError as e:
        sso.record_provider_error(db, provider, e.message)
        db.commit()
        raise
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action="sso_provider_tested",
        resource_type="sso_provider",
        resource_id=provider.id,
        result="success",
        request_id=getattr(request.state, "request_id", ""),
        metadata={"signing_keys": result["signing_keys"]},
    )
    db.commit()
    return ok(result, request)
