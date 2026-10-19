"""自定义连接器（A2）：界面配置的通用 HTTP 拉取通道。

配置存储形态（devices.connector_config_json）::

    {
      "url": "https://vendor.example/api/realtime",
      "method": "GET" | "POST",
      "headers_enc": {"X-Api-Key": "<Fernet 密文>"},   # 复用 llm/supplier_crypto
      "body_template": {...} | null,                   # POST JSON 请求体
      "interval_seconds": 300,                          # >=60
      "json_path": "$.data.items[*]",                   # JSONPath-lite，见 extract_path
      "field_map": {"deviceTemp": {"metric": "temp", "unit": null}},
      "timeout_seconds": 10,
      "time_field": "ts" | null                         # 记录内 ISO/epoch 时间字段
    }

取数与归一：extract_path 自实现（只支持 ``$.a.b`` 与 ``$.items[*].x`` 风格，
不引入 jsonpath 依赖）→ 得到记录列表/单对象 → 按 field_map 归一为
{厂商字段: 原始值} → 复用 service.ingest_values（与 webhook 通道同一入库口径）。

失败纪律：拉取失败（DNS/超时/HTTP/JSON/SSRF）→ consecutive_failures +=1、
记 last_error，本轮不重试（下一轮按 interval 再来）；连续 5 次自动 enabled=false
并写"_health"告警。成功一次即清零计数。
"""

from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.net_guard import check_egress_url
from app.modules.devices import service
from app.modules.devices.metrics import METRICS
from app.modules.devices.models import Device
from app.modules.devices.schemas import ConnectorConfigIn

log = get_logger(__name__)

#: 连续失败自动停用阈值
CONNECTOR_MAX_FAILURES = 5


class ConnectorError(Exception):
    """一次拉取失败（调用方计数，不在本模块重试）。"""


# ---------------------------------------------------------------------------
# 配置：校验 / 加密入库形态 / 掩码出参
# ---------------------------------------------------------------------------


def prepare_config(cfg: ConnectorConfigIn) -> dict[str, Any]:
    """入参配置 → 入库形态（headers 明文值全部 Fernet 加密为 headers_enc）。"""
    from app.modules.llm.supplier_crypto import encrypt_api_key

    # SSRF：能解析就必须是允许的地址（loopback/元数据等直接拒）；解析失败放行
    # 给拉取时报"不可达"——与 net_guard 既有语义一致。
    check_egress_url(cfg.url, context="device_connector")
    for field, entry in cfg.field_map.items():
        if entry.metric not in METRICS:
            raise AppError(
                "INVALID_REQUEST",
                f"field_map['{field}'].metric='{entry.metric}' 不在指标字典中，"
                "未归类指标请使用 other。",
                http_status=400,
            )
    stored: dict[str, Any] = {
        "url": cfg.url,
        "method": cfg.method,
        "headers_enc": {k: encrypt_api_key(v) for k, v in (cfg.headers or {}).items()},
        "body_template": cfg.body_template,
        "interval_seconds": cfg.interval_seconds,
        "json_path": cfg.json_path or "$",
        "field_map": {k: v.model_dump() for k, v in cfg.field_map.items()},
        "timeout_seconds": cfg.timeout_seconds,
        "time_field": cfg.time_field,
    }
    return stored


def config_to_out(stored: dict[str, Any] | None) -> dict[str, Any] | None:
    """入库形态 → 出参：headers 一律掩码（密文/明文都不出接口）。"""
    if not stored:
        return None
    from app.modules.llm.supplier_crypto import decrypt_api_key, mask_api_key

    masked: dict[str, str] = {}
    for name, cipher in (stored.get("headers_enc") or {}).items():
        try:
            masked[name] = mask_api_key(decrypt_api_key(cipher))
        except AppError:
            # 解密失败（主密钥轮换等）：不回显任何密文/明文片段
            masked[name] = "****（无法解密，请重新录入）"
    return {
        "url": stored.get("url"),
        "method": stored.get("method", "GET"),
        "headers_masked": masked,
        "body_template": stored.get("body_template"),
        "interval_seconds": stored.get("interval_seconds"),
        "json_path": stored.get("json_path", "$"),
        "field_map": stored.get("field_map") or {},
        "timeout_seconds": stored.get("timeout_seconds", 10),
        "time_field": stored.get("time_field"),
    }


def _decrypt_headers(stored: dict[str, Any]) -> dict[str, str]:
    from app.modules.llm.supplier_crypto import decrypt_api_key

    return {k: decrypt_api_key(v) for k, v in (stored.get("headers_enc") or {}).items()}


# ---------------------------------------------------------------------------
# JSONPath-lite（自实现，不加依赖）
#
# 选型说明：只支持点分路径 + [*] 通配——`$.a.b`、`$.items[*]`、`$.items[*].x`。
# 足够覆盖"取数组/取对象/数组里抽字段"三类厂商报文；条件/切片/递归一律不支持，
# 覆盖不了的接口用 json_path 选到对象、在 field_map 里点分取字段即可。
# ---------------------------------------------------------------------------


def extract_path(node: Any, path: str) -> list[Any]:
    """按 ``$.a.b`` / ``$.items[*].x`` 风格路径取节点，返回列表（可能为空）。"""
    p = (path or "").strip()
    if p.startswith("$"):
        p = p[1:]
    if p.startswith("."):
        p = p[1:]
    if not p:
        return [node]
    current: list[Any] = [node]
    for seg in p.split("."):
        if not seg:
            continue
        nxt: list[Any] = []
        key, sep, tail = seg.partition("[*]")
        if tail:
            # 仅支持 [*] 结尾（"items[*].x" 已由点分拆段，这里是写法错误兜底）
            raise ConnectorError(f"json_path 段不合法：{seg}（[*] 需位于段尾）")
        for item in current:
            if not isinstance(item, dict):
                continue
            if sep and key == "":  # 纯 [*] 段
                val = item
            else:
                if key not in item:
                    continue
                val = item[key]
            if sep:  # key[*] → 展开数组
                if isinstance(val, list):
                    nxt.extend(val)
                else:
                    nxt.append(val)
            else:
                nxt.append(val)
        current = nxt
    return current


def _first(node: Any, dotted: str) -> tuple[bool, Any]:
    vals = extract_path(node, dotted)
    return (True, vals[0]) if vals else (False, None)


def record_to_values(record: Any, field_map: dict[str, Any]) -> dict[str, Any]:
    """单条记录 → {厂商字段: 原始值}（只取 field_map 声明过的字段）。"""
    values: dict[str, Any] = {}
    for vendor_field in field_map:
        ok, val = _first(record, vendor_field)
        if ok:
            values[vendor_field] = val
    return values


def _record_time(record: Any, time_field: str | None, default: datetime) -> str:
    if time_field:
        ok, raw = _first(record, time_field)
        if ok:
            if isinstance(raw, int | float) and not isinstance(raw, bool):
                try:
                    return datetime.fromtimestamp(float(raw), UTC).isoformat()
                except (ValueError, OSError):
                    return default.isoformat()
            if isinstance(raw, str):
                try:
                    datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    return raw
                except ValueError:
                    return default.isoformat()
    return default.isoformat()


# ---------------------------------------------------------------------------
# 拉到即入库
# ---------------------------------------------------------------------------


def fetch_and_ingest(db: Session, device: Device, *, now: datetime | None = None) -> dict[str, int]:
    """执行一次连接器拉取并入库。失败抛 ConnectorError/AppError（由调用方计数）。"""
    now = now or datetime.now(UTC)
    stored = device.connector_config_json or {}
    url = stored.get("url")
    if not url:
        raise ConnectorError("连接器缺少 url 配置")
    # 拉取时刻复检 SSRF（域名背后 IP 可能已变——DNS 重绑定防线）
    check_egress_url(url, context=f"device_connector:{device.id[:8]}")
    timeout = float(stored.get("timeout_seconds") or 10)
    headers = _decrypt_headers(stored)
    method = str(stored.get("method") or "GET").upper()
    try:
        with httpx.Client(timeout=timeout) as client:
            if method == "POST":
                resp = client.post(url, headers=headers, json=stored.get("body_template") or {})
            else:
                resp = client.get(url, headers=headers)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPStatusError as e:
        raise ConnectorError(f"HTTP {e.response.status_code}") from e
    except httpx.HTTPError as e:
        raise ConnectorError(f"请求失败：{type(e).__name__}") from e
    except ValueError as e:
        raise ConnectorError("响应不是合法 JSON") from e

    field_map: dict[str, Any] = stored.get("field_map") or {}
    nodes = extract_path(payload, str(stored.get("json_path") or "$"))
    stats = {"records": 0, "accepted": 0, "alerts": 0}
    for record in nodes:
        values = record_to_values(record, field_map)
        if not values:
            continue
        stats["records"] += 1
        result = service.ingest_values(
            db,
            device,
            values,
            reported_at=_record_time(record, stored.get("time_field"), now),
            field_map=field_map,
            request_id=f"req_connector_{device.id[:8]}",
            source="connector",
        )
        stats["accepted"] += int(result["accepted"])
        stats["alerts"] += int(result["alerts_triggered"])
    if stats["records"] == 0:
        raise ConnectorError("json_path 未取到任何数据")
    return stats


# ---------------------------------------------------------------------------
# 轮询入口（celery beat / inline 线程共用）
# ---------------------------------------------------------------------------


def poll_due_connectors(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """轮询所有"到点"的启用连接器设备；每设备独立事务，坏设备不拖垮整轮。"""

    now = now or datetime.now(UTC)
    devices = list(
        db.scalars(
            select(Device).where(
                Device.source_type == "connector",
                Device.enabled.is_(True),
            )
        )
    )
    stats = {"due": 0, "ok": 0, "failed": 0, "disabled": 0}
    for device in devices:
        interval = int((device.connector_config_json or {}).get("interval_seconds") or 300)
        last = device.last_polled_at
        if last is not None:
            if last.tzinfo is None:  # SQLite 读回无时区：按 UTC 理解
                last = last.replace(tzinfo=UTC)
            if (now - last).total_seconds() < interval:
                continue
        stats["due"] += 1
        device.last_polled_at = now
        try:
            fetch_and_ingest(db, device, now=now)
            device.consecutive_failures = 0
            device.last_error = None
            stats["ok"] += 1
            db.commit()
        except Exception as e:  # noqa: BLE001 —— 单设备失败必须隔离
            db.rollback()
            fresh = db.get(Device, device.id)
            if fresh is None:
                continue
            device = fresh
            device.last_polled_at = now
            device.consecutive_failures += 1
            device.last_error = str(getattr(e, "message", None) or e)[:300]
            log.warning(
                "connector poll failed: device=%s fail=%s err=%s",
                device.id[:8],
                device.consecutive_failures,
                device.last_error,
            )
            if device.consecutive_failures >= CONNECTOR_MAX_FAILURES:
                device.enabled = False
                stats["disabled"] += 1
                service.record_health_alert(
                    db,
                    device,
                    f"【设备停用】{device.name}：连接器连续 "
                    f"{CONNECTOR_MAX_FAILURES} 次拉取失败（{device.last_error}），"
                    "已自动停用，请检查接口配置与网络后手动启用。",
                )
            stats["failed"] += 1
            db.commit()
    return stats
