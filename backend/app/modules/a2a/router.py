"""A2A 路由（v3.20）。

公开端点：``/.well-known/agent.json``（Agent Card，见 main.py）与
``POST /a2a/rpc``（入站 JSON-RPC，凭协作令牌鉴权）。

管理员端点：``/settings/a2a*``（读 ``settings:manage``，写 ``settings:manage_protected``）：
- 入站令牌生成/轮换（明文只返回一次）与启停；
- 协作方 CRUD、探测（拉取并缓存 Agent Card）、发起任务；
- 入站/出站任务列表与详情。

安全口径：协作方令牌 Fernet 加密、永不回显；出站每次前过 net_guard；
入站只执行只读技能白名单；错误只回稳定错误码。
"""

from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.rate_limit import RateLimiter
from app.core.responses import ok
from app.modules.a2a import service
from app.modules.a2a.models import A2aPeer
from app.modules.a2a.schemas import (
    A2aEndpointUpdate,
    A2aPeerCreate,
    A2aPeerUpdate,
    A2aTaskSend,
)
from app.modules.audit.service import log_audit

router = APIRouter(tags=["a2a"])

_read_gate = require_yard_permission("settings:manage")
_write_gate = require_yard_permission("settings:manage_protected")

_rpc_limiter = RateLimiter(max_attempts=120, window_seconds=60)


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", "req_unknown")


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _require_yard(user: CurrentUser) -> str:
    if user.yard_id is None:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


def _inbound_token(request: Request) -> str | None:
    header = request.headers.get("x-a2a-token")
    if header:
        return header.strip()
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


# ---------------------------------------------------------------- 入站


@router.post("/a2a/rpc")
def a2a_rpc(
    request: Request,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db_session),
):
    """A2A JSON-RPC 入口：认证失败走 HTTP 401，协议/业务错误走 JSON-RPC error。"""
    if not _rpc_limiter.is_allowed(_client_ip(request)):
        raise AppError("RATE_LIMITED", "请求过于频繁，请稍后再试。", http_status=429)
    endpoint = service.resolve_endpoint(db, _inbound_token(request))
    if not isinstance(payload, dict):
        return service.rpc_error(None, -32600, "请求体必须是 JSON 对象。")
    return service.handle_rpc(db, endpoint, payload, request_id=_request_id(request))


# ---------------------------------------------------------------- 状态与令牌


@router.get("/settings/a2a")
def get_a2a_status(
    request: Request,
    user: CurrentUser = Depends(_read_gate),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_yard(user)
    endpoint = service.get_endpoint(db, yard_id)
    base = str(request.base_url).rstrip("/")
    return ok(
        {
            "enabled": bool(settings.a2a_enabled),
            "card_url": f"{base}/.well-known/agent.json",
            "rpc_url": f"{base}/api/v1/a2a/rpc",
            "endpoint": (
                {
                    "enabled": endpoint.enabled,
                    "token_prefix": endpoint.token_prefix,
                    "last_used_at": (
                        endpoint.last_used_at.isoformat() if endpoint.last_used_at else None
                    ),
                }
                if endpoint
                else None
            ),
            "skills": [
                {"id": skill["id"], "name": skill["name"], "description": skill["description"]}
                for skill in service.SKILLS.values()
            ],
        },
        request,
    )


@router.post("/settings/a2a/token")
def rotate_a2a_token(
    request: Request,
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    """生成/轮换入站令牌；完整令牌只在此响应里出现一次。"""
    yard_id = _require_yard(user)
    endpoint, created = service.ensure_endpoint(db, yard_id, user.user_id)
    token = service.rotate_token(db, endpoint)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="a2a_inbound_token_rotated",
        resource_type="a2a_endpoint",
        resource_id=endpoint.id,
        result="success",
        request_id=_request_id(request),
        metadata={"created": created},
    )
    db.commit()
    return ok({"token": token, "token_prefix": endpoint.token_prefix}, request)


@router.patch("/settings/a2a")
def update_a2a_endpoint(
    request: Request,
    body: A2aEndpointUpdate,
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_yard(user)
    endpoint = service.get_endpoint(db, yard_id)
    if endpoint is None:
        raise AppError("A2A_ENDPOINT_MISSING", "请先生成协作令牌。", http_status=409)
    endpoint.enabled = body.enabled
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="a2a_inbound_toggled",
        resource_type="a2a_endpoint",
        resource_id=endpoint.id,
        result="success",
        request_id=_request_id(request),
        metadata={"enabled": endpoint.enabled},
    )
    db.commit()
    return ok({"enabled": endpoint.enabled}, request)


# ---------------------------------------------------------------- 协作方


@router.get("/settings/a2a/peers")
def list_peers(
    request: Request,
    user: CurrentUser = Depends(_read_gate),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_yard(user)
    return ok([service.peer_to_out(peer) for peer in service.list_peers(db, yard_id)], request)


@router.post("/settings/a2a/peers", status_code=201)
def create_peer(
    request: Request,
    body: A2aPeerCreate,
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_yard(user)
    base_url = service.normalize_base_url(body.base_url)
    exists = db.scalar(select(A2aPeer).where(A2aPeer.yard_id == yard_id, A2aPeer.name == body.name))
    if exists is not None:
        raise AppError("A2A_PEER_EXISTS", "同名协作方已存在。", http_status=409)
    peer = A2aPeer(
        yard_id=yard_id,
        name=body.name,
        base_url=base_url,
        token_enc=service.encrypt_peer_token(body.token),
        enabled=body.enabled,
        created_by=user.user_id,
    )
    db.add(peer)
    db.flush()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="a2a_peer_created",
        resource_type="a2a_peer",
        resource_id=peer.id,
        result="success",
        request_id=_request_id(request),
        metadata={"name": peer.name},
    )
    db.commit()
    return ok(service.peer_to_out(peer), request)


@router.patch("/settings/a2a/peers/{peer_id}")
def update_peer(
    peer_id: str,
    request: Request,
    body: A2aPeerUpdate,
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_yard(user)
    peer = service.get_peer_or_404(db, yard_id, peer_id)
    changes = body.model_dump(exclude_unset=True)
    touched: list[str] = []
    if changes.get("name") and changes["name"] != peer.name:
        exists = db.scalar(
            select(A2aPeer).where(
                A2aPeer.yard_id == yard_id,
                A2aPeer.name == changes["name"],
                A2aPeer.id != peer.id,
            )
        )
        if exists is not None:
            raise AppError("A2A_PEER_EXISTS", "同名协作方已存在。", http_status=409)
        peer.name = str(changes["name"])
        touched.append("name")
    if changes.get("base_url"):
        new_url = service.normalize_base_url(str(changes["base_url"]))
        if new_url != peer.base_url:
            peer.base_url = new_url
            # 换地址 = 旧卡片作废，必须重新探测
            peer.card_json = None
            peer.card_fetched_at = None
            peer.last_error = None
            touched.append("base_url")
    if "clear_token" in changes and changes["clear_token"]:
        peer.token_enc = None
        touched.append("token")
    elif changes.get("token"):
        peer.token_enc = service.encrypt_peer_token(str(changes["token"]))
        touched.append("token")
    if "enabled" in changes and changes["enabled"] is not None:
        peer.enabled = bool(changes["enabled"])
        touched.append("enabled")
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="a2a_peer_updated",
        resource_type="a2a_peer",
        resource_id=peer.id,
        result="success",
        request_id=_request_id(request),
        metadata={"name": peer.name, "fields": sorted(touched)},
    )
    db.commit()
    return ok(service.peer_to_out(peer), request)


@router.delete("/settings/a2a/peers/{peer_id}")
def delete_peer(
    peer_id: str,
    request: Request,
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_yard(user)
    peer = service.get_peer_or_404(db, yard_id, peer_id)
    db.delete(peer)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user.user_id,
        action="a2a_peer_deleted",
        resource_type="a2a_peer",
        resource_id=peer_id,
        result="success",
        request_id=_request_id(request),
        metadata={"name": peer.name},
    )
    db.commit()
    return ok({"deleted": peer_id}, request)


@router.post("/settings/a2a/peers/{peer_id}/probe")
def probe_peer(
    peer_id: str,
    request: Request,
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_yard(user)
    peer = service.get_peer_or_404(db, yard_id, peer_id)
    try:
        result = service.probe_peer(db, peer, request_id=_request_id(request))
    except AppError as e:
        peer.last_error = e.message[:300]
        db.commit()
        raise
    return ok(result, request)


@router.post("/settings/a2a/peers/{peer_id}/tasks", status_code=201)
def send_task(
    peer_id: str,
    request: Request,
    body: A2aTaskSend,
    user: CurrentUser = Depends(_write_gate),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_yard(user)
    peer = service.get_peer_or_404(db, yard_id, peer_id)
    task = service.send_task(
        db,
        peer,
        skill_id=body.skill_id,
        input_text=body.input_text,
        user_id=user.user_id,
        request_id=_request_id(request),
    )
    return ok(service.task_to_admin(task), request)


# ---------------------------------------------------------------- 任务


@router.get("/settings/a2a/tasks")
def list_tasks(
    request: Request,
    direction: str | None = None,
    status: str | None = None,
    limit: int = 50,
    user: CurrentUser = Depends(_read_gate),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_yard(user)
    rows = service.list_tasks(db, yard_id, direction=direction, status=status, limit=limit)
    return ok([service.task_to_admin(row) for row in rows], request)


@router.get("/settings/a2a/tasks/{task_id}")
def get_task(
    task_id: str,
    request: Request,
    user: CurrentUser = Depends(_read_gate),
    db: Session = Depends(get_db_session),
):
    yard_id = _require_yard(user)
    task = service.get_task_or_404(db, yard_id, task_id)
    return ok(service.task_to_admin(task), request)
