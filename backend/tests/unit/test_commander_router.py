"""指挥官 REST：preview 只规划不执行、stats、计划编辑与参数校验（离线）。"""

from __future__ import annotations

import pytest
from app.core.deps import CurrentUser, get_current_user, get_db_session
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.middleware import RequestIDMiddleware
from app.modules.chat.models import ChatSession
from app.modules.commander import metrics
from app.modules.commander import service as commander_service
from app.modules.commander.models import CommanderRun, CommanderSubtask
from app.modules.commander.router import router as commander_router
from app.modules.yards.repository import create_yard
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture()
def yard(db_session):
    row = create_yard(db_session, name="指挥官路由小院")
    db_session.commit()
    return row


@pytest.fixture()
def user(yard):
    return CurrentUser(
        user_id="u-router", yard_id=yard.id, roles=["yard_admin"], request_id="req-router"
    )


def _client(db, user) -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(commander_router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _run(db, yard, *, status: str = "queued", plan: dict | None = None) -> CommanderRun:
    run = CommanderRun(
        yard_id=yard.id,
        session_id="s-router",
        user_id="u-router",
        goal="路由目标",
        plan_json=plan or {"fallback": False, "subtasks": []},
        status=status,
        max_parallel=3,
        effective_parallel=3,
        degraded=False,
        tokens_total=9,
        latency_ms=0,
        roles_json=["yard_admin"],
    )
    db.add(run)
    db.commit()
    return run


def test_preview_creates_run_without_executing(db_session, yard, user, monkeypatch):
    run = _run(
        db_session,
        yard,
        plan={
            "goal": "做方案",
            "fallback": False,
            "subtasks": [],
            "clarify": ["面向哪个作物？"],
        },
    )
    calls: list[dict] = []

    async def fake_create(_db, *, ctx, session_id, user_content, context=""):
        calls.append(
            {
                "yard_id": ctx.yard_id,
                "session_id": session_id,
                "content": user_content,
                "context": context,
            }
        )
        return run

    async def must_not_execute(*_args, **_kwargs):
        raise AssertionError("preview 不允许执行")

    monkeypatch.setattr(commander_service, "create_plan_run", fake_create)
    monkeypatch.setattr(commander_service, "execute_run", must_not_execute)

    response = _client(db_session, user).post(
        "/api/v1/commander/preview",
        json={"content": "帮我做个方案", "session_id": "s-router", "context": "附件摘要"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["run"]["id"] == run.id
    assert data["clarify"] == ["面向哪个作物？"]
    assert data["run"]["clarify"] == ["面向哪个作物？"]
    assert calls == [
        {
            "yard_id": yard.id,
            "session_id": "s-router",
            "content": "帮我做个方案",
            "context": "附件摘要",
        }
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"content": "", "session_id": "s-router"},
        {"content": "x" * 4001, "session_id": "s-router"},
        {"content": "做方案"},
        {"content": "做方案", "session_id": "s-router", "unknown": 1},
    ],
)
def test_preview_body_validation(db_session, yard, user, payload):
    response = _client(db_session, user).post("/api/v1/commander/preview", json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_preview_rejects_session_of_another_user(db_session, yard, user):
    db_session.add(ChatSession(yard_id=yard.id, created_by="someone-else", title="别人的会话"))
    db_session.commit()
    session = db_session.scalar(select(ChatSession).where(ChatSession.created_by == "someone-else"))
    response = _client(db_session, user).post(
        "/api/v1/commander/preview",
        json={"content": "帮我做个方案", "session_id": session.id},
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "COMMANDER_RUN_NOT_FOUND"


def test_stats_endpoint_clamps_days(db_session, yard, user, monkeypatch):
    seen: list[int] = []

    def fake_stats(_db, yard_id, days=30):
        seen.append(days)
        assert yard_id == yard.id
        return {"runs": {"total": 0}}

    monkeypatch.setattr(metrics, "stats", fake_stats)
    client = _client(db_session, user)
    assert client.get("/api/v1/commander/stats?days=999").json()["data"]["stats"] == {
        "runs": {"total": 0}
    }
    assert client.get("/api/v1/commander/stats?days=0").status_code == 200
    assert seen == [365, 1]


def test_stats_requires_yard_context(db_session, user):
    orphan = CurrentUser(
        user_id="u-router", yard_id=None, roles=["yard_admin"], request_id="req-router"
    )
    response = _client(db_session, orphan).get("/api/v1/commander/stats")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "YARD_REQUIRED"


def test_edit_plan_endpoint_replaces_subtasks(db_session, yard, user):
    run = _run(db_session, yard)
    db_session.add(
        CommanderSubtask(
            run_id=run.id,
            yard_id=yard.id,
            seq=1,
            title="旧任务",
            instruction="旧指令",
            expected_output="",
            agent_source="commander",
            agent_name="指挥官",
            status="pending",
            depends_on_json=[],
            attempts=0,
        )
    )
    db_session.commit()

    response = _client(db_session, user).put(
        f"/api/v1/commander/runs/{run.id}/plan",
        json={
            "subtasks": [
                {"title": "第一步", "instruction": "先调研", "agent_id": None},
                {"title": "第二步", "instruction": "再写稿", "depends_on": [1]},
            ]
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]["run"]
    assert [item["title"] for item in data["subtasks"]] == ["第一步", "第二步"]
    assert [item["seq"] for item in data["subtasks"]] == [1, 2]
    assert data["subtasks"][1]["depends_on"] == [1]

    db_session.expire_all()
    rows = list(
        db_session.scalars(
            select(CommanderSubtask)
            .where(CommanderSubtask.run_id == run.id)
            .order_by(CommanderSubtask.seq.asc())
        )
    )
    assert [row.title for row in rows] == ["第一步", "第二步"]


def test_edit_plan_rejects_non_queued_run(db_session, yard, user):
    run = _run(db_session, yard, status="completed")
    response = _client(db_session, user).put(
        f"/api/v1/commander/runs/{run.id}/plan",
        json={"subtasks": [{"title": "改", "instruction": "改"}]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COMMANDER_RUN_STATE"
