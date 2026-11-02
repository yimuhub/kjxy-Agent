"""指挥官身份行（v3.51）：全量技能/工具挂载、不参与选兵、按任务取用与接口。"""

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
from app.modules.agents.agent_tools import register_agent_tools
from app.modules.agents.models import YardAgent
from app.modules.commander import identity
from app.modules.commander import matcher as commander_matcher
from app.modules.commander import planner as commander_planner
from app.modules.commander.router import router as commander_router
from app.modules.evolution.models import Skill
from app.modules.yards.repository import create_yard
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient


@pytest.fixture()
def yard(db_session):
    row = create_yard(db_session, name="指挥官身份小院")
    db_session.commit()
    return row


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


def _skill(db, yard_id, *, skill_code: str, name: str, description: str) -> Skill:
    row = Skill(
        yard_id=yard_id,
        skill_code=skill_code,
        version="1.0.0",
        definition_json={"name": name, "description": description},
        status="published",
    )
    db.add(row)
    db.commit()
    return row


def _team_agent(db, yard_id, *, code: str, name: str) -> YardAgent:
    row = YardAgent(
        yard_id=yard_id,
        agent_code=code,
        name=name,
        emoji="🧑‍🌾",
        persona_prompt=f"你是{name}。",
        task_prompt_template="完成任务：{{question}}",
        allowed_tools=["knowledge.search"],
        bound_skills=[],
        knowledge_scopes=[],
        status="published",
        visibility="public",
        created_by="u-owner",
    )
    db.add(row)
    db.commit()
    return row


def test_身份行一次挂满全部技能与工具且幂等(db_session, yard):
    register_agent_tools()
    skill = _skill(db_session, yard.id, skill_code="ppt", name="PPT 制作", description="演示文稿")

    first = identity.ensure_commander_agent(db_session, yard_id=yard.id, actor_id="u-owner")
    second = identity.ensure_commander_agent(db_session, yard_id=yard.id, actor_id="u-owner")

    assert first is not None and second.id == first.id
    assert first.agent_code == identity.COMMANDER_AGENT_CODE
    assert first.name == "指挥官"
    assert first.status == "published"
    assert first.created_from == "commander"
    assert f"skill:{skill.id}" in first.bound_skills
    assert "agent.generate" in first.allowed_tools
    assert set(first.allowed_tools) == set(identity.available_tool_codes())


def test_身份行不参与选兵候选(db_session, yard):
    """指挥官挂了全院技能，若成为候选会把每个子任务都抢走、团队被架空。"""
    identity.ensure_commander_agent(db_session, yard_id=yard.id, actor_id="u-owner")
    worker = _team_agent(db_session, yard.id, code="reporter", name="报表专员")

    candidates = commander_matcher.match_candidates(db_session, yard_id=yard.id, user_id="u-owner")
    catalog = commander_planner.visible_published_agents(
        db_session, yard_id=yard.id, user_id="u-owner"
    )
    picked = commander_matcher.lookup_by_code(
        db_session, yard_id=yard.id, user_id="u-owner", agent_code=identity.COMMANDER_AGENT_CODE
    )

    assert [agent.id for agent in candidates] == [worker.id]
    assert [agent.id for agent in catalog] == [worker.id]
    assert picked is None


def test_亲自执行按子任务取用最相关技能(db_session, yard):
    ppt = _skill(
        db_session,
        yard.id,
        skill_code="ppt",
        name="PPT 制作",
        description="演示文稿与幻灯片",
    )
    pest = _skill(
        db_session,
        yard.id,
        skill_code="pest",
        name="病虫害诊断",
        description="苹果树病虫害识别与防治建议",
    )
    identity.ensure_commander_agent(db_session, yard_id=yard.id, actor_id="u-owner")

    skills, tools = identity.executor_bindings(
        db_session, yard_id=yard.id, text="给苹果树做一次病虫害诊断并给出防治方案"
    )

    assert skills == [f"skill:{pest.id}"]
    assert f"skill:{ppt.id}" not in skills
    assert tools  # 工具全量交给工具闸，按风险分级拦截


def test_非交付子任务不给文档生成工具(db_session, yard):
    from app.modules.docs.tool import register_doc_generate_tool
    from app.modules.search.tools import register_search_tools

    register_agent_tools()
    register_search_tools()
    register_doc_generate_tool()
    identity.ensure_commander_agent(db_session, yard_id=yard.id, actor_id="u-owner")

    agent = identity.build_executor_agent(
        db_session,
        ctx=CurrentUser(
            user_id="u-owner", yard_id=yard.id, roles=["yard_owner"], request_id="req-1"
        ),
        question="先列一份大纲",
        allow_doc_tool=False,
    )

    assert "doc.generate" in identity.available_tool_codes()
    assert "doc.generate" not in agent.allowed_tools
    assert "web.search" in agent.allowed_tools
    assert agent.agent_code == identity.COMMANDER_AGENT_CODE


def test_裁剪清单拒绝未知编码并保存有效裁剪(db_session, yard):
    keep = _skill(db_session, yard.id, skill_code="ppt", name="PPT 制作", description="演示文稿")
    identity.ensure_commander_agent(db_session, yard_id=yard.id, actor_id="u-owner")

    with pytest.raises(AppError) as exc:
        identity.set_bindings(
            db_session,
            yard_id=yard.id,
            actor_id="u-owner",
            allowed_tools=["nope.tool"],
        )
    assert exc.value.http_status == 422

    payload = identity.set_bindings(
        db_session,
        yard_id=yard.id,
        actor_id="u-owner",
        bound_skills=[f"skill:{keep.id}"],
    )
    assert payload["skill_count"] == 1
    assert payload["skills"][0]["code"] == f"skill:{keep.id}"


def test_全量挂载后提示词只放十条规范其余进目录(db_session, yard):
    """上百项技能全塞会撑爆上下文：十条给完整规范，其余只进目录。"""
    from app.modules.agents import declarative

    rows = [
        _skill(
            db_session,
            yard.id,
            skill_code=f"skill_{index}",
            name=f"技能{index}",
            description="用于校验提示词预算的占位技能",
        )
        for index in range(12)
    ]

    prompt = declarative._resolve_bound_skills_prompt(
        db_session, yard.id, [f"skill:{row.id}" for row in rows]
    )

    assert prompt.count("<skill code=") == declarative.MAX_SKILL_SOP_BLOCKS
    assert prompt.count("\n- 【") == len(rows) - declarative.MAX_SKILL_SOP_BLOCKS
    assert "只列目录" in prompt


def test_接口出参含自建助手工具并可一键全量挂载(db_session, yard):
    register_agent_tools()
    _skill(db_session, yard.id, skill_code="ppt", name="PPT 制作", description="演示文稿")
    user = CurrentUser(
        user_id="u-owner", yard_id=yard.id, roles=["yard_owner"], request_id="req-identity"
    )
    client = _client(db_session, user)

    absent = client.get("/api/v1/commander/identity").json()["data"]
    assert absent["status"] == "absent"
    assert absent["skill_count"] == 0

    attached = client.post("/api/v1/commander/identity/attach-all").json()["data"]
    assert attached["status"] == "published"
    assert attached["skill_count"] == attached["available_skill_count"]
    assert attached["tool_count"] == attached["available_tool_count"]
    assert "agent.generate" in [tool["code"] for tool in attached["tools"]]

    rejected = client.put("/api/v1/commander/identity", json={"bound_skills": ["skill:not-exist"]})
    assert rejected.status_code == 422
