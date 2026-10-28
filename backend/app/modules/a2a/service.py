"""A2A 服务（v3.20）：Agent Card、入站 JSON-RPC 任务、协作方探测与出站任务。

安全口径：
- 入站：``Authorization: Bearer <协作令牌>``（兼容 ``X-A2A-Token``），令牌只存
  sha256；令牌决定小院归属，绝不从请求体里取 yard_id；
- 出站：保存/探测/每次发任务前都过 ``check_egress_url``（元数据地址永远拒绝），
  响应体上限 1MB、超时按配置，协作方令牌 Fernet 加密且不回显；
- 技能白名单：入站只执行 ``yard.summary``（只读统计）与 ``knowledge.search``
  （只检索本小院知识库），不暴露工具执行与写入能力。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.net_guard import check_egress_url
from app.modules.a2a.models import (
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
    STATE_CANCELED,
    STATE_COMPLETED,
    STATE_FAILED,
    STATE_SUBMITTED,
    STATE_WORKING,
    TERMINAL_STATES,
    A2aEndpoint,
    A2aPeer,
    A2aTask,
)
from app.modules.audit.service import log_audit
from app.modules.farm.models import FarmCycle, FarmPlot, FarmRecord, WorkOrder

log = get_logger(__name__)

PROTOCOL_VERSION = "0.2.5"
CARD_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")
RPC_PATH = "/api/v1/a2a/rpc"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_CARD_BYTES = 64 * 1024
MAX_INPUT_TEXT = 8000

SKILL_YARD_SUMMARY = "yard.summary"
SKILL_KNOWLEDGE_SEARCH = "knowledge.search"

SKILLS: dict[str, dict[str, Any]] = {
    SKILL_YARD_SUMMARY: {
        "id": SKILL_YARD_SUMMARY,
        "name": "小院概况",
        "description": "返回本小院的地块、在种周期、农事记录与未完结工单数量。",
        "tags": ["agriculture", "read-only"],
        "examples": ["给出本小院的台账概况"],
        "inputModes": ["text/plain"],
        "outputModes": ["text/plain"],
    },
    SKILL_KNOWLEDGE_SEARCH: {
        "id": SKILL_KNOWLEDGE_SEARCH,
        "name": "知识检索",
        "description": "在本小院知识库中检索与输入文本最相关的片段（只读）。",
        "tags": ["knowledge", "read-only"],
        "examples": ["水稻分蘖期追肥建议"],
        "inputModes": ["text/plain"],
        "outputModes": ["text/plain"],
    },
}

_REMOTE_STATE_MAP = {
    "submitted": STATE_SUBMITTED,
    "working": STATE_WORKING,
    "completed": STATE_COMPLETED,
    "failed": STATE_FAILED,
    "canceled": STATE_CANCELED,
    "cancelled": STATE_CANCELED,
    "rejected": STATE_FAILED,
    "input-required": STATE_WORKING,
    "auth-required": STATE_FAILED,
    "unknown": STATE_WORKING,
}

#: 测试注入口：httpx.MockTransport 假扮远端 A2A 平台（测试全离线）
_transport_override: httpx.BaseTransport | None = None


def set_test_transport(transport: httpx.BaseTransport | None) -> None:
    global _transport_override
    _transport_override = transport


def _now() -> datetime:
    return datetime.now(UTC)


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=settings.a2a_http_timeout_seconds,
        follow_redirects=False,
        transport=_transport_override,
    )


# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------


def build_agent_card(base_url: str, version: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "name": "科技小院多智能体平台",
        "description": "面向科技小院的农业知识与台账协作平台，对外开放只读概况与知识检索。",
        "url": f"{base}{RPC_PATH}",
        "preferredTransport": "JSONRPC",
        "version": version,
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [dict(skill) for skill in SKILLS.values()],
    }


def validate_remote_card(card: Any) -> dict[str, Any]:
    if not isinstance(card, dict):
        raise AppError("A2A_CARD_INVALID", "协作方返回的 Agent Card 不是 JSON 对象。", 502)
    name = card.get("name")
    skills = card.get("skills")
    if not isinstance(name, str) or not name.strip():
        raise AppError("A2A_CARD_INVALID", "Agent Card 缺少 name。", 502)
    if not isinstance(skills, list):
        raise AppError("A2A_CARD_INVALID", "Agent Card 缺少 skills 列表。", 502)
    for item in skills:
        if not isinstance(item, dict) or not str(item.get("id") or "").strip():
            raise AppError("A2A_CARD_INVALID", "Agent Card 的 skill 缺少 id。", 502)
    url = card.get("url")
    if url is not None and not isinstance(url, str):
        raise AppError("A2A_CARD_INVALID", "Agent Card 的 url 必须是字符串。", 502)
    if isinstance(url, str) and url and urlparse(url).scheme not in ("http", "https"):
        raise AppError("A2A_CARD_INVALID", "Agent Card 的 url 必须是 http(s) 地址。", 502)
    return card


def card_summary(card: dict[str, Any] | None) -> dict[str, Any] | None:
    if not card:
        return None
    skills = [s for s in (card.get("skills") or []) if isinstance(s, dict)]
    return {
        "name": card.get("name"),
        "description": str(card.get("description") or "")[:300],
        "url": card.get("url"),
        "version": card.get("version"),
        "skill_count": len(skills),
        "skills": [
            {"id": str(s.get("id")), "name": str(s.get("name") or s.get("id"))} for s in skills[:20]
        ],
    }


# ---------------------------------------------------------------------------
# 入站令牌
# ---------------------------------------------------------------------------


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_inbound_token() -> tuple[str, str]:
    """返回 ``(完整令牌, 展示前缀)``；完整令牌只在生成时返回一次。"""
    raw = "a2a_" + secrets.token_urlsafe(32)
    return raw, raw[:12]


def get_endpoint(db: Session, yard_id: str) -> A2aEndpoint | None:
    return db.scalar(select(A2aEndpoint).where(A2aEndpoint.yard_id == yard_id))


def ensure_endpoint(db: Session, yard_id: str, user_id: str) -> tuple[A2aEndpoint, bool]:
    endpoint = get_endpoint(db, yard_id)
    if endpoint is not None:
        return endpoint, False
    raw, prefix = new_inbound_token()
    endpoint = A2aEndpoint(
        yard_id=yard_id,
        token_hash=hash_token(raw),
        token_prefix=prefix,
        enabled=True,
        created_by=user_id,
    )
    db.add(endpoint)
    db.flush()
    return endpoint, True


def rotate_token(db: Session, endpoint: A2aEndpoint) -> str:
    raw, prefix = new_inbound_token()
    endpoint.token_hash = hash_token(raw)
    endpoint.token_prefix = prefix
    endpoint.last_used_at = None
    db.flush()
    return raw


def resolve_endpoint(db: Session, raw_token: str | None) -> A2aEndpoint:
    if not settings.a2a_enabled:
        raise AppError("A2A_DISABLED", "本平台未开启 A2A 协作。", http_status=404)
    if not raw_token:
        raise AppError("A2A_UNAUTHORIZED", "缺少协作令牌。", http_status=401)
    endpoint = db.scalar(select(A2aEndpoint).where(A2aEndpoint.token_hash == hash_token(raw_token)))
    if endpoint is None or not endpoint.enabled:
        raise AppError("A2A_UNAUTHORIZED", "协作令牌无效或已停用。", http_status=401)
    endpoint.last_used_at = _now()
    return endpoint


# ---------------------------------------------------------------------------
# 入站任务（JSON-RPC）
# ---------------------------------------------------------------------------


def rpc_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def rpc_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


def _message_payload(params: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    message = params.get("message")
    if not isinstance(message, dict):
        raise AppError("INVALID_REQUEST", "params.message 必须是对象。", http_status=400)
    message_id = str(message.get("messageId") or params.get("id") or uuid.uuid4())
    parts = message.get("parts")
    texts: list[str] = []
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            if part.get("kind") in (None, "text") and part.get("text"):
                texts.append(str(part["text"]))
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    return message_id, "\n".join(texts)[:MAX_INPUT_TEXT], metadata


def handle_rpc(
    db: Session,
    endpoint: A2aEndpoint,
    payload: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    """处理一条 JSON-RPC 请求；协议级错误用 JSON-RPC error，业务失败落任务状态。"""
    req_id = payload.get("id")
    if str(payload.get("jsonrpc") or "") != "2.0":
        return rpc_error(req_id, -32600, "仅支持 JSON-RPC 2.0。")
    method = str(payload.get("method") or "")
    params = payload.get("params")
    if not isinstance(params, dict):
        params = {}
    if method in ("message/send", "tasks/send"):
        return _handle_send(db, endpoint, req_id, params, request_id=request_id)
    if method == "tasks/get":
        task_id = str(params.get("id") or "")
        task = db.scalar(
            select(A2aTask).where(A2aTask.yard_id == endpoint.yard_id, A2aTask.id == task_id)
        )
        if task is None:
            return rpc_error(req_id, -32001, "任务不存在。")
        return rpc_result(req_id, task_out(task))
    if method == "tasks/cancel":
        task_id = str(params.get("id") or "")
        task = db.scalar(
            select(A2aTask).where(A2aTask.yard_id == endpoint.yard_id, A2aTask.id == task_id)
        )
        if task is None:
            return rpc_error(req_id, -32001, "任务不存在。")
        if task.status in TERMINAL_STATES:
            return rpc_error(req_id, -32002, "任务已结束，无法取消。")
        task.status = STATE_CANCELED
        task.finished_at = _now()
        db.flush()
        db.commit()
        return rpc_result(req_id, task_out(task))
    return rpc_error(req_id, -32601, f"不支持的方法：{method}。")


def _handle_send(
    db: Session,
    endpoint: A2aEndpoint,
    req_id: Any,
    params: dict[str, Any],
    *,
    request_id: str,
) -> dict[str, Any]:
    try:
        message_id, text, metadata = _message_payload(params)
    except AppError as e:
        return rpc_error(req_id, -32602, e.message)
    skill = str(metadata.get("skill") or metadata.get("skillId") or "").strip()
    if skill not in SKILLS:
        return rpc_error(
            req_id,
            -32602,
            f"未知技能 {skill or '(空)'}；可用技能：{', '.join(sorted(SKILLS))}。",
        )
    op_key = f"in-{message_id}"[:64]
    existing = db.scalar(
        select(A2aTask).where(A2aTask.yard_id == endpoint.yard_id, A2aTask.op_key == op_key)
    )
    if existing is not None:
        return rpc_result(req_id, task_out(existing))

    context_id = str(params.get("contextId") or message_id or uuid.uuid4())[:64]
    task = A2aTask(
        yard_id=endpoint.yard_id,
        direction=DIRECTION_INBOUND,
        skill_id=skill,
        status=STATE_WORKING,
        request_json={"messageId": message_id, "skill": skill, "text": text},
        context_id=context_id,
        op_key=op_key,
    )
    db.add(task)
    db.flush()
    try:
        result_text = run_skill(db, endpoint.yard_id, skill, text, request_id=request_id)
        task.status = STATE_COMPLETED
        task.result_json = {"text": result_text}
    except AppError as e:
        task.status = STATE_FAILED
        task.error_code = e.code
        task.error_message = e.message[:300]
        task.result_json = None
    task.remote_task_id = task.id
    task.finished_at = _now()
    db.flush()
    log_audit(
        db,
        yard_id=endpoint.yard_id,
        actor_id=None,
        action="a2a_task_received",
        resource_type="a2a_task",
        resource_id=task.id,
        result="success" if task.status == STATE_COMPLETED else "failed",
        request_id=request_id,
        metadata={"skill": skill, "message_id": message_id, "status": task.status},
    )
    db.commit()
    return rpc_result(req_id, task_out(task))


def run_skill(db: Session, yard_id: str, skill: str, text: str, *, request_id: str) -> str:
    if skill == SKILL_YARD_SUMMARY:
        return _yard_summary(db, yard_id)
    if skill == SKILL_KNOWLEDGE_SEARCH:
        return _knowledge_search(db, yard_id, text, request_id=request_id)
    raise AppError("A2A_SKILL_UNKNOWN", f"未知技能：{skill}。", http_status=400)


def _yard_summary(db: Session, yard_id: str) -> str:
    plots = db.scalar(select(func.count()).select_from(FarmPlot).where(FarmPlot.yard_id == yard_id))
    cycles = db.scalar(
        select(func.count())
        .select_from(FarmCycle)
        .where(FarmCycle.yard_id == yard_id, FarmCycle.status == "growing")
    )
    records = db.scalar(
        select(func.count()).select_from(FarmRecord).where(FarmRecord.yard_id == yard_id)
    )
    open_orders = db.scalar(
        select(func.count())
        .select_from(WorkOrder)
        .where(WorkOrder.yard_id == yard_id, WorkOrder.status.notin_(("closed", "cancelled")))
    )
    return (
        f"本小院当前有地块 {int(plots or 0)} 块、在种周期 {int(cycles or 0)} 个、"
        f"农事记录 {int(records or 0)} 条、未完结工单 {int(open_orders or 0)} 个。"
    )


def _knowledge_search(db: Session, yard_id: str, text: str, *, request_id: str) -> str:
    from app.modules.knowledge import service as knowledge_service

    query = (text or "").strip()
    if not query:
        raise AppError("INVALID_REQUEST", "知识检索需要输入检索文本。", http_status=400)
    ctx = YardContext(yard_id=yard_id, user_id="a2a-peer", roles=["viewer"], request_id=request_id)
    hits = knowledge_service.search(db, ctx, query, top_k=5)
    if not hits:
        return "本小院知识库中没有找到相关片段。"
    lines = []
    for index, hit in enumerate(hits, start=1):
        lines.append(f"[{index}] {hit.title}（{hit.source}）\n{hit.quote[:600]}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------------------
# 任务出参
# ---------------------------------------------------------------------------


def task_out(task: A2aTask) -> dict[str, Any]:
    text = ""
    if isinstance(task.result_json, dict):
        text = str(task.result_json.get("text") or "")
    status: dict[str, Any] = {
        "state": task.status,
        "timestamp": (task.finished_at or task.updated_at or task.created_at).isoformat(),
    }
    if task.status == STATE_FAILED and task.error_message:
        status["message"] = {
            "role": "agent",
            "parts": [{"kind": "text", "text": task.error_message}],
        }
    return {
        "kind": "task",
        "id": task.id,
        "contextId": task.context_id or task.id,
        "status": status,
        "artifacts": (
            [
                {
                    "artifactId": f"{task.id}-result",
                    "name": "result",
                    "parts": [{"kind": "text", "text": text}],
                }
            ]
            if text
            else []
        ),
        "metadata": {
            "skill": task.skill_id,
            "direction": task.direction,
            "errorCode": task.error_code,
        },
    }


def task_to_admin(task: A2aTask) -> dict[str, Any]:
    text = ""
    if isinstance(task.result_json, dict):
        text = str(task.result_json.get("text") or "")
    return {
        "id": task.id,
        "direction": task.direction,
        "peer_id": task.peer_id,
        "skill_id": task.skill_id,
        "status": task.status,
        "error_code": task.error_code,
        "error_message": task.error_message,
        "remote_task_id": task.remote_task_id,
        "result_text": text[:4000],
        "created_by": task.created_by,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


# ---------------------------------------------------------------------------
# 协作方（出站）
# ---------------------------------------------------------------------------


def normalize_base_url(url: str) -> str:
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise AppError("INVALID_REQUEST", "协作方地址必须是 http(s) 绝对地址。", http_status=422)
    if parsed.query or parsed.fragment:
        raise AppError("INVALID_REQUEST", "协作方地址不能带查询参数或锚点。", http_status=422)
    check_egress_url(str(url), context="a2a_peer")
    return str(url).strip().rstrip("/")


def get_peer_or_404(db: Session, yard_id: str, peer_id: str) -> A2aPeer:
    peer = db.scalar(select(A2aPeer).where(A2aPeer.id == peer_id, A2aPeer.yard_id == yard_id))
    if peer is None:
        raise AppError("A2A_PEER_NOT_FOUND", "协作方不存在。", http_status=404)
    return peer


def peer_to_out(peer: A2aPeer) -> dict[str, Any]:
    return {
        "id": peer.id,
        "name": peer.name,
        "base_url": peer.base_url,
        "enabled": peer.enabled,
        "token_configured": bool(peer.token_enc),
        "card": card_summary(peer.card_json),
        "card_fetched_at": peer.card_fetched_at.isoformat() if peer.card_fetched_at else None,
        "last_error": peer.last_error,
        "created_at": peer.created_at.isoformat() if peer.created_at else None,
    }


def list_peers(db: Session, yard_id: str) -> list[A2aPeer]:
    return list(
        db.scalars(
            select(A2aPeer).where(A2aPeer.yard_id == yard_id).order_by(A2aPeer.created_at.desc())
        )
    )


def _decrypt_peer_token(peer: A2aPeer) -> str | None:
    if not peer.token_enc:
        return None
    from app.modules.llm.supplier_crypto import decrypt_api_key

    return decrypt_api_key(peer.token_enc)


def encrypt_peer_token(token: str | None) -> str | None:
    if not token:
        return None
    from app.modules.llm.supplier_crypto import encrypt_api_key

    return encrypt_api_key(token)


def _read_json(resp: httpx.Response, *, endpoint: str) -> Any:
    if len(resp.content) > MAX_RESPONSE_BYTES:
        raise AppError("A2A_REMOTE_ERROR", f"协作方 {endpoint} 响应过大。", http_status=502)
    try:
        return json.loads(resp.content or b"null")
    except ValueError:
        raise AppError(
            "A2A_REMOTE_ERROR", f"协作方 {endpoint} 返回的不是 JSON。", http_status=502
        ) from None


def _auth_headers(token: str | None) -> dict[str, str]:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def fetch_peer_card(peer: A2aPeer) -> tuple[dict[str, Any], str]:
    """按 well-known 约定拉取 Agent Card；返回 ``(card, 实际地址)``。"""
    errors: list[str] = []
    for path in CARD_PATHS:
        url = f"{peer.base_url}{path}"
        check_egress_url(url, context="a2a_peer")
        try:
            with _client() as client:
                resp = client.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise AppError(
                "A2A_REMOTE_ERROR", f"协作方不可达：{e.__class__.__name__}", http_status=502
            ) from None
        if resp.status_code == 404:
            errors.append(path)
            continue
        if resp.status_code >= 400:
            raise AppError(
                "A2A_REMOTE_ERROR",
                f"协作方返回 HTTP {resp.status_code}。",
                http_status=502,
            )
        if len(resp.content) > MAX_CARD_BYTES:
            raise AppError("A2A_CARD_INVALID", "Agent Card 超过 64KB。", http_status=502)
        card = validate_remote_card(_read_json(resp, endpoint=path))
        return card, url
    raise AppError(
        "A2A_CARD_INVALID",
        f"协作方未提供 Agent Card（已尝试 {'、'.join(errors)}）。",
        http_status=502,
    )


def probe_peer(db: Session, peer: A2aPeer, *, request_id: str) -> dict[str, Any]:
    card, url = fetch_peer_card(peer)
    peer.card_json = card
    peer.card_fetched_at = _now()
    peer.last_error = None
    db.flush()
    log_audit(
        db,
        yard_id=peer.yard_id,
        actor_id=None,
        action="a2a_peer_probed",
        resource_type="a2a_peer",
        resource_id=peer.id,
        result="success",
        request_id=request_id,
        metadata={"name": peer.name, "card_url": url},
    )
    db.commit()
    return {"ok": True, "card": card_summary(card), "card_url": url}


def _post_rpc(url: str, token: str | None, body: dict[str, Any], *, context: str) -> dict[str, Any]:
    check_egress_url(url, context=context)
    try:
        with _client() as client:
            resp = client.post(url, json=body, headers=_auth_headers(token))
    except httpx.HTTPError as e:
        raise AppError(
            "A2A_REMOTE_ERROR", f"协作方不可达：{e.__class__.__name__}", http_status=502
        ) from None
    if resp.status_code in (401, 403):
        raise AppError("A2A_REMOTE_UNAUTHORIZED", "协作方拒绝了本平台的令牌。", http_status=502)
    if resp.status_code >= 400:
        raise AppError("A2A_REMOTE_ERROR", f"协作方返回 HTTP {resp.status_code}。", http_status=502)
    data = _read_json(resp, endpoint="/rpc")
    if not isinstance(data, dict):
        raise AppError("A2A_REMOTE_ERROR", "协作方返回的 JSON-RPC 响应不是对象。", http_status=502)
    error = data.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "远端返回 JSON-RPC 错误。")[:300]
        raise AppError("A2A_REMOTE_ERROR", message, http_status=502)
    return data


def _remote_text(result: dict[str, Any]) -> str:
    chunks: list[str] = []
    for artifact in result.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        for part in artifact.get("parts") or []:
            if isinstance(part, dict) and part.get("kind") in (None, "text") and part.get("text"):
                chunks.append(str(part["text"]))
    return "\n".join(chunks)[:MAX_INPUT_TEXT]


def _apply_remote_task(task: A2aTask, result: dict[str, Any]) -> None:
    status = result.get("status")
    state = str(status.get("state") if isinstance(status, dict) else "") or "submitted"
    task.status = _REMOTE_STATE_MAP.get(state, STATE_WORKING)
    task.remote_task_id = str(result.get("id") or "")[:64] or task.remote_task_id
    task.context_id = str(result.get("contextId") or "")[:64] or task.context_id
    text = _remote_text(result)
    if text:
        task.result_json = {"text": text}


def send_task(
    db: Session,
    peer: A2aPeer,
    *,
    skill_id: str,
    input_text: str,
    user_id: str,
    request_id: str,
) -> A2aTask:
    if not settings.a2a_enabled:
        raise AppError("A2A_DISABLED", "本平台未开启 A2A 协作。", http_status=400)
    if not peer.enabled:
        raise AppError("A2A_PEER_DISABLED", "协作方已停用。", http_status=409)
    if skill_id not in SKILLS:
        raise AppError(
            "A2A_SKILL_UNKNOWN",
            f"未知技能：{skill_id}；可用技能：{', '.join(sorted(SKILLS))}。",
            http_status=422,
        )
    if not peer.card_json:
        probe_peer(db, peer, request_id=request_id)
    card = peer.card_json or {}
    url = str(card.get("url") or f"{peer.base_url}{RPC_PATH}")
    try:
        check_egress_url(url, context="a2a_peer")
    except AppError:
        url = f"{peer.base_url}{RPC_PATH}"
        check_egress_url(url, context="a2a_peer")

    token = _decrypt_peer_token(peer)
    message_id = str(uuid.uuid4())
    task = A2aTask(
        yard_id=peer.yard_id,
        direction=DIRECTION_OUTBOUND,
        peer_id=peer.id,
        skill_id=skill_id,
        status=STATE_SUBMITTED,
        request_json={"skill": skill_id, "text": input_text, "messageId": message_id},
        op_key=f"out-{uuid.uuid4().hex}"[:64],
        created_by=user_id,
    )
    db.add(task)
    db.flush()
    body = {
        "jsonrpc": "2.0",
        "id": message_id,
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": input_text}],
                "messageId": message_id,
                "metadata": {"skill": skill_id},
            },
            "configuration": {"blocking": True},
        },
    }
    try:
        data = _post_rpc(url, token, body, context=f"a2a_peer:{peer.id[:8]}")
        result = data.get("result")
        if not isinstance(result, dict):
            raise AppError("A2A_REMOTE_ERROR", "协作方没有返回任务对象。", http_status=502)
        _apply_remote_task(task, result)
        for _ in range(2):
            if task.status in TERMINAL_STATES:
                break
            time.sleep(0.2)
            poll = _post_rpc(
                url,
                token,
                {
                    "jsonrpc": "2.0",
                    "id": str(uuid.uuid4()),
                    "method": "tasks/get",
                    "params": {"id": task.remote_task_id},
                },
                context=f"a2a_peer:{peer.id[:8]}:poll",
            )
            polled = poll.get("result")
            if isinstance(polled, dict):
                _apply_remote_task(task, polled)
    except AppError as e:
        task.status = STATE_FAILED
        task.error_code = e.code
        task.error_message = e.message[:300]
        peer.last_error = e.message[:300]
    if task.status not in TERMINAL_STATES:
        task.status = STATE_FAILED
        task.error_code = task.error_code or "A2A_TASK_TIMEOUT"
        task.error_message = task.error_message or "协作方在等待时间内未完成任务。"
    task.finished_at = _now()
    db.flush()
    log_audit(
        db,
        yard_id=peer.yard_id,
        actor_id=user_id,
        action="a2a_task_sent",
        resource_type="a2a_task",
        resource_id=task.id,
        result="success" if task.status == STATE_COMPLETED else "failed",
        request_id=request_id,
        metadata={
            "peer": peer.name,
            "skill": skill_id,
            "status": task.status,
            "error": task.error_code,
        },
    )
    db.commit()
    return task


# ---------------------------------------------------------------------------
# 任务查询
# ---------------------------------------------------------------------------


def list_tasks(
    db: Session,
    yard_id: str,
    *,
    direction: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[A2aTask]:
    query = select(A2aTask).where(A2aTask.yard_id == yard_id)
    if direction in (DIRECTION_INBOUND, DIRECTION_OUTBOUND):
        query = query.where(A2aTask.direction == direction)
    if status:
        query = query.where(A2aTask.status == status)
    query = query.order_by(A2aTask.created_at.desc()).limit(max(1, min(limit, 200)))
    return list(db.scalars(query))


def get_task_or_404(db: Session, yard_id: str, task_id: str) -> A2aTask:
    task = db.scalar(select(A2aTask).where(A2aTask.id == task_id, A2aTask.yard_id == yard_id))
    if task is None:
        raise AppError("A2A_TASK_NOT_FOUND", "任务不存在。", http_status=404)
    return task
