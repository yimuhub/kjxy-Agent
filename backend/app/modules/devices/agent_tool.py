"""sensor.query Agents 工具：本小院设备读数概况与窗口统计（只读）。

注册时机与 knowledge.search 一致：应用启动装配（main._startup_wiring）调用
``register_sensor_query_tool()``，向进程级 ``tool_registry`` 注册实现；
``tools.registry.BUILTIN_TOOLS`` 里的元数据行由 ``seed_builtin_tools`` 落库，
供 GET /tools 列表与 declarative.assess_risk 的 builtin 回退查询使用
（两处 schema 由 tests/unit/test_device_agent_tool.py 断言一致，防漂移）。

小院边界：入参不带小院——yard_id 只取自 YardContext，且对
devices / readings / device_alert_rules 每张表的查询都强制带小院过滤。

诚实口径：非数值读数（如风向 "NE"）不参与 min/max/avg 与规则比对，
样本数只统计数值样本；文本读数只在"最新值"里以 text 字段呈现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.devices.metrics import METRICS, normalize_code
from app.modules.devices.models import Device, DeviceAlertRule, Reading

if TYPE_CHECKING:
    from app.core.deps import YardContext

log = get_logger(__name__)

TOOL_CODE = "sensor.query"

#: 每设备最多输出的指标数（按最新读数时间倒序取前 N）
MAX_METRICS_PER_DEVICE = 8
#: 单设备窗口内最多载入统计的读数行数（防御超大窗口拖垮内存）
MAX_ROWS_PER_DEVICE = 2000

_OP_LABEL = {"lt": "低于", "gt": "高于"}

SENSOR_QUERY_INPUT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "device": {
            "type": "string",
            "minLength": 1,
            "maxLength": 120,
            "description": "设备名模糊匹配（仅限本小院设备）；缺省为本小院全部设备",
        },
        "metric": {
            "type": "string",
            "minLength": 1,
            "maxLength": 50,
            "description": "指标 code（如 temp）或中文名片段（如“湿度”）反查",
        },
        "hours": {
            "type": "integer",
            "minimum": 1,
            "maximum": 720,
            "default": 24,
            "description": "统计窗口（小时），1..720，默认 24",
        },
        "alert_rule": {
            "type": "boolean",
            "default": False,
            "description": "true 时附带窗口内启用规则的命中点数与最近一次触发值",
        },
    },
    "additionalProperties": False,
}


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _resolve_devices(db: Session, yard_id: str, keyword: str | None) -> list[Device]:
    stmt = (
        select(Device)
        .where(Device.yard_id == yard_id)
        .order_by(Device.created_at.desc(), Device.name)
    )
    devices = list(db.scalars(stmt))
    if not keyword:
        return devices
    kw = keyword.strip().casefold()
    return [d for d in devices if kw in (d.name or "").casefold()]


def _resolve_metrics(keyword: str | None) -> tuple[list[str] | None, str | None]:
    """metric 入参 → (codes, 错误消息)。命中规则：code 精确 > 归一化 > 中文名片段。

    返回 codes=None 表示"不过滤"（未提供 metric）；codes=[] 配合 message
    表示"无法识别"，由调用方原样回给 LLM 看可用清单。
    """
    if not keyword:
        return None, None
    raw = keyword.strip()
    lowered = raw.lower()
    if lowered in METRICS:
        return [lowered], None
    normalized = normalize_code(raw)
    if normalized != "other":
        return [normalized], None
    hits = [c for c, m in METRICS.items() if raw in m.name or m.name in raw]
    if hits:
        return hits, None
    catalog = "、".join(f"{m.code}({m.name})" for m in METRICS.values())
    return [], f"未识别指标「{raw}」，可用指标：{catalog}。"


def _window_rows(
    db: Session, yard_id: str, device_ids: list[str], codes: list[str] | None, start: datetime
) -> dict[str, list[Reading]]:
    """窗口内读数按设备分组（时间倒序、每设备限量）。device_ids 已限定小院。"""
    grouped: dict[str, list[Reading]] = {d: [] for d in device_ids}
    if not device_ids:
        return grouped
    stmt = (
        select(Reading)
        .where(
            Reading.yard_id == yard_id,
            Reading.device_id.in_(device_ids),
            Reading.collected_at >= start,
        )
        .order_by(Reading.collected_at.desc())
    )
    if codes:
        stmt = stmt.where(Reading.metric.in_(codes))
    for r in db.scalars(stmt):
        bucket = grouped.setdefault(r.device_id, [])
        if len(bucket) < MAX_ROWS_PER_DEVICE:
            bucket.append(r)
    return grouped


def _metric_stat(metric: str, rows: list[Reading]) -> dict[str, Any]:
    """单指标统计：最新值（含文本读数）+ 数值样本的 min/max/avg/样本数。"""
    info = METRICS.get(metric)
    latest = rows[0]  # 已按时间倒序
    values = [r.value for r in rows if r.value is not None]
    stat: dict[str, Any] = {
        "metric": metric,
        "metric_name": info.name if info else metric,
        "unit": latest.unit or (info.unit if info else ""),
        "latest_value": latest.value,
        "latest_text": latest.text,
        "latest_at": _iso(latest.collected_at),
        "samples": len(values),
    }
    if values:
        stat["min"] = min(values)
        stat["max"] = max(values)
        stat["avg"] = round(sum(values) / len(values), 3)
    return stat


def _eval_rules(
    db: Session,
    yard_id: str,
    devices: list[Device],
    codes: list[str] | None,
    start: datetime,
) -> list[dict[str, Any]]:
    """窗口内启用规则评估：命中点数 + 最近一次触发读数（内存比对，口径同
    service.evaluate_alerts 的 op/threshold 判定，但不写库、不去重）。"""
    device_ids = [d.id for d in devices]
    if not device_ids:
        return []
    stmt = select(DeviceAlertRule).where(
        DeviceAlertRule.yard_id == yard_id,
        DeviceAlertRule.enabled.is_(True),
    )
    if codes:
        stmt = stmt.where(DeviceAlertRule.metric.in_(codes))
    rules = [r for r in db.scalars(stmt) if not r.device_id or r.device_id in device_ids]
    if not rules:
        return []
    names = {d.id: d.name for d in devices}
    out: list[dict[str, Any]] = []
    for rule in rules:
        # 规则作用域：绑定设备则只看该设备，否则看本次筛选出的全部设备
        scope_ids = [rule.device_id] if rule.device_id else device_ids
        rows = list(
            db.scalars(
                select(Reading)
                .where(
                    Reading.yard_id == yard_id,
                    Reading.device_id.in_(scope_ids),
                    Reading.metric == rule.metric,
                    Reading.collected_at >= start,
                    Reading.value.is_not(None),
                )
                .order_by(Reading.collected_at.desc())
                .limit(MAX_ROWS_PER_DEVICE)
            )
        )
        hits = [
            r
            for r in rows
            # SQL 已过滤空值，这里 is-not-None 仅为 mypy 收窄（不改变语义）
            if r.value is not None
            and (r.value < rule.threshold if rule.op == "lt" else r.value > rule.threshold)
        ]
        info = METRICS.get(rule.metric)
        entry: dict[str, Any] = {
            "rule_id": rule.id,
            "metric": rule.metric,
            "metric_name": info.name if info else rule.metric,
            "op": rule.op,
            "op_label": _OP_LABEL.get(rule.op, rule.op),
            "threshold": rule.threshold,
            "device_scope": (
                names.get(rule.device_id, "全部匹配设备") if rule.device_id else "全部匹配设备"
            ),
            "hit_count": len(hits),
            "latest_hit": None,
        }
        if hits:
            h = hits[0]  # 时间倒序的第一条即最近一次
            entry["latest_hit"] = {
                "value": h.value,
                "device_name": names.get(h.device_id, h.device_id),
                "collected_at": _iso(h.collected_at),
            }
        out.append(entry)
    return out


def _fmt_value(stat: dict[str, Any]) -> str:
    latest = stat["latest_value"] if stat["latest_value"] is not None else stat["latest_text"]
    return f"{latest}{stat['unit'] or ''}"


def _summary_text(
    hours: int, devices_out: list[dict[str, Any]], rules_out: list[dict[str, Any]]
) -> str:
    lines = [f"最近 {hours} 小时设备读数概况（共 {len(devices_out)} 台设备）："]
    for d in devices_out:
        if not d.get("metrics"):
            lines.append(f"- {d['device_name']}：{d.get('message', '窗口内无读数')}")
            continue
        parts = [
            f"{m['metric_name']} {_fmt_value(m)}"
            f"（窗口 min={m.get('min', '—')} max={m.get('max', '—')} "
            f"avg={m.get('avg', '—')}，样本{m['samples']}）"
            for m in d["metrics"]
        ]
        lines.append(f"- {d['device_name']}：" + "；".join(parts))
    if rules_out:
        lines.append("规则命中评估：")
        for r in rules_out:
            latest = r["latest_hit"]
            tail = (
                f"，最近触发 {latest['value']}（{latest['device_name']} {latest['collected_at']}）"
                if latest
                else ""
            )
            lines.append(
                f"- {r['device_scope']} {r['metric_name']} {r['op_label']}阈值 {r['threshold']}："
                f"窗口内命中 {r['hit_count']} 点{tail}"
            )
    return "\n".join(lines)


def query_sensor(db: Session, *, yard_id: str, params: dict[str, Any]) -> dict[str, Any]:
    """sensor.query 核心查询（纯 db 注入，便于单测）。所有查询强制小院过滤。"""
    hours_raw = params.get("hours")
    hours = 24 if hours_raw is None else int(hours_raw)
    hours = max(1, min(720, hours))
    start = datetime.now(UTC) - timedelta(hours=hours)

    keyword = str(params.get("device") or "").strip() or None
    devices = _resolve_devices(db, yard_id, keyword)
    if keyword and not devices:
        all_names = [d.name for d in _resolve_devices(db, yard_id, None)]
        shown = "、".join(all_names[:20]) if all_names else "（无）"
        message = f"未找到设备「{keyword}」，现有设备：{shown}。"
        return {
            "window_hours": hours,
            "device_count": 0,
            "devices": [],
            "message": message,
            "summary_text": message,
        }

    codes, metric_error = _resolve_metrics(str(params.get("metric") or "").strip() or None)
    if metric_error:
        return {
            "window_hours": hours,
            "device_count": len(devices),
            "devices": [],
            "message": metric_error,
            "summary_text": metric_error,
        }

    grouped = _window_rows(db, yard_id, [d.id for d in devices], codes, start)
    devices_out: list[dict[str, Any]] = []
    for d in devices:
        rows = grouped.get(d.id, [])
        entry: dict[str, Any] = {
            "device_id": d.id,
            "device_name": d.name,
            "device_type": d.device_type,
        }
        if not rows:
            entry["metrics"] = []
            entry["message"] = f"窗口内无读数（最近 {hours} 小时）"
            devices_out.append(entry)
            continue
        by_metric: dict[str, list[Reading]] = {}
        for r in rows:  # rows 已按时间倒序 → 每指标首条即最新
            by_metric.setdefault(r.metric, []).append(r)
        ordered = sorted(by_metric, key=lambda m: by_metric[m][0].collected_at, reverse=True)
        entry["metrics"] = [_metric_stat(m, by_metric[m]) for m in ordered[:MAX_METRICS_PER_DEVICE]]
        devices_out.append(entry)

    result: dict[str, Any] = {
        "window_hours": hours,
        "device_count": len(devices_out),
        "devices": devices_out,
    }
    if bool(params.get("alert_rule")):
        result["rules"] = _eval_rules(db, yard_id, devices, codes, start)
    result["summary_text"] = _summary_text(hours, devices_out, list(result.get("rules") or []))
    return result


def _open_session() -> Session:
    """工具函数用会话工厂（knowledge.search 同款通道：函数内部自开会话）。

    单独成函数便于测试 monkeypatch 注入内存库会话。
    """
    from app.db.session import SessionLocal

    return SessionLocal()


def _sensor_query_tool_func(params: dict, ctx: YardContext) -> dict:
    """sensor.query 工具执行函数（ToolFunc 约定：(params, ctx)）。"""
    if not ctx.yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    db = _open_session()
    try:
        return query_sensor(db, yard_id=ctx.yard_id, params=dict(params or {}))
    finally:
        db.close()


def register_sensor_query_tool() -> Any:
    """向全局工具注册表注册 code=sensor.query（只读、无副作用）。

    注册表缺失时仅记录告警并返回 None，保证本模块 import 不失败（同 knowledge）。
    """
    try:
        from app.modules.tools.registry import ToolDef, tool_registry
    except Exception as e:
        log.warning("工具注册表不可用，sensor.query 未注册: %s", e)
        return None

    tool_def = ToolDef(
        code=TOOL_CODE,
        version="1.0.0",
        name="传感器读数查询",
        description=(
            "查询本小院设备的传感器读数：每设备最新值与窗口内 min/max/avg、样本数；"
            "支持设备名模糊、指标 code 或中文名片段过滤；可选附带告警规则命中评估。只读。"
        ),
        input_schema=SENSOR_QUERY_INPUT_SCHEMA,
        # 读=小院成员（devices 读接口同口径），因此不设额外权限点
        required_permissions=[],
        risk_level="read_only",
        side_effect=False,
        timeout_seconds=15,
        func=_sensor_query_tool_func,
    )
    try:
        tool_registry.register(tool_def)
    except Exception as e:
        log.warning("sensor.query 注册失败: %s", e)
        return None
    log.info("sensor.query 工具已注册")
    return tool_def
