"""告警→一键工单端到端：开工单/幂等复用同号/成员可用/跨小院 404/审计与出参。

▷ 工单实体复用 tasks 表（task_type=workorder.dispatch）：本测试同时断言
任务行内容（data 严格匹配工具入参 schema、meta 记溯源）与投递失败标记路径
（测试环境无 worker：或投递成功保持 queued、或 meta.enqueue_failed，二选一）。
"""

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.tasks.models import Task
from app.modules.yards.repository import create_yard


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="告警工单小院")
    db_session.commit()
    return t


@pytest.fixture()
def other_yard(db_session):
    t = create_yard(db_session, name="隔壁工单小院")
    db_session.commit()
    return t


def _hdr(user_id, yard_id, roles=("yard_admin",)):
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _make_alert(client, yard, *, metric="temp", threshold=30.0, value=35.0, name="一号气象站"):
    """建 webhook 设备+规则并推一条越限读数，返回告警行（API 出参）。"""
    dev = client.post(
        "/api/v1/devices",
        json={"name": name, "device_type": "weather", "source_type": "webhook"},
        headers=_hdr("u1", yard.id),
    ).json()["data"]
    client.post(
        "/api/v1/devices/alert-rules",
        json={"metric": metric, "op": "gt", "threshold": threshold},
        headers=_hdr("u1", yard.id),
    )
    client.post(
        f"/api/v1/devices/{dev['id']}/ingest",
        headers={"X-Device-Token": dev["device_token"]},
        json={"data": {metric: value}},
    )
    alerts = client.get("/api/v1/devices/alerts", headers=_hdr("u1", yard.id)).json()["data"]
    assert len(alerts) == 1
    return alerts[0]


def _create_wo(client, alert_id, hdr):
    return client.post(f"/api/v1/devices/alerts/{alert_id}/create-workorder", headers=hdr)


def test_create_workorder_task_and_backfill(client, db_session, yard):
    alert = _make_alert(client, yard)
    assert alert["workorder_id"] is None and alert["workorder_no"] is None

    resp = _create_wo(client, alert["id"], _hdr("u1", yard.id))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    wo_no = data["workorder_no"]
    assert wo_no and data["created"] is True and data["link"] == "/tasks"

    # 工单任务行：类型/标题（=告警文案）/描述（含指标、阈值、人工核查）/溯源 meta
    task = db_session.get(Task, wo_no)
    assert task is not None and task.yard_id == yard.id
    assert task.task_type == "workorder.dispatch"
    payload = task.input_json["data"]
    assert payload["title"].startswith("【设备告警】")
    assert "气温" in payload["description"] and "阈值" in payload["description"]
    assert "人工核查" in payload["description"]
    assert task.input_json["meta"]["alert_id"] == alert["id"]
    assert task.idempotency_key == f"device_alert:{alert['id']}"
    # 投递：无 worker 测试环境下要么投递成功保持 queued，要么标记 enqueue_failed
    assert task.status == "queued"
    assert task.input_json["meta"].get("enqueue_failed") in (None, True)

    # 告警出参回填工单号
    listed = client.get("/api/v1/devices/alerts", headers=_hdr("u1", yard.id)).json()["data"]
    assert listed[0]["workorder_id"] == wo_no and listed[0]["workorder_no"] == wo_no

    # 审计：action=alert_create_workorder 记工单与指标
    logs = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "alert_create_workorder", AuditLog.yard_id == yard.id)
        .all()
    )
    assert len(logs) == 1
    assert logs[0].metadata_json["workorder_id"] == wo_no
    assert logs[0].metadata_json["metric"] == "temp"


def test_create_workorder_idempotent_second_call_same_no(client, db_session, yard):
    alert = _make_alert(client, yard)
    first = _create_wo(client, alert["id"], _hdr("u1", yard.id)).json()["data"]
    second = _create_wo(client, alert["id"], _hdr("u1", yard.id))
    assert second.status_code == 200
    data = second.json()["data"]
    assert data["workorder_no"] == first["workorder_no"]
    assert data["created"] is False
    # 同小院只有一条工单任务（幂等键 + workorder_id 锚点双保险）
    rows = (
        db_session.query(Task)
        .filter(Task.yard_id == yard.id, Task.task_type == "workorder.dispatch")
        .count()
    )
    assert rows == 1


def test_member_can_open_workorder(client, yard):
    alert = _make_alert(client, yard, metric="humidity", threshold=90.0, value=95.0)
    member = _hdr("u-member", yard.id, roles=("operator",))
    resp = _create_wo(client, alert["id"], member)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["created"] is True


def test_cross_yard_and_unknown_alert_404(client, yard, other_yard):
    alert = _make_alert(client, yard)
    # 隔壁小院：存在与否一律 404（防枚举）
    assert _create_wo(client, alert["id"], _hdr("u2", other_yard.id)).status_code == 404
    # 不存在的告警同样 404
    assert _create_wo(client, "no-such-alert", _hdr("u1", yard.id)).status_code == 404
    # 未登录 → 401
    assert client.post(f"/api/v1/devices/alerts/{alert['id']}/create-workorder").status_code == 401


def test_health_alert_workorder_title_fallback(client, db_session, yard):
    """健康类告警（无规则、无文案兜底路径）：标题按"设备X 指标Y 超阈值"概括。"""
    from app.modules.devices.models import Device
    from app.modules.devices.service import record_health_alert

    dev = client.post(
        "/api/v1/devices",
        json={"name": "棚内探针", "device_type": "greenhouse", "source_type": "webhook"},
        headers=_hdr("u1", yard.id),
    ).json()["data"]
    device = db_session.get(Device, dev["id"])
    record_health_alert(db_session, device, "")  # 空文案 → 走标题兜底
    db_session.commit()
    alert = client.get("/api/v1/devices/alerts", headers=_hdr("u1", yard.id)).json()["data"][0]
    resp = _create_wo(client, alert["id"], _hdr("u1", yard.id))
    body = resp.json()
    assert resp.status_code == 200, resp.text
    assert body["data"]["created"] is True
    task = db_session.get(Task, body["data"]["workorder_no"])
    assert task.input_json["data"]["title"] == "设备 棚内探针 指标 _health 超阈值"
    assert "无（健康类告警或规则已删除）" in task.input_json["data"]["description"]
