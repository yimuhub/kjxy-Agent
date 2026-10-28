"""农业台账（v3.8）：地块/周期/记录/报表与小院边界。"""

from datetime import date

import pytest
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.auth import repository as auth_repo
from app.modules.farm import service
from app.modules.farm.schemas import (
    CycleCreate,
    PlotCreate,
    PlotUpdate,
    RecordCreate,
    ReportRequest,
)
from app.modules.yards import repository as yard_repo


@pytest.fixture()
def world(db_session):
    yard = yard_repo.create_yard(db_session, name="台账小院")
    other = yard_repo.create_yard(db_session, name="别家小院")
    owner = auth_repo.create_user(db_session, username="farm-owner", password="pw-farm")
    viewer = auth_repo.create_user(db_session, username="farm-viewer", password="pw-farm")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="operator")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    db_session.commit()
    return {"yard": yard, "other": other, "owner": owner, "viewer": viewer}


def _plot(db, yard_id, user_id, name="东一号田"):
    return service.create_plot(db, yard_id, user_id, PlotCreate(name=name, area_mu=12.5))


def test_plot_crud_and_duplicate_name(db_session, world):
    yard_id, user_id = world["yard"].id, world["owner"].id
    plot = _plot(db_session, yard_id, user_id)
    assert plot["name"] == "东一号田"
    assert plot["area_mu"] == 12.5

    with pytest.raises(AppError) as dup:
        _plot(db_session, yard_id, user_id)
    assert dup.value.code == "DUPLICATE_NAME"

    updated = service.update_plot(
        db_session, yard_id, plot["plot_id"], user_id, PlotUpdate(crop="水稻", status="fallow")
    )
    assert updated["crop"] == "水稻"
    assert updated["status"] == "fallow"

    service.delete_plot(db_session, yard_id, plot["plot_id"], user_id)
    assert service.list_plots(db_session, yard_id) == []


def test_cross_yard_plot_is_404(db_session, world):
    plot = _plot(db_session, world["yard"].id, world["owner"].id)
    with pytest.raises(AppError) as exc:
        service.update_plot(
            db_session,
            world["other"].id,
            plot["plot_id"],
            world["owner"].id,
            PlotUpdate(crop="玉米"),
        )
    assert exc.value.code == "FARM_PLOT_NOT_FOUND"
    assert exc.value.http_status == 404


def test_cycle_links_to_plot_and_record_validates_owner(db_session, world):
    yard_id, user_id = world["yard"].id, world["owner"].id
    plot = _plot(db_session, yard_id, user_id)
    cycle = service.create_cycle(
        db_session,
        yard_id,
        plot["plot_id"],
        user_id,
        CycleCreate(crop="水稻", variety="南粳46", planted_on=date(2026, 5, 1)),
    )
    assert cycle["plot_id"] == plot["plot_id"]

    with pytest.raises(AppError) as bad_cycle:
        service.create_record(
            db_session,
            yard_id,
            user_id,
            RecordCreate(
                plot_id=plot["plot_id"],
                cycle_id="no-such-cycle",
                kind="fertilizing",
                occurred_on=date(2026, 5, 2),
            ),
        )
    assert bad_cycle.value.code == "INVALID_REQUEST"


def test_records_filter_paginate_and_block_plot_delete(db_session, world):
    yard_id, user_id = world["yard"].id, world["owner"].id
    plot = _plot(db_session, yard_id, user_id)
    for day, kind in ((1, "fertilizing"), (2, "irrigation"), (3, "pesticide")):
        service.create_record(
            db_session,
            yard_id,
            user_id,
            RecordCreate(
                plot_id=plot["plot_id"],
                kind=kind,
                occurred_on=date(2026, 6, day),
                title=f"{kind}-{day}",
                quantity=2.5 if kind == "fertilizing" else None,
                unit="公斤",
                operator_name="张三",
            ),
        )

    items, total = service.list_records(db_session, yard_id, kind="fertilizing")
    assert total == 1
    assert items[0]["kind_label"] == "施肥"
    assert items[0]["quantity"] == 2.5

    page, total = service.list_records(
        db_session,
        yard_id,
        start=date(2026, 6, 1),
        end=date(2026, 6, 2),
        page=1,
        page_size=1,
    )
    assert total == 2
    assert len(page) == 1
    assert page[0]["occurred_on"] == date(2026, 6, 2)  # 倒序

    with pytest.raises(AppError) as exc:
        service.delete_plot(db_session, yard_id, plot["plot_id"], user_id)
    assert exc.value.code == "FARM_PLOT_HAS_RECORDS"


def test_reports_group_by_kind_and_honesty(db_session, world):
    yard_id, user_id = world["yard"].id, world["owner"].id
    plot = _plot(db_session, yard_id, user_id)
    service.create_record(
        db_session,
        yard_id,
        user_id,
        RecordCreate(
            plot_id=plot["plot_id"],
            kind="sowing",
            occurred_on=date(2026, 6, 10),
            title="插秧",
            content="机械插秧\n完成 12 亩",
            operator_name="李四",
        ),
    )

    log = service.build_report(
        db_session,
        yard_id,
        ReportRequest(kind="log", start=date(2026, 6, 1), end=date(2026, 6, 30)),
    )
    assert log["records"] == 1
    assert "农事日志" in log["title"]
    assert "东一号田" in log["content_md"]
    assert "机械插秧 完成 12 亩" in log["content_md"]
    assert "操作人：李四" in log["content_md"]
    assert log["stats"]["by_kind"] == {"sowing": 1}

    weekly = service.build_report(
        db_session,
        yard_id,
        ReportRequest(kind="weekly", start=date(2026, 6, 1), end=date(2026, 6, 30)),
    )
    assert "2026-W24" in weekly["content_md"] or "2026-W25" in weekly["content_md"]

    empty = service.build_report(
        db_session,
        yard_id,
        ReportRequest(kind="quarterly", start=date(2025, 1, 1), end=date(2025, 3, 31)),
    )
    assert empty["records"] == 0
    assert "没有农事记录" in empty["content_md"]


def test_report_rejects_unknown_kind(db_session, world):
    with pytest.raises(AppError) as exc:
        service.build_report(
            db_session,
            world["yard"].id,
            ReportRequest(kind="yearly", start=date(2026, 1, 1), end=date(2026, 1, 2)),
        )
    assert exc.value.code == "INVALID_REQUEST"


# ---------------------------------------------------------------------------
# HTTP 层：权限与出参
# ---------------------------------------------------------------------------


def _hdr(user, yard, roles) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=roles)
    return {"Authorization": f"Bearer {token}"}


def test_api_viewer_reads_but_cannot_write(client, world):
    create = client.post(
        "/api/v1/farm/plots",
        json={"name": "南二号田", "crop": "小麦", "area_mu": 8},
        headers=_hdr(world["owner"], world["yard"], ["operator"]),
    )
    assert create.status_code == 201, create.text
    plot_id = create.json()["data"]["plot_id"]

    read = client.get(
        "/api/v1/farm/plots", headers=_hdr(world["viewer"], world["yard"], ["viewer"])
    )
    assert read.status_code == 200
    assert [p["plot_id"] for p in read.json()["data"]] == [plot_id]

    denied = client.post(
        "/api/v1/farm/plots",
        json={"name": "越权田"},
        headers=_hdr(world["viewer"], world["yard"], ["viewer"]),
    )
    assert denied.status_code == 403

    record = client.post(
        "/api/v1/farm/records",
        json={
            "plot_id": plot_id,
            "kind": "irrigation",
            "occurred_on": "2026-06-01",
            "title": "滴灌",
            "quantity": 30,
            "unit": "立方米",
        },
        headers=_hdr(world["owner"], world["yard"], ["operator"]),
    )
    assert record.status_code == 201, record.text
    assert record.json()["data"]["kind_label"] == "灌溉"


def test_api_cross_yard_record_is_404(client, world):
    create = client.post(
        "/api/v1/farm/plots",
        json={"name": "本院地块"},
        headers=_hdr(world["owner"], world["yard"], ["operator"]),
    )
    plot_id = create.json()["data"]["plot_id"]

    denied = client.patch(
        f"/api/v1/farm/plots/{plot_id}",
        json={"crop": "越权"},
        headers=_hdr(world["owner"], world["other"], ["operator"]),
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "FARM_PLOT_NOT_FOUND"
