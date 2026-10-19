"""devices 服务层：令牌、读数归一入库、限频、告警比对与查询。

ingest（设备推送）与 connector（平台拉取）共用本模块的归一化与告警逻辑，
保证两条通道口径完全一致。任何通道都不写设备控制指令（只读红线）。
"""

import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.notify import send_alert
from app.modules.audit.service import log_audit
from app.modules.devices.metrics import METRICS, OTHER_METRIC, normalize_code
from app.modules.devices.models import Device, DeviceAlert, DeviceAlertRule, Reading

log = get_logger(__name__)

#: 同设备滑动窗口限频：窗口秒数 / 窗口内最多读数点数（超限 429）
INGEST_WINDOW_SECONDS = 60
INGEST_MAX_POINTS_PER_WINDOW = 100
#: 同 (rule, device) 告警去重窗口（分钟）
ALERT_DEDUPE_MINUTES = 30

_OP_LABEL = {"lt": "低于", "gt": "高于"}


# ---------------------------------------------------------------------------
# 令牌
# ---------------------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_device_token() -> tuple[str, str]:
    """生成一次性明文 token 与其 sha256；明文只在创建/轮换响应里出现一次。"""
    plain = secrets.token_urlsafe(32)
    return plain, hash_token(plain)


def token_matches(device: Device, token: str | None) -> bool:
    if not device.token_hash or not token:
        return False
    return hmac.compare_digest(device.token_hash, hash_token(token))


# ---------------------------------------------------------------------------
# 限频（进程内滑动窗口；多实例部署需换 Redis，与 rate_limit.py 同一诚实边界）
# ---------------------------------------------------------------------------


class _PointWindow:
    """按 key 统计窗口内读数点数：先 check 再 record（不足额则整请求拒绝）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, deque[tuple[float, int]]] = {}

    def _prune(self, key: str, now: float) -> deque[tuple[float, int]]:
        dq = self._hits.get(key)
        if dq is None:
            dq = deque()
            self._hits[key] = dq
        cutoff = now - INGEST_WINDOW_SECONDS
        while dq and dq[0][0] <= cutoff:
            dq.popleft()
        return dq

    def check(self, key: str, count: int) -> bool:
        now = time.monotonic()
        with self._lock:
            dq = self._prune(key, now)
            return sum(n for _, n in dq) + count <= INGEST_MAX_POINTS_PER_WINDOW

    def record(self, key: str, count: int) -> None:
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            self._hits[key].append((now, count))

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


_rate_window = _PointWindow()


# ---------------------------------------------------------------------------
# 通用查询
# ---------------------------------------------------------------------------


def get_device_or_404(db: Session, tenant_id: str, device_id: str) -> Device:
    """跨租户与不存在统一 404（不泄露其他租户资源是否存在）。"""
    device = db.get(Device, device_id)
    if device is None or device.tenant_id != tenant_id:
        raise AppError("NOT_FOUND", "设备不存在。", http_status=404)
    return device


def parse_reported_at(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise AppError("INVALID_REQUEST", "reported_at need ISO8601.", http_status=400) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def list_readings(
    db: Session,
    device: Device,
    *,
    metric: str | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 200,
) -> list[Reading]:
    stmt = select(Reading).where(Reading.device_id == device.id)
    if metric:
        stmt = stmt.where(Reading.metric == metric)
    if start:
        stmt = stmt.where(Reading.collected_at >= start)
    if end:
        stmt = stmt.where(Reading.collected_at <= end)
    stmt = stmt.order_by(Reading.collected_at.desc()).limit(min(limit, 1000))
    return list(db.scalars(stmt))


def latest_summary(db: Session, device: Device) -> list[dict[str, Any]]:
    """每指标最新一条读数（扫最近 500 条，够用且免方言窗口函数）。"""
    rows = list(
        db.scalars(
            select(Reading)
            .where(Reading.device_id == device.id)
            .order_by(Reading.collected_at.desc())
            .limit(500)
        )
    )
    seen: dict[str, dict[str, Any]] = {}
    for r in rows:
        if r.metric not in seen:
            info = METRICS.get(r.metric)
            seen[r.metric] = {
                "metric": r.metric,
                "metric_name": info.name if info else r.metric,
                "value": r.value,
                "text": r.text,
                "unit": r.unit,
                "collected_at": r.collected_at.isoformat(),
            }
    return list(seen.values())


# ---------------------------------------------------------------------------
# 入库核心（webhook / connector 共用）
# ---------------------------------------------------------------------------


def _coerce(raw: Any) -> tuple[float | None, str | None]:
    """数值优先；bool 不当数值；其余落 text。"""
    if isinstance(raw, bool):
        return None, str(raw)
    if isinstance(raw, int | float):
        return float(raw), None
    if isinstance(raw, str):
        try:
            return float(raw), None
        except ValueError:
            return None, raw
    if raw is None:
        return None, None
    return None, str(raw)


def _resolve_metric(
    vendor_field: str, field_map: dict[str, dict[str, Any]]
) -> tuple[str, str | None]:
    """厂商字段 → (metric code, 单位)。field_map 优先，其次字典精确/小写命中，否则 other。"""
    entry = field_map.get(vendor_field) if field_map else None
    if entry:
        metric = str(entry.get("metric") or OTHER_METRIC)
        if metric not in METRICS:
            metric = OTHER_METRIC
        unit = entry.get("unit")
        return metric, (str(unit) if unit else None)
    code = normalize_code(vendor_field)
    return code, (METRICS[code].unit or None if code in METRICS else None)


def build_reading(
    device: Device,
    vendor_field: str,
    raw_value: Any,
    *,
    field_map: dict[str, dict[str, Any]] | None,
    collected_at: datetime,
) -> Reading | None:
    """单字段 → Reading；无法同时得到数值与文本时返回 None（跳过）。"""
    metric, unit_override = _resolve_metric(vendor_field, field_map or {})
    value, text = _coerce(raw_value)
    if value is None and text is None:
        return None
    info = METRICS.get(metric)
    unit = unit_override or (info.unit if info and info.unit else None)
    raw_json: dict[str, Any] | None = None
    if metric == OTHER_METRIC or vendor_field != metric:
        # 字典外字段必留原文；映射改名也留原文（可追溯口径来源）
        raw_json = {"vendor_field": vendor_field, "raw_value": raw_value}
    return Reading(
        tenant_id=device.tenant_id,
        device_id=device.id,
        metric=metric,
        value=value,
        unit=unit,
        text=text,
        raw_json=raw_json,
        collected_at=collected_at,
    )


def evaluate_alerts(
    db: Session,
    readings: list[Reading],
    *,
    request_id: str = "",
    now: datetime | None = None,
) -> list[DeviceAlert]:
    """逐条读数比对启用规则；同 (rule, device) 30 分钟去重。

    仅数值读数参与比对（text 读数无比大小意义）。写库 + 审计 + send_alert。
    """
    value_rows = [r for r in readings if r.value is not None]
    metrics = {r.metric for r in value_rows}
    if not metrics:
        return []
    now = now or datetime.now(UTC)
    tenant_id = readings[0].tenant_id
    rules = list(
        db.scalars(
            select(DeviceAlertRule).where(
                DeviceAlertRule.tenant_id == tenant_id,
                DeviceAlertRule.enabled.is_(True),
                DeviceAlertRule.metric.in_(metrics),
            )
        )
    )
    if not rules:
        return []
    device_names: dict[str, str] = {}
    cutoff = now - timedelta(minutes=ALERT_DEDUPE_MINUTES)
    created: list[DeviceAlert] = []
    for reading in value_rows:
        value = reading.value
        if value is None:  # 防御（value_rows 已过滤，mypy 收窄）
            continue
        for rule in rules:
            if rule.device_id and rule.device_id != reading.device_id:
                continue
            beyond = value > rule.threshold
            hit = value < rule.threshold if rule.op == "lt" else beyond
            if not hit:
                continue
            deduped = db.scalars(
                select(DeviceAlert.id)
                .where(
                    DeviceAlert.rule_id == rule.id,
                    DeviceAlert.device_id == reading.device_id,
                    DeviceAlert.triggered_at >= cutoff,
                )
                .limit(1)
            ).first()
            if deduped:
                continue
            name = device_names.get(reading.device_id)
            if name is None:
                dev = db.get(Device, reading.device_id)
                name = dev.name if dev else reading.device_id
                device_names[reading.device_id] = name
            info = METRICS.get(reading.metric)
            metric_name = info.name if info else reading.metric
            unit = reading.unit or ""
            text = (
                f"【设备告警】{name}：{metric_name} 当前 {value}{unit}"
                f"{_OP_LABEL.get(rule.op, rule.op)}阈值 {rule.threshold}{unit}"
            )
            if rule.notify_note:
                text += f"。处置提示：{rule.notify_note}"
            alert = DeviceAlert(
                tenant_id=tenant_id,
                rule_id=rule.id,
                device_id=reading.device_id,
                metric=reading.metric,
                value=value,
                triggered_at=now,
                notified_text=text,
            )
            db.add(alert)
            created.append(alert)
            log_audit(
                db,
                tenant_id=tenant_id,
                actor_id=None,
                action="device_alert",
                resource_type="device",
                resource_id=reading.device_id,
                result="success",
                request_id=request_id,
                metadata={"rule_id": rule.id, "metric": reading.metric, "value": reading.value},
            )
            send_alert(title="device_alert", message=text)
    return created


def ingest_values(
    db: Session,
    device: Device,
    data: dict[str, Any],
    *,
    reported_at: str | None = None,
    field_map: dict[str, dict[str, Any]] | None = None,
    request_id: str = "",
    source: str = "webhook",
) -> dict[str, Any]:
    """把 {厂商字段: 原始值} 归一写入 readings 并触发告警比对。

    限频：整请求点数计入该设备 60s 滑动窗口，超限 429（整请求拒绝，不部分入库）。
    不 commit——由调用方（router/轮询器）决定事务边界。
    """
    if not device.enabled:
        raise AppError("DEVICE_DISABLED", "设备已停用。", http_status=403)
    if not data:
        raise AppError("INVALID_REQUEST", "data 为空。", http_status=400)
    if not _rate_window.check(device.id, len(data)):
        raise AppError(
            "RATE_LIMITED",
            f"上报过于频繁：每台设备 {INGEST_WINDOW_SECONDS} 秒内最多 "
            f"{INGEST_MAX_POINTS_PER_WINDOW} 个数据点。",
            http_status=429,
        )
    collected_at = parse_reported_at(reported_at)
    readings: list[Reading] = []
    other_count = 0
    for vendor_field, raw_value in data.items():
        reading = build_reading(
            device,
            str(vendor_field),
            raw_value,
            field_map=field_map,
            collected_at=collected_at,
        )
        if reading is None:
            continue
        if reading.metric == OTHER_METRIC:
            other_count += 1
        readings.append(reading)
        db.add(reading)
    _rate_window.record(device.id, len(data))
    alerts = evaluate_alerts(db, readings, request_id=request_id)
    log_audit(
        db,
        tenant_id=device.tenant_id,
        actor_id=None,
        action="device_ingest",
        resource_type="device",
        resource_id=device.id,
        result="success",
        request_id=request_id,
        metadata={"source": source, "accepted": len(readings), "other": other_count},
    )
    return {
        "accepted": len(readings),
        "other": other_count,
        "alerts_triggered": len(alerts),
        "collected_at": collected_at.isoformat(),
    }


def record_health_alert(
    db: Session,
    device: Device,
    text: str,
    *,
    request_id: str = "",
    now: datetime | None = None,
) -> None:
    """系统健康类告警（连接器连续失败自动停用等）：入告警表 + 运维通知。"""
    alert = DeviceAlert(
        tenant_id=device.tenant_id,
        rule_id=None,
        device_id=device.id,
        metric="_health",
        value=None,
        triggered_at=now or datetime.now(UTC),
        notified_text=text,
    )
    db.add(alert)
    log_audit(
        db,
        tenant_id=device.tenant_id,
        actor_id=None,
        action="device_health_alert",
        resource_type="device",
        resource_id=device.id,
        result="success",
        request_id=request_id,
        metadata={"text": text},
    )
    send_alert(title="device_health", message=text)


def device_to_dict(device: Device, connector_out: dict[str, Any] | None = None) -> dict[str, Any]:
    """设备出参（connector_out 由 connector.config_to_out 提供，已掩码）。"""

    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt else None

    return {
        "id": device.id,
        "tenant_id": device.tenant_id,
        "name": device.name,
        "device_type": device.device_type,
        "source_type": device.source_type,
        "model_hint": device.model_hint,
        "longitude": device.longitude,
        "latitude": device.latitude,
        "location_note": device.location_note,
        "enabled": device.enabled,
        "created_by": device.created_by,
        "created_at": _iso(device.created_at),
        "updated_at": _iso(device.updated_at),
        "has_ingest_token": bool(device.token_hash),
        "connector": connector_out,
        "last_polled_at": _iso(device.last_polled_at),
        "consecutive_failures": device.consecutive_failures,
        "last_error": device.last_error,
    }
