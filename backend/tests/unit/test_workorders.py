"""告警→工单→回执（v3.10）：自动转单、指派、状态机、回执、逾期升级。"""

from datetime import date, timedelta

import pytest
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.auth import repository as auth_repo
from app.modules.farm import workorder_service
from app.modules.farm.schemas import (
    AlertCreate,
    WorkOrderCreate,
    WorkOrderReceipt,
    WorkOrderUpdate,
)
from app.modules.yards import repository as yard_repo


@pytest.fixture()
def world(db_session):
    yard = yard_repo.create_yard(db_session, name="工单小院")
    other = yard_repo.create_yard(db_session, name="别家小院")
    admin = auth_repo.create_user(db_session, username="wo-admin", password="pw-wo")
    worker = auth_repo.create_user(db_session, username="wo-worker", password="pw-wo")
    outsider = auth_repo.create_user(db_session, username="wo-outsider", password="pw-wo")
    viewer = auth_repo.create_user(db_session, username="wo-viewer", password="pw-wo")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=worker.id, role="operator")
    yard_repo.add_membership(db_session, yard_id=other.id, user_id=outsider.id, role="operator")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    db_session.commit()
    return {
        "yard": yard,
        "other": other,
        "admin": admin,
        "worker": worker,
        "outsider": outsider,
        "viewer": viewer,
    }


def test_critical_alert_auto_creates_urgent_work_order(db_session, world):
    alert = workorder_service.create_alert(
        db_session,
        world["yard"].id,
        world["admin"].id,
        AlertCreate(title="稻飞虱爆发", severity="critical", detail="田块虫量超标"),
    )
    assert alert["status"] == "converted"
    assert alert["work_order_id"]

    order = workorder_service.get_work_order(db_session, world["yard"].id, alert["work_order_id"])
    assert order["priority"] == "urgent"
    assert order["source"] == "alert"
    assert order["source_ref"] == alert["alert_id"]
    assert order["status"] == "open"
    assert any(e["kind"] == "converted" for e in order["events"])


def test_manual_convert_and_duplicate_guard(db_session, world):
    alert = workorder_service.create_alert(
        db_session,
        world["yard"].id,
        world["admin"].id,
        AlertCreate(title="叶瘟预警", severity="high"),
    )
    assert alert["status"] == "new"
    order = workorder_service.convert_alert(
        db_session, world["yard"].id, alert["alert_id"], world["admin"].id
    )
    assert order["priority"] == "high"

    with pytest.raises(AppError) as exc:
        workorder_service.convert_alert(
            db_session, world["yard"].id, alert["alert_id"], world["admin"].id
        )
    assert exc.value.code == "ALERT_ALREADY_CONVERTED"


def test_assign_validate_and_status_transitions(db_session, world):
    order = workorder_service.create_work_order(
        db_session,
        world["yard"].id,
        world["admin"].id,
        WorkOrderCreate(title="清沟排水", priority="normal"),
    )
    with pytest.raises(AppError) as bad:
        workorder_service.update_work_order(
            db_session,
            world["yard"].id,
            order["work_order_id"],
            world["admin"].id,
            WorkOrderUpdate(assignee_id=world["outsider"].id),
        )
    assert bad.value.code == "INVALID_ASSIGNEE"

    assigned = workorder_service.update_work_order(
        db_session,
        world["yard"].id,
        order["work_order_id"],
        world["admin"].id,
        WorkOrderUpdate(assignee_id=world["worker"].id),
    )
    assert assigned["status"] == "assigned"
    assert assigned["assignee_name"] == "wo-worker"

    with pytest.raises(AppError) as skip:
        workorder_service.update_work_order(
            db_session,
            world["yard"].id,
            order["work_order_id"],
            world["admin"].id,
            WorkOrderUpdate(status="closed"),
        )
    assert skip.value.code == "INVALID_STATUS_TRANSITION"

    done = workorder_service.update_work_order(
        db_session,
        world["yard"].id,
        order["work_order_id"],
        world["worker"].id,
        WorkOrderUpdate(status="in_progress"),
    )
    assert done["status"] == "in_progress"


def test_receipt_closes_with_resolution_and_event(db_session, world):
    order = workorder_service.create_work_order(
        db_session,
        world["yard"].id,
        world["admin"].id,
        WorkOrderCreate(title="补施分蘖肥"),
    )
    receipt = workorder_service.submit_receipt(
        db_session,
        world["yard"].id,
        order["work_order_id"],
        world["worker"].id,
        WorkOrderReceipt(resolution="已按方案补施，苗情转绿", close=True),
    )
    assert receipt["status"] == "closed"
    assert receipt["resolution"] == "已按方案补施，苗情转绿"
    assert receipt["closed_at"] is not None
    assert receipt["events"][-1]["kind"] == "receipt"

    with pytest.raises(AppError) as again:
        workorder_service.submit_receipt(
            db_session,
            world["yard"].id,
            order["work_order_id"],
            world["worker"].id,
            WorkOrderReceipt(resolution="重复回执"),
        )
    assert again.value.code == "WORK_ORDER_CLOSED"


def test_escalate_and_overdue_scan(db_session, world):
    yesterday = date.today() - timedelta(days=1)
    order = workorder_service.create_work_order(
        db_session,
        world["yard"].id,
        world["admin"].id,
        WorkOrderCreate(title="逾期巡田", priority="normal", due_on=yesterday),
    )
    stats = workorder_service.escalate_overdue(db_session)
    assert stats == {"scanned": 1, "escalated": 1}
    detail = workorder_service.get_work_order(db_session, world["yard"].id, order["work_order_id"])
    assert detail["priority"] == "high"
    assert detail["escalation_level"] == 1

    escalated = workorder_service.escalate(
        db_session, world["yard"].id, order["work_order_id"], world["admin"].id
    )
    assert escalated["priority"] == "urgent"


def test_cross_yard_order_is_404(db_session, world):
    order = workorder_service.create_work_order(
        db_session,
        world["yard"].id,
        world["admin"].id,
        WorkOrderCreate(title="本院工单"),
    )
    with pytest.raises(AppError) as exc:
        workorder_service.get_work_order(db_session, world["other"].id, order["work_order_id"])
    assert exc.value.code == "WORK_ORDER_NOT_FOUND"


def _hdr(user, yard, roles) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=roles)
    return {"Authorization": f"Bearer {token}"}


def test_api_work_order_permissions(client, world):
    hdr_admin = _hdr(world["admin"], world["yard"], ["yard_admin"])
    hdr_viewer = _hdr(world["viewer"], world["yard"], ["viewer"])

    created = client.post(
        "/api/v1/farm/work-orders",
        json={"title": "清理排水沟", "priority": "high"},
        headers=hdr_admin,
    )
    assert created.status_code == 201, created.text
    order_id = created.json()["data"]["work_order_id"]

    assert client.get("/api/v1/farm/work-orders", headers=hdr_viewer).status_code == 200
    denied = client.post(
        "/api/v1/farm/work-orders",
        json={"title": "越权建单"},
        headers=hdr_viewer,
    )
    assert denied.status_code == 403

    assignable = client.get("/api/v1/farm/work-orders/assignable", headers=hdr_admin)
    assert assignable.status_code == 200
    usernames = {m["display_name"] for m in assignable.json()["data"]}
    assert "wo-worker" in usernames

    detail = client.get(f"/api/v1/farm/work-orders/{order_id}", headers=hdr_admin)
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "open"
