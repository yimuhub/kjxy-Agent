"""离线写入队列后端（v3.19）：幂等重放与版本冲突。

覆盖台账（地块/农事记录）与工单（创建/回执）两类入口：
- 同 `client_op_id` 重放只落一条、不改版本；
- 带旧 `expected_revision` 的修改返回 409，并回传服务器当前副本；
- 回执重放不会重复写事件或重复关闭。
"""

from __future__ import annotations

import pytest
from app.core.security import create_access_token
from app.modules.auth.repository import create_user
from app.modules.farm.models import FarmPlot, FarmRecord, WorkOrder, WorkOrderEvent
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="离线同步小院")
    user = create_user(db_session, username="offline_expert", password="Passw0rd123")
    add_membership(db_session, yard_id=yard.id, user_id=user.id, role="expert")
    db_session.commit()
    return {"yard": yard, "user": user}


def _hdr(world) -> dict[str, str]:
    token = create_access_token(
        user_id=world["user"].id, yard_id=world["yard"].id, roles=["expert"]
    )
    return {"Authorization": f"Bearer {token}"}


def _plot(client, world, **extra) -> dict:
    body = {"name": "离线试验田", "crop": "水稻", **extra}
    resp = client.post(f"{PREFIX}/farm/plots", json=body, headers=_hdr(world))
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


# ---------------------------------------------------------------- 地块


def test_plot_create_replay_returns_same_row(client, db_session, world):
    body = {"name": "重放地块", "client_op_id": "op-plot-0001"}
    first = client.post(f"{PREFIX}/farm/plots", json=body, headers=_hdr(world))
    second = client.post(f"{PREFIX}/farm/plots", json=body, headers=_hdr(world))
    assert first.status_code == second.status_code == 201
    a, b = first.json()["data"], second.json()["data"]
    assert a["plot_id"] == b["plot_id"]
    assert a["revision"] == b["revision"] == 1
    assert db_session.query(FarmPlot).filter_by(name="重放地块").count() == 1


def test_plot_update_rejects_stale_revision(client, world):
    plot = _plot(client, world, name="版本地块")
    url = f"{PREFIX}/farm/plots/{plot['plot_id']}"

    ok = client.patch(
        url, json={"expected_revision": 1, "notes": "第一次改动"}, headers=_hdr(world)
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["revision"] == 2
    assert ok.json()["data"]["notes"] == "第一次改动"

    stale = client.patch(
        url, json={"expected_revision": 1, "notes": "拿着旧版本再改"}, headers=_hdr(world)
    )
    assert stale.status_code == 409, stale.text
    err = stale.json()["error"]
    assert err["code"] == "FARM_REVISION_CONFLICT"
    assert err["details"]["current_revision"] == 2
    assert err["details"]["current"]["notes"] == "第一次改动"

    retry = client.patch(
        url, json={"expected_revision": 2, "notes": "看到最新版本后再改"}, headers=_hdr(world)
    )
    assert retry.status_code == 200
    assert retry.json()["data"]["revision"] == 3


def test_delete_checks_revision(client, db_session, world):
    plot = _plot(client, world, name="待删地块")
    url = f"{PREFIX}/farm/plots/{plot['plot_id']}"

    stale = client.delete(f"{url}?expected_revision=9", headers=_hdr(world))
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "FARM_REVISION_CONFLICT"
    assert db_session.query(FarmPlot).filter_by(id=plot["plot_id"]).count() == 1

    ok = client.delete(f"{url}?expected_revision=1", headers=_hdr(world))
    assert ok.status_code == 200
    db_session.expire_all()
    assert db_session.query(FarmPlot).filter_by(id=plot["plot_id"]).count() == 0


# ---------------------------------------------------------------- 农事记录


def test_record_replay_and_stale_update(client, db_session, world):
    plot = _plot(client, world, name="记录地块")
    body = {
        "plot_id": plot["plot_id"],
        "kind": "irrigation",
        "occurred_on": "2026-10-28",
        "title": "补灌一次",
        "client_op_id": "op-record-0001",
    }
    first = client.post(f"{PREFIX}/farm/records", json=body, headers=_hdr(world))
    second = client.post(f"{PREFIX}/farm/records", json=body, headers=_hdr(world))
    assert first.status_code == second.status_code == 201
    a, b = first.json()["data"], second.json()["data"]
    assert a["record_id"] == b["record_id"]
    assert a["revision"] == 1
    assert db_session.query(FarmRecord).filter_by(title="补灌一次").count() == 1

    url = f"{PREFIX}/farm/records/{a['record_id']}"
    patched = client.patch(
        url, json={"expected_revision": 1, "title": "补灌一次（复核）"}, headers=_hdr(world)
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["revision"] == 2
    stale = client.patch(
        url, json={"expected_revision": 1, "title": "旧版本覆盖"}, headers=_hdr(world)
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"]["current"]["title"] == "补灌一次（复核）"


def test_unknown_client_op_id_format_rejected(client, world):
    resp = client.post(
        f"{PREFIX}/farm/plots",
        json={"name": "非法键地块", "client_op_id": "short"},
        headers=_hdr(world),
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "INVALID_REQUEST"


# ---------------------------------------------------------------- 工单


def test_work_order_create_and_receipt_replay(client, db_session, world):
    body = {"title": "离线建单", "client_op_id": "op-order-0001"}
    first = client.post(f"{PREFIX}/farm/work-orders", json=body, headers=_hdr(world))
    second = client.post(f"{PREFIX}/farm/work-orders", json=body, headers=_hdr(world))
    assert first.status_code == second.status_code == 201
    a, b = first.json()["data"], second.json()["data"]
    assert a["work_order_id"] == b["work_order_id"]
    assert a["revision"] == 1
    assert db_session.query(WorkOrder).filter_by(title="离线建单").count() == 1

    order_id = a["work_order_id"]
    receipt = {"resolution": "已处理完毕", "close": True, "client_op_id": "op-receipt-0001"}
    r1 = client.post(
        f"{PREFIX}/farm/work-orders/{order_id}/receipt", json=receipt, headers=_hdr(world)
    )
    r2 = client.post(
        f"{PREFIX}/farm/work-orders/{order_id}/receipt", json=receipt, headers=_hdr(world)
    )
    assert r1.status_code == r2.status_code == 200, r1.text
    assert r1.json()["data"]["status"] == "closed"
    assert r2.json()["data"]["revision"] == r1.json()["data"]["revision"]
    db_session.expire_all()
    receipts = (
        db_session.query(WorkOrderEvent).filter_by(work_order_id=order_id, kind="receipt").count()
    )
    assert receipts == 1


def test_work_order_update_rejects_stale_revision(client, world):
    order = client.post(
        f"{PREFIX}/farm/work-orders", json={"title": "版本工单"}, headers=_hdr(world)
    ).json()["data"]
    url = f"{PREFIX}/farm/work-orders/{order['work_order_id']}"

    first = client.patch(
        url, json={"expected_revision": 1, "priority": "high"}, headers=_hdr(world)
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["revision"] == 2

    stale = client.patch(
        url, json={"expected_revision": 1, "priority": "urgent"}, headers=_hdr(world)
    )
    assert stale.status_code == 409
    err = stale.json()["error"]
    assert err["code"] == "FARM_REVISION_CONFLICT"
    assert err["details"]["current_revision"] == 2
    assert err["details"]["current"]["priority"] == "high"
