"""天气摘要接口集成测试（v3.70）。

覆盖：未配置供应商时 configured=False 不报错；已配置时摘要字段透传；
权限（无 farm:read 拒绝）与跨院隔离由统一依赖保证，这里只测主链路。
"""

from __future__ import annotations

import pytest
from app.modules.auth.repository import create_user
from app.modules.yards.repository import add_membership, create_yard
from app.modules.yards.settings_service import update_setting

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="天气小院")
    owner = create_user(db_session, username="wb_owner", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    db_session.commit()
    return {"yard": yard, "owner": owner}


def _login(client, username: str, yard_id: str) -> str:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": "Passw0rd123", "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_weather_brief_not_configured(client, db_session, world):
    token = _login(client, "wb_owner", world["yard"].id)
    resp = client.get(f"{PREFIX}/farm/weather/brief", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["configured"] is False
    assert data["brief"] is None
    assert data["warning"]


def test_weather_brief_configured(client, db_session, world, monkeypatch):
    update_setting(
        db_session,
        yard_id=world["yard"].id,
        key="farm_agent",
        value={
            "enabled": False,
            "explain_with_llm": False,
            "weather_provider": "qweather",
            "weather_api_key": "test-key",
            "weather_location": "101010100",
        },
        actor_id=world["owner"].id,
        request_id="",
    )
    sample = {
        "provider": "qweather",
        "generated_at": "2026-11-06T00:00:00+00:00",
        "next_24h": {
            "rain_mm": 3.2,
            "wind_max_ms": 5.1,
            "temp_max": 22.0,
            "temp_min": 12.0,
            "humidity_mean": 68.5,
        },
        "next_48h": {"rain_mm": None, "temp_min": 9.0},
    }

    def fake_forecast(yard_id, setting, *, use_cache=True):
        return sample, []

    monkeypatch.setattr("app.modules.farm.weather.forecast", fake_forecast)
    token = _login(client, "wb_owner", world["yard"].id)
    resp = client.get(f"{PREFIX}/farm/weather/brief", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["configured"] is True
    assert data["warning"] is None
    brief = data["brief"]
    assert brief["temp_max"] == 22.0
    assert brief["temp_min"] == 12.0
    assert brief["rain_mm"] == 3.2
    assert brief["wind_max_ms"] == 5.1
    assert brief["humidity_mean"] == 68.5
    assert brief["tomorrow_temp_min"] == 9.0
    assert brief["generated_at"] == "2026-11-06T00:00:00+00:00"


def test_weather_brief_requires_yard(client, db_session, world):
    # 平台级账号（无小院上下文）不给天气摘要
    from app.modules.auth.repository import create_user as cu

    cu(db_session, username="wb_plain", password="Passw0rd123")
    db_session.commit()
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": "wb_plain", "password": "Passw0rd123"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["data"]["access_token"]
    resp = client.get(f"{PREFIX}/farm/weather/brief", headers=_hdr(token))
    assert resp.status_code in (400, 403)
