"""指挥官规划：JSON 提取、clamp、依赖成环、修复与 fallback（离线）。"""

from __future__ import annotations

import json

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.agents.models import YardAgent
from app.modules.commander import planner
from app.modules.commander import service as commander_service
from app.modules.commander import settings as commander_settings
from app.modules.commander.models import CommanderRun
from app.modules.llm.schemas import LLMResponse
from app.modules.yards.repository import create_yard
from sqlalchemy import select


@pytest.fixture()
def yard(db_session):
    row = create_yard(db_session, name="指挥官规划小院")
    db_session.commit()
    return row


@pytest.fixture()
def ctx(yard):
    return YardContext(yard_id=yard.id, user_id="u-cmd", roles=["yard_admin"], request_id="req-cmd")


def _agent(db, yard, *, code: str, name: str, description: str, status: str = "published"):
    row = YardAgent(
        yard_id=yard.id,
        agent_code=code,
        name=name,
        description=description,
        emoji="🧩",
        persona_prompt="你是助手。",
        task_prompt_template="回答：{{question}}",
        allowed_tools=[],
        bound_skills=["office_ppt_deck"],
        knowledge_scopes=[],
        status=status,
        visibility="public",
        created_by="u-cmd",
    )
    db.add(row)
    db.flush()
    return row


def _resp(content: str) -> LLMResponse:
    return LLMResponse(
        content=content, input_tokens=10, output_tokens=20, model="fake", latency_ms=5
    )


def _plan_json(count: int = 2) -> str:
    tasks = []
    for index in range(1, count + 1):
        tasks.append(
            {
                "id": f"t{index}",
                "title": f"任务 {index}",
                "instruction": f"完成第 {index} 步",
                "expected_output": "文本",
                "agent_hint": "",
                "agent_code": "",
                "depends_on": [f"t{index - 1}"] if index > 1 else [],
            }
        )
    return json.dumps({"goal": "做一个 PPT", "subtasks": tasks}, ensure_ascii=False)


def test_parse_json_object_tolerates_fences_and_prefix():
    assert planner.parse_json_object('```json\n{"goal":"g"}\n```') == {"goal": "g"}
    assert planner.parse_json_object('好的，计划如下：{"goal":"g","subtasks":[]} 完毕') == {
        "goal": "g",
        "subtasks": [],
    }
    assert planner.parse_json_object("完全不是 JSON") is None


def test_normalize_clamps_subtask_count():
    raw = json.loads(_plan_json(4))
    plan = planner.normalize_plan(raw, max_subtasks=2, fallback_goal="目标")
    assert plan is not None
    assert len(plan["subtasks"]) == 2
    assert [item["seq"] for item in plan["subtasks"]] == [1, 2]


def test_normalize_drops_forward_and_self_dependencies():
    raw = {
        "goal": "g",
        "subtasks": [
            {"title": "a", "instruction": "a", "depends_on": [2, 1, 3]},
            {"title": "b", "instruction": "b", "depends_on": ["t1", "t2"]},
        ],
    }
    plan = planner.normalize_plan(raw, max_subtasks=6, fallback_goal="g")
    assert plan is not None
    assert plan["subtasks"][0]["depends_on"] == []
    assert plan["subtasks"][1]["depends_on"] == [1]


def test_normalize_breaks_cycles_by_dropping_forward_dependencies():
    raw = {
        "goal": "g",
        "subtasks": [
            {"id": "t1", "title": "a", "instruction": "a", "depends_on": ["t2"]},
            {"id": "t2", "title": "b", "instruction": "b", "depends_on": ["t1"]},
        ],
    }
    plan = planner.normalize_plan(raw, max_subtasks=6, fallback_goal="g")
    assert plan is not None
    # t1 的前向依赖 t2 被丢弃；t2 指向更早的 t1 合法 —— 全图无环
    assert plan["subtasks"][0]["depends_on"] == []
    assert plan["subtasks"][1]["depends_on"] == [1]


def test_normalize_returns_none_for_bad_shape():
    assert planner.normalize_plan({"goal": "g"}, max_subtasks=6, fallback_goal="g") is None
    assert planner.normalize_plan({"subtasks": []}, max_subtasks=6, fallback_goal="g") is None
    assert (
        planner.normalize_plan({"subtasks": [{"title": ""}]}, max_subtasks=6, fallback_goal="g")
        is None
    )


def test_fallback_plan_is_single_task():
    plan = planner.fallback_plan("帮我写一份销售提案")
    assert plan["fallback"] is True
    assert len(plan["subtasks"]) == 1
    assert plan["subtasks"][0]["depends_on"] == []


@pytest.mark.asyncio
async def test_create_plan_run_falls_back_to_single_task(db_session, yard, ctx, monkeypatch):
    commander_settings.update_settings(
        db_session,
        yard_id=yard.id,
        payload={"auto_create_agent": False},
        actor_id="u-cmd",
        request_id="req-cmd",
    )
    db_session.commit()

    async def garbage(*_args, **_kwargs):
        return _resp("模型今天不想输出 JSON")

    monkeypatch.setattr(planner, "complete_with_logging", garbage)
    run = await commander_service.create_plan_run(
        db_session,
        ctx=ctx,
        session_id="s-cmd",
        user_content="帮我做一份销售提案 PPT",
    )
    _run, subtasks = commander_service.get_run(db_session, yard.id, run.id)
    assert run.status == "queued"
    assert run.plan_json["fallback"] is True
    assert len(subtasks) == 1
    assert subtasks[0].title == "完成用户请求"
    assert subtasks[0].agent_source == "commander"


@pytest.mark.asyncio
async def test_create_plan_run_repairs_invalid_json_once(db_session, yard, ctx, monkeypatch):
    commander_settings.update_settings(
        db_session,
        yard_id=yard.id,
        payload={"auto_create_agent": False},
        actor_id="u-cmd",
        request_id="req-cmd",
    )
    db_session.commit()
    calls = {"n": 0}

    async def repair_then_ok(*_args, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _resp("这不是 JSON")
        return _resp(_plan_json(2))

    monkeypatch.setattr(planner, "complete_with_logging", repair_then_ok)
    run = await commander_service.create_plan_run(
        db_session, ctx=ctx, session_id="s-cmd", user_content="做一个 PPT"
    )
    _run, subtasks = commander_service.get_run(db_session, yard.id, run.id)
    assert calls["n"] == 2
    assert run.plan_json["fallback"] is False
    assert run.plan_json["plan_repaired"] is True
    assert len(subtasks) == 2
    assert subtasks[1].depends_on_json == [1]


@pytest.mark.asyncio
async def test_create_plan_run_binds_matching_existing_agent(db_session, yard, ctx, monkeypatch):
    agent = _agent(
        db_session, yard, code="ppt_master", name="PPT 设计师", description="演示文稿 幻灯片"
    )
    db_session.commit()

    async def plan(*_args, **_kwargs):
        return _resp(
            json.dumps(
                {
                    "goal": "做 PPT",
                    "subtasks": [
                        {
                            "id": "t1",
                            "title": "生成 PPT 幻灯片",
                            "instruction": "使用演示文稿技能生成幻灯片",
                            "agent_hint": "办公文档/PPT",
                            "expected_output": "pptx",
                            "depends_on": [],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )

    monkeypatch.setattr(planner, "complete_with_logging", plan)
    run = await commander_service.create_plan_run(
        db_session, ctx=ctx, session_id="s-cmd", user_content="做一个 PPT"
    )
    _run, subtasks = commander_service.get_run(db_session, yard.id, run.id)
    assert subtasks[0].agent_id == agent.id
    assert subtasks[0].agent_source == "existing"
    assert subtasks[0].agent_name == "PPT 设计师"


def test_normalize_clarify_filters_empty_and_caps_two():
    plan = planner.normalize_plan(
        {"goal": "做方案", "clarify": ["  面向哪个作物？ ", "", "交付什么格式？", "第三个问题？"]},
        max_subtasks=6,
        fallback_goal="目标",
    )
    assert plan is not None
    assert plan["fallback"] is False
    assert plan["subtasks"] == []
    assert plan["clarify"] == ["面向哪个作物？", "交付什么格式？"]
    assert plan["goal"] == "做方案"


def test_normalize_clarify_truncates_long_questions():
    plan = planner.normalize_plan({"clarify": ["问" * 300]}, max_subtasks=6, fallback_goal="目标")
    assert plan is not None
    assert len(plan["clarify"][0]) == 200


@pytest.mark.asyncio
async def test_create_plan_run_returns_cancelled_clarify_run(db_session, yard, ctx, monkeypatch):
    commander_settings.update_settings(
        db_session,
        yard_id=yard.id,
        payload={"auto_create_agent": False, "clarify_enabled": True},
        actor_id="u-cmd",
        request_id="req-cmd",
    )
    db_session.commit()

    async def clarify(*_args, **_kwargs):
        return _resp(
            json.dumps(
                {
                    "goal": "做一份方案",
                    "clarify": ["面向哪个作物？", "交付什么格式？"],
                },
                ensure_ascii=False,
            )
        )

    monkeypatch.setattr(planner, "complete_with_logging", clarify)
    run = await commander_service.create_plan_run(
        db_session, ctx=ctx, session_id="s-cmd", user_content="帮我做个方案"
    )
    _run, subtasks = commander_service.get_run(db_session, yard.id, run.id)
    assert run.status == "cancelled"
    assert run.error_code == "COMMANDER_NEEDS_INPUT"
    assert run.plan_json["clarify"] == ["面向哪个作物？", "交付什么格式？"]
    assert run.plan_json["subtasks"] == []
    assert run.plan_json["fallback"] is False
    assert run.tokens_total == 30
    assert subtasks == []
    assert commander_service.run_out(run, subtasks)["clarify"] == [
        "面向哪个作物？",
        "交付什么格式？",
    ]


@pytest.mark.asyncio
async def test_create_plan_run_ignores_clarify_when_disabled(db_session, yard, ctx, monkeypatch):
    commander_settings.update_settings(
        db_session,
        yard_id=yard.id,
        payload={"auto_create_agent": False, "clarify_enabled": False},
        actor_id="u-cmd",
        request_id="req-cmd",
    )
    db_session.commit()

    async def clarify(*_args, **_kwargs):
        return _resp(json.dumps({"clarify": ["问什么？"]}, ensure_ascii=False))

    monkeypatch.setattr(planner, "complete_with_logging", clarify)
    run = await commander_service.create_plan_run(
        db_session, ctx=ctx, session_id="s-cmd", user_content="帮我做个方案"
    )
    _run, subtasks = commander_service.get_run(db_session, yard.id, run.id)
    assert run.status == "queued"
    assert run.plan_json["fallback"] is True
    assert run.plan_json["clarify"] == []
    assert len(subtasks) == 1
    assert subtasks[0].title == "完成用户请求"
    assert subtasks[0].agent_source == "commander"


@pytest.mark.asyncio
async def test_create_plan_run_quota_precheck_rejects(db_session, yard, ctx, monkeypatch):
    def deny(*_args, **_kwargs):
        raise AppError("QUOTA_EXCEEDED", "本小院额度已用尽。", http_status=429)

    monkeypatch.setattr(commander_service.quota_engine, "preflight", deny)
    with pytest.raises(AppError) as exc:
        await commander_service.create_plan_run(
            db_session, ctx=ctx, session_id="s-cmd", user_content="帮我做个方案"
        )
    assert exc.value.code == "QUOTA_EXCEEDED"
    assert exc.value.http_status == 429
    assert db_session.scalars(select(CommanderRun)).all() == []


@pytest.mark.asyncio
async def test_full_control_snapshot_and_clarify_suppressed(db_session, yard, ctx, monkeypatch):
    """完全控制：运行快照 approval_mode=full，并且即使模型要求澄清也直接开跑。"""
    from app.modules.chat.models import ChatSession

    commander_settings.update_settings(
        db_session,
        yard_id=yard.id,
        payload={"auto_create_agent": False, "clarify_enabled": True},
        actor_id="u-cmd",
        request_id="req-cmd",
    )
    session = ChatSession(yard_id=yard.id, created_by=ctx.user_id, approval_mode="full")
    db_session.add(session)
    db_session.commit()

    async def clarify(*_args, **_kwargs):
        return _resp(json.dumps({"clarify": ["要几页？"]}, ensure_ascii=False))

    monkeypatch.setattr(planner, "complete_with_logging", clarify)
    run = await commander_service.create_plan_run(
        db_session, ctx=ctx, session_id=session.id, user_content="帮我做个方案"
    )
    _run, subtasks = commander_service.get_run(db_session, yard.id, run.id)
    assert run.approval_mode == "full"
    assert run.status == "queued"
    assert run.plan_json["clarify"] == []
    assert len(subtasks) == 1
    assert subtasks[0].agent_source == "commander"
    assert commander_service._run_ctx(run).approval_mode == "full"
