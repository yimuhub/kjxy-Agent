"""指挥官选兵：规则命中、统计加分、LLM 重排、相似复用、自动创建与降级回退。

全部离线：模型调用与 ``agent_builder.generate_agent`` 都在用例内 mock。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.core.deps import YardContext
from app.modules.agents import agent_builder
from app.modules.agents.models import YardAgent
from app.modules.commander import matcher
from app.modules.commander.models import CommanderSubtask
from app.modules.commander.settings import defaults
from app.modules.llm.schemas import LLMResponse
from app.modules.yards.repository import create_yard


@pytest.fixture()
def yard(db_session):
    row = create_yard(db_session, name="指挥官选兵小院")
    db_session.commit()
    return row


@pytest.fixture()
def ctx(yard):
    return YardContext(
        yard_id=yard.id, user_id="u-pick", roles=["yard_admin"], request_id="req-pick"
    )


def _agent(
    db,
    yard,
    *,
    code: str,
    name: str,
    description: str,
    status: str = "published",
    created_by: str = "u-pick",
    visibility: str = "public",
    created_from: str = "manual",
    created_at: datetime | None = None,
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
    if created_at is not None:
        row.created_at = created_at
    db.add(row)
    db.flush()
    return row


def _subtask_row(db, yard, *, agent_id: str | None, status: str) -> CommanderSubtask:
    row = CommanderSubtask(
        run_id=f"run-{uuid.uuid4().hex}",
        yard_id=yard.id,
        seq=1,
        title="统计用子任务",
        instruction="统计用子任务",
        expected_output="",
        agent_id=agent_id,
        status=status,
        depends_on_json=[],
    )
    db.add(row)
    db.flush()
    return row


def _fake_llm(monkeypatch, content: str | None = None, *, boom: bool = False):
    async def fake(_db, _request):
        if boom:
            raise RuntimeError("重排模型不可用")
        return LLMResponse(
            content=content or "",
            input_tokens=1,
            output_tokens=1,
            model="fake",
            latency_ms=1,
        )

    monkeypatch.setattr(matcher, "complete_with_logging", fake)


def _subtask(text: str, hint: str = "") -> dict:
    return {
        "seq": 1,
        "title": text,
        "instruction": text,
        "expected_output": "",
        "agent_hint": hint,
        "agent_code": "",
        "depends_on": [],
    }


@pytest.mark.asyncio
async def test_rule_score_hits_published_agent(db_session, yard, ctx):
    agent = _agent(db_session, yard, code="ppt_a", name="PPT 设计师", description="幻灯片 演示文稿")
    db_session.commit()
    settings = defaults()
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("生成 PPT 幻灯片")],
        settings=settings,
        plan_fallback=False,
    )
    assert picks[0]["agent_id"] == agent.id
    assert picks[0]["agent_source"] == "existing"


@pytest.mark.asyncio
async def test_similar_draft_is_reused_without_creating(db_session, yard, ctx, monkeypatch):
    existing = _agent(
        db_session,
        yard,
        code="gen_ppt_designer",
        name="PPT 设计师",
        description="专门生成幻灯片与演示文稿",
        status="draft",
        created_from="commander",
    )
    db_session.commit()
    called = {"n": 0}

    async def fail_if_called(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("应复用相似 Agent，不应再次创建")

    monkeypatch.setattr(agent_builder, "generate_agent", fail_if_called)
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("生成幻灯片演示文稿")],
        settings=defaults(),
        plan_fallback=False,
    )
    assert called["n"] == 0
    assert picks[0]["agent_id"] == existing.id
    assert picks[0]["agent_source"] == "existing"


@pytest.mark.asyncio
async def test_auto_create_agent_marks_generated(db_session, yard, ctx, monkeypatch):
    async def fake_generate(db, *, ctx, utterance, visibility, use_web=False, **_kwargs):
        row = _agent(
            db,
            yard,
            code="gen_sales_proposal",
            name="销售提案专家",
            description="负责销售提案与报价单",
            status="draft",
            created_from="generated",
        )
        return {"agent": {"id": row.id, "agent_code": row.agent_code, "name": row.name}}

    monkeypatch.setattr(agent_builder, "generate_agent", fake_generate)
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("撰写销售提案与报价单")],
        settings=defaults(),
        plan_fallback=False,
    )
    assert picks[0]["agent_source"] == "generated"
    assert picks[0]["agent_code"] == "gen_sales_proposal"
    assert picks[0]["agent_id"]
    # 智能体库据此显示「AI 创建」，与用户自己点「一句话生成」区分
    from app.modules.agents.models import YardAgent

    created = db_session.get(YardAgent, picks[0]["agent_id"])
    assert created is not None and created.created_from == "commander"


@pytest.mark.asyncio
async def test_auto_create_failure_falls_back_to_closest(db_session, yard, ctx, monkeypatch):
    closest = _agent(db_session, yard, code="doc_a", name="文档助手", description="写文档")

    async def boom(*_args, **_kwargs):
        raise RuntimeError("生成服务暂不可用")

    monkeypatch.setattr(agent_builder, "generate_agent", boom)
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("随便写点什么")],
        settings=defaults(),
        plan_fallback=False,
    )
    # 0 分候选仍比让指挥官代答更符合「回退到最接近候选」
    assert picks[0]["agent_source"] == "existing"
    assert picks[0]["agent_id"] == closest.id


@pytest.mark.asyncio
async def test_no_candidate_and_auto_create_off_uses_commander(db_session, yard, ctx):
    settings = defaults()
    settings["auto_create_agent"] = False
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("完全没人能做的任务")],
        settings=settings,
        plan_fallback=False,
    )
    assert picks[0]["agent_source"] == "commander"
    assert picks[0]["agent_id"] is None


@pytest.mark.asyncio
async def test_auto_publish_requires_permission_and_audits(db_session, yard, ctx, monkeypatch):
    from app.modules.audit.models import AuditLog

    async def fake_generate(db, *, ctx, utterance, visibility, use_web=False, **_kwargs):
        row = _agent(
            db,
            yard,
            code="gen_auto_pub",
            name="自动发布助手",
            description="生成后自动发布",
            status="draft",
            created_from="generated",
        )
        return {"agent": {"id": row.id, "agent_code": row.agent_code, "name": row.name}}

    monkeypatch.setattr(agent_builder, "generate_agent", fake_generate)
    settings = defaults()
    settings["auto_publish_agent"] = True
    await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("生成后自动发布")],
        settings=settings,
        plan_fallback=False,
    )
    row = db_session.query(YardAgent).filter(YardAgent.agent_code == "gen_auto_pub").one()
    assert row.status == "published"
    action = (
        db_session.query(AuditLog)
        .filter(AuditLog.action == "commander_agent_published")
        .one_or_none()
    )
    assert action is not None


def test_agent_stats_and_score_prefer_success(db_session, yard):
    good = _agent(db_session, yard, code="ppt_good", name="PPT 专家", description="幻灯片 演示文稿")
    bad = _agent(db_session, yard, code="ppt_bad", name="PPT 助手", description="幻灯片 演示文稿")
    for _ in range(4):
        _subtask_row(db_session, yard, agent_id=good.id, status="ok")
    for _ in range(4):
        _subtask_row(db_session, yard, agent_id=bad.id, status="error")
    _subtask_row(db_session, yard, agent_id=good.id, status="running")
    db_session.commit()

    stats = matcher.agent_stats(db_session, yard.id)
    assert stats[good.id] == {"total": 5, "ok": 4, "running": 1}
    assert stats[bad.id] == {"total": 4, "ok": 0, "running": 0}

    text = "幻灯片演示文稿"
    # stats 缺省时算法不变：两者关键词分一致
    assert matcher.score_agent(text, good) == matcher.score_agent(text, bad)
    assert matcher.score_agent(text, good, stats) > matcher.score_agent(text, bad, stats)
    best, _ = matcher.best_candidate(text, [bad, good], stats)
    assert best is good


def test_best_candidate_tie_breaks_on_lower_load(db_session, yard):
    busy = _agent(db_session, yard, code="ppt_busy", name="PPT 专家", description="幻灯片")
    idle = _agent(db_session, yard, code="ppt_idle", name="PPT 助手", description="幻灯片")
    stats = {
        busy.id: {"total": 5, "ok": 5, "running": 3},
        idle.id: {"total": 5, "ok": 5, "running": 0},
    }
    # 同分时与候选顺序无关，始终选 running 更少的
    assert matcher.best_candidate("幻灯片", [busy, idle], stats)[0] is idle
    assert matcher.best_candidate("幻灯片", [idle, busy], stats)[0] is idle


@pytest.mark.asyncio
async def test_llm_rerank_high_confidence_uses_llm_pick(db_session, yard, ctx, monkeypatch):
    weak = _agent(
        db_session,
        yard,
        code="ppt_weak",
        name="幻灯片专家",
        description="幻灯片 演示文稿 销售 提案",
    )
    strong = _agent(db_session, yard, code="sales_strong", name="销售顾问", description="销售")
    db_session.commit()
    payload = json.dumps(
        {"agent_code": strong.agent_code, "confidence": 0.9, "reason": "更贴合销售场景"},
        ensure_ascii=False,
    )
    _fake_llm(monkeypatch, payload)

    assert matcher.best_candidate("生成销售幻灯片提案", [weak, strong])[0] is weak
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("生成销售幻灯片提案")],
        settings=defaults(),
        plan_fallback=False,
    )
    assert weak.id != strong.id
    assert picks[0]["agent_id"] == strong.id


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invalid_json", "exception"])
async def test_llm_rerank_failure_falls_back_to_rule_best(db_session, yard, ctx, monkeypatch, mode):
    weak = _agent(
        db_session,
        yard,
        code="ppt_weak",
        name="幻灯片专家",
        description="幻灯片 演示文稿 销售 提案",
    )
    strong = _agent(db_session, yard, code="sales_strong", name="销售顾问", description="销售")
    db_session.commit()
    if mode == "invalid_json":
        _fake_llm(monkeypatch, "模型没有输出 JSON")
    else:
        _fake_llm(monkeypatch, boom=True)

    assert matcher.best_candidate("生成销售幻灯片提案", [weak, strong])[0] is weak
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("生成销售幻灯片提案")],
        settings=defaults(),
        plan_fallback=False,
    )
    # 模型不可用属“未决策”，规则最高分 weak 仍是兜底
    assert picks[0]["agent_id"] == weak.id


@pytest.mark.asyncio
async def test_llm_low_confidence_respects_auto_create_off(db_session, yard, ctx, monkeypatch):
    weak = _agent(
        db_session,
        yard,
        code="ppt_weak",
        name="幻灯片专家",
        description="幻灯片 演示文稿 销售 提案",
    )
    strong = _agent(db_session, yard, code="sales_strong", name="销售顾问", description="销售")
    db_session.commit()
    _fake_llm(
        monkeypatch,
        json.dumps(
            {"agent_code": strong.agent_code, "confidence": 0.59, "reason": "勉强"},
            ensure_ascii=False,
        ),
    )
    settings = defaults()
    settings["auto_create_agent"] = False
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("生成销售幻灯片提案")],
        settings=settings,
        plan_fallback=False,
    )
    # 不允许自动建时，置信度不足也只能退回规则最高分
    assert picks[0]["agent_id"] == weak.id


@pytest.mark.asyncio
async def test_llm_low_confidence_triggers_auto_create(db_session, yard, ctx, monkeypatch):
    _agent(
        db_session,
        yard,
        code="ppt_weak",
        name="幻灯片专家",
        description="幻灯片 演示文稿 销售 提案",
    )
    strong = _agent(db_session, yard, code="sales_strong", name="销售顾问", description="销售")
    db_session.commit()
    _fake_llm(
        monkeypatch,
        json.dumps(
            {"agent_code": strong.agent_code, "confidence": 0.4, "reason": "都不合适"},
            ensure_ascii=False,
        ),
    )

    async def fake_generate(db, *, ctx, utterance, visibility, use_web=False, **_kwargs):
        row = _agent(
            db,
            yard,
            code="gen_sales_deck",
            name="销售提案专家",
            description="销售提案与演示文稿",
            status="draft",
            created_from="generated",
        )
        return {"agent": {"id": row.id, "agent_code": row.agent_code, "name": row.name}}

    monkeypatch.setattr(agent_builder, "generate_agent", fake_generate)
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("生成销售幻灯片提案")],
        settings=defaults(),
        plan_fallback=False,
    )
    # 置信度 <0.6 视为“无合适”，应进入自动创建而不是硬用规则最高分
    assert picks[0]["agent_source"] == "generated"
    assert picks[0]["agent_code"] == "gen_sales_deck"


@pytest.mark.asyncio
async def test_similar_reuse_ignores_agents_older_than_90_days(db_session, yard, ctx, monkeypatch):
    _agent(
        db_session,
        yard,
        code="gen_old_ppt",
        name="旧版幻灯片助手",
        description="专门生成幻灯片与演示文稿",
        status="draft",
        created_from="commander",
        created_at=datetime.now(UTC) - timedelta(days=100),
    )
    db_session.commit()
    called = {"n": 0}

    async def fake_generate(db, *, ctx, utterance, visibility, use_web=False, **_kwargs):
        called["n"] += 1
        row = _agent(
            db,
            yard,
            code="gen_fresh_ppt",
            name="新版幻灯片助手",
            description="专门生成幻灯片与演示文稿",
            status="draft",
        )
        return {"agent": {"id": row.id, "agent_code": row.agent_code, "name": row.name}}

    monkeypatch.setattr(agent_builder, "generate_agent", fake_generate)
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("生成幻灯片演示文稿")],
        settings=defaults(),
        plan_fallback=False,
    )
    assert called["n"] == 1
    assert picks[0]["agent_source"] == "generated"
    assert picks[0]["agent_code"] == "gen_fresh_ppt"


@pytest.mark.asyncio
async def test_daily_limit_skips_create_and_falls_back_to_candidate(
    db_session, yard, ctx, monkeypatch
):
    _agent(
        db_session,
        yard,
        code="cmd_today",
        name="今日已建助手",
        description="与本次任务无关",
        status="draft",
        created_from="commander",
    )
    closest = _agent(db_session, yard, code="doc_a", name="文档助手", description="写文档")
    db_session.commit()
    called = {"n": 0}

    async def fail_if_called(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("达到每日上限后不应再创建")

    monkeypatch.setattr(agent_builder, "generate_agent", fail_if_called)
    settings = defaults()
    settings["daily_agent_limit"] = 1
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("撰写销售提案与报价单")],
        settings=settings,
        plan_fallback=False,
    )
    assert called["n"] == 0
    assert picks[0]["agent_source"] == "existing"
    assert picks[0]["agent_id"] == closest.id


@pytest.mark.asyncio
async def test_daily_limit_without_candidates_uses_commander(db_session, yard, ctx, monkeypatch):
    _agent(
        db_session,
        yard,
        code="cmd_today",
        name="今日已建助手",
        description="与本次任务无关",
        status="draft",
        created_from="commander",
    )
    db_session.commit()
    called = {"n": 0}

    async def fail_if_called(*_args, **_kwargs):
        called["n"] += 1
        raise AssertionError("达到每日上限后不应再创建")

    monkeypatch.setattr(agent_builder, "generate_agent", fail_if_called)
    settings = defaults()
    settings["daily_agent_limit"] = 1
    picks = await matcher.assign_agents(
        db_session,
        ctx=ctx,
        subtasks=[_subtask("撰写销售提案与报价单")],
        settings=settings,
        plan_fallback=False,
    )
    assert called["n"] == 0
    assert picks[0]["agent_source"] == "commander"
    assert picks[0]["agent_id"] is None
