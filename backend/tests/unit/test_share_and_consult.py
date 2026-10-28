"""跨院只读分享与诊断会诊（v3.12）。

口径：
1. 分享是快照：创建后再改业务数据不影响已发链接；
2. 对外内容脱敏：报表隐去操作人，诊断隐去地块/图片/人员；
3. 撤销与过期统一 404；令牌只返回一次，库里只存哈希；
4. 诊断会诊复用工单闭环，未结束时不重复派单。
"""

from datetime import date, timedelta

import pytest
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.auth import repository as auth_repo
from app.modules.farm import diagnosis_service, workorder_service
from app.modules.farm import service as farm_service
from app.modules.farm.schemas import (
    DiagnosisConsultRequest,
    DiagnosisCreate,
    PlotCreate,
    RecordCreate,
    WorkOrderReceipt,
)
from app.modules.share import service as share_service
from app.modules.share.models import ShareLink
from app.modules.share.schemas import ShareCreate
from app.modules.yards import repository as yard_repo
from sqlalchemy import select


@pytest.fixture()
def world(db_session):
    yard = yard_repo.create_yard(db_session, name="分享小院")
    other = yard_repo.create_yard(db_session, name="别家小院")
    admin = auth_repo.create_user(db_session, username="share-admin", password="pw-share")
    worker = auth_repo.create_user(db_session, username="share-worker", password="pw-share")
    viewer = auth_repo.create_user(db_session, username="share-viewer", password="pw-share")
    outsider = auth_repo.create_user(db_session, username="share-out", password="pw-share")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=admin.id, role="yard_admin")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=worker.id, role="operator")
    yard_repo.add_membership(db_session, yard_id=yard.id, user_id=viewer.id, role="viewer")
    yard_repo.add_membership(db_session, yard_id=other.id, user_id=outsider.id, role="operator")
    db_session.commit()
    return {
        "yard": yard,
        "other": other,
        "admin": admin,
        "worker": worker,
        "viewer": viewer,
        "outsider": outsider,
    }


def _record(db, yard_id, user_id, plot_name="东一号田", operator="张三"):
    plot = farm_service.create_plot(db, yard_id, user_id, PlotCreate(name=plot_name))
    farm_service.create_record(
        db,
        yard_id,
        user_id,
        RecordCreate(
            plot_id=plot["plot_id"],
            kind="fertilizing",
            occurred_on=date(2026, 10, 1),
            title="追施复合肥",
            content="每亩 15 公斤",
            operator_name=operator,
        ),
    )
    return plot


def _report_share(db, yard_id, user_id, **kw):
    return share_service.create_share(
        db,
        yard_id,
        user_id,
        ShareCreate(
            resource_type="farm_report",
            report_kind="log",
            start=date(2026, 10, 1),
            end=date(2026, 10, 31),
            **kw,
        ),
    )


def test_report_share_redacts_operator_and_counts_views(db_session, world):
    _record(db_session, world["yard"].id, world["admin"].id)
    created = _report_share(db_session, world["yard"].id, world["admin"].id)
    assert created["token"] and created["path"].startswith("/share/")

    public = share_service.resolve_public(db_session, created["token"])
    assert "追施复合肥" in public["content_md"]
    assert "张三" not in public["content_md"]
    assert "只读快照" in public["content_md"]
    assert public["view_count"] == 1

    again = share_service.resolve_public(db_session, created["token"])
    assert again["view_count"] == 2


def test_share_token_stored_hashed_only(db_session, world):
    _record(db_session, world["yard"].id, world["admin"].id)
    created = _report_share(db_session, world["yard"].id, world["admin"].id)
    row = db_session.scalar(select(ShareLink).where(ShareLink.id == created["share_id"]))
    assert row is not None
    assert created["token"] not in row.token_hash
    assert len(row.token_hash) == 64


def test_share_revoked_and_expired_are_404(db_session, world):
    _record(db_session, world["yard"].id, world["admin"].id)
    revoked = _report_share(db_session, world["yard"].id, world["admin"].id)
    share_service.revoke_share(db_session, world["yard"].id, revoked["share_id"], world["admin"].id)
    with pytest.raises(AppError) as exc:
        share_service.resolve_public(db_session, revoked["token"])
    assert exc.value.code == "SHARE_NOT_FOUND"

    expired = _report_share(db_session, world["yard"].id, world["admin"].id)
    row = db_session.scalar(select(ShareLink).where(ShareLink.id == expired["share_id"]))
    assert row is not None
    row.expires_at = row.expires_at - timedelta(days=30)
    db_session.commit()
    with pytest.raises(AppError) as exc2:
        share_service.resolve_public(db_session, expired["token"])
    assert exc2.value.code == "SHARE_NOT_FOUND"


def test_share_is_yard_scoped(db_session, world):
    _record(db_session, world["yard"].id, world["admin"].id)
    created = _report_share(db_session, world["yard"].id, world["admin"].id)
    assert share_service.list_shares(db_session, world["other"].id) == []
    with pytest.raises(AppError) as exc:
        share_service.revoke_share(
            db_session, world["other"].id, created["share_id"], world["outsider"].id
        )
    assert exc.value.code == "SHARE_NOT_FOUND"


def test_diagnosis_share_excludes_plot_and_people(db_session, world):
    plot = _record(db_session, world["yard"].id, world["admin"].id, plot_name="西三号棚")
    diag = diagnosis_service.create_diagnosis(
        db_session,
        world["yard"].id,
        world["admin"].id,
        DiagnosisCreate(
            crop="番茄",
            symptoms="叶片出现褐色斑点，边缘发黄",
            plot_id=plot["plot_id"],
        ),
    )
    created = share_service.create_share(
        db_session,
        world["yard"].id,
        world["admin"].id,
        ShareCreate(resource_type="diagnosis", diagnosis_id=diag["diagnosis_id"], expires_days=3),
    )
    public = share_service.resolve_public(db_session, created["token"])
    assert "番茄" in public["content_md"]
    assert "褐色斑点" in public["content_md"]
    assert "西三号棚" not in public["content_md"]
    assert "share-admin" not in public["content_md"]
    assert "免责" in public["content_md"] or "专家复核" in public["content_md"]


def test_consult_creates_linked_work_order_and_guards_duplicates(db_session, world):
    diag = diagnosis_service.create_diagnosis(
        db_session,
        world["yard"].id,
        world["admin"].id,
        DiagnosisCreate(crop="小麦", symptoms="叶尖枯黄，怀疑冻害"),
    )
    data = diagnosis_service.request_consult(
        db_session,
        world["yard"].id,
        diag["diagnosis_id"],
        world["admin"].id,
        DiagnosisConsultRequest(expert_id=world["worker"].id, note="请尽快看看"),
    )
    order = data["work_order"]
    assert order["source"] == "diagnosis"
    assert order["source_ref"] == diag["diagnosis_id"]
    assert order["priority"] == "high"
    assert order["assignee_id"] == world["worker"].id
    assert data["diagnosis"]["status"] == "expert_review"

    with pytest.raises(AppError) as exc:
        diagnosis_service.request_consult(
            db_session,
            world["yard"].id,
            diag["diagnosis_id"],
            world["admin"].id,
            DiagnosisConsultRequest(),
        )
    assert exc.value.code == "CONSULT_ALREADY_OPEN"

    workorder_service.submit_receipt(
        db_session,
        world["yard"].id,
        order["work_order_id"],
        world["worker"].id,
        WorkOrderReceipt(resolution="已现场确认，按冻害处理", close=True),
    )
    # 会诊工单结束后可以再次发起（例如复发）
    again = diagnosis_service.request_consult(
        db_session,
        world["yard"].id,
        diag["diagnosis_id"],
        world["admin"].id,
        DiagnosisConsultRequest(),
    )
    assert again["work_order"]["work_order_id"] != order["work_order_id"]


def _hdr(user, yard, roles) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=roles)
    return {"Authorization": f"Bearer {token}"}


def test_api_share_permissions_and_public_read(client, db_session, world):
    _record(db_session, world["yard"].id, world["admin"].id)
    hdr_admin = _hdr(world["admin"], world["yard"], ["yard_admin"])
    hdr_viewer = _hdr(world["viewer"], world["yard"], ["viewer"])

    payload = {
        "resource_type": "farm_report",
        "report_kind": "log",
        "start": "2026-10-01",
        "end": "2026-10-31",
        "expires_days": 7,
    }
    denied = client.post("/api/v1/shares", json=payload, headers=hdr_viewer)
    assert denied.status_code == 403

    created = client.post("/api/v1/shares", json=payload, headers=hdr_admin)
    assert created.status_code == 201, created.text
    data = created.json()["data"]
    token = data["token"]

    listed = client.get("/api/v1/shares", headers=hdr_admin)
    assert listed.status_code == 200
    assert any(item["share_id"] == data["share_id"] for item in listed.json()["data"])
    assert "token" not in listed.json()["data"][0]

    public = client.get(f"/api/v1/public/shares/{token}")
    assert public.status_code == 200
    assert "追施复合肥" in public.json()["data"]["content_md"]

    revoked = client.delete(f"/api/v1/shares/{data['share_id']}", headers=hdr_admin)
    assert revoked.status_code == 200
    gone = client.get(f"/api/v1/public/shares/{token}")
    assert gone.status_code == 404

    consult_denied = client.post(
        "/api/v1/farm/diagnoses/nope/consult",
        json={},
        headers=hdr_viewer,
    )
    assert consult_denied.status_code == 403
