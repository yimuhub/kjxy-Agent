"""设备数据智能分析 API（v3.23）：立即分析、发现、幂等、critical 转告警、证据不足、
权限与跨小院、洞察卡、定时任务幂等。

测试直接写 readings 行（绕开限频），时间相对当前时间构造；LLM 解释在各用例中显式关闭，
保证确定性且不触模型。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.devices.models import DeviceAlert, DeviceAnalysis, Reading
from app.modules.self_improve.models import YardJob, YardJobRun
from app.modules.yards.repository import create_yard
from app.modules.yards.settings_service import update_setting


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="设备智能分析小院")
    db_session.commit()
    return t


@pytest.fixture()
def other_yard(db_session):
    t = create_yard(db_session, name="隔壁分析小院")
    db_session.commit()
    return t


def _hdr(user_id, yard_id, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _create_device(client, yard, name="一号气象站", **kw):
    resp = client.post(
        "/api/v1/devices",
        json={"name": name, "device_type": "weather", "source_type": "webhook", **kw},
        headers=_hdr("u1", yard.id),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _disable_llm(db_session, yard_id):
    update_setting(
        db_session,
        yard_id=yard_id,
        key="device_analysis",
        value={"explain_with_llm": False},
        actor_id="u1",
        request_id="req_test",
    )
    db_session.commit()


def _add_readings(db_session, yard, device_id, *, points, now=None):
    """points: {metric: [值...]}，每个指标按小时从「len-1 小时前」到「现在」排列。"""
    now = now or datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    for metric, values in points.items():
        for index, value in enumerate(values):
            at = now - timedelta(hours=len(values) - 1 - index)
            db_session.add(
                Reading(
                    yard_id=yard.id,
                    device_id=device_id,
                    metric=metric,
                    value=float(value),
                    unit="",
                    collected_at=at,
                )
            )
    db_session.commit()


def _dry_soil_points():
    """12 小时：土壤水分下降 + 无降雨 + 高湿低温 → 灌溉/病害组合。"""
    return {
        "soil_moisture_20": [35.0 - i for i in range(12)],
        "rain": [0.0] * 12,
        "humidity": [88.0 + (i % 3) for i in range(12)],
        "temp": [14.5 + (i % 2) * 0.3 for i in range(12)],
        "light": [3000.0 + i * 10 for i in range(12)],
    }


def test_analysis_findings_quality_and_idempotent(client, db_session, yard):
    _disable_llm(db_session, yard.id)
    device = _create_device(client, yard)
    _add_readings(db_session, yard, device["id"], points=_dry_soil_points())

    first = client.post(f"/api/v1/devices/{device['id']}/analyses", headers=_hdr("u1", yard.id))
    assert first.status_code == 200, first.text
    data = first.json()["data"]
    assert data["created"] is True
    assert data["status"] == "ok"
    assert data["level"] in {"watch", "warning", "critical"}
    codes = {item["code"] for item in data["findings"]}
    assert "combo:irrigation" in codes
    assert "combo:disease_risk" in codes
    assert data["model"] is None  # LLM 已关闭，只出规则结论
    assert "本轮发现" in data["summary"] and "土壤水分" in data["summary"]
    assert data["metrics"]["soil_moisture_20"]["points"] == 12
    assert data["quality"]["score"] <= 100

    second = client.post(f"/api/v1/devices/{device['id']}/analyses", headers=_hdr("u1", yard.id))
    assert second.status_code == 200
    again = second.json()["data"]
    assert again["created"] is False and again["analysis_id"] == data["analysis_id"]
    assert db_session.query(DeviceAnalysis).count() == 1

    listed = client.get(
        f"/api/v1/devices/{device['id']}/analyses", headers=_hdr("u1", yard.id)
    ).json()["data"]
    assert len(listed) == 1 and listed[0]["analysis_id"] == data["analysis_id"]

    detail = client.get(f"/api/v1/devices/{device['id']}", headers=_hdr("u1", yard.id))
    assert detail.json()["data"]["latest_analysis"]["analysis_id"] == data["analysis_id"]

    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "device_analysis_run", AuditLog.yard_id == yard.id)
        .all()
    )
    assert len(logs) == 1 and logs[0].metadata_json["status"] == "ok"


def test_critical_threshold_creates_alert_and_workorder(client, db_session, yard):
    _disable_llm(db_session, yard.id)
    device = _create_device(client, yard, name="高温棚")
    client.post(
        "/api/v1/devices/alert-rules",
        json={"metric": "temp", "op": "gt", "threshold": 30, "notify_note": "注意通风降温"},
        headers=_hdr("u1", yard.id),
    )
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    points = {
        "temp": [40.0 + (i % 2) for i in range(8)],
        "humidity": [60.0 + i for i in range(8)],
    }
    _add_readings(db_session, yard, device["id"], points=points, now=now)

    resp = client.post(f"/api/v1/devices/{device['id']}/analyses", headers=_hdr("u1", yard.id))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["level"] == "critical"
    threshold = [item for item in data["findings"] if item["code"] == "threshold:temp"]
    assert threshold and threshold[0]["level"] == "critical"

    alerts = (
        db_session.query(DeviceAlert)
        .filter(DeviceAlert.device_id == device["id"], DeviceAlert.metric == "temp")
        .all()
    )
    assert len(alerts) == 1 and alerts[0].value == 41.0

    # critical 告警直接复用既有「告警 → 一键工单」链路
    wo = client.post(
        f"/api/v1/devices/alerts/{alerts[0].id}/create-workorder", headers=_hdr("u1", yard.id)
    )
    assert wo.status_code == 200, wo.text
    assert wo.json()["data"]["workorder_id"]


def test_insufficient_data_is_honest(client, db_session, yard):
    _disable_llm(db_session, yard.id)
    empty = _create_device(client, yard, name="未通电设备")
    resp = client.post(f"/api/v1/devices/{empty['id']}/analyses", headers=_hdr("u1", yard.id))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "DEVICE_ANALYSIS_INSUFFICIENT_DATA"

    sparse = _create_device(client, yard, name="刚上线设备")
    _add_readings(
        db_session, yard, sparse["id"], points={"temp": [20.0, 20.0], "humidity": [60.0, 60.0]}
    )
    resp = client.post(f"/api/v1/devices/{sparse['id']}/analyses", headers=_hdr("u1", yard.id))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "insufficient"
    assert data["findings"] == []
    assert "证据不足" in data["summary"]


def test_insights_permissions_and_tenant_isolation(client, db_session, yard, other_yard):
    _disable_llm(db_session, yard.id)
    device = _create_device(client, yard)
    _add_readings(db_session, yard, device["id"], points=_dry_soil_points())
    assert (
        client.post(
            f"/api/v1/devices/{device['id']}/analyses", headers=_hdr("u1", yard.id)
        ).status_code
        == 200
    )

    insights = client.get(
        "/api/v1/devices/insights", headers=_hdr("v1", yard.id, roles=("viewer",))
    )
    assert insights.status_code == 200
    payload = insights.json()["data"]
    assert payload["items"] and payload["items"][0]["device_id"] == device["id"]
    assert sum(payload["counts"].values()) >= 1

    viewer_run = client.post(
        f"/api/v1/devices/{device['id']}/analyses", headers=_hdr("v1", yard.id, roles=("viewer",))
    )
    assert viewer_run.status_code == 403

    other = client.post(
        f"/api/v1/devices/{device['id']}/analyses", headers=_hdr("u9", other_yard.id)
    )
    assert other.status_code == 404


def test_schedule_runs_once_per_day(client, db_session, yard):
    _disable_llm(db_session, yard.id)
    device = _create_device(client, yard)
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    _add_readings(db_session, yard, device["id"], points=_dry_soil_points(), now=now)

    from app.modules.devices.analysis_jobs import JOB_DEVICE_ANALYSIS, run_due_analyses, sync_job
    from app.modules.yards.settings_service import get_setting

    setting = get_setting(db_session, yard.id, "device_analysis")
    job = sync_job(db_session, yard_id=yard.id, setting=setting, actor_id="u1")
    job.next_run_at = now - timedelta(minutes=10)
    db_session.commit()

    stats = run_due_analyses(db_session, now=now)
    assert stats["due"] == 1 and stats["ok"] >= 1
    run_row = (
        db_session.query(YardJobRun)
        .filter(YardJobRun.yard_id == yard.id, YardJobRun.kind == JOB_DEVICE_ANALYSIS)
        .one()
    )
    assert run_row.status == "success" and run_row.output_json["ok"] >= 1
    assert db_session.query(DeviceAnalysis).filter_by(device_id=device["id"]).count() == 1

    # 同一天再次到点：幂等跳过，不重复落分析
    job = db_session.query(YardJob).filter_by(yard_id=yard.id, kind=JOB_DEVICE_ANALYSIS).one()
    job.next_run_at = now + timedelta(minutes=1)
    db_session.commit()
    run_due_analyses(db_session, now=now + timedelta(minutes=2))
    assert db_session.query(DeviceAnalysis).filter_by(device_id=device["id"]).count() == 1
