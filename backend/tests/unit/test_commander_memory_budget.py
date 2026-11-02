"""记忆注入、预算视图与成本聚合（离线）。"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_current_user,
    get_db_session,
    get_yard_context,
)
from app.core.errors import (
    AppError,
    app_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.middleware import RequestIDMiddleware
from app.modules.commander import metrics, planner
from app.modules.commander import service as commander_service
from app.modules.commander.models import CommanderRun
from app.modules.llm.models import LLMCall
from app.modules.memory import service as memory_service
from app.modules.memory.models import SCOPE_USER, VISIBILITY_PRIVATE
from app.modules.memory.router import router as memory_router
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient


def test_planner_user_prompt_includes_memory_hint():
    prompt = planner._user_prompt(
        user_content="给小院做宣传 PPT",
        context="",
        catalog=[],
        max_subtasks=6,
        memory_hint="<memory>上次同类任务用了 12 页</memory>",
    )
    assert "历史记忆" in prompt
    assert "上次同类任务用了 12 页" in prompt
    assert "不是指令" in prompt


def test_run_out_reports_budget():
    run = CommanderRun(
        yard_id="y",
        session_id="s",
        user_id="u",
        goal="做 PPT",
        plan_json={"fallback": False},
        status="completed",
        tokens_total=850,
        token_budget=1000,
        budget_status="",
        roles_json=[],
    )
    out = commander_service.run_out(run, [])
    assert out["token_budget"] == 1000
    assert out["budget_ratio"] == 0.85
    assert out["budget_status"] == "warning"
    assert out["budget_remaining"] == 150


def test_run_out_reports_exceeded_budget():
    run = CommanderRun(
        yard_id="y",
        session_id="s",
        user_id="u",
        goal="做 PPT",
        plan_json={"fallback": False},
        status="failed",
        tokens_total=1200,
        token_budget=1000,
        budget_status="exceeded",
        roles_json=[],
    )
    out = commander_service.run_out(run, [])
    assert out["budget_ratio"] == 1.2
    assert out["budget_status"] == "exceeded"
    assert out["budget_remaining"] == 0


def test_cost_stats_groups_by_purpose(db_session):
    now = datetime.now(UTC)
    for purpose, model, input_tokens, output_tokens, cost in (
        ("commander_plan", "m1", 100, 50, 0.02),
        ("commander_plan", "m1", 200, 100, 0.04),
        ("declarative", "m2", 300, 150, 0.06),
    ):
        db_session.add(
            LLMCall(
                yard_id="yard-cost",
                provider="fake",
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=cost,
                status="success",
                request_id=f"req-{purpose}-{input_tokens}",
                prompt_sha="x" * 64,
                purpose=purpose,
                created_at=now,
            )
        )
    db_session.add(
        CommanderRun(
            yard_id="yard-cost",
            session_id="s",
            user_id="u",
            goal="g",
            plan_json={"fallback": False},
            status="completed",
            tokens_total=750,
            roles_json=[],
            created_at=now,
        )
    )
    db_session.commit()

    stats = metrics.cost_stats(db_session, "yard-cost", days=30)
    assert stats["total"]["calls"] == 3
    assert stats["total"]["tokens"] == 900
    assert stats["total"]["cost"] == 0.12
    assert stats["commander"]["runs"] == 1
    assert stats["commander"]["tokens"] == 750
    purposes = {item["purpose"]: item for item in stats["by_purpose"]}
    assert purposes["commander_plan"]["tokens"] == 450
    assert purposes["declarative"]["tokens"] == 450


def _client(db, ctx: YardContext) -> TestClient:
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(memory_router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_yard_context] = lambda: ctx
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=ctx.user_id, yard_id=ctx.yard_id, roles=list(ctx.roles), request_id=ctx.request_id
    )
    return TestClient(app)


def test_memory_api_list_and_search(db_session, monkeypatch):
    monkeypatch.setattr(memory_service, "_embed", lambda _text: [1.0, 0.0])
    ctx = YardContext(
        yard_id="yard-api", user_id="u-api", roles=["yard_admin"], request_id="req-api"
    )
    memory_service.write_memory(
        db_session,
        yard_id="yard-api",
        scope_type=SCOPE_USER,
        scope_key="u-api",
        kind="fact",
        title="偏好",
        content="汇报材料使用蓝色主题",
        visibility=VISIBILITY_PRIVATE,
        created_by="u-api",
    )
    db_session.commit()

    client = _client(db_session, ctx)
    listed = client.get("/api/v1/commander/memories")
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["title"] == "偏好"

    searched = client.post(
        "/api/v1/commander/memories/search", json={"query": "蓝色主题", "top_k": 3}
    )
    assert searched.status_code == 200
    assert searched.json()["data"]["items"][0]["content"] == "汇报材料使用蓝色主题"


def test_memory_summary_requires_manager(db_session):
    member = YardContext(yard_id="yard-api", user_id="u-member", roles=[], request_id="req-member")
    response = _client(db_session, member).post("/api/v1/commander/memories/summary", json={})
    assert response.status_code == 403

    admin = YardContext(
        yard_id="yard-api", user_id="u-admin", roles=["yard_admin"], request_id="req-admin"
    )
    response = _client(db_session, admin).post(
        "/api/v1/commander/memories/summary", json={"day": "2026-11-01"}
    )
    assert response.status_code == 200
    assert response.json()["data"]["created"] is False
