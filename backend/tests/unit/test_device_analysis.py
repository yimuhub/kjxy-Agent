"""设备分析规则层单元测试（v3.23）：聚合、质量、阈值/趋势/异常/组合与去重。"""

from datetime import UTC, datetime, timedelta

from app.modules.devices import analysis


def _series(values, start, *, step_hours=1, metric="temp"):
    t0 = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
    return {
        metric: [(t0 + timedelta(hours=i * step_hours), float(v)) for i, v in enumerate(values)]
    }


def test_summarize_basic_stats():
    summary = analysis.summarize("temp", _series([20, 24, 22], None)["temp"])
    assert summary is not None
    assert summary.points == 3
    assert summary.first == 20 and summary.last == 22
    assert round(summary.mean, 1) == 22.0
    assert summary.minimum == 20 and summary.maximum == 24
    assert round(summary.to_dict()["change"], 1) == 2.0


def test_hourly_buckets_group_and_sum():
    t0 = datetime(2026, 10, 29, 0, 10, tzinfo=UTC)
    rows = analysis.hourly_buckets([(t0, 0.2), (t0 + timedelta(minutes=20), 0.3)])
    assert len(rows) == 1
    assert rows[0]["count"] == 2
    assert rows[0]["sum"] == 0.5


def test_quality_detects_stuck_out_of_range_and_low_battery():
    t0 = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
    series = {
        "temp": [(t0 + timedelta(hours=i), 22.0) for i in range(6)],
        "humidity": [(t0 + timedelta(hours=i), 120.0) for i in range(6)],
        "battery": [(t0, 15.0)],
    }
    quality = analysis.assess_quality(series, window_from=t0, window_to=t0 + timedelta(hours=6))
    assert quality["stuck"] and quality["stuck"][0]["metric"] == "temp"
    assert quality["out_of_range"] and quality["out_of_range"][0]["metric"] == "humidity"
    assert quality["battery"] == 15.0
    assert quality["score"] < 100
    assert any("卡死" in note for note in quality["notes"])


def test_threshold_warning_and_critical_split():
    t0 = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
    rule = [{"id": "r1", "metric": "temp", "op": "gt", "threshold": 30, "notify_note": ""}]
    warn = analysis.detect_thresholds({"temp": [(t0, 34.0)]}, rule)
    crit = analysis.detect_thresholds({"temp": [(t0, 40.0)]}, rule)
    assert warn and warn[0]["level"] == "warning"
    assert crit and crit[0]["level"] == "critical"
    assert "高于阈值" in crit[0]["title"]
    # 未越限不产生发现
    assert analysis.detect_thresholds({"temp": [(t0, 20.0)]}, rule) == []


def test_trend_soil_moisture_drop_is_warning():
    points = [(datetime(2026, 10, 29, i, tzinfo=UTC), 40.0 - i) for i in range(8)]
    findings = analysis.detect_trends({"soil_moisture_20": points})
    assert findings and findings[0]["rule"] == "trend"
    assert findings[0]["level"] == "warning"
    assert "下降" in findings[0]["title"]


def test_anomaly_needs_enough_points_and_zscore():
    flat = [(datetime(2026, 10, 29, i, tzinfo=UTC), 20.0) for i in range(9)]
    flat.append((datetime(2026, 10, 29, 9, tzinfo=UTC), 35.0))
    findings = analysis.detect_anomalies({"temp": flat})
    assert findings and findings[0]["code"] == "anomaly:temp"
    assert analysis.detect_anomalies({"temp": flat[:5]}) == []


def test_combo_irrigation_requires_dry_rain():
    t0 = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
    series = {
        "soil_moisture_20": [(t0 + timedelta(hours=i), 30.0 - i) for i in range(6)],
        "rain": [(t0 + timedelta(hours=i), 0.0) for i in range(6)],
    }
    findings = analysis.detect_combos(series)
    assert any(item["code"] == "combo:irrigation" for item in findings)
    wet = {**series, "rain": [(t0, 20.0)]}
    assert not any(item["code"] == "combo:irrigation" for item in analysis.detect_combos(wet))


def test_combo_disease_and_pest_window():
    t0 = datetime(2026, 10, 29, 0, 0, tzinfo=UTC)
    series = {
        "humidity": [(t0, 90.0)],
        "temp": [(t0, 14.0)],
        "light": [(t0, 2000.0)],
        "pest_count": [
            (t0, 10.0),
            (t0 + timedelta(hours=1), 14.0),
            (t0 + timedelta(hours=2), 20.0),
        ],
    }
    codes = {item["code"] for item in analysis.detect_combos(series)}
    assert {"combo:disease_risk", "combo:pest_window"} <= codes


def test_overall_level_and_dedupe_keep_highest():
    findings = [
        {"code": "a", "level": "info"},
        {"code": "a", "level": "warning"},
        {"code": "b", "level": "watch"},
    ]
    merged = analysis.dedupe(findings)
    assert analysis.overall_level(merged) == "warning"
    assert [item["level"] for item in merged if item["code"] == "a"] == ["warning"]
    assert analysis.overall_level([]) == "info"
