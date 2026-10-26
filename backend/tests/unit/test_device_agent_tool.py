"""sensor.query 工具单元测试：概况/模糊设备/中文指标/窗口/跨小院/规则评估。

核心函数 query_sensor 直接注入内存库会话（与 API 测试共用 conftest 内存库）；
另有一条 tool_registry.execute 全链路用例（schema 校验 + 审计），用
monkeypatch 把工具内部会话通道 _open_session 换绑到同一内存库。
"""

from datetime import UTC, datetime, timedelta

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.devices import agent_tool
from app.modules.devices.agent_tool import (
    SENSOR_QUERY_INPUT_SCHEMA,
    query_sensor,
    register_sensor_query_tool,
)
from app.modules.devices.models import Device, DeviceAlertRule, Reading
from app.modules.tools.registry import BUILTIN_TOOLS, tool_registry

YARD_A = "t-sensor-a"
YARD_B = "t-sensor-b"


def _device(db, yard_id: str, name: str) -> Device:
    dev = Device(yard_id=yard_id, name=name, source_type="webhook")
    db.add(dev)
    db.flush()
    return dev


def _reading(
    db,
    dev: Device,
    metric: str,
    value: float | None,
    *,
    hours_ago: float = 1.0,
    text: str | None = None,
    unit: str | None = None,
) -> Reading:
    r = Reading(
        yard_id=dev.yard_id,
        device_id=dev.id,
        metric=metric,
        value=value,
        text=text,
        unit=unit,
        collected_at=datetime.now(UTC) - timedelta(hours=hours_ago),
    )
    db.add(r)
    return r


@pytest.fixture()
def env(db_session):
    """两个小院：A 有气象站+棚室两个设备与读数，B 有同名设备与自己的读数。"""
    ws = _device(db_session, YARD_A, "一号气象站 weather-A")
    gh = _device(db_session, YARD_A, "东棚温室 controller")
    _reading(db_session, ws, "temp", 20.0, hours_ago=100)  # 默认 24h 窗口外
    _reading(db_session, ws, "temp", 25.0, hours_ago=6)
    _reading(db_session, ws, "temp", 35.0, hours_ago=2)
    _reading(db_session, ws, "humidity", 61.0, hours_ago=3)
    _reading(db_session, ws, "wind_dir", None, hours_ago=3, text="NE")
    _reading(db_session, gh, "soil_moisture_20", 22.5, hours_ago=1)
    other = _device(db_session, YARD_B, "一号气象站 weather-A")
    _reading(db_session, other, "temp", 99.9, hours_ago=1)
    db_session.commit()
    return {"ws": ws, "gh": gh, "other": other}


def _by_name(out: dict, name: str) -> dict:
    return next(d for d in out["devices"] if d["device_name"] == name)


# ---------------------------------------------------------------------------
# 概况与统计
# ---------------------------------------------------------------------------


def test_zero_param_overview(db_session, env):
    out = query_sensor(db_session, yard_id=YARD_A, params={})
    assert out["window_hours"] == 24
    assert out["device_count"] == 2
    ws = _by_name(out, "一号气象站 weather-A")
    temp = next(m for m in ws["metrics"] if m["metric"] == "temp")
    # 100 小时前的旧读数不进 24h 窗口：min=25 max=35 avg=30 样本=2
    assert temp["latest_value"] == 35.0 and temp["unit"] == "℃"
    assert (temp["min"], temp["max"], temp["avg"], temp["samples"]) == (25.0, 35.0, 30.0, 2)
    # 文本读数：无 min/max，最新值走 text
    wind = next(m for m in ws["metrics"] if m["metric"] == "wind_dir")
    assert wind["latest_text"] == "NE" and wind["samples"] == 0 and "min" not in wind
    assert "最近 24 小时" in out["summary_text"] and "一号气象站" in out["summary_text"]


def test_device_fuzzy_match_and_not_found(db_session, env):
    out = query_sensor(db_session, yard_id=YARD_A, params={"device": "WEATHER"})
    assert out["device_count"] == 1  # 大小写不敏感的子串匹配
    assert out["devices"][0]["device_name"] == "一号气象站 weather-A"

    miss = query_sensor(db_session, yard_id=YARD_A, params={"device": "不存在机"})
    assert miss["device_count"] == 0
    assert "未找到设备「不存在机」" in miss["message"]
    assert "一号气象站 weather-A" in miss["message"]  # 给出现有设备清单


def test_metric_chinese_name_and_code(db_session, env):
    # 中文名片段"湿度"：仅命中"空气湿度"（土壤水分的中文名不含"湿度"）
    out = query_sensor(db_session, yard_id=YARD_A, params={"metric": "湿度"})
    codes = {m["metric"] for d in out["devices"] for m in d["metrics"]}
    assert codes == {"humidity"}
    # "水分" 命中土壤水分系列（本小院只有 20cm 有数据）
    out_w = query_sensor(db_session, yard_id=YARD_A, params={"metric": "水分"})
    assert {m["metric"] for d in out_w["devices"] for m in d["metrics"]} == {"soil_moisture_20"}
    # 指标 code 直接命中
    out2 = query_sensor(db_session, yard_id=YARD_A, params={"metric": "temp"})
    codes2 = {m["metric"] for d in out2["devices"] for m in d["metrics"]}
    assert codes2 == {"temp"}
    # 高置信同义词归一：temperature → temp
    out3 = query_sensor(db_session, yard_id=YARD_A, params={"metric": "temperature"})
    assert {m["metric"] for d in out3["devices"] for m in d["metrics"]} == {"temp"}
    # 完全无法识别 → 给出可用清单
    bad = query_sensor(db_session, yard_id=YARD_A, params={"metric": "紫外线"})
    assert "未识别指标「紫外线」" in bad["message"] and "temp(气温)" in bad["message"]


def test_window_hours_filter(db_session, env):
    out = query_sensor(db_session, yard_id=YARD_A, params={"device": "气象站", "hours": 3})
    ws = _by_name(out, "一号气象站 weather-A")
    temp = next(m for m in ws["metrics"] if m["metric"] == "temp")
    assert (temp["min"], temp["max"], temp["samples"]) == (35.0, 35.0, 1)  # 6h 前那条也出窗
    assert out["window_hours"] == 3


def test_device_without_readings_message(db_session, env):
    out = query_sensor(db_session, yard_id=YARD_A, params={"device": "controller", "hours": 1})
    gh = _by_name(out, "东棚温室 controller")
    assert gh["metrics"] == [] and "窗口内无读数" in gh["message"]


# ---------------------------------------------------------------------------
# 小院边界
# ---------------------------------------------------------------------------


def test_cross_yard_isolation(db_session, env):
    out = query_sensor(db_session, yard_id=YARD_B, params={})
    assert out["device_count"] == 1  # 只看得到自己那台同名设备
    ws = out["devices"][0]
    temp = next(m for m in ws["metrics"] if m["metric"] == "temp")
    assert temp["latest_value"] == 99.9  # B 小院自己的数据
    # A 小院视角永远看不到 99.9
    out_a = query_sensor(db_session, yard_id=YARD_A, params={"metric": "temp"})
    vals = [m["latest_value"] for d in out_a["devices"] for m in d["metrics"]]
    assert 99.9 not in vals


# ---------------------------------------------------------------------------
# 规则命中评估
# ---------------------------------------------------------------------------


def test_alert_rule_evaluation(db_session, env):
    gt_rule = DeviceAlertRule(yard_id=YARD_A, metric="temp", op="gt", threshold=30, enabled=True)
    lt_rule = DeviceAlertRule(
        yard_id=YARD_A, metric="humidity", op="lt", threshold=70, enabled=True
    )
    disabled = DeviceAlertRule(yard_id=YARD_A, metric="temp", op="gt", threshold=1, enabled=False)
    scoped_other = DeviceAlertRule(
        yard_id=YARD_A,
        metric="temp",
        op="gt",
        threshold=1,
        enabled=True,
        device_id=env["gh"].id,  # 棚室没有 temp 读数 → 恒 0 命中
    )
    db_session.add_all([gt_rule, lt_rule, disabled, scoped_other])
    db_session.commit()

    out = query_sensor(db_session, yard_id=YARD_A, params={"alert_rule": True})
    rules = {r["rule_id"]: r for r in out["rules"]}
    assert disabled.id not in rules  # 停用规则不参与评估
    assert rules[gt_rule.id]["hit_count"] == 1  # ws：35>30；25 不命中；旧点出窗
    assert rules[gt_rule.id]["latest_hit"]["value"] == 35.0
    assert rules[gt_rule.id]["latest_hit"]["device_name"] == "一号气象站 weather-A"
    assert rules[lt_rule.id]["hit_count"] == 1  # humidity 61 < 70
    assert rules[scoped_other.id]["hit_count"] == 0
    assert "规则命中评估：" in out["summary_text"]
    assert "窗口内命中 1 点" in out["summary_text"]

    # alert_rule 默认 False：不输出 rules 段
    assert "rules" not in query_sensor(db_session, yard_id=YARD_A, params={})


# ---------------------------------------------------------------------------
# 注册与 execute 全链路
# ---------------------------------------------------------------------------


def test_registration_and_builtin_schema_parity():
    tool_def = register_sensor_query_tool()
    assert tool_def is not None
    assert tool_registry.get("sensor.query").code == "sensor.query"
    # 防漂移：注册表实现与 BUILTIN 元数据行的入参 schema 必须一致
    builtin = next(t for t in BUILTIN_TOOLS if t["code"] == "sensor.query")
    assert builtin["input_schema_json"] == SENSOR_QUERY_INPUT_SCHEMA
    # 种子低风险口径：read_only 且无副作用
    assert tool_def.risk_level == "read_only" and tool_def.side_effect is False


@pytest.mark.asyncio
async def test_execute_via_registry_validates_and_audits(db_session, env, monkeypatch):
    from app.modules.tools.registry import tool_registry

    register_sensor_query_tool()
    monkeypatch.setattr(agent_tool, "_open_session", lambda: db_session)
    ctx = YardContext(
        yard_id=YARD_A, user_id="u-sensor", roles=["operator"], request_id="req-sensor"
    )
    result = await tool_registry.execute(
        db_session, "sensor.query", {"device": "气象站", "hours": 6}, ctx, "req-sensor"
    )
    assert result["devices"][0]["device_name"] == "一号气象站 weather-A"

    # 非法参数（hours=0）→ TOOL_VALIDATION_FAILED 400（registry jsonschema 校验）
    with pytest.raises(AppError) as e:
        await tool_registry.execute(db_session, "sensor.query", {"hours": 0}, ctx, "req-sensor")
    assert e.value.code == "TOOL_VALIDATION_FAILED" and e.value.http_status == 400
