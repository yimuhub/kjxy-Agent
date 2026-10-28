"""一句话业务端到端：预览零副作用、确认落库、缺字段/指纹/权限。"""

import pytest
from app.core.security import create_access_token
from app.modules.audit.models import AuditLog
from app.modules.yards.repository import create_yard

PREFIX = "/api/v1"
TODAY = "2026-10-29"


@pytest.fixture()
def yard(db_session):
    t = create_yard(db_session, name="一句话业务小院")
    db_session.commit()
    return t


def _hdr(user_id: str, yard_id: str, roles=("yard_admin",)) -> dict[str, str]:
    token = create_access_token(user_id=user_id, yard_id=yard_id, roles=list(roles))
    return {"Authorization": f"Bearer {token}"}


def _create_plot(client, yard_id: str, name: str = "东一号田", crop: str = "水稻") -> dict:
    resp = client.post(
        f"{PREFIX}/farm/plots",
        json={"name": name, "crop": crop, "area_mu": 12},
        headers=_hdr("u1", yard_id),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _preview(client, yard_id: str, module: str, text: str, **extra):
    payload = {"module": module, "text": text, "today": TODAY, **extra}
    return client.post(f"{PREFIX}/quick-actions/preview", json=payload, headers=_hdr("u1", yard_id))


def _confirm(client, yard_id: str, preview: dict):
    body = {
        "module": preview["module"],
        "action": preview["action"],
        "payload": preview["payload"],
        "fingerprint": preview["fingerprint"],
    }
    return client.post(f"{PREFIX}/quick-actions/confirm", json=body, headers=_hdr("u1", yard_id))


def test_plot_preview_has_no_side_effect_and_confirm_creates(client, db_session, yard):
    before = client.get(f"{PREFIX}/farm/plots", headers=_hdr("u1", yard.id)).json()["data"]
    resp = _preview(client, yard.id, "farm", "新增地块 东二号田 小麦 8.5 亩，沙壤土")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["action"] == "plot.create"
    assert data["ready"] is True and data["missing"] == []
    assert data["payload"] == {
        "name": "东二号田",
        "crop": "小麦",
        "area_mu": 8.5,
        "soil_type": "沙壤土",
        "notes": "",
    }

    # 预览不落库
    mid = client.get(f"{PREFIX}/farm/plots", headers=_hdr("u1", yard.id)).json()["data"]
    assert mid == before

    created = _confirm(client, yard.id, {"module": "farm", **data})
    assert created.status_code == 201, created.text
    body = created.json()["data"]
    assert body["action"] == "plot.create"
    assert body["created"]["name"] == "东二号田"

    listed = client.get(f"{PREFIX}/farm/plots", headers=_hdr("u1", yard.id)).json()["data"]
    assert any(row["name"] == "东二号田" for row in listed)
    audits = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "quick_action_confirmed", AuditLog.yard_id == yard.id)
        .all()
    )
    assert len(audits) == 1
    assert audits[0].metadata_json["quick_action"] == "plot.create"


def test_record_and_diagnosis_preview_confirm(client, yard):
    plot = _create_plot(client, yard.id)

    rec = _preview(client, yard.id, "farm", "东一号田 今天追肥 尿素 5 公斤")
    assert rec.status_code == 200, rec.text
    rec_data = rec.json()["data"]
    assert rec_data["action"] == "record.create"
    assert rec_data["missing"] == []
    assert rec_data["payload"]["plot_id"] == plot["plot_id"]
    assert rec_data["payload"]["kind"] == "fertilizing"
    assert rec_data["payload"]["quantity"] == 5
    assert rec_data["payload"]["unit"] == "公斤"
    assert rec_data["payload"]["occurred_on"] == TODAY

    confirmed = _confirm(client, yard.id, {"module": "farm", **rec_data})
    assert confirmed.status_code == 201, confirmed.text
    records = client.get(
        f"{PREFIX}/farm/records?plot_id={plot['plot_id']}", headers=_hdr("u1", yard.id)
    ).json()["data"]
    assert len(records) == 1 and records[0]["kind"] == "fertilizing"

    diag = _preview(client, yard.id, "diagnosis", "东一号田 水稻 叶片发黄有褐斑，最近连阴雨")
    assert diag.status_code == 200, diag.text
    diag_data = diag.json()["data"]
    assert diag_data["action"] == "diagnosis.create"
    assert diag_data["missing"] == []
    assert diag_data["payload"]["crop"] == "水稻"
    assert diag_data["payload"]["plot_id"] == plot["plot_id"]
    assert "叶片发黄" in diag_data["payload"]["symptoms"]
    assert diag_data["payload"]["environment"] == {"recent_weather": "连阴雨"}

    confirmed = _confirm(client, yard.id, {"module": "diagnosis", **diag_data})
    assert confirmed.status_code == 201, confirmed.text
    diagnoses = client.get(f"{PREFIX}/farm/diagnoses", headers=_hdr("u1", yard.id)).json()["data"]
    assert len(diagnoses) == 1
    assert diagnoses[0]["crop"] == "水稻"


def test_missing_fields_stale_fingerprint_and_permission(client, yard):
    missing = _preview(client, yard.id, "farm", "新增地块 西三号田")
    assert missing.status_code == 200
    data = missing.json()["data"]
    assert data["ready"] is False
    assert any(item.startswith("面积") for item in data["missing"])
    # 缺字段时前端不该能确认；即使硬提交也会被 Schema 拦下
    rejected = _confirm(client, yard.id, {"module": "farm", **data})
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "QUICK_ACTION_AMBIGUOUS"

    valid = _preview(client, yard.id, "farm", "新增地块 北五号田 玉米 6 亩")
    assert valid.json()["data"]["ready"] is True
    body = {
        "module": "farm",
        "action": valid.json()["data"]["action"],
        "payload": valid.json()["data"]["payload"],
        "fingerprint": "0" * 64,
    }
    stale = client.post(f"{PREFIX}/quick-actions/confirm", json=body, headers=_hdr("u1", yard.id))
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "QUICK_ACTION_STALE"
    plots = client.get(f"{PREFIX}/farm/plots", headers=_hdr("u1", yard.id)).json()["data"]
    assert all(row["name"] != "北五号田" for row in plots)

    forbidden = client.post(
        f"{PREFIX}/quick-actions/preview",
        json={"module": "farm", "text": "新增地块 南六号田 3 亩"},
        headers=_hdr("viewer", yard.id, roles=("viewer",)),
    )
    assert forbidden.status_code == 403


def test_work_order_converts_matching_alert(client, yard):
    plot = _create_plot(client, yard.id)
    alert = client.post(
        f"{PREFIX}/farm/alerts",
        json={
            "title": "东一号田虫情告警",
            "detail": "虫情计数连续 3 天上升",
            "severity": "high",
            "plot_id": plot["plot_id"],
        },
        headers=_hdr("u1", yard.id),
    ).json()["data"]

    resp = _preview(client, yard.id, "work_order", "把东一号田的虫情告警转成工单，明天到期")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["action"] == "alert.convert"
    assert data["payload"]["alert_id"] == alert["alert_id"]
    assert data["payload"]["due_on"] == "2026-10-30"
    assert data["missing"] == []

    confirmed = _confirm(client, yard.id, {"module": "work_order", **data})
    assert confirmed.status_code == 201, confirmed.text
    body = confirmed.json()["data"]
    assert body["created"]["source"] == "alert"
    assert body["created"]["due_on"] == "2026-10-30"

    alerts = client.get(f"{PREFIX}/farm/alerts", headers=_hdr("u1", yard.id)).json()["data"]
    assert alerts[0]["status"] == "converted"
    assert alerts[0]["work_order_id"] == body["created"]["work_order_id"]
