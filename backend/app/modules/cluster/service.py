"""多集群节点服务：URL 规范化、增删改查与只读版本探测。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.cluster.models import (
    STATUS_DEGRADED,
    STATUS_NEVER,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    ClusterNode,
)

MAX_NODES = 50
PROBE_TIMEOUT_SECONDS = 3.0
STALE_SECONDS = 600

STATUS_LABELS = {
    STATUS_NEVER: "未探测",
    STATUS_ONLINE: "在线",
    STATUS_DEGRADED: "版本/迁移不一致",
    STATUS_OFFLINE: "离线",
}


def _now() -> datetime:
    return datetime.now(UTC)


def normalize_base_url(raw: str) -> str:
    """只接受无凭据的 http(s) 根地址；去掉尾部斜杠与空路径。"""
    text_value = (raw or "").strip()
    if not text_value:
        raise AppError("INVALID_REQUEST", "节点地址不能为空。", http_status=400)
    if len(text_value) > 300 or any(ch.isspace() or ord(ch) < 32 for ch in text_value):
        raise AppError("INVALID_REQUEST", "节点地址过长或含非法字符。", http_status=400)
    try:
        parts = urlsplit(text_value)
    except ValueError as exc:
        raise AppError("INVALID_REQUEST", "节点地址格式不正确。", http_status=400) from exc
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise AppError("INVALID_REQUEST", "节点地址必须是 http/https 完整地址。", http_status=400)
    if parts.username or parts.password:
        raise AppError("INVALID_REQUEST", "节点地址不能包含用户名或口令。", http_status=400)
    if parts.query or parts.fragment:
        raise AppError("INVALID_REQUEST", "节点地址不能带查询串或片段。", http_status=400)
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def normalize_name(raw: str) -> str:
    name = (raw or "").strip()
    if not name or len(name) > 60:
        raise AppError("INVALID_REQUEST", "节点名称需为 1–60 个字符。", http_status=400)
    return name


def normalize_note(raw: str | None) -> str:
    return (raw or "").strip()[:200]


def list_nodes(db: Session) -> list[ClusterNode]:
    return list(
        db.scalars(select(ClusterNode).order_by(ClusterNode.created_at.desc()).limit(MAX_NODES))
    )


def get_node(db: Session, node_id: str) -> ClusterNode:
    row = db.get(ClusterNode, node_id)
    if row is None:
        raise AppError("CLUSTER_NODE_NOT_FOUND", "集群节点不存在。", http_status=404)
    return row


def create_node(
    db: Session,
    *,
    name: str,
    base_url: str,
    note: str | None,
    created_by: str | None,
) -> ClusterNode:
    count = db.scalar(select(func.count()).select_from(ClusterNode)) or 0
    if count >= MAX_NODES:
        raise AppError(
            "CLUSTER_NODE_LIMIT",
            f"集群节点最多 {MAX_NODES} 个。",
            http_status=400,
        )
    row = ClusterNode(
        name=normalize_name(name),
        base_url=normalize_base_url(base_url),
        note=normalize_note(note),
        status=STATUS_NEVER,
        created_by=created_by,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "DUPLICATE_CLUSTER_NODE",
            "节点名称或地址已存在。",
            http_status=409,
        ) from exc
    return row


def update_node(
    db: Session,
    node_id: str,
    *,
    name: str | None = None,
    base_url: str | None = None,
    note: str | None = None,
) -> ClusterNode:
    row = get_node(db, node_id)
    if name is not None:
        row.name = normalize_name(name)
    if base_url is not None:
        row.base_url = normalize_base_url(base_url)
        # 地址变了，旧探测结论不再可信
        row.status = STATUS_NEVER
        row.version = ""
        row.migration_head = ""
        row.latency_ms = 0
        row.last_checked_at = None
        row.last_ok_at = None
        row.last_error = ""
    if note is not None:
        row.note = normalize_note(note)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise AppError(
            "DUPLICATE_CLUSTER_NODE",
            "节点名称或地址已存在。",
            http_status=409,
        ) from exc
    return row


def delete_node(db: Session, node_id: str) -> None:
    row = get_node(db, node_id)
    db.delete(row)
    db.flush()


def migration_head(db: Session) -> str:
    try:
        value = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError:
        db.rollback()
        return ""
    return str(value or "")


def _fetch_meta(base_url: str) -> tuple[dict[str, Any], int]:
    """GET /api/v1/meta（公开只读）；禁止跟随重定向，避免被带到内网其他服务。"""
    started = perf_counter()
    with httpx.Client(
        timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS, connect=PROBE_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as client:
        response = client.get(f"{base_url.rstrip('/')}/api/v1/meta")
    latency = round((perf_counter() - started) * 1000)
    if response.status_code != 200:
        raise RuntimeError(f"HTTP {response.status_code}")
    payload = response.json()
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("meta 响应格式不正确")
    return data, latency


def _safe_error(exc: Exception) -> str:
    message = f"{type(exc).__name__}: {exc}".strip()
    return message[:280]


def probe_node(
    db: Session,
    node: ClusterNode,
    *,
    expected_version: str,
    expected_head: str,
) -> ClusterNode:
    """探测一次并就地更新结论；不 commit，由调用方决定事务边界。"""
    checked = _now()
    try:
        data, latency = _fetch_meta(node.base_url)
        version = str(data.get("version") or "")[:40]
        head = str(data.get("migration_head") or "")[:80]
        mismatches: list[str] = []
        if version != expected_version:
            mismatches.append(f"版本 {version or '未知'} ≠ 本机 {expected_version}")
        if head != expected_head:
            mismatches.append(f"迁移 {head or '未知'} ≠ 本机 {expected_head or '未知'}")
        node.status = STATUS_DEGRADED if mismatches else STATUS_ONLINE
        node.version = version
        node.migration_head = head
        node.latency_ms = latency
        node.last_checked_at = checked
        node.last_ok_at = checked
        node.last_error = "；".join(mismatches)[:300]
    except Exception as exc:  # 网络/解析/超时都归为离线，原因只留短文本
        node.status = STATUS_OFFLINE
        node.latency_ms = 0
        node.last_checked_at = checked
        node.last_error = _safe_error(exc)
    db.flush()
    return node


def probe_all(
    db: Session,
    *,
    expected_version: str,
    expected_head: str,
) -> tuple[list[ClusterNode], dict[str, int]]:
    nodes = list_nodes(db)
    for node in nodes:
        probe_node(
            db,
            node,
            expected_version=expected_version,
            expected_head=expected_head,
        )
    summary = {
        "total": len(nodes),
        "online": sum(1 for row in nodes if row.status == STATUS_ONLINE),
        "degraded": sum(1 for row in nodes if row.status == STATUS_DEGRADED),
        "offline": sum(1 for row in nodes if row.status == STATUS_OFFLINE),
    }
    return nodes, summary


def _is_stale(row: ClusterNode, now: datetime) -> bool:
    if row.last_checked_at is None:
        return False
    checked = row.last_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    return now - checked > timedelta(seconds=STALE_SECONDS)


def node_out(row: ClusterNode, *, now: datetime | None = None) -> dict[str, Any]:
    moment = now or _now()
    return {
        "id": row.id,
        "name": row.name,
        "base_url": row.base_url,
        "note": row.note,
        "status": row.status,
        "status_label": STATUS_LABELS.get(row.status, row.status),
        "stale": _is_stale(row, moment),
        "version": row.version,
        "migration_head": row.migration_head,
        "latency_ms": row.latency_ms,
        "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
        "last_ok_at": row.last_ok_at.isoformat() if row.last_ok_at else None,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
