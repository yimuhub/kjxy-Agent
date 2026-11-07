"""投入产出看板 API 集成测试（v3.71）：/yards/current/owner/roi。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.deps import get_db_session
from app.core.errors import AppError, app_error_handler, validation_error_handler
from app.core.security import create_access_token
from app.modules.auth.models import User
from app.modules.commander.models import CommanderRun
from app.modules.farm.models import CropDiagnosis, FarmRecord, WorkOrder
from app.modules.yards.models import Yard, YardMembership
from app.modules.yards.owner_router import router as owner_router
from app.modules.yards.repository import create_yard
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

BASE = "/api/v1/yards/current/owner"


def _user(db, username: str, **kwargs) -> User:
    user = User(
        username=username,
        email=kwargs.pop("email", f"{username}@example.com"),
        phone=kwargs.pop("phone", None),
        password_hash="x",
        display_name=kwargs.pop("display_name", username),
        status="active",
        token_version=0,
        **kwargs,
    )
    db.add(user)
    db.flush()
    return user


def _hdr(user: User, yard: Yard, role: str) -> dict[str, str]:
    token = create_access_token(user_id=user.id, yard_id=yard.id, roles=[role])
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def api_client(db_session):
    app = FastAPI()
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.include_router(owner_router, prefix="/api/v1")

    def override_db():
        yield db_session

    app.dependency_overrides[get_db_session] = override_db
    return TestClient(app)


def test_roi_aggregates_everything(api_client, db_session):
    yard = create_yard(db_session, name="ROI小院")
    owner = _user(db_session, "roi_owner")
    db_session.add(YardMembership(yard_id=yard.id, user_id=owner.id, role="yard_owner"))
    now = datetime.now(UTC)

    # 工单：2 闭环（1 有 closed_at 算时长）、1 活跃逾期、1 活跃正常
    db_session.add_all(
        [
            WorkOrder(
                yard_id=yard.id,
                title="已闭环",
                status="done",
                created_at=now - timedelta(hours=12),
                closed_at=now - timedelta(hours=6),
            ),
            WorkOrder(
                yard_id=yard.id,
                title="已关闭无时长",
                status="closed",
                closed_at=None,
            ),
            WorkOrder(
                yard_id=yard.id,
                title="逾期中",
                status="in_progress",
                due_on=(now - timedelta(days=3)).date(),
            ),
            WorkOrder(yard_id=yard.id, title="正常在办", status="open"),
        ]
    )
    # 诊断：1 已复核（2 小时）
    db_session.add(
        CropDiagnosis(
            yard_id=yard.id,
            crop="水稻",
            symptoms="叶黄",
            status="reviewed",
            created_at=now - timedelta(hours=2),
            expert_reviewed_at=now,
        )
    )
    # 指挥官：1 成功 1 失败
    db_session.add_all(
        [
            CommanderRun(
                yard_id=yard.id,
                session_id="s1",
                user_id=owner.id,
                goal="g",
                status="completed",
                tokens_total=1200,
            ),
            CommanderRun(
                yard_id=yard.id,
                session_id="s1",
                user_id=owner.id,
                goal="g2",
                status="failed",
                tokens_total=300,
            ),
        ]
    )
    db_session.add(
        FarmRecord(
            yard_id=yard.id,
            plot_id="p1",
            kind="irrigate",
            occurred_on=now.date(),
            title="浇水",
            content="浇水",
        )
    )
    db_session.commit()

    resp = api_client.get(f"{BASE}/roi", headers=_hdr(owner, yard, "yard_owner"))
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]

    assert data["work_orders"]["total"] == 4
    assert data["work_orders"]["closed"] == 2
    assert data["work_orders"]["active"] == 2
    assert data["work_orders"]["overdue"] == 1
    assert data["work_orders"]["close_rate"] == 50.0
    assert data["work_orders"]["avg_close_hours"] == 6.0

    assert data["diagnoses"]["total"] == 1
    assert data["diagnoses"]["reviewed"] == 1
    assert data["diagnoses"]["avg_review_hours"] == 2.0

    assert data["commander_runs"]["total"] == 2
    assert data["commander_runs"]["completed"] == 1
    assert data["commander_runs"]["success_rate"] == 50.0
    assert data["commander_runs"]["tokens"] == 1500

    assert data["assets"]["farm_records"] >= 1
    # 人时：2 工单×0.25 + 1 诊断×0.5 + 1 run×1.0 = 2.0
    assert data["saved_hours_estimate"] == 2.0
    assert data["saved_hours_assumptions"]["completed_run_hours"] == 1.0


def test_roi_requires_insights_read(api_client, db_session):
    yard = create_yard(db_session, name="ROI权限小院")
    member = _user(db_session, "roi_member")
    db_session.add(YardMembership(yard_id=yard.id, user_id=member.id, role="viewer"))
    db_session.commit()
    resp = api_client.get(f"{BASE}/roi", headers=_hdr(member, yard, "viewer"))
    assert resp.status_code == 403
