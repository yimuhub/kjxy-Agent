"""设备接入核心单元测试：字典归一、令牌、JSONPath-lite、连接器配置加密、
告警比对去重、限频计数、连接器轮询失败自动停用。

不依赖 TestClient（API 行为见 integration/test_device_ingest_flow.py）。
"""

import os
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.fernet import Fernet

# 连接器 headers 加密复用 supplier_crypto：测试密钥（与 test_llm_suppliers 同约定，
# 必须在触发 get_fernet() 之前设置）
os.environ.setdefault("SUPPLIER_KEY_SECRET", Fernet.generate_key().decode())

from app.core.errors import AppError  # noqa: E402
from app.modules.devices import connector, service  # noqa: E402
from app.modules.devices.metrics import METRICS, normalize_code  # noqa: E402
from app.modules.devices.models import Device, DeviceAlert, DeviceAlertRule, Reading  # noqa: E402
from app.modules.devices.schemas import ConnectorConfigIn  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_rate_window():
    service._rate_window.reset()
    yield
    service._rate_window.reset()


def _device(db, yard_id="t1", name="气象站A", **kw) -> Device:
    dev = Device(
        yard_id=yard_id,
        name=name,
        source_type=kw.pop("source_type", "webhook"),
        device_type="weather",
        **kw,
    )
    db.add(dev)
    db.commit()
    return dev


# ---------------------------------------------------------------------------
# 指标字典与归一化
# ---------------------------------------------------------------------------


def test_metric_dict_contains_required_codes():
    for code in (
        "temp",
        "humidity",
        "rain",
        "wind_speed",
        "wind_dir",
        "light",
        "co2",
        "soil_moisture_10",
        "soil_moisture_20",
        "soil_moisture_40",
        "soil_moisture_60",
        "soil_temp",
        "soil_ec",
        "soil_ph",
        "water_level",
        "pest_count",
        "other",
    ):
        assert code in METRICS, code
    assert METRICS["temp"].unit == "℃"
    assert METRICS["temp"].name == "气温"


def test_normalize_code_tolerates_case_but_no_fuzzy_guess():
    assert normalize_code(" Temp ") == "temp"
    # 模糊猜测会把厂商私有字段错映射成标准指标——宁可进 other 保 raw
    # 高置信同义词已入白名单；拼写错误等模糊写法仍进 other 保 raw
    assert normalize_code("temperature") == "temp"
    assert normalize_code("tempreture") == "other"


def test_build_reading_known_other_and_text(db_session):
    dev = _device(db_session)
    r1 = service.build_reading(dev, "temp", 25.5, field_map=None, collected_at=datetime.now(UTC))
    assert r1 is not None and r1.metric == "temp" and r1.value == 25.5
    assert r1.unit == "℃" and r1.raw_json is None

    r2 = service.build_reading(
        dev, "vendor_xyz", {"a": 1}, field_map=None, collected_at=datetime.now(UTC)
    )
    assert r2 is not None and r2.metric == "other"
    assert r2.raw_json == {"vendor_field": "vendor_xyz", "raw_value": {"a": 1}}

    r3 = service.build_reading(
        dev, "wind_dir", "NE", field_map=None, collected_at=datetime.now(UTC)
    )
    assert r3 is not None and r3.metric == "wind_dir"
    assert r3.value is None and r3.text == "NE"

    r4 = service.build_reading(
        dev, "humidity", "61.2", field_map=None, collected_at=datetime.now(UTC)
    )
    assert r4 is not None and r4.value == 61.2  # 数字字符串归一为数值

    # field_map 显式映射改名：unit 覆盖 + raw 保留
    fm = {"deviceTemp": {"metric": "temp", "unit": "F"}}
    r5 = service.build_reading(dev, "deviceTemp", 77, field_map=fm, collected_at=datetime.now(UTC))
    assert r5 is not None and r5.metric == "temp" and r5.unit == "F"
    assert r5.raw_json == {"vendor_field": "deviceTemp", "raw_value": 77}


# ---------------------------------------------------------------------------
# 令牌
# ---------------------------------------------------------------------------


def test_token_issue_match_rotate(db_session):
    dev = _device(db_session)
    plain, digest = service.issue_device_token()
    assert plain and len(digest) == 64
    assert dev.token_hash != plain
    dev.token_hash = digest
    db_session.commit()
    assert service.token_matches(dev, plain) is True
    assert service.token_matches(dev, plain + "x") is False
    assert service.token_matches(dev, None) is False
    # rotate：新明文生效、旧明文失效
    new_plain, dev.token_hash = service.issue_device_token()
    db_session.commit()
    assert service.token_matches(dev, new_plain) is True
    assert service.token_matches(dev, plain) is False


# ---------------------------------------------------------------------------
# JSONPath-lite（自实现，无依赖）
# ---------------------------------------------------------------------------


def test_extract_path_dotted_and_star():
    payload = {"data": {"items": [{"t": 1}, {"t": 2}], "meta": {"n": 2}}}
    assert connector.extract_path(payload, "$.data.meta.n") == [2]
    assert connector.extract_path(payload, "data.items[0]") == []  # 不支持下标数字
    assert connector.extract_path(payload, "$.data.items[*].t") == [1, 2]
    assert connector.extract_path(payload, "$.data.items[*]") == [{"t": 1}, {"t": 2}]
    assert connector.extract_path(payload, "$") == [payload]
    assert connector.extract_path(payload, "$.nope.deep") == []


def test_record_to_values_only_declared_fields():
    record = {"deviceTemp": 21.3, "extra": 1, "sub": {"pm": 5}}
    fm = {"deviceTemp": {"metric": "temp"}, "sub.pm": {"metric": "other"}}
    assert connector.record_to_values(record, fm) == {"deviceTemp": 21.3, "sub.pm": 5}


# ---------------------------------------------------------------------------
# 连接器配置：校验 / 加密 / 掩码
# ---------------------------------------------------------------------------


def _cfg(**kw) -> ConnectorConfigIn:
    base = {
        "url": "https://vendor.example.com/api/realtime",
        "field_map": {"temp": {"metric": "temp"}},
        "interval_seconds": 300,
    }
    base.update(kw)
    return ConnectorConfigIn(**base)


def test_prepare_config_encrypts_headers_and_masks_output():
    stored = connector.prepare_config(
        _cfg(headers={"X-Api-Key": "super-secret-key-9876", "X-Yard": "abc"})
    )
    enc = stored["headers_enc"]["X-Api-Key"]
    assert enc != "super-secret-key-9876" and "super-secret" not in str(stored)
    out = connector.config_to_out(stored)
    assert out is not None
    masked = out["headers_masked"]["X-Api-Key"]
    assert "super-secret-key" not in masked and masked != enc
    assert masked.startswith("supe")  # mask 只露前后 4 位
    assert out["url"] == "https://vendor.example.com/api/realtime"
    assert out["interval_seconds"] == 300
    assert "headers_enc" not in out


def test_prepare_config_rejects_unknown_metric_and_localhost():
    with pytest.raises(AppError) as e:
        connector.prepare_config(_cfg(field_map={"t": {"metric": "temperature"}}))
    assert e.value.http_status == 400
    with pytest.raises(AppError) as e2:
        connector.prepare_config(_cfg(url="http://127.0.0.1:8080/api"))
    assert e2.value.code == "SSRF_BLOCKED"
    with pytest.raises(AppError) as e3:
        connector.prepare_config(_cfg(url="http://169.254.169.254/latest/meta-data"))
    assert e3.value.code == "SSRF_BLOCKED"


def test_ingest_values_rate_limit_window(db_session, monkeypatch):
    dev = _device(db_session)
    monkeypatch.setattr(service, "INGEST_MAX_POINTS_PER_WINDOW", 5)
    now_iso = datetime.now(UTC).isoformat()
    r1 = service.ingest_values(db_session, dev, {"temp": 1, "humidity": 2}, reported_at=now_iso)
    db_session.commit()
    assert r1["accepted"] == 2
    with pytest.raises(AppError) as e:
        service.ingest_values(
            db_session, dev, {"temp": 3, "humidity": 4, "light": 5, "co2": 6}, reported_at=now_iso
        )
    assert e.value.http_status == 429
    # 已被禁用：403
    monkeypatch.setattr(service, "INGEST_MAX_POINTS_PER_WINDOW", 1000)
    dev.enabled = False
    with pytest.raises(AppError) as e2:
        service.ingest_values(db_session, dev, {"temp": 1}, reported_at=now_iso)
    assert e2.value.http_status == 403


# ---------------------------------------------------------------------------
# 告警：触发 + 30 分钟去重
# ---------------------------------------------------------------------------


def test_alert_trigger_and_dedupe(db_session):
    dev = _device(db_session)
    rule = DeviceAlertRule(
        yard_id="t1", device_id=dev.id, metric="temp", op="gt", threshold=30.0, enabled=True
    )
    db_session.add(rule)
    db_session.commit()
    now_iso = datetime.now(UTC).isoformat()

    res = service.ingest_values(db_session, dev, {"temp": 35}, reported_at=now_iso)
    db_session.commit()
    assert res["alerts_triggered"] == 1
    # 同 rule+device：30 分钟内第二次不再产生
    res2 = service.ingest_values(db_session, dev, {"temp": 40}, reported_at=now_iso)
    db_session.commit()
    assert res2["alerts_triggered"] == 0
    assert db_session.query(DeviceAlert).count() == 1
    alert = db_session.query(DeviceAlert).first()
    assert alert is not None and alert.rule_id == rule.id
    assert alert.notified_text and "气温" in alert.notified_text
    # 未超阈值不触发
    res3 = service.ingest_values(db_session, dev, {"temp": 20}, reported_at=now_iso)
    db_session.commit()
    assert res3["alerts_triggered"] == 0


def test_alert_rule_scope_device_vs_yard(db_session):
    dev1 = _device(db_session, name="A")
    dev2 = _device(db_session, name="B")
    db_session.add(
        DeviceAlertRule(yard_id="t1", device_id=dev1.id, metric="temp", op="lt", threshold=5.0)
    )
    db_session.add(
        DeviceAlertRule(yard_id="t1", device_id=None, metric="pest_count", op="gt", threshold=50)
    )
    db_session.commit()
    now_iso = datetime.now(UTC).isoformat()
    res = service.ingest_values(
        db_session, dev2, {"temp": 1, "pest_count": 90}, reported_at=now_iso
    )
    db_session.commit()
    assert res["alerts_triggered"] == 1  # 只有小院级虫情规则命中 dev2


# ---------------------------------------------------------------------------
# 连接器拉取与轮询（无网络：httpx MockTransport + 故障注入）
# ---------------------------------------------------------------------------


def _connector_device(db, **cfg_over) -> Device:
    stored = connector.prepare_config(
        _cfg(
            method="GET",
            json_path="$.data.items[*]",
            field_map={
                "deviceTemp": {"metric": "temp"},
                "deviceHum": {"metric": "humidity"},
            },
            **cfg_over,
        )
    )
    return _device(db, name="拉取设备", source_type="connector", connector_config_json=stored)


class _FakeHttpx:
    """替身：httpx.Client(...) 上下文管理器返回挂 MockTransport 的真实 Client。"""

    def __init__(self, handler):
        self._handler = handler

    def Client(self, **kwargs):  # noqa: N802 —— 对齐 httpx 命名
        import httpx

        return httpx.Client(transport=httpx.MockTransport(self._handler))


def test_fetch_and_ingest_normalizes_records(db_session, monkeypatch):
    import httpx

    dev = _connector_device(db_session)
    payload = {
        "data": {
            "items": [
                {"deviceTemp": 22.5, "deviceHum": 55, "ts": "2026-10-19T08:00:00Z"},
                {"deviceTemp": 23.1, "deviceHum": 53, "ts": "2026-10-19T08:30:00Z"},
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-Yard") == "abc"
        return httpx.Response(200, json=payload)

    # 用一个假 httpx 模块属性注入 transport（URL 保持公网形态，DNS 在离线环境放行）
    stored = dict(dev.connector_config_json or {})
    from app.modules.llm.supplier_crypto import encrypt_api_key

    stored["headers_enc"] = {"X-Yard": encrypt_api_key("abc")}
    # time_field 补进配置
    stored["time_field"] = "ts"
    dev.connector_config_json = stored
    real_httpx = connector.httpx
    connector.httpx = _FakeHttpx(handler)  # type: ignore[attr-defined]
    try:
        stats = connector.fetch_and_ingest(db_session, dev)
        db_session.commit()
    finally:
        connector.httpx = real_httpx  # type: ignore[attr-defined]
    assert stats["records"] == 2 and stats["accepted"] == 4
    rows = list(
        db_session.query(Reading)
        .filter(Reading.device_id == dev.id)
        .order_by(Reading.collected_at)
        .all()
    )
    assert {r.metric for r in rows} == {"temp", "humidity"}
    # 记录内时间戳生效（首条应为配置里的 ts，而非"现在"）
    assert rows[0].collected_at.strftime("%Y-%m-%dT%H:%M") == "2026-10-19T08:00"


def test_poll_auto_disables_after_five_failures(db_session, monkeypatch):
    dev = _connector_device(db_session)
    calls = {"n": 0}

    def boom(db, device, *, now=None):  # noqa: ARG001
        calls["n"] += 1
        raise connector.ConnectorError("DNS 解析失败")

    monkeypatch.setattr(connector, "fetch_and_ingest", boom)
    # 每轮把"现在"推后 1h，保证每轮都到期
    base = datetime.now(UTC)
    for i in range(5):
        connector.poll_due_connectors(db_session, now=base + timedelta(hours=i + 1))
    db_session.refresh(dev)
    assert dev.enabled is False and dev.consecutive_failures == 5
    health = db_session.query(DeviceAlert).filter(DeviceAlert.metric == "_health").count()
    assert health == 1
    # 停用后不再轮询
    connector.poll_due_connectors(db_session, now=base + timedelta(hours=10))
    assert calls["n"] == 5


def test_poll_skips_not_due_devices(db_session, monkeypatch):
    dev = _connector_device(db_session, interval_seconds=3600)
    polled: list[str] = []
    monkeypatch.setattr(
        connector,
        "fetch_and_ingest",
        lambda db, device, *, now=None: polled.append(device.id),
    )
    base = datetime.now(UTC)
    connector.poll_due_connectors(db_session, now=base)
    connector.poll_due_connectors(db_session, now=base + timedelta(minutes=30))
    assert polled == [dev.id]  # 第二轮未到 1h 间隔，不再拉


def test_readings_are_append_only(db_session):
    dev = _device(db_session)
    ts = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    service.ingest_values(db_session, dev, {"temp": 10}, reported_at=ts)
    service.ingest_values(db_session, dev, {"temp": 10.5}, reported_at=ts)  # 补报同刻
    db_session.commit()
    rows = db_session.query(Reading).filter(Reading.device_id == dev.id).all()
    assert len(rows) == 2  # 非唯一约束：允许补报


def test_normalize_code_synonyms_and_suffix():
    """同义词白名单与 _cm 后缀宽容：高置信写法归一，未知字段仍进 other。"""
    from app.modules.devices.metrics import OTHER_METRIC, normalize_code

    assert normalize_code("temperature") == "temp"
    assert normalize_code("Air_Temperature") == "temp"
    assert normalize_code("rainfall") == "rain"
    assert normalize_code("soil_moisture_20cm") == "soil_moisture_20"
    assert normalize_code("vendor_flag") == OTHER_METRIC
    assert normalize_code("rainofall") == OTHER_METRIC  # 不做模糊猜测
