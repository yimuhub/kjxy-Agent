"""Agent 自动执行规则（v3.28）：确定性规则先行，模型只做解释。

设计口径（与设备分析同哲学）：

- **规则可复核**：每条发现都带具体数值/时间/单据 id，能追溯到读数、台账或药剂登记；
- **不编造**：设备未绑定地块就无法确定作物时，只出告警、不出诊断；药剂只从已录入的
  农事记录里识别名称，不猜有效成分与安全间隔期；
- **不越权**：不做设备控制，不替人确认采购/用药结论；用药相关动作一律提示走专家复核；
- **诚实边界**：没有设备、没有台账、没有天气预报时明确写进 warnings，而不是凭空预警。

本模块是纯函数：输入证据 dict，输出发现 dict 列表，不碰数据库。
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Any

from app.modules.devices.analysis import LEVEL_LABELS, LEVEL_ORDER

#: 规则码 → 中文名（界面与推送共用口径）
RULE_LABELS: dict[str, str] = {
    "water.low": "土壤水分偏低",
    "disease.risk": "病害风险上升",
    "pest.rising": "虫情计数上升",
    "device.battery": "设备电量偏低",
    "device.stuck": "传感器疑似卡死",
    "device.offline": "设备长时间无上报",
    "weather.rain": "降雨影响作业窗口",
    "weather.wind": "大风影响施药",
    "weather.cold": "低温/降温风险",
    "weather.heat": "高温风险",
    "pesticide.phi": "采收安全间隔期不足",
    "pesticide.unregistered": "用药未在药剂库登记",
    "plot.missing": "缺少地块档案",
}

#: 动作类型 → 中文名
ACTION_LABELS: dict[str, str] = {
    "alert": "告警",
    "work_order": "工单",
    "diagnosis": "作物诊断",
    "record": "台账记录",
    "pesticide": "药剂登记",
    "plot": "地块建档",
}

#: 严重级别 → 告警级别 / 工单优先级
ALERT_SEVERITY = {"info": "low", "watch": "normal", "warning": "high", "critical": "critical"}
WORK_PRIORITY = {"info": "low", "watch": "normal", "warning": "high", "critical": "urgent"}

DEFAULT_THRESHOLDS: dict[str, float] = {
    "soil_moisture_low": 20.0,
    "rain_no_spray_mm": 10.0,
    "wind_no_spray_ms": 10.0,
    "temp_drop_c": 8.0,
    "temp_min_c": 3.0,
    "temp_max_c": 35.0,
    "humidity_disease_pct": 85.0,
    "offline_hours": 6.0,
}


def _finding(
    *,
    code: str,
    level: str,
    title: str,
    detail: str,
    suggestion: str,
    evidence: list[dict[str, Any]],
    dedupe_key: str,
    actions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "code": code,
        "rule_label": RULE_LABELS.get(code, code),
        "level": level,
        "level_label": LEVEL_LABELS.get(level, level),
        "title": title,
        "detail": detail,
        "suggestion": suggestion,
        "evidence": evidence,
        "dedupe_key": dedupe_key,
        "actions": actions,
    }


def _action(
    kind: str, title: str, *, detail: str = "", payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "kind": kind,
        "kind_label": ACTION_LABELS.get(kind, kind),
        "title": title,
        "detail": detail,
        "payload": payload or {},
    }


def _at(value: str | datetime | None) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else value.isoformat()


def _fmt(value: float, unit: str = "") -> str:
    text = f"{value:g}"
    return f"{text}{unit}" if unit else text


# ---------------------------------------------------------------------------
# 设备侧规则
# ---------------------------------------------------------------------------


def _soil_moisture(device: dict[str, Any]) -> tuple[str, float, str] | None:
    for metric in ("soil_moisture_20", "soil_moisture_10", "soil_moisture_40", "soil_moisture_60"):
        item = (device.get("readings") or {}).get(metric)
        if isinstance(item, dict) and isinstance(item.get("value"), int | float):
            return metric, float(item["value"]), str(item.get("unit") or "")
    return None


def _device_rules(evidence: dict[str, Any], thresholds: dict[str, float]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    forecast = (evidence.get("weather") or {}).get("forecast") or {}
    next_rain = ((forecast.get("next_24h") or {}).get("rain_mm")) if forecast else None

    for device in evidence.get("devices") or []:
        name = str(device.get("name") or device.get("id"))
        plot_id = device.get("plot_id")
        plot_name = device.get("plot_name") or ""
        where = plot_name or name
        readings = device.get("readings") or {}
        analysis = device.get("analysis") or {}

        # 1) 墒情偏低且近期无有效降雨 → 告警 + 灌溉工单
        moisture = _soil_moisture(device)
        if moisture is not None:
            metric, value, unit = moisture
            threshold = float(thresholds.get("soil_moisture_low") or 20.0)
            rain_window = float(device.get("rain_mm") or 0.0)
            rain_coming = next_rain is not None and float(next_rain) >= 5.0
            if value < threshold and rain_window < 2.0 and not rain_coming:
                at = _at((readings.get(metric) or {}).get("at"))
                actions = [
                    _action(
                        "alert",
                        f"墒情预警：{where}土壤水分偏低",
                        detail=(
                            f"{metric} {_fmt(value, unit)} < 阈值 {_fmt(threshold, unit)}，"
                            "近期无有效降雨。"
                        ),
                        payload={"plot_id": plot_id, "device_id": device.get("id")},
                    ),
                    _action(
                        "work_order",
                        f"灌溉核查：{where}（土壤水分 {_fmt(value, unit)}）",
                        detail="建议现场核查墒情与灌溉设施，确认是否需要灌溉；执行前请结合天气预报。",
                        payload={
                            "plot_id": plot_id,
                            "device_id": device.get("id"),
                            "priority": WORK_PRIORITY["warning"],
                        },
                    ),
                ]
                if plot_id:  # 没有地块就写不进台账，不产出无法执行的记录动作
                    actions.append(
                        _action(
                            "record",
                            f"{where} 墒情偏低（{_fmt(value, unit)}）",
                            detail=(
                                f"设备「{name}」{at} 读数 {_fmt(value, unit)}，"
                                f"窗口降雨 {rain_window:g} mm。"
                            ),
                            payload={"plot_id": plot_id, "kind": "scouting"},
                        )
                    )
                findings.append(
                    _finding(
                        code="water.low",
                        level="warning",
                        title=f"{where} 土壤水分 {_fmt(value, unit)}，低于 {_fmt(threshold, unit)}",
                        detail=(
                            f"设备「{name}」{at} 的{metric}读数为 {_fmt(value, unit)}，"
                            f"窗口内降雨累计 {rain_window:g} mm"
                            + (
                                f"，未来 24 小时预报降雨 {float(next_rain):g} mm"
                                if next_rain is not None
                                else "，未配置天气预报"
                            )
                            + "。"
                        ),
                        suggestion="请核查墒情与灌溉计划，确认是否需要安排灌溉；先到现场确认探头位置与读数合理性。",
                        evidence=[
                            {
                                "kind": "reading",
                                "device_id": device.get("id"),
                                "metric": metric,
                                "value": round(value, 3),
                                "unit": unit,
                                "at": at,
                            }
                        ],
                        dedupe_key=f"water.low:{device.get('id')}",
                        actions=actions,
                    )
                )

        # 2) 高湿低温 / 累计降雨 → 病害风险（有作物才建诊断）
        humidity = (readings.get("humidity") or {}).get("value")
        temp = (readings.get("temp") or {}).get("value")
        rain_window = float(device.get("rain_mm") or 0.0)
        humidity_limit = float(thresholds.get("humidity_disease_pct") or 85.0)
        risky = (
            isinstance(humidity, int | float)
            and isinstance(temp, int | float)
            and float(humidity) >= humidity_limit
            and float(temp) <= 16.0
        ) or rain_window >= 20.0
        if risky:
            crop = str(device.get("crop") or "")
            parts = []
            if isinstance(humidity, int | float):
                parts.append(f"空气湿度 {float(humidity):.0f}%")
            if isinstance(temp, int | float):
                parts.append(f"气温 {float(temp):.1f}℃")
            parts.append(f"窗口降雨 {rain_window:g} mm")
            actions = [
                _action(
                    "alert",
                    f"病害风险：{where}高湿{'低温' if isinstance(temp, int | float) else '多雨'}",
                    detail="；".join(parts) + "，属于病害易发条件，请加强巡田。",
                    payload={"plot_id": plot_id, "device_id": device.get("id")},
                )
            ]
            if crop:
                actions.append(
                    _action(
                        "diagnosis",
                        f"{crop} 病害风险自动诊断（{where}）",
                        detail="依据设备温湿度与降雨条件自动发起，结论仅作辅助，用药需专家复核。",
                        payload={
                            "plot_id": plot_id,
                            "device_id": device.get("id"),
                            "crop": crop,
                        },
                    )
                )
            findings.append(
                _finding(
                    code="disease.risk",
                    level="watch",
                    title=f"{where} 出现病害易发条件（{'；'.join(parts)}）",
                    detail=(
                        f"设备「{name}」最新读数："
                        + "；".join(parts)
                        + "。"
                        + (
                            "已具备发起自动诊断的作物信息。"
                            if crop
                            else "未绑定地块/作物，本轮只出告警。"
                        )
                    ),
                    suggestion="加强巡田、通风降湿；发现病斑走作物诊断并由专家复核，不直接按本条建议用药。",
                    evidence=[
                        {
                            "kind": "reading",
                            "device_id": device.get("id"),
                            "metric": "humidity",
                            "value": humidity,
                            "at": _at((readings.get("humidity") or {}).get("at")),
                        },
                        {
                            "kind": "reading",
                            "device_id": device.get("id"),
                            "metric": "temp",
                            "value": temp,
                            "at": _at((readings.get("temp") or {}).get("at")),
                        },
                    ],
                    dedupe_key=f"disease.risk:{device.get('id')}",
                    actions=actions,
                )
            )

        # 3) 虫情计数上升（来自设备分析规则，避免重复实现）
        for item in analysis.get("findings") or []:
            code = str(item.get("code") or "")
            if code != "combo:pest_window":
                continue
            findings.append(
                _finding(
                    code="pest.rising",
                    level="warning",
                    title=f"{where} 虫情计数连续上升",
                    detail=str(item.get("detail") or ""),
                    suggestion="检查诱捕设备与田间虫口密度；防治措施需结合作物与用药规范，必要时走作物诊断。",
                    evidence=[{"kind": "analysis", "device_id": device.get("id"), "code": code}],
                    dedupe_key=f"pest.rising:{device.get('id')}",
                    actions=[
                        _action(
                            "alert",
                            f"虫情预警：{where}虫口上升",
                            detail=str(item.get("title") or "虫情计数连续上升"),
                            payload={"plot_id": plot_id, "device_id": device.get("id")},
                        ),
                        _action(
                            "work_order",
                            f"虫情核查：{where}",
                            detail="布设/检查诱捕设备，确认虫口密度后再决定防治措施。",
                            payload={
                                "plot_id": plot_id,
                                "device_id": device.get("id"),
                                "priority": WORK_PRIORITY["warning"],
                            },
                        ),
                    ],
                )
            )

        # 4) 设备自身健康：低电量 / 卡死 / 长时间无上报
        battery = (readings.get("battery") or {}).get("value")
        if isinstance(battery, int | float) and float(battery) < 20:
            findings.append(
                _finding(
                    code="device.battery",
                    level="watch",
                    title=f"设备「{name}」电量 {float(battery):g}%",
                    detail=f"最近一次电量读数 {float(battery):g}%，低于 20%。",
                    suggestion="尽快安排更换电池或充电，避免断测。",
                    evidence=[
                        {
                            "kind": "reading",
                            "device_id": device.get("id"),
                            "device_name": name,
                            "metric": "battery",
                            "value": float(battery),
                            "at": _at((readings.get("battery") or {}).get("at")),
                        }
                    ],
                    dedupe_key=f"device.battery:{device.get('id')}",
                    actions=[
                        _action(
                            "alert",
                            f"设备电量偏低：{name}",
                            detail=f"电量 {float(battery):g}%，低于 20%。",
                            payload={"device_id": device.get("id")},
                        ),
                        _action(
                            "work_order",
                            f"设备维护：{name} 电量 {float(battery):g}%",
                            detail="更换电池或充电，并复核近 24 小时数据完整性。",
                            payload={
                                "device_id": device.get("id"),
                                "priority": WORK_PRIORITY["watch"],
                            },
                        ),
                    ],
                )
            )

        stuck = [
            item
            for item in (analysis.get("findings") or [])
            if str(item.get("code") or "").startswith("stuck:")
        ]
        if stuck:
            titles = "；".join(str(item.get("title")) for item in stuck[:2])
            findings.append(
                _finding(
                    code="device.stuck",
                    level="watch",
                    title=f"设备「{name}」疑似传感器卡死",
                    detail=titles,
                    suggestion="检查设备在线状态、探头与安装位置；卡死期间的数据不要用于决策。",
                    evidence=[
                        {
                            "kind": "analysis",
                            "device_id": device.get("id"),
                            "device_name": name,
                            "codes": [i.get("code") for i in stuck],
                        }
                    ],
                    dedupe_key=f"device.stuck:{device.get('id')}",
                    actions=[
                        _action(
                            "alert",
                            f"数据质量：{name}疑似卡死",
                            detail=titles,
                            payload={"device_id": device.get("id")},
                        ),
                        _action(
                            "work_order",
                            f"设备维护：{name} 传感器疑似卡死",
                            detail="现场检查探头与通信，必要时更换。",
                            payload={
                                "device_id": device.get("id"),
                                "priority": WORK_PRIORITY["watch"],
                            },
                        ),
                    ],
                )
            )

        offline_hours = device.get("offline_hours")
        limit = float(thresholds.get("offline_hours") or 6.0)
        if isinstance(offline_hours, int | float) and float(offline_hours) >= limit:
            findings.append(
                _finding(
                    code="device.offline",
                    level="warning",
                    title=f"设备「{name}」已 {float(offline_hours):g} 小时无上报",
                    detail=(
                        f"最近一条读数时间 {device.get('last_reading_at') or '未知'}，"
                        f"超过 {limit:g} 小时。"
                    ),
                    suggestion=(
                        "检查供电、网络与连接器/Webhook 配置；"
                        "恢复前不要把该设备数据当作实时依据。"
                    ),
                    evidence=[
                        {
                            "kind": "device",
                            "device_id": device.get("id"),
                            "device_name": name,
                            "last_reading_at": device.get("last_reading_at"),
                        }
                    ],
                    dedupe_key=f"device.offline:{device.get('id')}",
                    actions=[
                        _action(
                            "alert",
                            f"设备离线：{name}",
                            detail=f"超过 {float(offline_hours):g} 小时无读数。",
                            payload={"device_id": device.get("id")},
                        ),
                        _action(
                            "work_order",
                            f"设备维护：{name} 长时间无上报",
                            detail="检查供电与网络，核对连接器轮询日志或 Webhook 配置。",
                            payload={
                                "device_id": device.get("id"),
                                "priority": WORK_PRIORITY["warning"],
                            },
                        ),
                    ],
                )
            )

    # 5) 没有地块但有设备 → 建议建档（不自动建，避免凭空造出地块）
    devices = evidence.get("devices") or []
    plots = evidence.get("plots") or []
    if devices and not plots:
        first = devices[0]
        name = str(first.get("location") or f"{first.get('name')}所在地块")
        findings.append(
            _finding(
                code="plot.missing",
                level="info",
                title="有设备但还没有地块档案",
                detail=(
                    f"本院已接入 {len(devices)} 台设备，但没有任何地块；"
                    "缺少地块与作物时，Agent 无法把预警落到具体地块，也无法自动发起诊断。"
                ),
                suggestion="确认地块名称与作物后建档，并把设备安装位置与地块对应起来。",
                evidence=[
                    {"kind": "device", "device_id": first.get("id"), "name": first.get("name")}
                ],
                dedupe_key="plot.missing",
                actions=[
                    _action(
                        "plot",
                        f"建议建档：{name}",
                        detail="按设备安装位置建议的地块名，作物与面积需人工补充。",
                        payload={
                            "name": name,
                            "crop": "",
                            "notes": "由 Agent 依据设备接入情况建议建档",
                        },
                    )
                ],
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 天气预报规则
# ---------------------------------------------------------------------------


def _weather_rules(
    evidence: dict[str, Any], thresholds: dict[str, float], today: date
) -> list[dict[str, Any]]:
    weather = evidence.get("weather") or {}
    forecast = weather.get("forecast") or {}
    if not forecast:
        return []
    day = today.isoformat()
    next24 = forecast.get("next_24h") or {}
    next48 = forecast.get("next_48h") or {}
    rain = next24.get("rain_mm")
    wind = next24.get("wind_max_ms")
    temp_min = next48.get("temp_min")
    temp_max = next24.get("temp_max")
    findings: list[dict[str, Any]] = []

    if isinstance(rain, int | float) and float(rain) >= float(
        thresholds.get("rain_no_spray_mm") or 10.0
    ):
        findings.append(
            _finding(
                code="weather.rain",
                level="warning",
                title=f"未来 24 小时降雨 {float(rain):g} mm，不宜施药/追肥",
                detail=(
                    f"{weather.get('provider') or '天气预报'} 未来 24 小时累计降雨 "
                    f"{float(rain):g} mm，超过作业阈值 "
                    f"{float(thresholds.get('rain_no_spray_mm') or 10):g} mm。"
                ),
                suggestion="把施药/追肥调整到降雨前 6 小时以外或雨后再安排；已排期的作业请复核。",
                evidence=[{"kind": "forecast", "window": "next_24h", "rain_mm": float(rain)}],
                dedupe_key=f"weather.rain:{day}",
                actions=[
                    _action(
                        "alert",
                        "作业窗口提醒：未来 24 小时有雨",
                        detail=f"预报累计降雨 {float(rain):g} mm，不宜施药/追肥。",
                        payload={"rain_mm": float(rain)},
                    )
                ],
            )
        )

    if isinstance(wind, int | float) and float(wind) >= float(
        thresholds.get("wind_no_spray_ms") or 10.0
    ):
        findings.append(
            _finding(
                code="weather.wind",
                level="watch",
                title=f"未来 24 小时最大风速 {float(wind):g} m/s",
                detail=(
                    f"超过施药飘移阈值 {float(thresholds.get('wind_no_spray_ms') or 10):g} m/s，"
                    "喷施易飘移。"
                ),
                suggestion="避开大风时段施药；必要时改用其他施药方式并做好周边敏感区防护。",
                evidence=[{"kind": "forecast", "window": "next_24h", "wind_max_ms": float(wind)}],
                dedupe_key=f"weather.wind:{day}",
                actions=[
                    _action(
                        "alert",
                        "作业窗口提醒：未来 24 小时有大风",
                        detail=f"最大风速 {float(wind):g} m/s。",
                        payload={"wind_max_ms": float(wind)},
                    )
                ],
            )
        )

    drop = None
    latest_temp = None
    for device in evidence.get("devices") or []:
        value = ((device.get("readings") or {}).get("temp") or {}).get("value")
        if isinstance(value, int | float):
            latest_temp = float(value)
            break
    if isinstance(temp_min, int | float) and isinstance(latest_temp, float):
        drop = latest_temp - float(temp_min)

    cold = (
        isinstance(temp_min, int | float)
        and float(temp_min) <= float(thresholds.get("temp_min_c") or 3.0)
    ) or (drop is not None and drop >= float(thresholds.get("temp_drop_c") or 8.0))
    if cold:
        level = (
            "critical" if isinstance(temp_min, int | float) and float(temp_min) <= 0 else "warning"
        )
        parts = []
        if isinstance(temp_min, int | float):
            parts.append(f"未来 48 小时最低 {float(temp_min):g}℃")
        if drop is not None:
            parts.append(f"较当前降温 {drop:g}℃")
        findings.append(
            _finding(
                code="weather.cold",
                level=level,
                title="低温/强降温风险：" + "、".join(parts),
                detail=(
                    "；".join(parts) + "，触发阈值（最低 "
                    f"{float(thresholds.get('temp_min_c') or 3):g}℃ 或降温 "
                    f"{float(thresholds.get('temp_drop_c') or 8):g}℃）。"
                ),
                suggestion="提前做好防冻（覆盖、熏烟、灌水调温按当地规范），并检查棚室设施安全。",
                evidence=[
                    {"kind": "forecast", "window": "next_48h", "temp_min": temp_min},
                    {"kind": "reading", "metric": "temp", "value": latest_temp},
                ],
                dedupe_key=f"weather.cold:{day}",
                actions=[
                    _action(
                        "alert",
                        "低温预警：" + "、".join(parts),
                        detail="请提前安排防冻措施。",
                        payload={"temp_min": temp_min, "drop_c": round(drop, 3) if drop else None},
                    ),
                    _action(
                        "work_order",
                        "防冻准备：" + "、".join(parts),
                        detail="检查防冻物资与设施，按作物与设施类型落实保温措施。",
                        payload={"priority": WORK_PRIORITY[level]},
                    ),
                ],
            )
        )

    if isinstance(temp_max, int | float) and float(temp_max) >= float(
        thresholds.get("temp_max_c") or 35.0
    ):
        findings.append(
            _finding(
                code="weather.heat",
                level="warning",
                title=f"未来 24 小时最高 {float(temp_max):g}℃，高温风险",
                detail=(
                    f"超过高温阈值 {float(thresholds.get('temp_max_c') or 35):g}℃，"
                    "需关注灌溉与设施通风。"
                ),
                suggestion="结合墒情安排早晚灌溉，棚室加强通风遮阳；避免正午高温时段作业。",
                evidence=[{"kind": "forecast", "window": "next_24h", "temp_max": float(temp_max)}],
                dedupe_key=f"weather.heat:{day}",
                actions=[
                    _action(
                        "alert",
                        "高温预警：未来 24 小时最高 " + f"{float(temp_max):g}℃",
                        detail="关注灌溉与设施通风。",
                        payload={"temp_max": float(temp_max)},
                    ),
                    _action(
                        "work_order",
                        "高温应对：灌溉与通风核查",
                        detail="结合墒情安排早晚灌溉，棚室加强通风遮阳。",
                        payload={"priority": WORK_PRIORITY["warning"]},
                    ),
                ],
            )
        )
    return findings


# ---------------------------------------------------------------------------
# 药剂与台账规则
# ---------------------------------------------------------------------------


def _match_product(products: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    for product in products:
        name = str(product.get("name") or "").strip()
        if name and name in text:
            return product
    return None


def _cycles_by_plot(evidence: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for cycle in evidence.get("cycles") or []:
        grouped.setdefault(str(cycle.get("plot_id")), []).append(cycle)
    return grouped


def _pesticide_rules(evidence: dict[str, Any], today: date) -> list[dict[str, Any]]:
    products = [p for p in (evidence.get("pesticides") or []) if p.get("status") == "active"]
    cycles = _cycles_by_plot(evidence)
    findings: list[dict[str, Any]] = []
    for record in evidence.get("records") or []:
        if record.get("kind") != "pesticide":
            continue
        text = f"{record.get('title') or ''} {record.get('content') or ''}".strip()
        if not text:
            continue
        occurred_on = record.get("occurred_on")
        try:
            applied_on = (
                occurred_on
                if isinstance(occurred_on, date)
                else date.fromisoformat(str(occurred_on)[:10])
            )
        except (TypeError, ValueError):
            continue
        product = _match_product(products, text)
        if product is None:
            name = _guess_product_name(text)
            if not name:
                continue
            findings.append(
                _finding(
                    code="pesticide.unregistered",
                    level="watch",
                    title=f"用药记录里的药剂「{name}」未在药剂库登记",
                    detail=(
                        f"{applied_on.isoformat()} 的用药记录提到「{name}」，"
                        "药剂库中没有同名登记，无法自动核算安全间隔期。"
                    ),
                    suggestion="补登药剂的有效成分、登记作物与安全间隔期后再安排采收；不要按未登记药剂估算安全期。",
                    evidence=[
                        {
                            "kind": "record",
                            "record_id": record.get("id"),
                            "occurred_on": applied_on.isoformat(),
                        }
                    ],
                    dedupe_key=f"pesticide.unregistered:{name}",
                    actions=[
                        _action(
                            "pesticide",
                            f"待登记药剂：{name}",
                            detail="只按记录里的名称建草稿，有效成分与安全间隔期需人工补充。",
                            payload={"name": name, "record_id": record.get("id")},
                        )
                    ],
                )
            )
            continue
        interval = int(product.get("pre_harvest_interval_days") or 0)
        if interval <= 0:
            continue
        earliest = applied_on + timedelta(days=interval)
        plot_id = str(record.get("plot_id") or "")
        harvest_dates = [
            c.get("expected_harvest_on")
            for c in cycles.get(plot_id) or []
            if c.get("expected_harvest_on")
        ]
        if not harvest_dates:
            continue
        harvest = min(
            date.fromisoformat(str(h)[:10]) if not isinstance(h, date) else h for h in harvest_dates
        )
        if earliest <= harvest:
            continue
        findings.append(
            _finding(
                code="pesticide.phi",
                level="critical",
                title=(
                    f"采收早于安全间隔期：{product.get('name')} 最早可采 "
                    f"{earliest.isoformat()}，计划采收 {harvest.isoformat()}"
                ),
                detail=(
                    f"{applied_on.isoformat()} 使用「{product.get('name')}」，"
                    f"安全间隔期 {interval} 天；计划采收日期 "
                    f"{harvest.isoformat()} 早于最早可采日期 "
                    f"{earliest.isoformat()}，相差 {(earliest - harvest).days} 天。"
                ),
                suggestion=(
                    "推迟采收或按当地规定调整用药方案；"
                    "采收前请按登记标签与当地监管要求再次确认。"
                ),
                evidence=[
                    {
                        "kind": "record",
                        "record_id": record.get("id"),
                        "occurred_on": applied_on.isoformat(),
                        "product_id": product.get("id"),
                        "pre_harvest_interval_days": interval,
                    },
                    {
                        "kind": "cycle",
                        "plot_id": plot_id,
                        "expected_harvest_on": harvest.isoformat(),
                    },
                ],
                dedupe_key=f"pesticide.phi:{record.get('id')}",
                actions=[
                    _action(
                        "alert",
                        f"采收安全间隔期不足：{product.get('name')}",
                        detail=f"最早可采 {earliest.isoformat()}，计划采收 {harvest.isoformat()}。",
                        payload={"plot_id": plot_id, "product_id": product.get("id")},
                    ),
                    _action(
                        "work_order",
                        f"采收计划复核：{product.get('name')} 安全间隔期",
                        detail=(
                            f"推迟采收至 {earliest.isoformat()} 之后，或按当地规定调整用药方案。"
                        ),
                        payload={
                            "plot_id": plot_id,
                            "priority": WORK_PRIORITY["critical"],
                            "due_on": earliest.isoformat(),
                        },
                    ),
                ],
            )
        )
    return findings


_PRODUCT_SUFFIXES = (
    "可湿性粉剂",
    "水分散粒剂",
    "悬浮剂",
    "微乳剂",
    "水乳剂",
    "乳油",
    "水剂",
    "粉剂",
    "颗粒剂",
    "种衣剂",
)


def _guess_product_name(text: str) -> str:
    """从用药记录里取一个「像药剂名」的片段；取不到就返回空，不硬编。"""
    import re

    cleaned = text.replace("用药", " ").replace("施用", " ").replace("喷洒", " ")
    for suffix in _PRODUCT_SUFFIXES:
        index = cleaned.find(suffix)
        if index > 0:
            start = index
            while start > 0 and cleaned[start - 1] not in " ：:，,；;。\n\t（(":
                start -= 1
            candidate = cleaned[start : index + len(suffix)].strip()
            if 2 <= len(candidate) <= 30:
                return candidate
    match = re.search(
        r"([\u4e00-\u9fa5A-Za-z0-9·\-]{2,20})[ 　]*(?:倍液|倍|克|毫升|ml|g|kg)", cleaned
    )
    if match:
        return match.group(1)
    return ""


# ---------------------------------------------------------------------------
# 同类设备健康问题合并（避免一院 20 台设备离线就产出 20 张工单）
# ---------------------------------------------------------------------------

#: 可合并的设备健康规则 → 合并标题用词
_DEVICE_HEALTH_LABELS = {
    "device.offline": "长时间无上报",
    "device.stuck": "传感器疑似卡死",
    "device.battery": "电量偏低",
}


def _device_name(item: dict[str, Any]) -> str:
    for entry in item.get("evidence") or []:
        name = entry.get("device_name")
        if name:
            return str(name)
    title = str(item.get("title") or "")
    match = re.search(r"「(.+?)」", title)
    return match.group(1) if match else title


def _is_unbound_disease(item: dict[str, Any]) -> bool:
    """病害发现里没有任何地块绑定 → 只能告警，不能按地块自动诊断。"""
    actions = item.get("actions") or []
    return all(not (action.get("payload") or {}).get("plot_id") for action in actions)


def _coalesce_device_health(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同类设备问题命中多台设备时合并成一条，动作也合成一条。

    - 设备健康（离线/卡死/低电量）：2 台及以上合并成一条巡检工单；
    - 未绑定地块的病害风险：合并成一条告警（没有地块就不该产生多条诊断）；
    - 有地块的病害风险仍然逐地块保留，便于按地块发起诊断。
    单台设备保持原来的逐台描述。
    """
    buckets: dict[str, list[dict[str, Any]]] = {}
    others: list[dict[str, Any]] = []
    for item in findings:
        code = str(item.get("code") or "")
        if code in _DEVICE_HEALTH_LABELS:
            buckets.setdefault(code, []).append(item)
        elif code == "disease.risk" and _is_unbound_disease(item):
            buckets.setdefault("disease.risk:unbound", []).append(item)
        else:
            others.append(item)

    for key, items in buckets.items():
        if len(items) == 1:
            others.append(items[0])
            continue
        if key == "disease.risk:unbound":
            label = "出现病害易发条件"
            with_work_order = False
        else:
            label = _DEVICE_HEALTH_LABELS[key]
            with_work_order = True
        level = max(items, key=lambda i: LEVEL_ORDER.get(str(i.get("level")), 0))["level"]
        names = [_device_name(item) for item in items]
        shown = "、".join(names[:5])
        if len(names) > 5:
            shown = f"{shown} 等 {len(names)} 台"
        device_ids = sorted(
            {
                str(entry.get("device_id"))
                for item in items
                for entry in (item.get("evidence") or [])
                if entry.get("device_id")
            }
        )
        digest = hashlib.sha1(",".join(device_ids).encode()).hexdigest()[:16]
        evidence = [
            {
                "kind": "device",
                "device_id": entry.get("device_id"),
                "device_name": entry.get("device_name"),
            }
            for item in items
            for entry in (item.get("evidence") or [])
            if entry.get("device_id")
        ][:20]
        actions = [
            _action(
                "alert",
                f"{len(items)} 台设备{label}",
                detail=shown,
                payload={"device_ids": device_ids},
            )
        ]
        if with_work_order:
            actions.append(
                _action(
                    "work_order",
                    f"设备巡检：{len(items)} 台设备{label}",
                    detail=f"{shown}。逐一检查供电、网络、探头与安装位置。",
                    payload={
                        "device_ids": device_ids,
                        "priority": WORK_PRIORITY.get(level, "normal"),
                    },
                )
            )
        others.append(
            _finding(
                code="disease.risk" if key.startswith("disease.risk") else key,
                level=level,
                title=f"{len(items)} 台设备{label}",
                detail=f"{shown}。",
                suggestion=str(items[0].get("suggestion") or ""),
                evidence=evidence,
                dedupe_key=f"{key}:group:{digest}",
                actions=actions,
            )
        )
    return others


# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------


def evaluate(
    evidence: dict[str, Any],
    thresholds: dict[str, Any] | None,
    *,
    now: datetime,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """跑全部规则，按等级降序去重后返回发现列表。"""
    limits = {**DEFAULT_THRESHOLDS}
    for key, value in (thresholds or {}).items():
        try:
            limits[key] = float(value)
        except (TypeError, ValueError):
            continue
    day = today or now.date()
    findings = _coalesce_device_health(
        [
            *_device_rules(evidence, limits),
            *_weather_rules(evidence, limits, day),
            *_pesticide_rules(evidence, day),
        ]
    )
    best: dict[str, dict[str, Any]] = {}
    for item in findings:
        key = str(item.get("dedupe_key") or item.get("code"))
        current = best.get(key)
        if current is None or LEVEL_ORDER.get(item["level"], 0) > LEVEL_ORDER.get(
            current["level"], 0
        ):
            best[key] = item
    return sorted(
        best.values(),
        key=lambda item: (-LEVEL_ORDER.get(item["level"], 0), str(item.get("code"))),
    )


def overall_level(findings: list[dict[str, Any]]) -> str:
    level = "info"
    for item in findings:
        if LEVEL_ORDER.get(str(item.get("level")), 0) > LEVEL_ORDER[level]:
            level = str(item["level"])
    return level


def rule_summary(findings: list[dict[str, Any]], *, device_count: int) -> str:
    if not findings:
        return (
            f"本轮检查了 {device_count} 台设备的读数、天气与台账，未发现需要自动处理的异常。"
            if device_count
            else (
                "本院暂无启用设备，也没有可用的天气或台账证据，本轮不产出预警。"
                "请先接入设备或补录地块与农事记录。"
            )
        )
    top = "；".join(str(item.get("title")) for item in findings[:3])
    more = "" if len(findings) <= 3 else f"；另有 {len(findings) - 3} 条。"
    return f"本轮发现 {len(findings)} 条需要关注：{top}{more}"


__all__ = [
    "ACTION_LABELS",
    "_coalesce_device_health",
    "DEFAULT_THRESHOLDS",
    "RULE_LABELS",
    "evaluate",
    "overall_level",
    "rule_summary",
]
