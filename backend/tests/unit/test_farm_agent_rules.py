"""Agent 自动执行规则单测（v3.28）：确定性、可复核、不编造。"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.modules.farm import agent_rules, weather

NOW = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 30)


def _device(**overrides):
    device = {
        "id": "dev-1",
        "name": "东一号气象站",
        "type": "weather",
        "location": "东一号田",
        "plot_id": "plot-1",
        "plot_name": "东一号田",
        "crop": "水稻",
        "readings": {},
        "rain_mm": 0.0,
        "last_reading_at": NOW.isoformat(),
        "offline_hours": 0.5,
        "analysis": None,
    }
    device.update(overrides)
    return device


def _reading(value, unit="", at=None):
    return {"value": value, "unit": unit, "at": (at or NOW).isoformat()}


def _evidence(**overrides):
    data = {
        "devices": [],
        "weather": {"forecast": None, "warnings": []},
        "plots": [
            {
                "id": "plot-1",
                "name": "东一号田",
                "crop": "水稻",
                "status": "active",
                "location": "",
            }
        ],
        "cycles": [],
        "records": [],
        "pesticides": [],
        "diagnoses": [],
        "work_orders": [],
        "warnings": [],
    }
    data.update(overrides)
    return data


def _codes(findings):
    return [item["code"] for item in findings]


def test_water_low_triggers_alert_work_order_and_record():
    device = _device(readings={"soil_moisture_20": _reading(14.5, "%")})
    findings = agent_rules.evaluate(_evidence(devices=[device]), None, now=NOW, today=TODAY)
    water = next(item for item in findings if item["code"] == "water.low")
    assert water["level"] == "warning"
    assert "14.5%" in water["title"]
    kinds = [action["kind"] for action in water["actions"]]
    assert kinds == ["alert", "work_order", "record"]
    assert water["dedupe_key"] == "water.low:dev-1"


def test_water_low_skips_when_rain_comes():
    device = _device(readings={"soil_moisture_20": _reading(12, "%")})
    evidence = _evidence(
        devices=[device],
        weather={
            "forecast": {
                "provider": "qweather",
                "next_24h": {"rain_mm": 8.0},
                "next_48h": {},
            },
            "warnings": [],
        },
    )
    assert "water.low" not in _codes(agent_rules.evaluate(evidence, None, now=NOW, today=TODAY))


def test_disease_risk_only_creates_diagnosis_when_crop_known():
    device = _device(
        readings={"humidity": _reading(92, "%"), "temp": _reading(14, "℃")},
    )
    findings = agent_rules.evaluate(_evidence(devices=[device]), None, now=NOW, today=TODAY)
    disease = next(item for item in findings if item["code"] == "disease.risk")
    assert [a["kind"] for a in disease["actions"]] == ["alert", "diagnosis"]

    blind = _device(
        readings={"humidity": _reading(92, "%"), "temp": _reading(14, "℃")},
        crop="",
        plot_id=None,
        plot_name="",
    )
    findings = agent_rules.evaluate(_evidence(devices=[blind]), None, now=NOW, today=TODAY)
    disease = next(item for item in findings if item["code"] == "disease.risk")
    assert [a["kind"] for a in disease["actions"]] == ["alert"]
    assert "未绑定地块" in disease["detail"]


def test_device_health_rules():
    device = _device(
        readings={"battery": _reading(12, "%")},
        offline_hours=9.0,
        analysis={
            "findings": [
                {
                    "code": "stuck:soil_moisture_20",
                    "level": "watch",
                    "title": "土壤水分连续 6 点不变",
                }
            ]
        },
    )
    findings = agent_rules.evaluate(_evidence(devices=[device]), None, now=NOW, today=TODAY)
    codes = _codes(findings)
    assert {"device.battery", "device.offline", "device.stuck"} <= set(codes)
    for code in ("device.battery", "device.offline", "device.stuck"):
        item = next(f for f in findings if f["code"] == code)
        assert [a["kind"] for a in item["actions"]] == ["alert", "work_order"]


def test_device_health_merges_multiple_devices_into_one_action():
    devices = [_device(id=f"dev-{i}", name=f"站点{i}", offline_hours=10.0 + i) for i in range(1, 4)]
    findings = agent_rules.evaluate(_evidence(devices=devices), None, now=NOW, today=TODAY)
    offline = [item for item in findings if item["code"] == "device.offline"]
    assert len(offline) == 1
    merged = offline[0]
    assert merged["title"] == "3 台设备长时间无上报"
    assert merged["dedupe_key"].startswith("device.offline:group:")
    assert [a["kind"] for a in merged["actions"]] == ["alert", "work_order"]
    assert merged["actions"][0]["payload"]["device_ids"] == ["dev-1", "dev-2", "dev-3"]
    assert "站点1" in merged["detail"] and "站点3" in merged["detail"]


def test_unbound_disease_risks_merge_into_single_alert():
    devices = [
        _device(
            id=f"dev-{i}",
            name=f"分析站{i}",
            readings={"humidity": _reading(92, "%"), "temp": _reading(14, "℃")},
            plot_id=None,
            plot_name="",
            crop="",
        )
        for i in range(1, 4)
    ]
    findings = agent_rules.evaluate(_evidence(devices=devices), None, now=NOW, today=TODAY)
    disease = [item for item in findings if item["code"] == "disease.risk"]
    assert len(disease) == 1
    merged = disease[0]
    assert merged["title"] == "3 台设备出现病害易发条件"
    assert [a["kind"] for a in merged["actions"]] == ["alert"]
    assert "分析站2" in merged["detail"]


def test_weather_rules_and_custom_thresholds():
    forecast = {
        "provider": "qweather",
        "next_24h": {"rain_mm": 15.0, "wind_max_ms": 12.0, "temp_max": 36.0},
        "next_48h": {"temp_min": 2.0},
    }
    device = _device(readings={"temp": _reading(12, "℃")})
    evidence = _evidence(devices=[device], weather={"forecast": forecast, "warnings": []})
    codes = set(_codes(agent_rules.evaluate(evidence, None, now=NOW, today=TODAY)))
    assert {"weather.rain", "weather.wind", "weather.cold", "weather.heat"} <= codes

    relaxed = {
        "rain_no_spray_mm": 50,
        "wind_no_spray_ms": 30,
        "temp_max_c": 45,
        "temp_min_c": -10,
        "temp_drop_c": 30,
    }
    codes = set(_codes(agent_rules.evaluate(evidence, relaxed, now=NOW, today=TODAY)))
    assert not ({"weather.rain", "weather.wind", "weather.cold", "weather.heat"} & codes)


def test_pesticide_safety_interval_conflict_is_critical():
    records = [
        {
            "id": "rec-1",
            "plot_id": "plot-1",
            "kind": "pesticide",
            "occurred_on": "2026-10-28",
            "title": "喷施 吡虫啉",
            "content": "",
        }
    ]
    products = [{"id": "p-1", "name": "吡虫啉", "pre_harvest_interval_days": 7, "status": "active"}]
    cycles = [
        {"id": "c-1", "plot_id": "plot-1", "crop": "水稻", "expected_harvest_on": "2026-11-01"}
    ]
    findings = agent_rules.evaluate(
        _evidence(records=records, pesticides=products, cycles=cycles),
        None,
        now=NOW,
        today=TODAY,
    )
    phi = next(item for item in findings if item["code"] == "pesticide.phi")
    assert phi["level"] == "critical"
    assert [a["kind"] for a in phi["actions"]] == ["alert", "work_order"]
    assert phi["actions"][1]["payload"]["priority"] == "urgent"


def test_pesticide_unregistered_suggests_draft_not_fabricated():
    records = [
        {
            "id": "rec-2",
            "plot_id": "plot-1",
            "kind": "pesticide",
            "occurred_on": "2026-10-29",
            "title": "喷施 多菌灵可湿性粉剂",
            "content": "",
        }
    ]
    findings = agent_rules.evaluate(_evidence(records=records), None, now=NOW, today=TODAY)
    item = next(f for f in findings if f["code"] == "pesticide.unregistered")
    action = item["actions"][0]
    assert action["kind"] == "pesticide"
    assert action["payload"]["name"] == "多菌灵可湿性粉剂"
    # 只按记录名称建草稿，不猜有效成分/间隔期
    assert set(action["payload"]) == {"name", "record_id"}


def test_plot_missing_when_devices_without_plots():
    evidence = _evidence(devices=[_device(plot_id=None, plot_name="")], plots=[])
    findings = agent_rules.evaluate(evidence, None, now=NOW, today=TODAY)
    item = next(f for f in findings if f["code"] == "plot.missing")
    assert item["actions"][0]["kind"] == "plot"
    assert item["actions"][0]["payload"]["name"] == "东一号田"


def test_evaluate_dedupes_and_sorts_by_level():
    low = _device(readings={"soil_moisture_20": _reading(10, "%")})
    critical = _device(
        id="dev-2",
        name="西二号站",
        plot_id="plot-2",
        plot_name="西二号田",
        readings={"soil_moisture_20": _reading(8, "%")},
    )
    evidence = _evidence(
        devices=[low, low, critical],
        weather={
            "forecast": {
                "provider": "qweather",
                "next_24h": {},
                "next_48h": {"temp_min": -2.0},
            },
            "warnings": [],
        },
    )
    findings = agent_rules.evaluate(evidence, None, now=NOW, today=TODAY)
    keys = [item["dedupe_key"] for item in findings]
    assert len(keys) == len(set(keys))
    assert findings[0]["level"] == "critical"


def test_rule_summary_is_honest_when_no_evidence():
    assert "不产出预警" in agent_rules.rule_summary([], device_count=0)
    assert "未发现" in agent_rules.rule_summary([], device_count=3)


def test_qweather_normalization_converts_wind_and_aggregates():
    hourly = {
        "hourly": [
            {"temp": "10", "humidity": "90", "precip": "1.2", "windSpeed": "36"},
            {"temp": "16", "humidity": "80", "precip": "2.8", "windSpeed": "18"},
        ]
    }
    extra = {"hourly": [{"temp": "3"}, {"temp": "20"}]}
    data = weather.normalize_qweather(hourly, extra)
    assert data["next_24h"]["rain_mm"] == 4.0
    assert data["next_24h"]["wind_max_ms"] == 10.0  # 36 km/h → 10 m/s
    assert data["next_24h"]["temp_max"] == 16.0
    assert data["next_48h"]["temp_min"] == 3.0


def test_openweather_normalization_sums_three_hour_rain():
    now = datetime.now(UTC).timestamp()
    payload = {
        "list": [
            {
                "dt": now + 3 * 3600,
                "main": {"temp": 18, "humidity": 70},
                "rain": {"3h": 2.5},
                "wind": {"speed": 4},
            },
            {
                "dt": now + 30 * 3600,
                "main": {"temp": 8, "humidity": 60},
                "rain": {"3h": 9.0},
                "wind": {"speed": 4},
            },
        ]
    }
    data = weather.normalize_openweather(payload)
    assert data["next_24h"]["rain_mm"] == 2.5
    assert data["next_24h"]["temp_max"] == 18.0
    assert data["next_48h"]["temp_min"] == 8.0
