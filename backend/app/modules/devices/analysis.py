"""设备读数分析：聚合、数据质量与规则发现（v3.23）。

设计口径（与方案 §1.6 一致）：

- **只算不存原文**：输入是窗口内的读数点，输出是统计摘要 + 发现列表，不复制原文；
- **规则先行、确定性可复核**：阈值 / 趋势 / 异常(z-score) / 卡死 / 质量 / 农艺组合，
  每条发现都带具体数值与时间，能追溯到点；
- **诚实边界**：点数太少或覆盖太低时返回 ``insufficient``，不产出结论、不让模型编；
- **不做控制、不做因果断言**：建议只提示「需要人工确认」，用药类建议仍走专家复核口径。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.modules.devices.metrics import METRICS

#: 参与分析的指标（数值型）→ 合理量程；越界视为数据质量问题
RANGES: dict[str, tuple[float, float]] = {
    "temp": (-40.0, 60.0),
    "humidity": (0.0, 100.0),
    "rain": (0.0, 300.0),
    "wind_speed": (0.0, 75.0),
    "wind_dir": (0.0, 360.0),
    "light": (0.0, 200_000.0),
    "co2": (0.0, 10_000.0),
    "soil_moisture_10": (0.0, 100.0),
    "soil_moisture_20": (0.0, 100.0),
    "soil_moisture_40": (0.0, 100.0),
    "soil_moisture_60": (0.0, 100.0),
    "soil_temp": (-20.0, 70.0),
    "soil_ec": (0.0, 20_000.0),
    "soil_ph": (0.0, 14.0),
    "water_level": (0.0, 1000.0),
    "pest_count": (0.0, 100_000.0),
    "battery": (0.0, 100.0),
}

#: 趋势分析的关注指标与「窗口内变化量」门槛（百分点 / 单位）
TREND_THRESHOLDS: dict[str, float] = {
    "soil_moisture_10": 6.0,
    "soil_moisture_20": 6.0,
    "soil_moisture_40": 6.0,
    "soil_moisture_60": 6.0,
    "temp": 8.0,
    "soil_temp": 6.0,
    "water_level": 10.0,
    "pest_count": 10.0,
}

#: 判定「传感器卡死」的连续同值点数（按 1 位小数比较）
STUCK_POINTS = 6
#: 判定 z-score 异常所需的最少点数
ANOMALY_MIN_POINTS = 8
#: 产出结论所需的最少数值点数（所有指标合计）
MIN_POINTS = 6
#: 产出结论所需的最小窗口覆盖率（有数据小时 / 窗口小时）
MIN_COVERAGE = 0.15

LEVEL_ORDER = {"info": 0, "watch": 1, "warning": 2, "critical": 3}
LEVEL_LABELS = {"info": "提示", "watch": "关注", "warning": "预警", "critical": "严重"}

_METRIC_LABEL_FALLBACK = "指标"


def metric_label(code: str) -> str:
    info = METRICS.get(code)
    return info.name if info else code


def metric_unit(code: str) -> str:
    info = METRICS.get(code)
    return info.unit if info else ""


def overall_level(findings: list[dict[str, Any]]) -> str:
    level = "info"
    for item in findings:
        if LEVEL_ORDER.get(str(item.get("level")), 0) > LEVEL_ORDER[level]:
            level = str(item["level"])
    return level


def _fmt(value: float, unit: str = "") -> str:
    text = f"{value:g}"
    return f"{text}{unit}" if unit else text


def _at(value: datetime) -> str:
    return value.isoformat()


# ---------------------------------------------------------------------------
# 聚合与统计
# ---------------------------------------------------------------------------


@dataclass
class MetricSummary:
    metric: str
    points: int
    first_at: datetime
    last_at: datetime
    first: float
    last: float
    mean: float
    minimum: float
    maximum: float
    std: float
    total: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "metric_name": metric_label(self.metric),
            "unit": metric_unit(self.metric),
            "points": self.points,
            "first_at": _at(self.first_at),
            "last_at": _at(self.last_at),
            "first": round(self.first, 3),
            "last": round(self.last, 3),
            "mean": round(self.mean, 3),
            "min": round(self.minimum, 3),
            "max": round(self.maximum, 3),
            "std": round(self.std, 3),
            "sum": round(self.total, 3),
            "change": round(self.last - self.first, 3),
        }


def summarize(metric: str, points: list[tuple[datetime, float]]) -> MetricSummary | None:
    if not points:
        return None
    ordered = sorted(points, key=lambda item: item[0])
    values = [value for _, value in ordered]
    return MetricSummary(
        metric=metric,
        points=len(values),
        first_at=ordered[0][0],
        last_at=ordered[-1][0],
        first=values[0],
        last=values[-1],
        mean=statistics.fmean(values),
        minimum=min(values),
        maximum=max(values),
        std=statistics.pstdev(values) if len(values) > 1 else 0.0,
        total=sum(values),
    )


def hourly_buckets(points: list[tuple[datetime, float]]) -> list[dict[str, Any]]:
    """按小时聚合：数量、均值、极值与首末值；累计量（雨量）另给 sum。"""
    buckets: dict[datetime, list[float]] = {}
    for at, value in points:
        key = at.replace(minute=0, second=0, microsecond=0)
        buckets.setdefault(key, []).append(value)
    rows = []
    for key in sorted(buckets):
        values = buckets[key]
        rows.append(
            {
                "hour": key.isoformat(),
                "count": len(values),
                "mean": round(statistics.fmean(values), 3),
                "min": round(min(values), 3),
                "max": round(max(values), 3),
                "sum": round(sum(values), 3),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# 数据质量
# ---------------------------------------------------------------------------


def assess_quality(
    series: dict[str, list[tuple[datetime, float]]],
    *,
    window_from: datetime,
    window_to: datetime,
) -> dict[str, Any]:
    """数据质量：覆盖率、跳变越界、卡死、低电量。score 100 起扣，最低 0。"""
    total_points = sum(len(items) for items in series.values())
    hours = max(int((window_to - window_from).total_seconds() // 3600), 1)
    covered = {
        at.replace(minute=0, second=0, microsecond=0)
        for items in series.values()
        for at, _ in items
    }
    coverage = round(min(len(covered) / hours, 1.0), 3)

    out_of_range: list[dict[str, Any]] = []
    stuck: list[dict[str, Any]] = []
    jumps: list[dict[str, Any]] = []
    for metric, items in series.items():
        ordered = sorted(items, key=lambda item: item[0])
        low, high = RANGES.get(metric, (-1e12, 1e12))
        for at, value in ordered:
            if value < low or value > high:
                out_of_range.append(
                    {"metric": metric, "value": value, "at": _at(at), "range": [low, high]}
                )
        run_value: float | None = None
        run_len = 0
        for _, value in ordered:
            rounded = round(value, 1)
            if run_value is not None and rounded == run_value:
                run_len += 1
            else:
                run_value = rounded
                run_len = 1
            if run_len == STUCK_POINTS:
                stuck.append({"metric": metric, "value": rounded, "points": run_len})
                break
        # 相邻两点跳变超过量程 40% 记为可疑跳变（不判对错，只提示核查）
        span = max(high - low, 1e-9)
        for (_, prev), (at, cur) in zip(ordered, ordered[1:], strict=False):
            if abs(cur - prev) > span * 0.4:
                jumps.append({"metric": metric, "from": prev, "to": cur, "at": _at(at)})

    score = 100
    notes: list[str] = []
    if coverage < 0.5:
        score -= int((0.5 - coverage) * 100)
        notes.append(f"窗口覆盖率 {coverage:.0%}，存在缺测时段")
    if out_of_range:
        score -= min(len(out_of_range) * 5, 30)
        notes.append(f"{len(out_of_range)} 个读数超出合理量程")
    if stuck:
        score -= min(len(stuck) * 10, 30)
        notes.append(f"{len(stuck)} 个指标疑似传感器卡死")
    if jumps:
        notes.append(f"{len(jumps)} 处读数跳变，建议核对设备或补报")

    battery = series.get("battery") or []
    if battery:
        latest_battery = sorted(battery, key=lambda item: item[0])[-1][1]
        if latest_battery < 20:
            score -= 10
            notes.append(f"设备电量 {latest_battery:g}%，建议尽快更换/充电")

    return {
        "score": max(score, 0),
        "coverage": coverage,
        "points": total_points,
        "hours": hours,
        "covered_hours": len(covered),
        "out_of_range": out_of_range[:5],
        "stuck": stuck[:5],
        "jumps": jumps[:5],
        "battery": (sorted(battery, key=lambda item: item[0])[-1][1] if battery else None),
        "notes": notes,
    }


def has_enough_evidence(
    series: dict[str, list[tuple[datetime, float]]], quality: dict[str, Any]
) -> bool:
    points = int(quality.get("points") or 0)
    coverage = float(quality.get("coverage") or 0)
    return points >= MIN_POINTS and coverage >= MIN_COVERAGE


# ---------------------------------------------------------------------------
# 规则发现
# ---------------------------------------------------------------------------


def _finding(
    *,
    code: str,
    level: str,
    metric: str,
    title: str,
    detail: str,
    evidence: list[dict[str, Any]],
    suggestion: str,
    rule: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "level": level,
        "metric": metric,
        "metric_name": metric_label(metric),
        "title": title,
        "detail": detail,
        "evidence": evidence,
        "suggestion": suggestion,
        "rule": rule,
    }


def _evidence(at: datetime, value: float, metric: str) -> dict[str, Any]:
    return {
        "at": _at(at),
        "value": round(value, 3),
        "unit": metric_unit(metric),
    }


def detect_thresholds(
    series: dict[str, list[tuple[datetime, float]]],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """阈值规则：用窗口内最新值比对启用规则；超出 20% 以上升级为 critical。"""
    findings: list[dict[str, Any]] = []
    for rule in rules:
        metric = str(rule.get("metric") or "")
        items = series.get(metric) or []
        if not items:
            continue
        at, latest = sorted(items, key=lambda item: item[0])[-1]
        threshold = float(rule.get("threshold") or 0)
        op = str(rule.get("op") or "gt")
        hit = latest > threshold if op == "gt" else latest < threshold
        if not hit:
            continue
        unit = metric_unit(metric)
        name = metric_label(metric)
        distance = (latest - threshold) / abs(threshold) if threshold else 0.0
        level = "critical" if abs(distance) >= 0.2 else "warning"
        direction = "高于" if op == "gt" else "低于"
        note = str(rule.get("notify_note") or "").strip()
        findings.append(
            _finding(
                code=f"threshold:{metric}",
                level=level,
                metric=metric,
                title=f"{name} {_fmt(latest, unit)}，{direction}阈值 {_fmt(threshold, unit)}",
                detail=(
                    f"{at.isoformat()} 的{name}读数为 {_fmt(latest, unit)}，"
                    f"{direction}设定阈值 {_fmt(threshold, unit)}。"
                ),
                evidence=[_evidence(at, latest, metric)],
                suggestion=note or "请人工核查读数与阈值设置是否合理，必要时到现场确认。",
                rule="threshold",
            )
        )
    return findings


def detect_trends(series: dict[str, list[tuple[datetime, float]]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for metric, limit in TREND_THRESHOLDS.items():
        items = sorted(series.get(metric) or [], key=lambda item: item[0])
        if len(items) < 3:
            continue
        first_at, first = items[0]
        last_at, last = items[-1]
        delta = last - first
        if abs(delta) < limit:
            continue
        unit = metric_unit(metric)
        name = metric_label(metric)
        rising = delta > 0
        findings.append(
            _finding(
                code=f"trend:{metric}",
                level="warning" if metric.startswith("soil_moisture") else "watch",
                metric=metric,
                title=f"{name}持续{'上升' if rising else '下降'} {_fmt(abs(delta), unit)}",
                detail=(
                    f"从 {first_at.isoformat()} 的 {_fmt(first, unit)} 变化到 "
                    f"{last_at.isoformat()} 的 {_fmt(last, unit)}，"
                    f"累计{'上升' if rising else '下降'} {_fmt(abs(delta), unit)}。"
                ),
                evidence=[_evidence(first_at, first, metric), _evidence(last_at, last, metric)],
                suggestion=(
                    "土壤水分持续下降且近期无有效降雨时，请确认是否需要灌溉。"
                    if metric.startswith("soil_moisture") and not rising
                    else "请结合现场情况确认变化是否由环境或设备引起。"
                ),
                rule="trend",
            )
        )
    return findings


def detect_anomalies(series: dict[str, list[tuple[datetime, float]]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for metric, items in series.items():
        if metric in {"battery", "rain"}:
            continue
        ordered = sorted(items, key=lambda item: item[0])
        values = [value for _, value in ordered]
        if len(values) < ANOMALY_MIN_POINTS:
            continue
        mean = statistics.fmean(values)
        std = statistics.pstdev(values)
        if std <= 1e-9:
            continue
        latest_at, latest = ordered[-1]
        z = (latest - mean) / std
        if abs(z) < 3:
            continue
        unit = metric_unit(metric)
        name = metric_label(metric)
        findings.append(
            _finding(
                code=f"anomaly:{metric}",
                level="watch",
                metric=metric,
                title=f"{name}出现异常偏离（z={z:.1f}）",
                detail=(
                    f"窗口均值 {_fmt(mean, unit)}、标准差 {_fmt(std, unit)}；"
                    f"{latest_at.isoformat()} 的读数为 {_fmt(latest, unit)}。"
                ),
                evidence=[_evidence(latest_at, latest, metric)],
                suggestion="请核对是否由传感器遮挡、故障或极端天气引起。",
                rule="anomaly",
            )
        )
    return findings


def detect_quality_findings(quality: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for item in quality.get("stuck") or []:
        metric = str(item.get("metric"))
        findings.append(
            _finding(
                code=f"stuck:{metric}",
                level="watch",
                metric=metric,
                title=f"{metric_label(metric)}连续 {item.get('points')} 点不变",
                detail=(
                    f"{metric_label(metric)}连续 {item.get('points')} 次读数都是 "
                    f"{item.get('value')}，可能是传感器卡死或上报未更新。"
                ),
                evidence=[],
                suggestion="请检查设备是否在线、探头是否损坏。",
                rule="stuck",
            )
        )
    battery = quality.get("battery")
    if isinstance(battery, int | float) and battery < 20:
        findings.append(
            _finding(
                code="battery:low",
                level="watch",
                metric="battery",
                title=f"设备电量偏低（{battery:g}%）",
                detail=f"最近一次电量读数为 {battery:g}%，低于 20%。",
                evidence=[],
                suggestion="请尽快安排更换电池或充电，避免断测。",
                rule="quality",
            )
        )
    if float(quality.get("coverage") or 0) < 0.5:
        findings.append(
            _finding(
                code="quality:coverage",
                level="info",
                metric="",
                title="存在缺测时段",
                detail=(
                    f"窗口内 {quality.get('covered_hours')}/{quality.get('hours')} 小时有数据，"
                    "结论基于已采集部分。"
                ),
                evidence=[],
                suggestion="如需完整结论，请检查设备供电与网络后等待补测。",
                rule="quality",
            )
        )
    return findings


def _mean(series: dict[str, list[tuple[datetime, float]]], metric: str) -> float | None:
    items = series.get(metric) or []
    if not items:
        return None
    return statistics.fmean(value for _, value in items)


def detect_combos(series: dict[str, list[tuple[datetime, float]]]) -> list[dict[str, Any]]:
    """农艺组合：只提示风险与核查方向，不做病因断言、不直接给用药结论。"""
    findings: list[dict[str, Any]] = []
    humidity = _mean(series, "humidity")
    temp = _mean(series, "temp")
    light = _mean(series, "light")
    if humidity is not None and temp is not None and humidity >= 85 and temp <= 16:
        findings.append(
            _finding(
                code="combo:disease_risk",
                level="watch" if light is None or light < 8000 else "info",
                metric="humidity",
                title="高湿低温，病害风险上升",
                detail=(
                    f"窗口内平均空气湿度 {humidity:.0f}%、平均气温 {temp:.1f}℃"
                    + (f"、平均光照 {light:.0f} lux" if light is not None else "")
                    + "，属于棚内高湿低温组合。"
                ),
                evidence=[],
                suggestion="建议加强通风降湿、检查棚膜结露；如出现病斑，走作物诊断与专家复核。",
                rule="combo",
            )
        )

    moisture = _mean(series, "soil_moisture_20")
    moisture_metric = "soil_moisture_20"
    if moisture is None:
        moisture = _mean(series, "soil_moisture_10")
        moisture_metric = "soil_moisture_10"
    rain_items = sorted(series.get("rain") or [], key=lambda item: item[0])
    rain_total = sum(value for _, value in rain_items) if rain_items else None
    if moisture is not None and rain_total is not None and rain_total < 1:
        items = sorted(series.get(moisture_metric) or [], key=lambda item: item[0])
        if len(items) >= 3:
            drop = items[0][1] - items[-1][1]
            if drop >= 5:
                findings.append(
                    _finding(
                        code="combo:irrigation",
                        level="warning",
                        metric=moisture_metric,
                        title=f"土壤水分下降 {drop:.1f} 个百分点且无有效降雨",
                        detail=(
                            f"{metric_label(moisture_metric)}从 {items[0][1]:.1f}% 降到 "
                            f"{items[-1][1]:.1f}%，同期降雨累计 {rain_total:.1f} mm。"
                        ),
                        evidence=[
                            _evidence(items[0][0], items[0][1], moisture_metric),
                            _evidence(items[-1][0], items[-1][1], moisture_metric),
                        ],
                        suggestion="建议核查墒情与灌溉计划，确认是否需要安排灌溉。",
                        rule="combo",
                    )
                )

    pest_items = sorted(series.get("pest_count") or [], key=lambda item: item[0])
    if len(pest_items) >= 3:
        recent = [value for _, value in pest_items[-3:]]
        if recent[0] > 0 and recent[-1] >= recent[0] * 1.3 and recent[-1] - recent[0] >= 5:
            findings.append(
                _finding(
                    code="combo:pest_window",
                    level="warning",
                    metric="pest_count",
                    title=f"虫情计数连续上升（{recent[0]:g} → {recent[-1]:g}）",
                    detail=(
                        f"最近 3 次虫情计数为 {'、'.join(f'{v:g}' for v in recent)}，"
                        "呈上升趋势。"
                    ),
                    evidence=[_evidence(pest_items[-1][0], recent[-1], "pest_count")],
                    suggestion="建议布设/检查诱捕设备，确认虫口密度后再决定防治措施。",
                    rule="combo",
                )
            )
    return findings


def dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一 code 只留一条（多规则命中时保留等级最高的）。"""
    best: dict[str, dict[str, Any]] = {}
    for item in findings:
        code = str(item.get("code"))
        current = best.get(code)
        if current is None or LEVEL_ORDER.get(item["level"], 0) > LEVEL_ORDER[current["level"]]:
            best[code] = item
    return sorted(
        best.values(),
        key=lambda item: (-LEVEL_ORDER.get(item["level"], 0), str(item.get("code"))),
    )


def rule_summary(findings: list[dict[str, Any]], *, device_name: str) -> str:
    if not findings:
        return f"{device_name} 本轮未发现需要处理的异常，各项读数在正常范围内。"
    top = findings[:3]
    titles = "；".join(str(item.get("title")) for item in top)
    more = "。" if len(findings) <= 3 else f"；另有 {len(findings) - 3} 条发现。"
    return f"{device_name} 本轮发现 {len(findings)} 条：{titles}{more}"
