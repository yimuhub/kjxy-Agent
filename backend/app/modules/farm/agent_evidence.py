"""Agent 自动执行的证据汇总（v3.28）。

把所有判断依据**一次性**取成纯数据，规则层只读 dict：

- 设备：类型/位置/在线状态 + 每指标最新读数 + 窗口降雨累计 + 最近一次分析发现；
- 天气：按小院配置取 24/48 小时预报（未配置则明确写进 warnings）；
- 台账：地块、生长周期（含预计采收）、近 30 天农事记录；
- 药剂：已登记药剂（只取 active 参与安全间隔核算）；
- 闭环：未关闭诊断与在办工单，用于降噪与展示。

取数只读，不写库；单台设备取数失败不阻断整体（如实记 warning）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.modules.devices.models import Device, Reading
from app.modules.farm import weather
from app.modules.farm.models import (
    CropDiagnosis,
    FarmCycle,
    FarmPlot,
    FarmRecord,
    PesticideProduct,
    WorkOrder,
)
from app.modules.yards.settings_service import get_setting

log = get_logger(__name__)

RECORD_LOOKBACK_DAYS = 30
MAX_RECORDS = 300
OPEN_DIAGNOSIS_STATUSES = ("open", "agent_diagnosed", "expert_review")
OPEN_ORDER_STATUSES = ("open", "assigned", "in_progress")


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized is not None else None


def _norm(text: str | None) -> str:
    return "".join(str(text or "").split()).lower()


def _match_plot(device: Device, plots: list[FarmPlot]) -> FarmPlot | None:
    """没有设备-地块绑定表：按位置描述与地块名做保守匹配，匹配不上就不硬指。"""
    note = _norm(device.location_note)
    if note:
        for plot in plots:
            name = _norm(plot.name)
            if name and (name in note or note in name):
                return plot
    if len(plots) == 1:
        return plots[0]
    name = _norm(device.name)
    for plot in plots:
        pname = _norm(plot.name)
        if pname and pname in name:
            return plot
    return None


def _latest_readings(db: Session, device_ids: list[str]) -> dict[str, dict[str, Any]]:
    """每台设备每个指标的最新读数（逐台取最近 500 条，设备数少且走 device_id 索引）。"""
    latest: dict[str, dict[str, Any]] = {}
    for device_id in device_ids:
        rows = list(
            db.scalars(
                select(Reading)
                .where(Reading.device_id == device_id)
                .order_by(Reading.collected_at.desc())
                .limit(500)
            )
        )
        bucket: dict[str, Any] = {}
        for row in rows:
            if row.metric in bucket:
                continue
            bucket[row.metric] = {
                "value": row.value,
                "text": row.text,
                "unit": row.unit,
                "at": _as_utc(row.collected_at),
            }
        latest[device_id] = bucket
    return latest


def _rain_totals(db: Session, device_ids: list[str], window_from: datetime) -> dict[str, float]:
    if not device_ids:
        return {}
    rows = db.execute(
        select(Reading.device_id, func.coalesce(func.sum(Reading.value), 0.0))
        .where(
            Reading.device_id.in_(device_ids),
            Reading.metric == "rain",
            Reading.collected_at >= window_from,
        )
        .group_by(Reading.device_id)
    ).all()
    return {row[0]: float(row[1] or 0.0) for row in rows}


def _crop_for(plot: FarmPlot | None, cycles: list[FarmCycle]) -> str:
    if plot is not None:
        if plot.crop:
            return plot.crop
        for cycle in cycles:
            if cycle.plot_id == plot.id and cycle.status == "growing" and cycle.crop:
                return cycle.crop
    crops = {c.crop for c in cycles if c.status == "growing" and c.crop}
    return crops.pop() if len(crops) == 1 else ""


def collect(
    db: Session,
    yard_id: str,
    *,
    now: datetime | None = None,
    window_hours: int = 24,
) -> dict[str, Any]:
    """汇总一次自动执行的证据。"""
    now = _as_utc(now) or datetime.now(UTC)
    window_from = now - timedelta(hours=max(6, min(int(window_hours or 24), 168)))
    warnings: list[str] = []

    devices = list(
        db.scalars(select(Device).where(Device.yard_id == yard_id, Device.enabled.is_(True)))
    )
    plots = list(
        db.scalars(
            select(FarmPlot).where(FarmPlot.yard_id == yard_id, FarmPlot.status != "archived")
        )
    )
    cycles = list(db.scalars(select(FarmCycle).where(FarmCycle.yard_id == yard_id)))
    device_ids = [d.id for d in devices]
    readings = _latest_readings(db, device_ids)
    rain_totals = _rain_totals(db, device_ids, window_from)

    analyses: dict[str, Any] = {}
    if device_ids:
        try:
            from app.modules.devices import analysis_service

            analyses = analysis_service.latest_map(db, yard_id, device_ids)
        except Exception as e:  # noqa: BLE001 —— 分析缺失不影响读数规则
            log.warning("farm agent latest analysis failed: %s", e)
            warnings.append("最近一次设备分析读取失败，本轮只按最新读数判断。")

    device_rows: list[dict[str, Any]] = []
    for device in devices:
        plot = _match_plot(device, plots)
        item_readings = readings.get(device.id, {})
        last_at = None
        for value in item_readings.values():
            at = value.get("at")
            if isinstance(at, datetime) and (last_at is None or at > last_at):
                last_at = at
        offline_hours = (
            round((now - last_at).total_seconds() / 3600, 2) if last_at is not None else None
        )
        analysis = analyses.get(device.id)
        device_rows.append(
            {
                "id": device.id,
                "name": device.name,
                "type": device.device_type,
                "location": device.location_note or "",
                "plot_id": plot.id if plot else None,
                "plot_name": plot.name if plot else "",
                "crop": _crop_for(plot, cycles),
                "readings": {
                    metric: {
                        "value": value.get("value"),
                        "unit": value.get("unit"),
                        "at": _as_utc(value.get("at")),
                    }
                    for metric, value in item_readings.items()
                },
                "rain_mm": rain_totals.get(device.id, 0.0),
                "last_reading_at": _iso(last_at),
                "offline_hours": offline_hours,
                "last_polled_at": _iso(device.last_polled_at),
                "consecutive_failures": int(device.consecutive_failures or 0),
                "last_error": device.last_error,
                "analysis": (
                    {
                        "analysis_id": analysis.id,
                        "status": analysis.status,
                        "level": analysis.level,
                        "summary": analysis.summary,
                        "findings": analysis.findings_json or [],
                        "created_at": _iso(analysis.created_at),
                    }
                    if analysis is not None
                    else None
                ),
            }
        )

    if not devices:
        warnings.append("本院没有启用中的设备：本轮只能依据天气与台账判断，无法给出读数级预警。")

    setting = get_setting(db, yard_id, "farm_agent")
    forecast, weather_warnings = weather.forecast(yard_id, setting)
    warnings.extend(weather_warnings)

    since = (now - timedelta(days=RECORD_LOOKBACK_DAYS)).date()
    records = list(
        db.scalars(
            select(FarmRecord)
            .where(FarmRecord.yard_id == yard_id, FarmRecord.occurred_on >= since)
            .order_by(FarmRecord.occurred_on.desc())
            .limit(MAX_RECORDS)
        )
    )
    pesticides = list(
        db.scalars(select(PesticideProduct).where(PesticideProduct.yard_id == yard_id))
    )
    diagnoses = list(
        db.scalars(
            select(CropDiagnosis)
            .where(
                CropDiagnosis.yard_id == yard_id,
                CropDiagnosis.status.in_(OPEN_DIAGNOSIS_STATUSES),
            )
            .order_by(CropDiagnosis.created_at.desc())
            .limit(50)
        )
    )
    open_orders = list(
        db.scalars(
            select(WorkOrder)
            .where(
                WorkOrder.yard_id == yard_id,
                WorkOrder.status.in_(OPEN_ORDER_STATUSES),
            )
            .order_by(WorkOrder.created_at.desc())
            .limit(100)
        )
    )

    return {
        "yard_id": yard_id,
        "now": now.isoformat(),
        "window_from": window_from.isoformat(),
        "window_hours": window_hours,
        "devices": device_rows,
        "weather": {"forecast": forecast, "warnings": weather_warnings},
        "plots": [
            {
                "id": p.id,
                "name": p.name,
                "crop": p.crop,
                "status": p.status,
                "location": p.location,
            }
            for p in plots
        ],
        "cycles": [
            {
                "id": c.id,
                "plot_id": c.plot_id,
                "crop": c.crop,
                "status": c.status,
                "expected_harvest_on": c.expected_harvest_on.isoformat()
                if isinstance(c.expected_harvest_on, date)
                else c.expected_harvest_on,
            }
            for c in cycles
        ],
        "records": [
            {
                "id": r.id,
                "plot_id": r.plot_id,
                "kind": r.kind,
                "occurred_on": r.occurred_on.isoformat(),
                "title": r.title,
                "content": r.content,
                "quantity": r.quantity,
                "unit": r.unit,
                "source": getattr(r, "source", "manual"),
            }
            for r in records
        ],
        "pesticides": [
            {
                "id": p.id,
                "name": p.name,
                "active_ingredient": p.active_ingredient,
                "registered_crops": p.registered_crops or [],
                "pre_harvest_interval_days": p.pre_harvest_interval_days,
                "status": p.status,
            }
            for p in pesticides
        ],
        "diagnoses": [
            {
                "id": d.id,
                "crop": d.crop,
                "status": d.status,
                "device_id": d.device_id,
                "created_at": _iso(d.created_at),
            }
            for d in diagnoses
        ],
        "work_orders": [
            {
                "id": o.id,
                "title": o.title,
                "status": o.status,
                "priority": o.priority,
                "source": o.source,
                "plot_id": o.plot_id,
            }
            for o in open_orders
        ],
        "warnings": warnings,
    }


def summarize(evidence: dict[str, Any]) -> dict[str, Any]:
    """落库用的证据摘要：只留计数与少量标识，不复制读数全文。"""
    devices = evidence.get("devices") or []
    forecast = (evidence.get("weather") or {}).get("forecast") or {}
    return {
        "devices": len(devices),
        "device_names": [d.get("name") for d in devices][:10],
        "plots": len(evidence.get("plots") or []),
        "cycles": len(evidence.get("cycles") or []),
        "records_30d": len(evidence.get("records") or []),
        "pesticides": len(evidence.get("pesticides") or []),
        "open_diagnoses": len(evidence.get("diagnoses") or []),
        "open_work_orders": len(evidence.get("work_orders") or []),
        "weather_provider": forecast.get("provider") if forecast else None,
        "weather_generated_at": forecast.get("generated_at") if forecast else None,
        "warnings": list(evidence.get("warnings") or [])[:8],
    }


__all__ = ["collect", "summarize"]
