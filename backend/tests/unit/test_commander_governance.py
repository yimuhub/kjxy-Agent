"""监督测评、人格锁定与版本快照（离线规则评分）。"""

from __future__ import annotations

import asyncio
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
from app.modules.commander import evaluator, planner, snapshots
from app.modules.commander import settings as commander_settings
from app.modules.commander.governance_models import CommanderEvaluation, CommanderSnapshot
from app.modules.commander.models import CommanderRun, CommanderSubtask
from app.modules.commander.router import router as commander_router
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient


def _ctx(roles: list[str] | None = None, user_id: str = "u-admin") -> YardContext:
    return YardContext(
        yard_id="yard-p1",
        user_id=user_id,
        roles=roles if roles is not None else ["yard_admin"],
        request_id="req-p1",
    )


def _seed_aggressive_week(db) -> None:  # type: ignore[no-untyped-def]
    now = datetime.now(UTC)
    for index in range(3):
        run = CommanderRun(
            yard_id="yard-p1",
            session_id=f"s-{index}",
            user_id="u-admin",
            goal="做方案",
            plan_json={"fallback": False},
            status="completed",
            tokens_total=500,
            roles_json=["yard_admin"],
            created_at=now,
        )
        db.add(run)
        db.flush()
        db.add(
            CommanderSubtask(
                run_id=run.id,
                yard_id="yard-p1",
                seq=1,
                title="执行",
                instruction="执行",
                status="ok",
                attempts=3,
                agent_source="generated",
                remediation_json=[{"round": 1, "action": "new_agent"}],
                artifact_json=[{"type": "doc"}],
                created_at=now,
            )
        )
    db.commit()


def test_classify_persona_by_risk_budget():
    base = {
        "runs": {
            "total": 4,
            "fallback": 0,
            "degraded": 0,
            "budget_warning": 0,
            "budget_exceeded": 0,
            "completed": 4,
            "partial": 0,
            "failed": 0,
            "tokens": 0,
        },
        "subtasks": {
            "total": 8,
            "ok": 8,
            "error": 0,
            "skipped": 0,
            "extra_attempts": 8,
            "remediated": 6,
            "remediated_ok": 6,
            "generated_agents_used": 6,
            "artifacts": 4,
        },
        "errors": {"top_codes": [], "distinct": 0},
        "cost": {"calls": 4, "input_tokens": 100, "output_tokens": 50, "estimated": 0.01},
        "memory": {"episodes": 3, "summaries": 1},
        "auto_created_agents": 2,
        "latency": {"p90": 10, "max": 20},
    }
    persona, risk, parts = evaluator.classify_persona(base)
    assert persona == "aggressive"
    assert risk > 0.45
    assert parts["generated_agent_rate"] == 0.75

    steady = {**base, "subtasks": {**base["subtasks"], "extra_attempts": 0, "remediated": 0,
                                  "generated_agents_used": 0}}
    persona, risk, _ = evaluator.classify_persona(steady)
    assert persona == "steady"
    assert risk <= 0.2


def test_planner_prompt_includes_persona():
    aggressive = planner._system_prompt("aggressive")
    steady = planner._system_prompt("steady")
    assert "进取型" in aggressive
    assert "稳健保守型" in steady
    assert aggressive != steady


def test_run_evaluation_applies_persona_and_snapshot(db_session):
    _seed_aggressive_week(db_session)
    row, created = asyncio.run(
        evaluator.run_evaluation(db_session, _ctx(), use_llm=False)
    )
    db_session.commit()

    assert created is True
    assert row.persona == "aggressive"
    assert row.persona_applied is True
    assert row.source == "rule"
    assert 0 <= evaluator.total_score(row.scores_json) <= 100
    assert "人格判定" in row.report_md
    assert commander_settings.settings_for(db_session, "yard-p1")["persona"] == "aggressive"
    assert db_session.query(CommanderSnapshot).filter_by(source="persona").count() == 1

    again, created_again = asyncio.run(
        evaluator.run_evaluation(db_session, _ctx(), use_llm=False)
    )
    assert created_again is False
    assert again.id == row.id
    assert db_session.query(CommanderEvaluation).count() == 1


def test_persona_lock_blocks_auto_switch(db_session):
    _seed_aggressive_week(db_session)
    commander_settings.update_settings(
        db_session,
        yard_id="yard-p1",
        payload={"persona_locked": True},
        actor_id="u-admin",
        request_id="req-p1",
    )
    row, _ = asyncio.run(evaluator.run_evaluation(db_session, _ctx(), use_llm=False))
    db_session.commit()

    assert row.persona == "aggressive"
    assert row.persona_applied is False
    assert row.metrics_json["persona_reason"] == "persona_locked"
    assert commander_settings.settings_for(db_session, "yard-p1")["persona"] == "balanced"


def test_evolution_lock_blocks_auto_switch(db_session):
    _seed_aggressive_week(db_session)
    commander_settings.update_settings(
        db_session,
        yard_id="yard-p1",
        payload={"evolution_lock": True},
        actor_id="u-admin",
        request_id="req-p1",
    )
    row, _ = asyncio.run(evaluator.run_evaluation(db_session, _ctx(), use_llm=False))
    db_session.commit()

    assert row.persona_applied is False
    assert row.metrics_json["persona_reason"] == "evolution_locked"


def test_snapshot_restore_restores_settings_and_backs_up(db_session):
    commander_settings.update_settings(
        db_session,
        yard_id="yard-p1",
        payload={"persona": "aggressive", "max_parallel": 5},
        actor_id="u-admin",
        request_id="req-p1",
    )
    snap = snapshots.create_snapshot(
        db_session, yard_id="yard-p1", label="稳定版", created_by="u-admin"
    )
    db_session.commit()
    assert snap.payload_json["persona"] == "aggressive"

    commander_settings.update_settings(
        db_session,
        yard_id="yard-p1",
        payload={"persona": "steady", "max_parallel": 2},
        actor_id="u-admin",
        request_id="req-p1",
    )
    backup, restored = snapshots.restore_snapshot(
        db_session,
        yard_id="yard-p1",
        snapshot_id=snap.id,
        actor_id="u-admin",
        request_id="req-p1",
    )
    db_session.commit()

    assert restored["persona"] == "aggressive"
    assert restored["max_parallel"] == 5
    assert backup.source == "restore"
    assert db_session.query(CommanderSnapshot).count() == 2


def _client(db, ctx: YardContext) -> TestClient:  # type: ignore[no-untyped-def]
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    app.add_exception_handler(AppError, app_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
    app.include_router(commander_router, prefix="/api/v1")
    app.dependency_overrides[get_db_session] = lambda: db
    app.dependency_overrides[get_yard_context] = lambda: ctx
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        user_id=ctx.user_id, yard_id=ctx.yard_id, roles=list(ctx.roles), request_id=ctx.request_id
    )
    return TestClient(app)


def test_evaluation_and_snapshot_api_permissions(db_session):
    member = _client(db_session, _ctx(roles=[], user_id="u-member"))
    assert member.get("/api/v1/commander/evaluations").status_code == 403
    assert member.post("/api/v1/commander/snapshots", json={}).status_code == 403

    admin = _client(db_session, _ctx())
    run = admin.post("/api/v1/commander/evaluations/run", json={"use_llm": False})
    assert run.status_code == 200
    assert run.json()["data"]["evaluation"]["source"] == "rule"
    assert run.json()["data"]["created"] is True

    listed = admin.get("/api/v1/commander/evaluations")
    assert listed.status_code == 200
    assert len(listed.json()["data"]["items"]) == 1

    created = admin.post(
        "/api/v1/commander/snapshots", json={"label": "发布前", "reason": "手动"}
    )
    assert created.status_code == 200
    snapshot_id = created.json()["data"]["snapshot"]["id"]
    restore = admin.post(f"/api/v1/commander/snapshots/{snapshot_id}/restore")
    assert restore.status_code == 200
    assert restore.json()["data"]["backup"]["source"] == "restore"
