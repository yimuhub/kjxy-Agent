"""多集群节点服务：URL 规范化、增删改查与只读版本探测。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.net_guard import check_egress_url
from app.modules.cluster.models import (
    STATUS_DEGRADED,
    STATUS_NEVER,
    STATUS_OFFLINE,
    STATUS_ONLINE,
    ClusterNode,
)

MAX_NODES = 50
PROBE_TIMEOUT_SECONDS = 3.0
#: 批量探测并发数：50 个节点串行最坏 150s，会占住请求线程；8 路并发压到 ~20s
PROBE_WORKERS = 8
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
    # 不按 MAX_NODES 裁剪：并发建节点把行数顶到上限之上时，被裁掉的节点会从
    # 列表和 probe_all 里“消失”，但数据仍在库里——宁可多展示，不能假装不存在。
    return list(db.scalars(select(ClusterNode).order_by(ClusterNode.created_at.desc())))


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
        # 并发首建的竞态兜底：两个请求可能同时通过上面的 count 预检。落库后复核，
        # 超限的一方回滚自己的插入（后到者失败），不让第 51 个悄悄生效。
        total = db.scalar(select(func.count()).select_from(ClusterNode)) or 0
        if total > MAX_NODES:
            db.rollback()
            raise AppError(
                "CLUSTER_NODE_LIMIT",
                f"集群节点最多 {MAX_NODES} 个。",
                http_status=400,
            )
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
    """GET /api/v1/meta（公开只读）；禁止跟随重定向，避免被带到内网其他服务。

    出网前过 ``check_egress_url``，与全项目「服务端出网必过 net_guard」契约一致：
    链路本地/云元数据地址任何开关都阻断，环回默认阻断；内网私有段沿用 ADR-0003
    默认放行（多实例内网部署是正当形态），纯公网部署由 ``enforce_model_egress_guard``
    总闸一并收紧。校验按域名解析后的每个 IP 判定，挡的是首跳直达内网/元数据。
    """
    meta_url = f"{base_url.rstrip('/')}/api/v1/meta"
    check_egress_url(meta_url, context="cluster_probe")
    started = perf_counter()
    with httpx.Client(
        timeout=httpx.Timeout(PROBE_TIMEOUT_SECONDS, connect=PROBE_TIMEOUT_SECONDS),
        follow_redirects=False,
    ) as client:
        response = client.get(meta_url)
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


def _apply_probe(
    node: ClusterNode,
    outcome: tuple[dict[str, Any], int] | Exception,
    *,
    expected_version: str,
    expected_head: str,
    checked: datetime,
) -> None:
    if isinstance(outcome, Exception):
        node.status = STATUS_OFFLINE
        node.latency_ms = 0
        node.last_checked_at = checked
        node.last_error = _safe_error(outcome)
        # 离线时清掉上次的版本/迁移号：留着旧值会让界面把过期版本当成现况。
        # 上次成功时间（last_ok_at）保留，用于「最后一次正常是什么时候」。
        node.version = ""
        node.migration_head = ""
        return
    data, latency = outcome
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
        outcome: tuple[dict[str, Any], int] | Exception = _fetch_meta(node.base_url)
    except Exception as exc:  # 网络/解析/超时/SSRF 拦截都归为离线，原因只留短文本
        outcome = exc
    _apply_probe(
        node,
        outcome,
        expected_version=expected_version,
        expected_head=expected_head,
        checked=checked,
    )
    db.flush()
    return node


def _fetch_all(base_urls: list[str]) -> list[tuple[dict[str, Any], int] | Exception]:
    """并发抓取全部节点元数据；网络失败按节点收集，绝不抛出。"""
    if not base_urls:
        return []
    if len(base_urls) == 1:
        try:
            return [_fetch_meta(base_urls[0])]
        except Exception as exc:  # noqa: BLE001 - 单节点也按离线结果返回
            return [exc]
    with ThreadPoolExecutor(max_workers=min(PROBE_WORKERS, len(base_urls))) as pool:
        futures = [pool.submit(_fetch_meta, url) for url in base_urls]
        results: list[tuple[dict[str, Any], int] | Exception] = []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - 逐节点归为离线
                results.append(exc)
        return results


def probe_all(
    db: Session,
    *,
    expected_version: str,
    expected_head: str,
) -> tuple[list[ClusterNode], dict[str, int]]:
    """批量探测：先并发跑完所有 HTTP 请求，再串行落库。

    网络阶段不碰写事务，避免最坏 50×3s 的 I/O 占住请求线程和 SQLite 写锁。
    """
    nodes = list_nodes(db)
    checked = _now()
    outcomes = _fetch_all([node.base_url for node in nodes])
    for node, outcome in zip(nodes, outcomes, strict=True):
        _apply_probe(
            node,
            outcome,
            expected_version=expected_version,
            expected_head=expected_head,
            checked=checked,
        )
    db.flush()
    summary = {
        "total": len(nodes),
        "online": sum(1 for row in nodes if row.status == STATUS_ONLINE),
        "degraded": sum(1 for row in nodes if row.status == STATUS_DEGRADED),
        "offline": sum(1 for row in nodes if row.status == STATUS_OFFLINE),
    }
    return nodes, summary


def _is_stale(row: ClusterNode, now: datetime) -> bool:
    """探测结论是否已超出可观测窗口（STALE_SECONDS）。

    从未探测不算“过期”——那是“未探测”状态，界面另有标签；但库里有在线/降级
    结论却缺时间戳（历史数据或手工导入）按过期处理，避免把陈旧版本当现况。
    """
    if row.last_checked_at is None:
        return row.status in (STATUS_ONLINE, STATUS_DEGRADED)
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
