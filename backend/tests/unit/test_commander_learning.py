"""经验资产：工作流沉淀、失败黑名单与技能萃取。"""

from __future__ import annotations

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
from app.modules.commander import learning
from app.modules.commander import settings as commander_settings
from app.modules.commander.asset_models import CommanderSkillSnippet, CommanderWorkflow
from app.modules.commander.models import CommanderRun, CommanderSubtask
from app.modules.commander.router import router as commander_router
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient


def _ctx(roles: list[str] | None = None, user_id: str = "u-admin") -> YardContext:
    return YardContext(
        yard_id="yard-p2",
        user_id=user_id,
        roles=roles if roles is not None else ["yard_admin"],
        request_id="req-p2",
    )


def _run(
    db,  # type: ignore[no-untyped-def]
    *,
    goal: str = "给小院做宣传 PPT",
    status: str = "completed",
    tokens: int = 1200,
    index: int = 0,
) -> CommanderRun:
    run = CommanderRun(
        yard_id="yard-p2",
        session_id=f"s-p2-{index}",
        user_id="u-admin",
        goal=goal,
        plan_json={"fallback": False},
        status=status,
        tokens_total=tokens,
        roles_json=["yard_admin"],
    )
    db.add(run)
    db.flush()
    return run


def _subtask(
    db,  # type: ignore[no-untyped-def]
    run: CommanderRun,
    *,
    seq: int = 1,
    status: str = "ok",
    agent_code: str = "ppt-agent",
    title: str = "生成宣传 PPT",
) -> CommanderSubtask:
    st = CommanderSubtask(
        run_id=run.id,
        yard_id=run.yard_id,
        seq=seq,
        title=title,
        instruction="按小院介绍与产品清单生成 8 页宣传 PPT，并校验版式。",
        expected_output="pptx 文件",
        agent_id="agent-1",
        agent_code=agent_code,
        agent_name="演示助手",
        agent_source="existing",
        status=status,
        error_code="LLM_TIMEOUT" if status == "error" else None,
        remediation_json=(
            [{"round": 1, "action": "retry", "message": "upstream timeout"}]
            if status == "error"
            else []
        ),
        output_excerpt="ok" if status == "ok" else None,
    )
    db.add(st)
    db.flush()
    return st


def test_workflow_learned_from_successful_run_and_idempotent(db_session):
    run = _run(db_session)
    _subtask(db_session, run)
    _subtask(db_session, run, seq=2, title="校验并交付")

    first = learning.learn_from_run(db_session, run=run, subtasks=_subtasks(db_session, run))
    db_session.commit()
    assert first is not None
    assert first.code.startswith("WF-")
    assert len(first.steps_json) == 2
    assert first.success_count == 1
    assert first.cost_baseline_tokens == 1200
    assert first.status == "active"

    learning.learn_from_run(db_session, run=run, subtasks=_subtasks(db_session, run))
    db_session.commit()
    refreshed = db_session.get(CommanderWorkflow, first.id)
    assert refreshed is not None
    assert refreshed.success_count == 1
    assert refreshed.source_run_ids_json == [run.id]


def test_match_workflow_and_failure_threshold(db_session):
    run = _run(db_session)
    _subtask(db_session, run)
    learning.learn_from_run(db_session, run=run, subtasks=_subtasks(db_session, run))
    db_session.commit()

    matched = learning.match_workflow(
        db_session, yard_id="yard-p2", text="帮我给小院做一份宣传 PPT"
    )
    assert matched is not None
    workflow, score = matched
    assert workflow.code.startswith("WF-")
    assert score >= learning.MATCH_THRESHOLD
    assert "可复用" in learning.workflow_hint(workflow)

    assert (
        learning.match_workflow(db_session, yard_id="yard-p2", text="统计番茄产量趋势")
        is None
    )

    failed_one = _run(db_session, status="failed", index=1)
    _subtask(db_session, failed_one, status="error")
    learning.learn_from_run(
        db_session, run=failed_one, subtasks=_subtasks(db_session, failed_one), failure_threshold=2
    )
    db_session.commit()
    assert learning.retrieve_failures(db_session, yard_id="yard-p2", text="给小院做宣传 PPT") == []

    failed_two = _run(db_session, status="failed", index=2)
    _subtask(db_session, failed_two, status="error")
    learning.learn_from_run(
        db_session, run=failed_two, subtasks=_subtasks(db_session, failed_two), failure_threshold=2
    )
    db_session.commit()
    cases = learning.retrieve_failures(db_session, yard_id="yard-p2", text="给小院做宣传 PPT")
    assert len(cases) == 1
    assert cases[0].status == "active"
    assert cases[0].occurrences == 2
    assert "拆分" in cases[0].avoidance


def test_skill_extraction_promotes_after_two_runs(db_session):
    for index in range(2):
        run = _run(db_session, index=index)
        _subtask(db_session, run)
        learning.learn_from_run(db_session, run=run, subtasks=_subtasks(db_session, run))

    skills = learning.extract_skills_for_yard(db_session, yard_id="yard-p2")
    db_session.commit()
    assert len(skills) == 1
    skill = skills[0]
    assert skill.status == "active"
    assert skill.sample_count == 2
    assert skill.success_rate == 1.0
    assert skill.code.startswith("SK-")

    learning.update_skill_status(skill, status="archived")
    db_session.commit()
    learning.extract_skills_for_yard(db_session, yard_id="yard-p2")
    db_session.commit()
    refreshed = db_session.get(CommanderSkillSnippet, skill.id)
    assert refreshed is not None
    assert refreshed.status == "archived"


def test_scan_yard_is_idempotent(db_session):
    for index in range(2):
        run = _run(db_session, index=index)
        _subtask(db_session, run)
    db_session.commit()

    first = learning.scan_yard(db_session, yard_id="yard-p2")
    db_session.commit()
    second = learning.scan_yard(db_session, yard_id="yard-p2")
    db_session.commit()

    assert first["runs_scanned"] == 2
    assert second["workflows_created"] == 0
    workflow = db_session.query(CommanderWorkflow).one()
    assert workflow.success_count == 2
    assert db_session.query(CommanderSkillSnippet).count() == 1


def _subtasks(db, run: CommanderRun) -> list[CommanderSubtask]:  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    return list(
        db.scalars(
            select(CommanderSubtask)
            .where(CommanderSubtask.run_id == run.id)
            .order_by(CommanderSubtask.seq)
        )
    )


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


def test_asset_api_permissions_and_crud(db_session):
    member = _client(db_session, _ctx(roles=[], user_id="u-member"))
    assert member.get("/api/v1/commander/assets").status_code == 403
    assert member.post("/api/v1/commander/assets/scan").status_code == 403

    admin = _client(db_session, _ctx())
    overview = admin.get("/api/v1/commander/assets")
    assert overview.status_code == 200
    assert overview.json()["data"]["counts"]["workflows"] == 0

    created = admin.post(
        "/api/v1/commander/assets/workflows",
        json={
            "name": "宣传 PPT 标准流程",
            "scene": "给小院做宣传 PPT",
            "preconditions": "已有小院介绍与产品清单",
            "steps": [
                {
                    "title": "整理素材",
                    "instruction": "汇总小院介绍与产品卖点",
                    "expected_output": "素材清单",
                },
                {
                    "title": "生成 PPT",
                    "instruction": "按素材生成 8 页 PPT",
                    "expected_output": "pptx",
                },
            ],
        },
    )
    assert created.status_code == 200
    workflow_id = created.json()["data"]["workflow"]["id"]
    assert created.json()["data"]["workflow"]["source"] == "manual"

    archived = admin.put(
        f"/api/v1/commander/assets/workflows/{workflow_id}", json={"status": "archived"}
    )
    assert archived.status_code == 200
    assert archived.json()["data"]["workflow"]["status"] == "archived"

    missing = admin.put(
        "/api/v1/commander/assets/workflows/not-found", json={"status": "active"}
    )
    assert missing.status_code == 404

    scan = admin.post("/api/v1/commander/assets/scan")
    assert scan.status_code == 200
    assert "counts" in scan.json()["data"]


def test_settings_defaults_include_learning_toggles(db_session):
    defaults = commander_settings.settings_for(db_session, "yard-p2")
    assert defaults["auto_learn_workflows"] is True
    assert defaults["auto_extract_skills"] is True
    assert defaults["failure_blacklist_threshold"] == 2
