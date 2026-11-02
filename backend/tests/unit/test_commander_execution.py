"""指挥官执行器：并发、依赖顺序、失败矩阵、降级、重试与取消（全部离线）。"""

from __future__ import annotations

import asyncio

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.db.base import Base
from app.modules.agents.models import YardAgent
from app.modules.audit.models import AuditLog
from app.modules.commander import service as commander_service
from app.modules.commander.models import CommanderRun, CommanderSubtask
from app.modules.llm.schemas import LLMRequest, LLMResponse
from app.modules.yards.repository import create_yard
from sqlalchemy import create_engine, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def env(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'commander.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = factory()
    yard = create_yard(db, name="指挥官执行小院")
    db.commit()

    monkeypatch.setattr(commander_service, "_session_factory", factory)
    monkeypatch.setattr(commander_service, "RETRY_BACKOFF_SECONDS", 0.0)

    async def fake_summary(_db, _request: LLMRequest) -> LLMResponse:
        return LLMResponse(
            content="汇总答复：已完成。",
            input_tokens=3,
            output_tokens=4,
            model="fake",
            latency_ms=1,
        )

    monkeypatch.setattr(commander_service, "complete_with_logging", fake_summary)
    try:
        yield db, factory, yard
    finally:
        db.close()
        engine.dispose()


def _make_run(
    db,
    yard,
    *,
    specs: list[tuple[int, list[int]]],
    max_parallel: int = 3,
    session_id: str = "s-exec",
    status: str = "queued",
    subtask_status: str = "pending",
    attempts: int = 0,
) -> CommanderRun:
    run = CommanderRun(
        yard_id=yard.id,
        session_id=session_id,
        user_id="u-exec",
        goal="完成测试目标",
        plan_json={"fallback": False, "subtasks": []},
        status=status,
        max_parallel=max_parallel,
        effective_parallel=max_parallel,
        degraded=False,
        tokens_total=0,
        roles_json=["yard_admin"],
    )
    db.add(run)
    db.flush()
    for seq, deps in specs:
        db.add(
            CommanderSubtask(
                run_id=run.id,
                yard_id=yard.id,
                seq=seq,
                title=f"任务 {seq}",
                instruction=f"完成任务 {seq}",
                expected_output="文本",
                agent_source="commander",
                agent_name="指挥官",
                status=subtask_status,
                depends_on_json=list(deps),
                attempts=attempts,
            )
        )
    db.commit()
    return run


def _subtasks(db, run_id: str) -> list[CommanderSubtask]:
    return list(
        db.query(CommanderSubtask)
        .filter(CommanderSubtask.run_id == run_id)
        .order_by(CommanderSubtask.seq.asc())
    )


def _ctx(yard) -> YardContext:
    return YardContext(
        yard_id=yard.id, user_id="u-exec", roles=["yard_admin"], request_id="req-exec"
    )


@pytest.mark.asyncio
async def test_three_independent_subtasks_run_with_three_concurrency(env, monkeypatch):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, []), (2, []), (3, [])])
    state = {"active": 0, "peak": 0}

    async def fake_invoke(_db, *, run, subtask, ctx, settings, context):
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        return {
            "answer": f"out-{subtask.seq}",
            "input_tokens": 1,
            "output_tokens": 1,
            "artifacts": [],
        }

    monkeypatch.setattr(commander_service, "_invoke_agent", fake_invoke)
    summary = await commander_service.execute_run(run.id)
    assert state["peak"] == 3
    assert summary["degraded"] is False
    assert summary["failed"] == []

    db.expire_all()
    rows = _subtasks(db, run.id)
    assert [row.status for row in rows] == ["ok", "ok", "ok"]
    assert [row.attempts for row in rows] == [1, 1, 1]
    refreshed = db.get(CommanderRun, run.id)
    assert refreshed.status == "completed"
    assert refreshed.tokens_total == 6 + 7  # 子任务 3×2 + 汇总 3+4


@pytest.mark.asyncio
async def test_dependency_order_and_upstream_context(env, monkeypatch):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, []), (2, [1])])
    seen: list[int] = []
    contexts: dict[int, str] = {}

    async def fake_invoke(_db, *, run, subtask, ctx, settings, context):
        seen.append(int(subtask.seq))
        contexts[int(subtask.seq)] = context
        await asyncio.sleep(0.01)
        return {
            "answer": f"out-{subtask.seq}",
            "input_tokens": 1,
            "output_tokens": 1,
            "artifacts": [],
        }

    monkeypatch.setattr(commander_service, "_invoke_agent", fake_invoke)
    summary = await commander_service.execute_run(run.id)
    assert seen == [1, 2]
    assert "out-1" in contexts[2]
    assert summary["failed"] == []


@pytest.mark.asyncio
async def test_failed_dependency_skips_downstream(env, monkeypatch):
    from app.modules.commander import settings as commander_settings

    db, _factory, yard = env
    # 本次只验依赖传播与失败矩阵，关掉自主补救让失败在第 1 轮就落定
    commander_settings.update_settings(
        db,
        yard_id=yard.id,
        payload={"auto_remediate": False},
        actor_id="u-exec",
        request_id="req-exec",
    )
    db.commit()
    run = _make_run(db, yard, specs=[(1, []), (2, [1]), (3, [])])

    async def fake_invoke(_db, *, run, subtask, ctx, settings, context):
        if int(subtask.seq) == 1:
            raise AppError("AGENT_NOT_FOUND", "该助手已被删除。", http_status=404)
        return {
            "answer": f"out-{subtask.seq}",
            "input_tokens": 1,
            "output_tokens": 1,
            "artifacts": [],
        }

    monkeypatch.setattr(commander_service, "_invoke_agent", fake_invoke)
    summary = await commander_service.execute_run(run.id)
    db.expire_all()
    rows = {row.seq: row for row in _subtasks(db, run.id)}
    assert rows[1].status == "error"
    assert rows[1].error_code == "AGENT_NOT_FOUND"
    assert rows[2].status == "skipped"
    assert rows[2].error_code == "COMMANDER_DEPENDENCY_FAILED"
    assert rows[3].status == "ok"
    refreshed = db.get(CommanderRun, run.id)
    assert refreshed.status == "partial"
    # 失败原因优先给人能读懂的结果说明，错误码只做兜底
    assert {(item["seq"], item["reason"]) for item in summary["failed"]} == {
        (1, "该助手已被删除。"),
        (2, "COMMANDER_DEPENDENCY_FAILED"),
    }


@pytest.mark.asyncio
async def test_transient_429_retries_once_and_degrades_to_two(env, monkeypatch):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, []), (2, []), (3, [])])
    attempts: dict[int, int] = {}

    async def fake_invoke(_db, *, run, subtask, ctx, settings, context):
        seq = int(subtask.seq)
        attempts[seq] = attempts.get(seq, 0) + 1
        if attempts[seq] == 1:
            raise AppError(
                "LLM_ERROR",
                "上游限流，请稍后重试。",
                http_status=429,
                details={"category": "rate_limit"},
            )
        return {
            "answer": f"out-{seq}",
            "input_tokens": 2,
            "output_tokens": 1,
            "artifacts": [],
        }

    monkeypatch.setattr(commander_service, "_invoke_agent", fake_invoke)
    summary = await commander_service.execute_run(run.id)
    db.expire_all()
    rows = _subtasks(db, run.id)
    assert attempts == {1: 2, 2: 2, 3: 2}
    assert [row.attempts for row in rows] == [2, 2, 2]
    assert all(row.status == "ok" for row in rows)
    refreshed = db.get(CommanderRun, run.id)
    assert refreshed.degraded is True
    assert refreshed.effective_parallel == 2
    assert summary["degraded"] is True


def test_retry_resets_error_and_skipped_without_clearing_attempts(env):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, []), (2, [1])], status="partial")
    rows = _subtasks(db, run.id)
    rows[0].status = "error"
    rows[0].error_code = "COMMANDER_SUBTASK_ERROR"
    rows[0].attempts = 2
    rows[1].status = "skipped"
    rows[1].attempts = 1
    db.commit()

    refreshed = commander_service.retry_run(db, _ctx(yard), run.id)
    db.expire_all()
    rows = _subtasks(db, run.id)
    assert refreshed.status == "queued"
    assert [row.status for row in rows] == ["pending", "pending"]
    assert [row.attempts for row in rows] == [2, 1]
    assert all(row.error_code is None for row in rows)


def test_retry_without_failed_tasks_is_rejected(env):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])], status="completed", subtask_status="ok")
    with pytest.raises(AppError) as exc:
        commander_service.retry_run(db, _ctx(yard), run.id)
    assert exc.value.code == "COMMANDER_NOTHING_TO_RETRY"


def test_cancel_marks_pending_skipped_and_keeps_finished(env):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, []), (2, [])], max_parallel=1)
    rows = _subtasks(db, run.id)
    rows[0].status = "ok"
    db.commit()

    cancelled = commander_service.cancel_run(db, _ctx(yard), run.id)
    db.expire_all()
    rows = _subtasks(db, run.id)
    assert cancelled.status == "cancelled"
    assert rows[0].status == "ok"
    assert rows[1].status == "skipped"
    assert rows[1].error_code == "COMMANDER_RUN_CANCELLED"


def test_cancel_finished_run_is_conflict(env):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])], status="completed", subtask_status="ok")
    with pytest.raises(AppError) as exc:
        commander_service.cancel_run(db, _ctx(yard), run.id)
    assert exc.value.code == "COMMANDER_RUN_STATE"


@pytest.mark.asyncio
async def test_run_in_background_retries_summary_seq_on_conflict(env, monkeypatch):
    """C13：并发写同一会话撞 (session_id, seq) 时重新取号，汇总消息不能整条丢失。"""
    db, factory, yard = env
    from app.modules.chat.models import ChatMessage, ChatSession
    from sqlalchemy.exc import IntegrityError

    session = ChatSession(yard_id=yard.id, created_by="u-exec", title="并发会话")
    db.add(session)
    db.commit()
    run = _make_run(db, yard, specs=[(1, [])], session_id=session.id)
    db.commit()

    async def fake_invoke(_db, *, run, subtask, ctx, settings, context):
        return {"answer": "结果", "input_tokens": 1, "output_tokens": 1, "artifacts": []}

    monkeypatch.setattr(commander_service, "_invoke_agent", fake_invoke)

    tripped = {"done": False}

    def flaky_factory():
        active = factory()
        real_flush = active.flush

        def flush(*args, **kwargs):
            if not tripped["done"] and any(isinstance(o, ChatMessage) for o in active.new):
                tripped["done"] = True
                rival = factory()
                try:
                    rival.add(
                        ChatMessage(
                            session_id=session.id,
                            yard_id=yard.id,
                            seq=2,
                            role="assistant",
                            content="别的运行先落了一条",
                            status="ok",
                        )
                    )
                    rival.commit()
                finally:
                    rival.close()
                raise IntegrityError("INSERT", {}, Exception("uq_chat_messages_session_seq"))
            return real_flush(*args, **kwargs)

        active.flush = flush
        return active

    monkeypatch.setattr(commander_service, "_session_factory", flaky_factory)
    await commander_service.run_in_background(run.id)

    db.expire_all()
    rows = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id)
        .order_by(ChatMessage.seq.asc())
        .all()
    )
    assert [row.seq for row in rows] == [2, 3]
    assert rows[1].role == "assistant"
    assert rows[1].payload_json["run_id"] == run.id
    assert rows[1].content


@pytest.mark.asyncio
async def test_run_in_background_writes_assistant_message(env, monkeypatch):
    db, _factory, yard = env
    from app.modules.chat.models import ChatMessage, ChatSession

    session = ChatSession(yard_id=yard.id, created_by="u-exec", title="指挥官会话")
    db.add(session)
    db.flush()
    run = _make_run(db, yard, specs=[(1, [])], session_id=session.id)
    db.add(
        ChatMessage(
            session_id=session.id,
            yard_id=yard.id,
            seq=1,
            role="user",
            content="帮我做 PPT",
        )
    )
    db.commit()

    async def fake_invoke(_db, *, run, subtask, ctx, settings, context):
        return {"answer": "结果", "input_tokens": 1, "output_tokens": 1, "artifacts": []}

    monkeypatch.setattr(commander_service, "_invoke_agent", fake_invoke)
    await commander_service.run_in_background(run.id)

    db.expire_all()
    msg = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session.id, ChatMessage.role == "assistant")
        .one()
    )
    assert msg.seq == 2
    assert msg.content == "汇总答复：已完成。"
    assert msg.payload_json["type"] == "commander_summary"
    assert msg.payload_json["run_id"] == run.id
    assert msg.routed_by == "commander"


@pytest.mark.asyncio
async def test_invalid_plan_fallback_creates_single_commander_task(env, monkeypatch):
    from app.modules.commander import planner
    from app.modules.commander import settings as commander_settings

    db, _factory, yard = env
    commander_settings.update_settings(
        db,
        yard_id=yard.id,
        payload={"auto_create_agent": False},
        actor_id="u-exec",
        request_id="req-exec",
    )
    db.commit()

    async def not_json(*_args, **_kwargs):
        return LLMResponse(
            content="不是 JSON", input_tokens=1, output_tokens=1, model="fake", latency_ms=1
        )

    monkeypatch.setattr(planner, "complete_with_logging", not_json)
    ctx = _ctx(yard)
    run = await commander_service.create_plan_run(
        db, ctx=ctx, session_id="s-exec", user_content="帮我做一份销售提案"
    )
    _run, rows = commander_service.get_run(db, yard.id, run.id)
    # 规划不可用不再算降级：走能力计划，仍由指挥官执行
    assert run.plan_json["fallback"] is False
    assert run.plan_json["assumed"] is True
    assert len(rows) >= 1
    assert rows[0].agent_source == "commander"


def _agent(
    db,
    yard,
    *,
    code: str,
    name: str,
    status: str = "published",
    created_from: str = "manual",
    visibility: str = "public",
    created_by: str = "u-exec",
    description: str = "测试助手",
) -> YardAgent:
    row = YardAgent(
        yard_id=yard.id,
        agent_code=code,
        name=name,
        description=description,
        emoji="🧩",
        persona_prompt="你是助手。",
        task_prompt_template="回答：{{question}}",
        allowed_tools=[],
        bound_skills=[],
        knowledge_scopes=[],
        status=status,
        visibility=visibility,
        created_from=created_from,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row


def test_update_plan_replaces_subtasks_and_normalizes_dependencies(env):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, []), (2, [1]), (3, [1])])
    published = _agent(db, yard, code="writer", name="写作助手")
    draft = _agent(
        db,
        yard,
        code="gen_painter",
        name="AI 绘图师",
        status="draft",
        created_from="commander",
    )
    db.commit()

    commander_service.update_plan(
        db,
        _ctx(yard),
        run.id,
        [
            {"title": "总纲", "instruction": "列大纲", "agent_id": None, "depends_on": []},
            {
                "title": "撰写",
                "instruction": "写正文",
                "agent_id": published.id,
                # 2=self、3=前向 → 静默丢弃，只保留 1
                "depends_on": [1, 2, 3],
            },
            {
                "title": "配图",
                "instruction": "生成插图",
                "agent_id": draft.id,
                "depends_on": [1, 2],
            },
        ],
    )
    db.expire_all()
    rows = _subtasks(db, run.id)
    assert [row.seq for row in rows] == [1, 2, 3]
    assert [row.title for row in rows] == ["总纲", "撰写", "配图"]
    assert [row.status for row in rows] == ["pending", "pending", "pending"]
    assert rows[0].agent_source == "commander"
    assert rows[1].agent_id == published.id
    assert rows[1].agent_name == "写作助手"
    assert rows[1].agent_source == "existing"
    assert rows[1].depends_on_json == [1]
    assert rows[2].agent_id == draft.id
    assert rows[2].agent_source == "generated"
    assert rows[2].depends_on_json == [1, 2]

    refreshed = db.get(CommanderRun, run.id)
    assert [item["seq"] for item in refreshed.plan_json["subtasks"]] == [1, 2, 3]
    assert [item["agent_code"] for item in refreshed.plan_json["generated_agents"]] == [
        "gen_painter"
    ]
    assert refreshed.plan_json["fallback"] is False


def test_update_plan_deletes_removed_subtasks(env):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, []), (2, []), (3, [])])
    commander_service.update_plan(
        db, _ctx(yard), run.id, [{"title": "只留一个", "instruction": "做完"}]
    )
    db.expire_all()
    rows = _subtasks(db, run.id)
    assert len(rows) == 1
    assert rows[0].title == "只留一个"

    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "commander_plan_edited", AuditLog.resource_id == run.id
        )
    )
    assert audit is not None
    assert audit.metadata_json == {"subtasks": 1, "deleted": 2}


def test_update_plan_rejects_non_queued_run(env):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])], status="completed", subtask_status="ok")
    with pytest.raises(AppError) as exc:
        commander_service.update_plan(
            db, _ctx(yard), run.id, [{"title": "改", "instruction": "改"}]
        )
    assert exc.value.code == "COMMANDER_RUN_STATE"
    assert exc.value.http_status == 409


def test_update_plan_rejects_unusable_agent(env):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])])
    manual_draft = _agent(db, yard, code="manual_draft", name="手工草稿", status="draft")
    db.commit()
    with pytest.raises(AppError) as exc:
        commander_service.update_plan(
            db,
            _ctx(yard),
            run.id,
            [{"title": "改", "instruction": "改", "agent_id": manual_draft.id}],
        )
    assert exc.value.code == "INVALID_REQUEST"
    assert exc.value.http_status == 400
    assert "手工草稿" in exc.value.message


@pytest.mark.asyncio
async def test_execute_run_quota_precheck_blocks_before_running(env, monkeypatch):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])])

    def deny(*_args, **_kwargs):
        raise AppError("QUOTA_EXCEEDED", "本小院额度已用尽。", http_status=429)

    monkeypatch.setattr(commander_service.quota_engine, "preflight", deny)
    with pytest.raises(AppError) as exc:
        await commander_service.execute_run(run.id)
    assert exc.value.http_status == 429

    db.expire_all()
    assert db.get(CommanderRun, run.id).status == "queued"
    assert [row.status for row in _subtasks(db, run.id)] == ["pending"]


@pytest.mark.asyncio
async def test_execute_run_claim_loses_when_another_task_already_started(env, monkeypatch):
    """C1：读后判断与占位之间被别的任务抢跑时，条件更新必须让失败方 409，而不是双跑。"""
    db, factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])])
    run_id = run.id

    def hijack(*_args, **_kwargs):
        # 模拟另一个执行任务在「读到 queued」之后、「写入 running」之前完成占位
        other = factory()
        try:
            other.execute(
                update(CommanderRun).where(CommanderRun.id == run_id).values(status="running")
            )
            other.commit()
        finally:
            other.close()

    monkeypatch.setattr(commander_service, "_precheck_quota", hijack)
    with pytest.raises(AppError) as exc:
        await commander_service.execute_run(run_id)
    assert exc.value.code == commander_service.ERR_RUN_BUSY
    assert exc.value.http_status == 409


@pytest.mark.asyncio
async def test_run_in_background_does_not_fail_a_running_run(env, monkeypatch):
    """C1：抢位失败的一方兜底时不得把正在跑的 run 改写成 FAILED。"""
    db, factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])])
    run_id = run.id

    async def busy(*_args, **_kwargs):
        other = factory()
        try:
            other.execute(
                update(CommanderRun).where(CommanderRun.id == run_id).values(status="running")
            )
            other.commit()
        finally:
            other.close()
        raise AppError(commander_service.ERR_RUN_BUSY, "该指挥官运行正在执行中。", http_status=409)

    monkeypatch.setattr(commander_service, "execute_run", busy)
    await commander_service.run_in_background(run_id)

    db.expire_all()
    stored = db.get(CommanderRun, run_id)
    assert stored.status == "running"
    assert stored.error_code != "COMMANDER_EXECUTE_FAILED"


@pytest.mark.asyncio
async def test_execute_run_writes_started_audit(env, monkeypatch):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, []), (2, [])], max_parallel=2)

    async def fake_invoke(_db, *, run, subtask, ctx, settings, context):
        return {"answer": "ok", "input_tokens": 1, "output_tokens": 1, "artifacts": []}

    monkeypatch.setattr(commander_service, "_invoke_agent", fake_invoke)
    await commander_service.execute_run(run.id)

    db.expire_all()
    audit = db.scalar(
        select(AuditLog).where(
            AuditLog.action == "commander_run_started", AuditLog.resource_id == run.id
        )
    )
    assert audit is not None
    assert audit.metadata_json == {"subtasks": 2, "max_parallel": 3}


# ---------------------------------------------------------------------------
# 自主补救：换助手 / 新建助手 / 指挥官自执行 / 轮尽报告能力缺口
# ---------------------------------------------------------------------------


def _bind_subtask(
    db,
    run,
    *,
    agent,
    title: str = "写销售方案",
    instruction: str = "撰写一份农业销售方案",
) -> CommanderSubtask:
    row = db.scalar(
        select(CommanderSubtask).where(CommanderSubtask.run_id == run.id, CommanderSubtask.seq == 1)
    )
    assert row is not None
    row.title = title
    row.instruction = instruction
    row.agent_id = agent.id
    row.agent_code = agent.agent_code
    row.agent_name = agent.name
    row.agent_source = "existing"
    db.commit()
    return row


def _ok_result(seq: int) -> dict:
    return {"answer": f"out-{seq}", "input_tokens": 1, "output_tokens": 1, "artifacts": []}


@pytest.mark.asyncio
async def test_remediation_switches_to_alternative_agent_and_succeeds(env, monkeypatch):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])])
    failing = _agent(db, yard, code="writer_a", name="销售方案助手A", description="写销售方案")
    better = _agent(db, yard, code="writer_b", name="销售方案助手B", description="写销售方案")
    _bind_subtask(db, run, agent=failing)

    async def fake_invoke(_db, *, run, subtask, ctx, settings, context):
        if subtask.agent_id != better.id:
            raise AppError("COMMANDER_SUBTASK_ERROR", "原助手无法完成。")
        return _ok_result(int(subtask.seq))

    monkeypatch.setattr(commander_service, "_invoke_agent", fake_invoke)
    summary = await commander_service.execute_run(run.id)

    db.expire_all()
    row = _subtasks(db, run.id)[0]
    assert summary["failed"] == []
    assert row.status == "ok"
    assert row.agent_id == better.id
    assert row.agent_name == "销售方案助手B"
    assert row.attempts == 2
    assert [item["action"] for item in (row.remediation_json or [])] == ["alt_agent"]
    audit = db.scalar(select(AuditLog).where(AuditLog.action == "commander_subtask_remediated"))
    assert audit is not None
    assert audit.metadata_json["action"] == "alt_agent"
    assert audit.metadata_json["seq"] == 1


@pytest.mark.asyncio
async def test_remediation_new_agent_then_commander_self(env, monkeypatch):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])])
    origin = _agent(db, yard, code="writer_a", name="写作助手", description="写销售方案")
    _bind_subtask(db, run, agent=origin)
    holder: dict[str, YardAgent | None] = {"agent": None}

    async def fake_create(db_, *, ctx, text, settings):
        if holder["agent"] is None:
            holder["agent"] = _agent(
                db_, yard, code="writer_new", name="新写作助手", description="写销售方案"
            )
            db_.commit()
        return holder["agent"]

    async def fake_invoke(_db, *, run, subtask, ctx, settings, context):
        if subtask.agent_source == "commander":
            return _ok_result(int(subtask.seq))
        raise AppError("COMMANDER_SUBTASK_ERROR", "该助手技能不足。")

    monkeypatch.setattr(commander_service.matcher, "create_remediation_agent", fake_create)
    monkeypatch.setattr(commander_service, "_invoke_agent", fake_invoke)

    summary = await commander_service.execute_run(run.id)
    db.expire_all()
    row = _subtasks(db, run.id)[0]
    assert summary["failed"] == []
    assert row.status == "ok"
    assert row.agent_source == "commander"
    assert row.attempts == 3
    assert [item["action"] for item in (row.remediation_json or [])] == [
        "new_agent",
        "self",
    ]


@pytest.mark.asyncio
async def test_remediation_exhausted_reports_capability_gap(env, monkeypatch):
    from app.modules.commander import settings as commander_settings

    db, _factory, yard = env
    commander_settings.update_settings(
        db,
        yard_id=yard.id,
        payload={"remediate_rounds": 5},
        actor_id="u-exec",
        request_id="req-exec",
    )
    db.commit()
    run = _make_run(db, yard, specs=[(1, [])])
    origin = _agent(db, yard, code="writer_a", name="写作助手A", description="写销售方案")
    _agent(db, yard, code="writer_b", name="写作助手B", description="写销售方案")
    _bind_subtask(db, run, agent=origin)

    async def no_agent(_db, *, ctx, text, settings):
        return None

    async def always_fail(_db, *, run, subtask, ctx, settings, context):
        raise AppError("COMMANDER_SUBTASK_ERROR", "助手无法完成该任务。")

    monkeypatch.setattr(commander_service.matcher, "create_remediation_agent", no_agent)
    monkeypatch.setattr(commander_service, "_invoke_agent", always_fail)

    summary = await commander_service.execute_run(run.id)
    db.expire_all()
    row = _subtasks(db, run.id)[0]
    assert row.status == "error"
    # 队列见底（换人 → 建人 → 亲自执行）即停手：剩下的轮数只是把同一个执行者再跑一遍
    assert row.attempts == 3
    assert row.error_code == "COMMANDER_CAPABILITY_GAP"
    assert [item["action"] for item in (row.remediation_json or [])] == ["alt_agent", "self"]
    assert "已自主尝试 3 轮" in (row.output_excerpt or "")
    assert "不具备完成该任务的能力" in (row.output_excerpt or "")
    refreshed = db.get(CommanderRun, run.id)
    assert refreshed.status == "failed"
    assert refreshed.error_code == "COMMANDER_CAPABILITY_GAP"
    assert summary["failed"][0]["reason"].startswith("已自主尝试 3 轮")


@pytest.mark.asyncio
async def test_tool_risk_blocked_stops_remediation_with_permission_hint(env, monkeypatch):
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])])
    origin = _agent(db, yard, code="writer_a", name="写作助手", description="写销售方案")
    _bind_subtask(db, run, agent=origin)
    calls = {"n": 0}

    async def blocked(_db, *, run, subtask, ctx, settings, context):
        calls["n"] += 1
        raise AppError("TOOL_RISK_BLOCKED", "高风险工具需要审批。", http_status=403)

    monkeypatch.setattr(commander_service, "_invoke_agent", blocked)
    await commander_service.execute_run(run.id)

    db.expire_all()
    row = _subtasks(db, run.id)[0]
    assert calls["n"] == 1
    assert row.attempts == 1
    assert row.error_code == "TOOL_RISK_BLOCKED"
    assert "完全控制" in (row.output_excerpt or "")


class _LockingDB:
    """只覆盖 _safe_commit 用到的接口：三个脏数据集合 + commit/rollback。"""

    def __init__(self, *, pending: bool, fail_first: bool = False) -> None:
        self.new = ["obj"] if pending else []
        self.dirty: list[str] = []
        self.deleted: list[str] = []
        self.commits = 0
        self.rollbacks = 0
        self._fail_first = fail_first

    def commit(self) -> None:
        self.commits += 1
        if self.commits == 1:
            raise OperationalError("COMMIT", {}, Exception("database is locked"))
        if self._fail_first and self.commits == 2:
            raise OperationalError("COMMIT", {}, Exception("database is locked"))

    def rollback(self) -> None:
        self.rollbacks += 1


def test_safe_commit_raises_loudly_when_locked_with_pending_changes(monkeypatch) -> None:
    """C4：commit 失败后回滚，本事务待写内容在函数内无法重放。

    重试只能提交一个空事务并「成功返回」，调用方会误判已落库——所以有脏数据时必须抛出。
    """
    monkeypatch.setattr(commander_service.time, "sleep", lambda _seconds: None)
    db = _LockingDB(pending=True)
    with pytest.raises(OperationalError):
        commander_service._safe_commit(db)  # type: ignore[arg-type]
    assert db.commits == 1
    assert db.rollbacks == 1


def test_safe_commit_retries_lock_only_when_nothing_pending(monkeypatch) -> None:
    monkeypatch.setattr(commander_service.time, "sleep", lambda _seconds: None)
    db = _LockingDB(pending=False)
    commander_service._safe_commit(db)  # type: ignore[arg-type]
    assert db.commits == 2
    assert db.rollbacks == 1


# ---------------------------------------------------------------------------
# token 记账（C3/C8）与瞬态判定（C15）
# ---------------------------------------------------------------------------


def _ledger_row(db, run, subtask_seq: int, *, input_tokens: int, output_tokens: int) -> None:
    """按真实 task_id 前缀写一笔 llm_calls 账本行（模拟一次已发生的模型调用）。"""
    from app.modules.llm.models import LLMCall

    db.add(
        LLMCall(
            yard_id=run.yard_id,
            task_id=f"cmd-{run.id[:12]}-{subtask_seq}-call1",
            provider="fake",
            model="fake",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            status="success",
            request_id="req-exec",
            prompt_sha="0" * 64,
            purpose="declarative",
        )
    )
    db.flush()


@pytest.mark.asyncio
async def test_failed_subtask_tokens_counted_from_ledger(env, monkeypatch):
    """C3：失败子任务也真花了钱，账本增量必须进 tokens_total 与子任务行。

    旧口径只在成功分支记 result 自报值，失败轮直接 ``tokens: 0``——补救轮再多，
    ``run.tokens_total`` 也不涨，预算闸长期不告警。
    """
    from app.modules.commander import settings as commander_settings

    db, _factory, yard = env
    commander_settings.update_settings(
        db,
        yard_id=yard.id,
        payload={"auto_create_agent": False},
        actor_id="u-exec",
        request_id="req-exec",
    )
    db.commit()
    run = _make_run(db, yard, specs=[(1, []), (2, [])])

    async def fake_invoke(session, *, run, subtask, ctx, settings, context):
        _ledger_row(session, run, int(subtask.seq), input_tokens=100, output_tokens=50)
        if int(subtask.seq) == 1:
            raise AppError("COMMANDER_SUBTASK_ERROR", "助手无法完成该任务。")
        return _ok_result(int(subtask.seq))

    async def no_agent(_db, *, ctx, text, settings):
        return None

    monkeypatch.setattr(commander_service.matcher, "create_remediation_agent", no_agent)
    monkeypatch.setattr(commander_service, "_invoke_agent", fake_invoke)

    await commander_service.execute_run(run.id)

    db.expire_all()
    rows = {row.seq: row for row in _subtasks(db, run.id)}
    assert rows[1].status == "error"
    assert (rows[1].input_tokens, rows[1].output_tokens) == (100, 50)
    refreshed = db.get(CommanderRun, run.id)
    # 两个子任务各 150 + 汇总 3+4；旧口径只记成功那一个的 2
    assert refreshed.tokens_total >= 300


@pytest.mark.asyncio
async def test_remediation_rounds_accumulate_in_subtask_tokens(env, monkeypatch):
    """C3：同一子任务连烧两轮才成功，入账取「账本累计」而不是「最后一轮自报」。"""
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])])
    origin = _agent(db, yard, code="writer_a", name="写作助手", description="写销售方案")
    better = _agent(db, yard, code="writer_b", name="接手助手", description="写销售方案")
    _bind_subtask(db, run, agent=origin)
    rounds = {"n": 0}

    async def flaky_invoke(session, *, run, subtask, ctx, settings, context):
        rounds["n"] += 1
        _ledger_row(session, run, int(subtask.seq), input_tokens=100, output_tokens=50)
        if rounds["n"] < 2:
            raise AppError("COMMANDER_SUBTASK_ERROR", "上一轮失败。")
        return {"answer": "成品", "input_tokens": 10, "output_tokens": 5, "artifacts": []}

    monkeypatch.setattr(commander_service, "_invoke_agent", flaky_invoke)
    await commander_service.execute_run(run.id)

    db.expire_all()
    row = _subtasks(db, run.id)[0]
    assert row.status == "ok"
    assert row.attempts == 2
    # 第二轮只自报 10/5，账本里两轮合计 200/100
    assert (row.input_tokens, row.output_tokens) == (200, 100)
    assert row.agent_id in {origin.id, better.id}


def test_retry_run_reclaims_reset_subtask_tokens(env):
    """C8：retry 把子任务 token 清零，运行级总数必须同扣，否则总数虚高。"""
    db, _factory, yard = env
    run = _make_run(db, yard, specs=[(1, [])], status="failed", subtask_status="error")
    row = _subtasks(db, run.id)[0]
    row.input_tokens = 400
    row.output_tokens = 100
    refreshed = db.get(CommanderRun, run.id)
    refreshed.tokens_total = 507  # 500 子任务 + 7 汇总
    db.commit()

    commander_service.retry_run(db, _ctx(yard), refreshed.id)

    db.expire_all()
    after = db.get(CommanderRun, refreshed.id)
    assert after.status == "queued"
    assert _subtasks(db, refreshed.id)[0].input_tokens == 0
    assert after.tokens_total == 7
    assert after.budget_status == ""


def test_transient_uses_category_not_error_text() -> None:
    """C15：正文里出现「500 亩 / 429 元」不再被当成上游抖动。"""
    assert commander_service._is_transient(
        AppError("LLM_ERROR", "限流", details={"category": "rate_limit"})
    )
    assert commander_service._is_transient(TimeoutError())
    assert not commander_service._is_transient(
        AppError("COMMANDER_SUBTASK_ERROR", "第 500 亩地面积 429 平方米，方案已生成。")
    )
    # providers 把各种上游失败都写成 502：分类存在时以分类为准，鉴权失败不该重试
    assert not commander_service._is_transient(
        AppError(
            "LLM_ERROR",
            "图像服务鉴权失败。",
            http_status=502,
            details={"category": "auth"},
        )
    )
