"""提示词模块、复盘/周报告与月度迭代。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

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
from app.modules.commander import planner, prompt_modules, reports
from app.modules.commander import settings as commander_settings
from app.modules.commander.evolution_models import CommanderIteration
from app.modules.commander.governance_models import CommanderSnapshot
from app.modules.commander.models import CommanderRun, CommanderSubtask
from app.modules.commander.router import router as commander_router
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker


def _ctx(roles: list[str] | None = None, user_id: str = "u-admin") -> YardContext:
    return YardContext(
        yard_id="yard-p3",
        user_id=user_id,
        roles=roles if roles is not None else ["yard_admin"],
        request_id="req-p3",
    )


def _run(
    db,  # type: ignore[no-untyped-def]
    *,
    status: str = "completed",
    tokens: int = 900,
    index: int = 0,
    created_at: datetime | None = None,
) -> CommanderRun:
    run = CommanderRun(
        yard_id="yard-p3",
        session_id=f"s-p3-{index}",
        user_id="u-admin",
        goal="做一份 8 页宣传 PPT",
        plan_json={"fallback": False},
        status=status,
        tokens_total=tokens,
        roles_json=["yard_admin"],
    )
    if created_at is not None:
        run.created_at = created_at
        run.updated_at = created_at
    db.add(run)
    db.flush()
    return run


def _subtask(db, run: CommanderRun, *, status: str = "ok", seq: int = 1):  # type: ignore[no-untyped-def]
    st = CommanderSubtask(
        run_id=run.id,
        yard_id=run.yard_id,
        seq=seq,
        title="生成宣传 PPT",
        instruction="整理素材并生成 8 页宣传 PPT。",
        expected_output="pptx",
        agent_id="agent-1",
        agent_code="ppt-agent",
        agent_name="演示助手",
        agent_source="existing",
        status=status,
        error_code="LLM_TIMEOUT" if status == "error" else None,
        output_excerpt="ok" if status == "ok" else None,
    )
    db.add(st)
    db.flush()
    return st


def test_prompt_modules_compose_respects_budget_and_toggles():
    core = "核心契约"
    persona = "当前人格：平衡型。"
    text = prompt_modules.compose(core, persona, ["delivery", "risk"], 4500)
    assert text.startswith(core)
    assert "当前人格" in text
    assert "【交付优先】" in text
    assert "【风险预防】" in text
    assert "【成本纪律】" not in text

    tiny = prompt_modules.compose(core, persona, list(prompt_modules.DEFAULT_MODULES), 2000)
    assert tiny.startswith(core)
    assert len(tiny) <= 2000

    assert prompt_modules.normalize_modules(["risk", "risk", "unknown"]) == ["risk"]
    assert prompt_modules.normalize_modules("bad") == list(prompt_modules.DEFAULT_MODULES)


def test_prompt_preview_reports_modules_and_chars(db_session):
    cfg = commander_settings.settings_for(db_session, "yard-p3")
    preview = planner.preview_system_prompt(cfg)
    assert preview["max_chars"] == 4500
    assert preview["chars"] == len(preview["system"])
    assert preview["tokens"] > 0
    assert {item["code"] for item in preview["modules"]} == set(prompt_modules.PROMPT_MODULES)
    assert all(item["enabled"] for item in preview["modules"])


def test_daily_and_weekly_reports_are_deterministic(db_session):
    now = datetime.now(UTC)
    run = _run(db_session, created_at=now)
    _subtask(db_session, run)
    _subtask(db_session, run, seq=2, status="error")
    db_session.commit()

    daily = reports.daily_report(db_session, yard_id="yard-p3", day=now.date())
    assert "每日复盘" in daily["markdown"]
    assert daily["metrics"]["runs"] == 1
    assert daily["metrics"]["ok"] == 1
    assert daily["metrics"]["failed"] == 1
    assert "LLM_TIMEOUT" in daily["markdown"]

    empty_day = reports.daily_report(
        db_session, yard_id="yard-p3", day=now.date() - timedelta(days=3)
    )
    assert empty_day["metrics"]["runs"] == 0
    assert "今日无运行" in empty_day["markdown"]

    weekly = reports.weekly_optimization(db_session, yard_id="yard-p3")
    assert weekly["week_key"].startswith(str(now.year))
    assert "每周优化报告" in weekly["markdown"]
    assert "## 五、下周建议" in weekly["markdown"]
    assert weekly["metrics"]["runs"] == 1
    assert weekly["evaluation"] is None


def test_run_iteration_returns_rival_row_on_concurrent_first_create(db_session, monkeypatch):
    """C11：同月并发首建撞 (yard_id, month_key) 唯一约束时返回对方那条，而不是抛 500。"""
    ctx = _ctx()
    real_create = reports.snapshots.create_snapshot
    planted = {"done": False}

    def plant(*args, **kwargs):
        snapshot = real_create(*args, **kwargs)
        # 在「查过没有」与「插入」之间插入对手的行：真实场景是定时任务与手动触发撞车
        if not planted["done"]:
            planted["done"] = True
            rival = sessionmaker(bind=db_session.get_bind())()
            try:
                rival.add(
                    CommanderIteration(
                        yard_id=ctx.yard_id,
                        month_key="2026-11",
                        version_label="V-202611-9",
                        persona="balanced",
                        summary="另一个任务先写入了",
                        report_md="# 对手",
                        metrics_json={},
                        changes_json={},
                        created_by="u-rival",
                    )
                )
                rival.commit()
            finally:
                rival.close()
        return snapshot

    monkeypatch.setattr(reports.snapshots, "create_snapshot", plant)
    row, created = reports.run_iteration(db_session, ctx=ctx, month="2026-11")
    db_session.commit()

    assert created is False
    assert row.version_label == "V-202611-9"
    assert row.summary == "另一个任务先写入了"
    assert db_session.query(CommanderIteration).count() == 1


def test_run_iteration_is_idempotent_and_snapshots(db_session):
    now = datetime.now(UTC)
    for index in range(2):
        run = _run(db_session, index=index, created_at=now)
        _subtask(db_session, run)
    db_session.commit()

    first, created = reports.run_iteration(db_session, ctx=_ctx(), month="2026-09")
    db_session.commit()
    assert created is True
    assert first.month_key == "2026-09"
    assert first.version_label == "V-202609-1"
    assert first.snapshot_id

    snapshot = db_session.get(CommanderSnapshot, first.snapshot_id)
    assert snapshot is not None
    assert snapshot.source == "monthly"

    again, created_again = reports.run_iteration(db_session, ctx=_ctx(), month="2026-09")
    db_session.commit()
    assert created_again is False
    assert again.id == first.id

    second, created_second = reports.run_iteration(db_session, ctx=_ctx(), month="2026-10")
    db_session.commit()
    assert created_second is True
    assert second.version_label == "V-202610-2"

    latest = reports.latest_iteration(db_session, "yard-p3")
    assert latest is not None
    assert latest.id == second.id
    assert len(reports.list_iterations(db_session, "yard-p3")) == 2
    out = reports.iteration_out(first)
    assert out["changes"]["version_label"] == "V-202609-1"


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


def test_evolution_api_permissions_and_payloads(db_session):
    member = _client(db_session, _ctx(roles=[], user_id="u-member"))
    for path in (
        "/api/v1/commander/prompt-preview",
        "/api/v1/commander/reports/daily",
        "/api/v1/commander/reports/weekly",
        "/api/v1/commander/iterations",
    ):
        assert member.get(path).status_code == 403, path
    assert member.post("/api/v1/commander/iterations/run", json={}).status_code == 403

    admin = _client(db_session, _ctx())
    preview = admin.get("/api/v1/commander/prompt-preview")
    assert preview.status_code == 200
    assert preview.json()["data"]["chars"] > 0

    assert admin.get("/api/v1/commander/reports/daily").status_code == 200
    assert admin.get("/api/v1/commander/reports/daily", params={"day": "bad"}).status_code == 400
    assert admin.get("/api/v1/commander/reports/weekly").status_code == 200
    assert admin.get("/api/v1/commander/reports/weekly", params={"week": "bad"}).status_code == 400

    first = admin.post("/api/v1/commander/iterations/run", json={"month": "2026-09"})
    assert first.status_code == 200
    assert first.json()["data"]["iteration"]["version_label"] == "V-202609-1"
    again = admin.post("/api/v1/commander/iterations/run", json={"month": "2026-09"})
    assert again.status_code == 200
    assert again.json()["data"]["created"] is False

    listed = admin.get("/api/v1/commander/iterations")
    assert listed.status_code == 200
    assert len(listed.json()["data"]["items"]) == 1
    latest = admin.get("/api/v1/commander/iterations/latest")
    assert latest.json()["data"]["iteration"]["month_key"] == "2026-09"
    assert admin.post("/api/v1/commander/iterations/run", json={"month": "bad"}).status_code == 400


def test_settings_defaults_include_prompt_modules(db_session):
    cfg = commander_settings.settings_for(db_session, "yard-p3")
    assert cfg["prompt_modules"] == ["delivery", "cost", "concise", "risk"]
    assert cfg["prompt_max_chars"] == 4500
