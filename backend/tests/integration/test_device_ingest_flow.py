"""设备接入端到端流程（API 层）：建 webhook 设备拿一次性 token → ingest 归一、
401/403/429、告警与去重、rotate-token、跨小院 404、连接器配置加密掩码、
能力字典与权限（成员可读 / 管理员可写）。

DB 用 conftest 内存库 + get_db_session 覆盖；连接器密钥 env 与 test_llm_suppliers 同约定。
"""

import os

import pytest
from app.core.security import create_access_token
from app.modules.devices import service
from app.modules.yards.repository import create_yard
from cryptography.fernet import Fernet

# 连接器 headers 加密复用 supplier_crypto：测试密钥（与 test_llm_suppliers 同约定，
# 只需在首次调用加密功能前设置好）
os.environ.setdefault("SUPPLIER_KEY_SECRET", Fernet.generate_key().decode())


@pytest.fixture(autouse=True)
def _clean_rate_window():
    service._rate_window.reset()
    yield
    service._rate_window.reset()


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="设备接入小院")
    db_session.commit()
    return t


@pytest.fixture()
def other_yard(db_session):
    t = create_yard(db_session, name="隔壁小院")
    db_session.commit()
    return t


def _hdr(user_id, yard_id, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _create_webhook(client, yard, name="一号气象站", **kw):
    body = {"name": name, "device_type": "weather", "source_type": "webhook", **kw}
    resp = client.post("/api/v1/devices", json=body, headers=_hdr("u1", yard.id))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_create_webhook_device_one_time_token_and_capabilities(client, yard):
    data = _create_webhook(client, yard)
    token = data.get("device_token")
    assert token and len(token) > 20
    assert data["has_ingest_token"] is True

    # 列表/详情永不回显 token 字段
    got = client.get(f"/api/v1/devices/{data['id']}", headers=_hdr("u1", yard.id)).json()["data"]
    assert "device_token" not in got
    lst = client.get("/api/v1/devices", headers=_hdr("u1", yard.id)).json()["data"]
    assert len(lst) == 1 and "device_token" not in lst[0]

    caps = client.get("/api/v1/devices/capabilities", headers=_hdr("u1", yard.id)).json()["data"]
    codes = {m["code"] for m in caps["metrics"]}
    assert {"temp", "soil_moisture_40", "pest_count", "other"} <= codes
    assert {s["value"] for s in caps["source_types"]} == {"webhook", "connector"}


def test_ingest_flow_normalize_auth_ratelimit(client, yard, monkeypatch):
    dev = _create_webhook(client, yard)
    token = dev["device_token"]
    url = f"/api/v1/devices/{dev['id']}/ingest"

    # 正确：Header 令牌；字典内归一（含数字字符串）、字典外进 other 保 raw
    resp = client.post(
        url,
        headers={"X-Device-Token": token},
        json={"data": {"temp": 25.5, "humidity": "61.2", "vendor_flag": "XX"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["accepted"] == 3 and body["other"] == 1

    rows = client.get(f"/api/v1/devices/{dev['id']}/readings", headers=_hdr("u1", yard.id)).json()[
        "data"
    ]
    by_metric = {r["metric"]: r for r in rows}
    assert by_metric["temp"]["value"] == 25.5 and by_metric["temp"]["unit"] == "℃"
    assert by_metric["humidity"]["value"] == 61.2  # 数字字符串归一为数值
    assert by_metric["other"]["raw_json"]["vendor_field"] == "vendor_flag"

    # 详情含最新值 summary
    detail = client.get(f"/api/v1/devices/{dev['id']}", headers=_hdr("u1", yard.id)).json()["data"]
    assert {s["metric"] for s in detail["latest"]} == {"temp", "humidity", "other"}

    # 令牌错 → 401；不存在设备 → 404
    bad1 = client.post(url, headers={"X-Device-Token": "wrong"}, json={"data": {"temp": 1}})
    assert bad1.status_code == 401
    bad2 = client.post(url, json={"token": token + "x", "data": {"temp": 1}})
    assert bad2.status_code == 401
    nopath = client.post("/api/v1/devices/nope/ingest", json={"token": token, "data": {"temp": 1}})
    assert nopath.status_code == 404

    # 限频：窗口额度调小后整请求 429
    monkeypatch.setattr(service, "INGEST_MAX_POINTS_PER_WINDOW", 5)
    ok = client.post(url, headers={"X-Device-Token": token}, json={"data": {"temp": 1, "light": 2}})
    assert ok.status_code == 200
    over = client.post(
        url,
        headers={"X-Device-Token": token},
        json={"data": {"temp": 1, "light": 2, "rain": 3, "co2": 4}},
    )
    assert over.status_code == 429
    assert over.json()["error"]["code"] == "RATE_LIMITED"

    # 禁用设备 → 403
    client.patch(
        f"/api/v1/devices/{dev['id']}",
        json={"enabled": False},
        headers=_hdr("u1", yard.id),
    )
    blocked = client.post(url, headers={"X-Device-Token": token}, json={"data": {"temp": 1}})
    assert blocked.status_code == 403


def test_rotate_token_invalidates_old(client, yard):
    dev = _create_webhook(client, yard)
    url = f"/api/v1/devices/{dev['id']}/ingest"
    first = client.post(
        url, headers={"X-Device-Token": dev["device_token"]}, json={"data": {"temp": 1}}
    )
    assert first.status_code == 200

    rotated = client.post(
        f"/api/v1/devices/{dev['id']}/rotate-token", headers=_hdr("u1", yard.id)
    ).json()["data"]
    new_token = rotated["device_token"]
    assert new_token != dev["device_token"]
    old = client.post(
        url, headers={"X-Device-Token": dev["device_token"]}, json={"data": {"temp": 2}}
    )
    assert old.status_code == 401
    fresh = client.post(url, headers={"X-Device-Token": new_token}, json={"data": {"temp": 2}})
    assert fresh.status_code == 200


def test_alert_rule_trigger_dedupe_and_query(client, yard):
    dev = _create_webhook(client, yard)
    token = dev["device_token"]
    rule = client.post(
        "/api/v1/devices/alert-rules",
        json={"metric": "temp", "op": "gt", "threshold": 30, "notify_note": "注意通风"},
        headers=_hdr("u1", yard.id),
    ).json()["data"]
    assert rule["metric"] == "temp"

    r1 = client.post(
        f"/api/v1/devices/{dev['id']}/ingest",
        headers={"X-Device-Token": token},
        json={"data": {"temp": 35}},
    ).json()["data"]
    assert r1["alerts_triggered"] == 1

    # 30 分钟去重
    r2 = client.post(
        f"/api/v1/devices/{dev['id']}/ingest",
        headers={"X-Device-Token": token},
        json={"data": {"temp": 42}},
    ).json()["data"]
    assert r2["alerts_triggered"] == 0

    alerts = client.get("/api/v1/devices/alerts", headers=_hdr("u1", yard.id)).json()["data"]
    assert len(alerts) == 1
    assert alerts[0]["rule_id"] == rule["id"]
    assert alerts[0]["device_name"] == "一号气象站"
    assert "注意通风" in (alerts[0]["notified_text"] or "")
    assert alerts[0]["metric_name"] == "气温"

    # 停用规则后不再触发
    client.patch(
        f"/api/v1/devices/alert-rules/{rule['id']}",
        json={"enabled": False},
        headers=_hdr("u1", yard.id),
    )
    r3 = client.post(
        f"/api/v1/devices/{dev['id']}/ingest",
        headers={"X-Device-Token": token},
        json={"data": {"temp": 50}},
    ).json()["data"]
    assert r3["alerts_triggered"] == 0


def test_cross_yard_isolation(client, yard, other_yard):
    dev = _create_webhook(client, yard)
    # 隔壁小院管理员：GET/PATCH/DELETE 一律 404（防枚举）
    hdr2 = _hdr("u2", other_yard.id)
    assert client.get(f"/api/v1/devices/{dev['id']}", headers=hdr2).status_code == 404
    assert client.get(f"/api/v1/devices/{dev['id']}/readings", headers=hdr2).status_code == 404
    off_patch = client.patch(f"/api/v1/devices/{dev['id']}", json={"enabled": False}, headers=hdr2)
    assert off_patch.status_code == 404
    assert client.post(f"/api/v1/devices/{dev['id']}/rotate-token", headers=hdr2).status_code == 404
    # 列表互不可见
    assert client.get("/api/v1/devices", headers=hdr2).json()["data"] == []
    # 同名可在另一小院创建（唯一约束是小院内）
    dup_ok = client.post(
        "/api/v1/devices",
        json={"name": "一号气象站", "source_type": "webhook"},
        headers=hdr2,
    )
    assert dup_ok.status_code == 200
    # 同小院重名 → 409
    dup = client.post(
        "/api/v1/devices",
        json={"name": "一号气象站", "source_type": "webhook"},
        headers=_hdr("u1", yard.id),
    )
    assert dup.status_code == 409


def test_connector_device_config_encrypted_masked(client, yard):
    body = {
        "name": "厂商云拉取",
        "device_type": "weather",
        "source_type": "connector",
        "connector": {
            "url": "https://vendor.example.com/api/realtime",
            "method": "GET",
            "headers": {"X-Api-Key": "super-secret-token-abcd"},
            "interval_seconds": 120,
            "json_path": "$.data.items[*]",
            "field_map": {"deviceTemp": {"metric": "temp"}, "deviceHum": {"metric": "humidity"}},
            "timeout_seconds": 5,
        },
    }
    resp = client.post("/api/v1/devices", json=body, headers=_hdr("u1", yard.id))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 响应永不含明文/密文密钥，只有掩码
    text = resp.text
    assert "super-secret-token" not in text and "headers_enc" not in text
    masked = data["connector"]["headers_masked"]["X-Api-Key"]
    assert "super-secret-token" not in masked

    # interval < 60 → 拒绝（pydantic ge=60，全局校验处理器统一 400 INVALID_REQUEST）
    bad = dict(body["connector"], interval_seconds=30)
    bad_resp = client.post(
        "/api/v1/devices",
        json=dict(body, name="坏周期", connector=bad),
        headers=_hdr("u1", yard.id),
    )
    assert bad_resp.status_code == 400
    assert bad_resp.json()["error"]["code"] == "INVALID_REQUEST"

    # localhost → SSRF 拒绝
    ssrf = client.post(
        "/api/v1/devices",
        json=dict(
            body,
            name="内网",
            connector=dict(body["connector"], url="http://127.0.0.1:9999/api"),
        ),
        headers=_hdr("u1", yard.id),
    )
    assert ssrf.status_code == 400
    assert ssrf.json()["error"]["code"] == "SSRF_BLOCKED"

    # 字典外 metric → 400（prepare_config 校验，区别于 pydantic 的 422）
    badmap = client.post(
        "/api/v1/devices",
        json=dict(
            body,
            name="错映射",
            connector=dict(body["connector"], field_map={"a": {"metric": "temperature"}}),
        ),
        headers=_hdr("u1", yard.id),
    )
    assert badmap.status_code == 400

    # 缺 connector 配置 → 400
    nocfg = client.post(
        "/api/v1/devices",
        json={"name": "裸connector", "source_type": "connector"},
        headers=_hdr("u1", yard.id),
    )
    assert nocfg.status_code == 400


def test_members_read_admin_write(client, yard):
    _create_webhook(client, yard)
    member = _hdr("u3", yard.id, roles=("operator",))
    assert client.get("/api/v1/devices", headers=member).status_code == 200
    assert client.get("/api/v1/devices/capabilities", headers=member).status_code == 200
    forbidden = client.post(
        "/api/v1/devices", json={"name": "越权设备", "source_type": "webhook"}, headers=member
    )
    assert forbidden.status_code == 403
    # 未登录 → 401
    assert client.get("/api/v1/devices").status_code == 401


def test_delete_device_cascades(client, yard):
    dev = _create_webhook(client, yard)
    client.post(
        f"/api/v1/devices/{dev['id']}/ingest",
        headers={"X-Device-Token": dev["device_token"]},
        json={"data": {"temp": 20}},
    )
    resp = client.delete(f"/api/v1/devices/{dev['id']}", headers=_hdr("u1", yard.id))
    assert resp.status_code == 200
    gone = client.get(f"/api/v1/devices/{dev['id']}", headers=_hdr("u1", yard.id))
    assert gone.status_code == 404
