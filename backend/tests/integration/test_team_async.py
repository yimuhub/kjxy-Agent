"""班组异步契约、执行者隔离、周期 CAS 与真实文档回归。"""

import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.security import create_access_token
from app.modules.agents import declarative
from app.modules.agents.teams import research, scheduler, service, stream
from app.modules.agents.teams.models import AgentTeam, TeamRun
from app.modules.agents.teams.worker_tasks import run_team_task
from app.modules.audit.models import AuditLog
from app.modules.auth.models import User
from app.modules.llm import providers_fake
from app.modules.llm.models import LLMTrace
from app.modules.tasks import worker_tasks
from app.modules.tasks.models import Task
from app.modules.yards.models import YardMembership
from app.modules.yards.repository import create_yard
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

TEAMS = "/api/v1/agents/teams"
RUNS = "/api/v1/agents/team-runs"


def headers(yard_id, user_id="owner", roles=None):
    return {
        "Authorization": "Bearer "
        + create_access_token(user_id=user_id, yard_id=yard_id, roles=roles or ["yard_admin"])
    }


@pytest.fixture()
def env(db_session, monkeypatch):
    db = db_session
    yard = create_yard(db, name="异步班组院")
    for user_id in ("owner", "other"):
        db.add(User(id=user_id, username=user_id, password_hash="unused"))
        db.add(YardMembership(yard_id=yard.id, user_id=user_id, role="yard_admin"))
    db.commit()
    members = []
    for i in range(2):
        agent = declarative.create_agent(
            db,
            yard_id=yard.id,
            actor_id="owner",
            request_id="test",
            req=declarative.DeclarativeAgentCreate(
                agent_code=f"async_{i}",
                name=f"成员{i}",
                persona_prompt="测试助手",
                task_prompt_template="回答{{question}}",
                knowledge_scopes=["yard"],
                visibility="public",
            ),
        )
        agent.status = "published"
        members.append({"agent_id": agent.id, "role_name": agent.name})
    team = service.create_team(
        db, yard_id=yard.id, actor_id="owner", name="研究班组", members=members, request_id="test"
    )
    db.commit()
    factory = sessionmaker(bind=db.get_bind(), autoflush=False)
    monkeypatch.setattr(worker_tasks, "_session_factory", factory)

    async def no_web(*args, **kwargs):
        return {"results": []}

    monkeypatch.setattr(research, "search_web", no_web)
    providers_fake.reset_scripted_tool_responses()
    yield SimpleNamespace(
        yard_id=yard.id,
        team=team,
        db=db,
        factory=factory,
        ctx=YardContext(yard.id, "owner", ["yard_admin"], "test"),
    )
    providers_fake.reset_scripted_tool_responses()


def start(client, env, **fields):
    response = client.post(
        f"{TEAMS}/{env.team.id}/run",
        headers=headers(env.yard_id),
        json={"input": "研究问题", **fields},
    )
    assert response.status_code == 202, response.text
    return response.json()["data"]


def finish(client, env, queued):
    worker_tasks.run_task_inline(queued["task_id"])
    return client.get(f"{RUNS}/{queued['id']}", headers=headers(env.yard_id)).json()["data"]


def schedule_due(env, *, enabled=True):
    env.team.schedule_json = {
        "enabled": enabled,
        "interval_minutes": 5,
        "input": "定时研究",
        "output_format": "md",
    }
    env.team.schedule_created_by = "owner"
    env.team.next_run_at = datetime.now(UTC) - timedelta(minutes=1)
    env.db.commit()


def test_202_ledger_task_identity_and_summary_tokens(client, env):
    providers_fake.set_scripted_tool_responses(
        [{"content": "第一成员"}, {"content": "第二成员"}, {"content": "模型综合答案"}]
    )
    queued = start(client, env)
    assert queued["status"] == "queued"
    assert queued["created_by"] == "owner" and queued["trigger"] == "manual"
    assert queued["team_name"] == "研究班组" and len(queued["members"]) == 2
    task = env.db.get(Task, queued["task_id"])
    assert task.input_json["data"] == {"run_id": queued["id"]}
    done = finish(client, env, queued)
    assert done["status"] == "completed" and done["answer"] == "模型综合答案"
    trace = env.db.scalar(select(LLMTrace).where(LLMTrace.run_id == f"team-summary-{done['id']}"))
    assert trace is not None
    member_tokens = sum(m.get("token_usage", {}).get("total_tokens", 0) for m in done["members"])
    assert (
        done["token_usage"]["total_tokens"]
        == member_tokens + trace.input_tokens + trace.output_tokens
    )
    env.db.refresh(task)
    assert task.result_json == {"run_id": done["id"], "status": "completed"}


def test_private_member_owner_only_even_for_admin(client, env):
    agent = declarative.get_agent(
        env.db, yard_id=env.yard_id, agent_id=env.team.members_json[0]["agent_id"]
    )
    agent.visibility = "private"
    env.db.commit()
    response = client.post(
        f"{TEAMS}/{env.team.id}/run",
        headers=headers(env.yard_id, "other"),
        json={"input": "读取他人私有资料"},
    )
    assert response.status_code == 404
    assert env.db.scalar(select(func.count()).select_from(TeamRun)) == 0
    assert start(client, env)["created_by"] == "owner"


@pytest.mark.parametrize(
    "path,method", [("", "get"), ("/stream", "get"), ("/document", "get"), ("/retry", "post")]
)
def test_run_endpoints_no_admin_bypass(client, env, path, method):
    run = start(client, env)
    for yard_id, user_id in ((env.yard_id, "other"), ("foreign", "owner")):
        response = getattr(client, method)(
            f"{RUNS}/{run['id']}{path}", headers=headers(yard_id, user_id, ["platform_admin"])
        )
        assert response.status_code == 404


def test_history_pagination_filters_and_legacy_visibility(client, env):
    for i in range(5):
        env.db.add(
            TeamRun(
                team_id=env.team.id,
                team_name="快照",
                yard_id=env.yard_id,
                created_by="owner" if i < 3 else "other",
                input=f"问题{i}",
                answer="答案预览",
                status="completed",
                trigger="manual",
                members_json=env.team.members_json,
            )
        )
    legacy = TeamRun(
        team_id="deleted",
        yard_id=env.yard_id,
        created_by=None,
        input="历史记录",
        status="failed",
        trigger="schedule",
        members_json=[],
    )
    env.db.add(legacy)
    env.db.commit()
    first = client.get(RUNS, params={"page_size": 2}, headers=headers(env.yard_id)).json()["data"]
    second = client.get(
        RUNS, params={"page_size": 2, "page": 2}, headers=headers(env.yard_id)
    ).json()["data"]
    assert first["total"] == second["total"] == 4
    assert len({r["id"] for r in first["runs"] + second["runs"]}) == 4
    filtered = client.get(
        RUNS,
        params={"team_id": env.team.id, "status": "completed", "trigger": "manual"},
        headers=headers(env.yard_id),
    )
    assert filtered.json()["data"]["total"] == 3
    assert filtered.json()["data"]["runs"][0]["answer_preview"] == "答案预览"
    assert (
        client.get(f"{RUNS}/{legacy.id}", headers=headers(env.yard_id, "other")).status_code == 200
    )
    assert client.get(RUNS, params={"page": 0}, headers=headers(env.yard_id)).status_code == 400


@pytest.mark.parametrize("fmt", ["text", "md", "docx"])
def test_real_documents_and_not_ready(client, env, fmt):
    providers_fake.set_scripted_tool_responses(
        [{"content": "甲"}, {"content": "乙"}, {"content": "# 研究结论\n\n确切结果"}]
    )
    queued = start(client, env, output_format=fmt)
    url = f"{RUNS}/{queued['id']}/document"
    assert client.get(url, headers=headers(env.yard_id)).status_code == 409
    done = finish(client, env, queued)
    assert done["document_available"] is True
    md = client.get(url, params={"format": "md"}, headers=headers(env.yard_id))
    assert md.status_code == 200 and "确切结果" in md.content.decode()
    assert "attachment; filename*=UTF-8''" in md.headers["content-disposition"]
    docx = client.get(url, params={"format": "docx"}, headers=headers(env.yard_id))
    assert docx.status_code == 200 and docx.content.startswith(b"PK")
    with ZipFile(BytesIO(docx.content)) as archive:
        assert "确切结果" in archive.read("word/document.xml").decode()
    assert (
        client.get(url, params={"format": "pdf"}, headers=headers(env.yard_id)).status_code == 400
    )


def test_synthesis_failure_retry_preserves_members_and_research(client, env, monkeypatch):
    calls = []

    async def collect(db, *, ctx, question):
        calls.append(ctx.user_id)
        return {"knowledge": [{"document_id": "d", "title": "证据"}], "web": [], "warnings": []}

    monkeypatch.setattr(research, "collect_research", collect)
    providers_fake.set_scripted_tool_responses(
        [
            {"content": "甲"},
            {"content": "乙"},
            {"error": {"code": "LLM_ERROR", "message": "汇总失败"}},
        ]
    )
    failed = finish(client, env, start(client, env))
    assert failed["status"] == "failed" and all(m["status"] == "ok" for m in failed["members"])
    assert (
        client.get(f"{RUNS}/{failed['id']}/document", headers=headers(env.yard_id)).status_code
        == 409
    )
    original_ids = [m["task_id"] for m in failed["members"]]
    providers_fake.set_scripted_tool_responses([{"content": "汇总恢复"}])
    retry = client.post(f"{RUNS}/{failed['id']}/retry", headers=headers(env.yard_id))
    assert retry.status_code == 202
    done = finish(client, env, retry.json()["data"])
    assert done["answer"] == "汇总恢复"
    assert [m["task_id"] for m in done["members"]] == original_ids
    assert calls == ["owner"]
    assert done["research"]["knowledge"][0]["document_id"] == "d"


def test_member_citations_and_warnings_are_saved_once(client, env, monkeypatch):
    contexts = []

    async def member(*args, **kwargs):
        contexts.append(kwargs["context"])
        return SimpleNamespace(
            output={"answer": "成员回答"},
            citations=[{"document_id": "d", "title": "成员资料"}],
            warnings=["共同警告", "共同警告", "知识库暂无命中，回答不包含引用。"],
        )

    monkeypatch.setattr(declarative, "run_declarative_agent", member)
    done = finish(client, env, start(client, env))
    assert done["status"] == "completed"
    assert len(done["research"]["knowledge"]) == 1
    warnings = done["research"]["warnings"]
    assert warnings.count("共同警告") == 1
    assert "知识库暂无命中，回答不包含引用。" not in warnings
    assert all("team_research" in value for value in contexts)


@pytest.mark.parametrize("kind", ["user", "membership"])
def test_worker_rechecks_enabled_identity(client, env, kind):
    queued = start(client, env)
    if kind == "user":
        env.db.get(User, "owner").status = "disabled"
    else:
        env.db.scalar(
            select(YardMembership).where(YardMembership.user_id == "owner")
        ).status = "disabled"
    env.db.commit()
    worker_tasks.run_task_inline(queued["task_id"])
    run = env.db.get(TeamRun, queued["id"], populate_existing=True)
    assert run.status == "failed" and "TEAM_EXECUTOR_UNAVAILABLE" in run.last_error
    assert all(m.status == "pending" for m in service.members_of_run(env.db, run))


def test_worker_rebuilds_roles_and_rejects_context_spoof(client, env, monkeypatch):
    queued = start(client, env)
    task = env.db.get(Task, queued["task_id"])
    task.status = "running"
    env.db.scalar(select(YardMembership).where(YardMembership.user_id == "owner")).role = "viewer"
    env.db.commit()
    context = {"task_id": task.id, "yard_id": env.yard_id, "actor_id": "other"}
    with pytest.raises(AppError, match="任务身份"):
        run_team_task({"run_id": queued["id"]}, context)
    seen = []

    async def collect(db, *, ctx, question):
        seen.append(ctx.roles)
        return {"knowledge": [], "web": [], "warnings": []}

    monkeypatch.setattr(research, "collect_research", collect)
    run_team_task({"run_id": queued["id"]}, {**context, "actor_id": "owner"})
    assert seen == [["viewer"]]
    with pytest.raises(AppError) as exc:
        run_team_task({"run_id": queued["id"]}, {**context, "actor_id": "owner"})
    assert exc.value.code == "TEAM_RUN_STATE_CONFLICT"


def test_schedule_api_gate_defaults_validation_and_disabled(client, env):
    url = f"{TEAMS}/{env.team.id}/schedule"
    payload = {"enabled": True, "input": "定时问题"}
    assert (
        client.put(url, json=payload, headers=headers(env.yard_id, "other", ["viewer"])).status_code
        == 403
    )
    for invalid in (
        {"input": " "},
        {"interval_minutes": 4},
        {"interval_minutes": 43201},
        {"output_format": "pdf"},
    ):
        assert (
            client.put(url, json={**payload, **invalid}, headers=headers(env.yard_id)).status_code
            == 400
        )
    # 停用时允许清空问题，便于不想运行但保留其余配置
    empty = client.put(
        url,
        json={"enabled": False, "interval_minutes": 1440, "input": ""},
        headers=headers(env.yard_id),
    )
    assert empty.status_code == 200, empty.text
    response = client.put(url, json=payload, headers=headers(env.yard_id))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["schedule"]["interval_minutes"] == 1440
    assert data["next_run_at"].endswith("+00:00")
    disabled = client.put(url, json={**payload, "enabled": False}, headers=headers(env.yard_id))
    assert disabled.json()["data"]["next_run_at"] is None


def test_schedule_single_claim_busy_skip_and_disabled(env):
    # 超时运行由调度巡检回收，不能永久占住班组
    stale = TeamRun(
        team_id=env.team.id,
        team_name="超时",
        yard_id=env.yard_id,
        created_by="owner",
        input="旧问题",
        status="running",
        members_json=env.team.members_json,
    )
    env.db.add(stale)
    env.db.commit()
    stale.updated_at = datetime.now(UTC) - timedelta(minutes=200)
    env.db.commit()
    schedule_due(env)
    now = datetime.now(UTC)
    first = scheduler.run_due_jobs(env.db, now=now)
    env.db.refresh(stale)
    assert stale.status == "failed" and "TEAM_RUN_TIMEOUT" in stale.last_error
    assert first == {"due": 1, "queued": 1, "failed": 0, "skipped": 0}
    with env.factory() as other:
        assert scheduler.run_due_jobs(other, now=now)["due"] == 0
    scheduled = list(env.db.scalars(select(TeamRun).where(TeamRun.trigger == "schedule")))
    assert len(scheduled) == 1
    assert env.db.scalar(select(func.count()).select_from(Task)) == 1
    run = scheduled[0]
    assert run.created_by == "owner"
    assert run.output_format == "md"
    schedule_due(env)
    assert scheduler.run_due_jobs(env.db)["skipped"] == 1
    assert (
        env.db.scalar(
            select(func.count()).select_from(TeamRun).where(TeamRun.trigger == "schedule")
        )
        == 1
    )
    assert env.db.scalar(select(AuditLog).where(AuditLog.action == "team_schedule_skipped"))
    schedule_due(env, enabled=False)
    assert scheduler.run_due_jobs(env.db)["due"] == 0
    schedule_due(env)
    env.team.enabled = False
    env.db.commit()
    assert scheduler.run_due_jobs(env.db)["due"] == 0


def test_schedule_cas_stale_reader_loses(env):
    schedule_due(env)
    now = datetime.now(UTC)
    with env.factory() as second:
        stale = second.get(AgentTeam, env.team.id)
        assert scheduler._claim_due(env.db, env.team, now, 5)
        env.db.commit()
        assert not scheduler._claim_due(second, stale, now, 5)
        second.rollback()


def test_schedule_transaction_rolls_back_next_run_and_ledger(env, monkeypatch):
    schedule_due(env)
    before = env.team.next_run_at

    def fail(*args, **kwargs):
        raise RuntimeError("task insert failed")

    monkeypatch.setattr(service, "create_run_task", fail)
    scheduler.run_due_jobs(env.db)
    env.db.refresh(env.team)
    assert env.team.next_run_at == before
    assert env.db.scalar(select(func.count()).select_from(TeamRun)) == 0
    assert env.db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize("revoked", ["user", "membership", "private"])
def test_schedule_revoked_creator_records_failure(env, revoked):
    schedule_due(env)
    if revoked == "user":
        env.db.get(User, "owner").status = "disabled"
    elif revoked == "membership":
        env.db.scalar(
            select(YardMembership).where(YardMembership.user_id == "owner")
        ).status = "disabled"
    else:
        agent = declarative.get_agent(
            env.db, yard_id=env.yard_id, agent_id=env.team.members_json[0]["agent_id"]
        )
        agent.visibility = "private"
        agent.created_by = "other"
    env.db.commit()
    stats = scheduler.run_due_jobs(env.db)
    assert stats["failed"] == 1 and stats["queued"] == 0
    failure = env.db.scalar(select(TeamRun))
    assert failure.status == "failed" and failure.last_error
    assert failure.created_by == "owner"
    assert len(service.members_of_run(env.db, failure)) == 2
    assert env.db.scalar(select(func.count()).select_from(Task)) == 0


def test_enqueue_failure_can_retry(client, env, monkeypatch):
    monkeypatch.setattr("app.modules.tasks.router.enqueue_task", lambda task_id: False)
    queued = start(client, env)
    run = env.db.get(TeamRun, queued["id"], populate_existing=True)
    assert run.status == "failed" and "ENQUEUE_FAILED" in run.last_error
    assert env.db.get(Task, queued["task_id"]).status == "cancelled"
    monkeypatch.setattr("app.modules.tasks.router.enqueue_task", lambda task_id: True)
    response = client.post(f"{RUNS}/{run.id}/retry", headers=headers(env.yard_id))
    assert response.status_code == 202
    assert response.json()["data"]["task_id"] != queued["task_id"]


def test_queued_stream_does_not_end_early(monkeypatch):
    states = iter(["queued", "running", "completed"])

    def snapshot(*args):
        return {"status": next(states), "team_id": "team", "member_count": 0}, []

    monkeypatch.setattr(stream, "_read_snapshot", snapshot)
    monkeypatch.setattr(stream, "POLL_INTERVAL_SECONDS", 0)

    async def collect():
        return [frame async for frame in stream._stream_frames("yard", "run", "owner")]

    frames = asyncio.run(collect())
    assert '"status": "queued"' in frames[0]
    assert '"status": "completed"' in frames[-1]
    assert sum("team-end" in frame for frame in frames) == 1
