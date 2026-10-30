"""Agent 自动执行接口集成测试（v3.28）。

覆盖：立即运行 → 规则发现 → 自动告警/观察记录 + 待确认工单/诊断；
确认动作落地；重复运行降噪；权限与跨院隔离；设置校验。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.modules.auth.repository import create_user
from app.modules.devices.models import Device, Reading
from app.modules.farm.models import FarmPlot, WorkOrder
from app.modules.yards.repository import add_membership, create_yard
from app.modules.yards.settings_service import update_setting

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="自动化小院")
    other = create_yard(db_session, name="别家小院")
    owner = create_user(db_session, username="fa_owner", password="Passw0rd123")
    viewer = create_user(db_session, username="fa_viewer", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    # 同一个人同时是另一家小院的负责人：用于验证跨院取动作一律 404
    add_membership(db_session, yard_id=other.id, user_id=owner.id, role="yard_owner")
    db_session.commit()
    return {"yard": yard, "other": other, "owner": owner, "viewer": viewer}


def _login(client, username: str, yard_id: str) -> str:
    resp = client.post(
        f"{PREFIX}/auth/login",
        json={"username": username, "password": "Passw0rd123", "yard_id": yard_id},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _enable_agent(db, yard_id: str, actor_id: str, **overrides):
    value = {
        "enabled": True,
        "explain_with_llm": False,
        "actions": {
            "alert": "auto",
            "work_order": "confirm",
            "diagnosis": "confirm",
            "record": "auto",
            "pesticide": "confirm",
            "plot": "confirm",
        },
    }
    value.update(overrides)
    return update_setting(
        db, yard_id=yard_id, key="farm_agent", value=value, actor_id=actor_id, request_id=""
    )


def _seed_plot_and_device(db, yard_id: str) -> Device:
    plot = FarmPlot(yard_id=yard_id, name="东一号田", crop="水稻", status="active")
    db.add(plot)
    db.flush()
    device = Device(
        yard_id=yard_id,
        name="东一号气象站",
        device_type="weather",
        source_type="webhook",
        location_note="东一号田",
    )
    db.add(device)
    db.flush()
    now = datetime.now(UTC)
    for metric, value, unit in (
        ("soil_moisture_20", 12.0, "%"),
        ("rain", 0.0, "mm"),
        ("battery", 15.0, "%"),
        ("humidity", 92.0, "%RH"),
        ("temp", 14.0, "℃"),
    ):
        db.add(
            Reading(
                yard_id=yard_id,
                device_id=device.id,
                metric=metric,
                value=value,
                unit=unit,
                collected_at=now - timedelta(minutes=5),
            )
        )
    db.commit()
    return device


def _run(client, token: str) -> dict:
    resp = client.post(f"{PREFIX}/farm/automation/run", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def test_run_creates_actions_and_respects_policy(client, db_session, world):
    yard_id = world["yard"].id
    _enable_agent(db_session, yard_id, world["owner"].id)
    _seed_plot_and_device(db_session, yard_id)
    token = _login(client, "fa_owner", yard_id)

    data = _run(client, token)
    assert data["status"] == "ok"
    codes = {item["code"] for item in data["findings"]}
    assert "water.low" in codes

    by_kind = {item["kind"]: item for item in data["actions"]}
    assert by_kind["alert"]["status"] == "executed"
    assert by_kind["record"]["status"] == "executed"
    assert by_kind["work_order"]["status"] == "pending"
    assert by_kind["diagnosis"]["status"] == "pending"
    assert by_kind["alert"]["target_type"] == "farm_alert"

    overview = client.get(f"{PREFIX}/farm/automation/overview", headers=_hdr(token)).json()["data"]
    assert overview["enabled"] is True
    assert overview["pending_count"] >= 2
    assert overview["last_run"]["run_id"] == data["run_id"]


def test_confirm_action_creates_work_order(client, db_session, world):
    yard_id = world["yard"].id
    _enable_agent(db_session, yard_id, world["owner"].id)
    _seed_plot_and_device(db_session, yard_id)
    token = _login(client, "fa_owner", yard_id)
    data = _run(client, token)
    action = next(item for item in data["actions"] if item["kind"] == "work_order")

    resp = client.post(
        f"{PREFIX}/farm/automation/actions/{action['id']}/confirm", headers=_hdr(token)
    )
    assert resp.status_code == 200, resp.text
    confirmed = resp.json()["data"]
    assert confirmed["status"] == "executed"
    assert confirmed["target_type"] == "work_order"

    order = db_session.get(WorkOrder, confirmed["target_id"])
    assert order is not None
    assert order.source == "agent"
    assert order.agent_run_id == data["run_id"]
    assert order.priority == "high"

    orders = client.get(f"{PREFIX}/farm/work-orders", headers=_hdr(token)).json()["data"]
    assert any(item["work_order_id"] == order.id for item in orders)


def test_second_run_dedupes_actions(client, db_session, world):
    yard_id = world["yard"].id
    _enable_agent(db_session, yard_id, world["owner"].id)
    _seed_plot_and_device(db_session, yard_id)
    token = _login(client, "fa_owner", yard_id)
    first = _run(client, token)
    second = _run(client, token)
    assert first["findings"] and second["findings"]
    assert second["actions"] == []
    assert "降噪窗口" in second["summary"]


def test_dismiss_pending_action(client, db_session, world):
    yard_id = world["yard"].id
    _enable_agent(db_session, yard_id, world["owner"].id)
    _seed_plot_and_device(db_session, yard_id)
    token = _login(client, "fa_owner", yard_id)
    data = _run(client, token)
    action = next(item for item in data["actions"] if item["kind"] == "work_order")
    resp = client.post(
        f"{PREFIX}/farm/automation/actions/{action['id']}/dismiss", headers=_hdr(token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "dismissed"
    # 已忽略的不能再确认
    again = client.post(
        f"{PREFIX}/farm/automation/actions/{action['id']}/confirm", headers=_hdr(token)
    )
    assert again.status_code == 409


def test_viewer_cannot_run_and_cross_yard_action_is_404(client, db_session, world):
    yard_id = world["yard"].id
    _enable_agent(db_session, yard_id, world["owner"].id)
    _seed_plot_and_device(db_session, yard_id)
    owner_token = _login(client, "fa_owner", yard_id)
    data = _run(client, owner_token)
    action = next(item for item in data["actions"] if item["kind"] == "work_order")

    viewer_token = _login(client, "fa_viewer", yard_id)
    resp = client.post(f"{PREFIX}/farm/automation/run", headers=_hdr(viewer_token))
    assert resp.status_code == 403

    other_token = _login(client, "fa_owner", world["other"].id)
    resp = client.post(
        f"{PREFIX}/farm/automation/actions/{action['id']}/confirm", headers=_hdr(other_token)
    )
    assert resp.status_code == 404


def test_no_evidence_run_is_insufficient(client, db_session, world):
    yard_id = world["yard"].id
    _enable_agent(db_session, yard_id, world["owner"].id)
    token = _login(client, "fa_owner", yard_id)
    data = _run(client, token)
    assert data["status"] == "insufficient"
    assert "不产出预警" in data["summary"]
    assert data["findings"] == []


def test_settings_reject_unknown_policy_and_missing_weather_key(client, db_session, world):
    token = _login(client, "fa_owner", world["yard"].id)
    bad_policy = client.put(
        f"{PREFIX}/yards/current/settings/farm_agent",
        json={"actions": {"alert": "always"}},
        headers=_hdr(token),
    )
    assert bad_policy.status_code == 400

    missing_key = client.put(
        f"{PREFIX}/yards/current/settings/farm_agent",
        json={"weather_provider": "qweather", "weather_location": "101010100"},
        headers=_hdr(token),
    )
    assert missing_key.status_code == 400

    ok = client.put(
        f"{PREFIX}/yards/current/settings/farm_agent",
        json={"enabled": True, "thresholds": {"soil_moisture_low": 25}},
        headers=_hdr(token),
    )
    assert ok.status_code == 200, ok.text
    data = ok.json()["data"]["farm_agent"]
    assert data["thresholds"]["soil_moisture_low"] == 25
    assert data["weather_api_key_configured"] is False
    assert "weather_api_key" not in data
