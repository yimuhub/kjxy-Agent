"""内置办公技能种子、文档 SOP 注入与助手自动绑定的单元测试（3.35）。"""

from __future__ import annotations

import pytest
from app.modules.agents import factory_frameworks as ff
from app.modules.agents.factory_blueprint import heuristic_blueprint
from app.modules.agents.models import YardAgent
from app.modules.evolution.models import Skill
from app.modules.market import office_skills as office
from app.modules.market.models import MarketItem
from app.modules.yards.repository import create_yard

EXPECTED_CODES = {
    "office_word_report",
    "office_excel_table",
    "office_ppt_deck",
    "office_weekly_report",
    "office_data_clean",
    "office_doc_review",
    "office_ppt_storyline",
    "office_ppt_design",
    "office_ppt_chart",
    "office_ppt_review",
    "office_ppt_template",
}
EXPECTED_ITEMS = len(EXPECTED_CODES)


@pytest.fixture(scope="module")
def items() -> list[dict]:
    return office.builtin_items()


def _agent(
    db,
    yard_id: str,
    code: str,
    name: str,
    *,
    bound: list[str] | None = None,
    description: str | None = None,
    persona: str = "你是办公文档助手。",
) -> YardAgent:
    agent = YardAgent(
        yard_id=yard_id,
        agent_code=code,
        name=name,
        description=description if description is not None else f"{name}负责办公文档",
        emoji="📄",
        persona_prompt=persona,
        task_prompt_template="{{question}}",
        allowed_tools=["knowledge.search"],
        bound_skills=list(bound or []),
        knowledge_scopes=["yard"],
        status="published",
        visibility="public",
        version="1.0.0",
        created_from="manual",
    )
    db.add(agent)
    db.flush()
    return agent


def test_office_items_shape(items: list[dict]) -> None:
    assert len(items) == EXPECTED_ITEMS
    assert {item["code"] for item in items} == EXPECTED_CODES
    for item in items:
        assert item["item_type"] == "skill"
        assert item["item_key"] == f"builtin:skill:{item['code']}"
        definition = item["definition_json"]
        assert definition["category"] == "office"
        assert definition["skill_group"] == office.GROUP_OFFICE
        assert definition["summary"] == definition["description"]
        assert definition["allowed_tools"] == ["knowledge.search"]
        assert definition["knowledge_scopes"] == ["yard"]
        assert definition["guardrails"]["require_citation"] is False
        assert len(definition["steps"]) >= 4
        assert len(definition["eval_cases"]) >= 2
        assert definition["sources"] == []


def test_seed_and_provision(db_session) -> None:
    assert office.seed_office_skills(db_session) == EXPECTED_ITEMS
    assert office.seed_office_skills(db_session) == 0
    yard = create_yard(db_session, name="办公技能单测小院")
    db_session.flush()
    assert (
        office.ensure_office_skills(
            db_session, yard_id=yard.id, actor_id="u-office", request_id="test"
        )
        == EXPECTED_ITEMS
    )
    ids = office.office_skill_ids(db_session, yard.id)
    assert set(ids) == EXPECTED_CODES
    assert db_session.query(Skill).filter(Skill.yard_id == yard.id).count() == EXPECTED_ITEMS
    office_items = db_session.query(MarketItem).filter(
        MarketItem.item_key.like("builtin:skill:office%")
    )
    assert office_items.count() == EXPECTED_ITEMS


def test_format_and_text_selection() -> None:
    assert office.select_office_skill_codes("做一份表格", "xlsx")[0] == "office_excel_table"
    assert office.select_office_skill_codes("做个汇报", "pptx")[0] == "office_ppt_deck"
    assert "office_ppt_deck" in office.match_office_skill_codes("做一版 PPT 汇报材料")
    assert office.match_office_skill_codes("随便聊聊") == []


def test_office_guidance_prefers_bound_skill(db_session) -> None:
    yard = create_yard(db_session, name="办公技能单测小院")
    db_session.flush()
    office.ensure_office_skills(db_session, yard_id=yard.id, actor_id="u-office")
    ids = office.office_skill_ids(db_session, yard.id)
    text = office.office_guidance(
        db_session,
        yard.id,
        text="做一份台账",
        fmt="xlsx",
        bound_skills=[f"skill:{ids['office_excel_table']}"],
    )
    assert "Excel" in text and "表头" in text
    # 未绑定办公技能时按目标格式自动挑选
    auto = office.office_guidance(db_session, yard.id, text="做个汇报", fmt="pptx")
    assert "PPT" in auto
    assert "演示" in auto


def test_bind_office_skills_to_office_agents_is_idempotent(db_session) -> None:
    yard = create_yard(db_session, name="办公技能单测小院")
    db_session.flush()
    office.ensure_office_skills(db_session, yard_id=yard.id, actor_id="u-office")
    agent = _agent(db_session, yard.id, "office_doc_assistant", "办公文档助手")
    other = _agent(
        db_session,
        yard.id,
        "irrigation_advisor",
        "灌溉顾问",
        description="负责水肥与灌溉建议",
        persona="你是灌溉顾问，负责水肥与灌溉建议。",
    )

    assert office.bind_office_skills_to_office_agents(db_session) == 1
    assert agent.bound_skills
    assert all(str(v).startswith("skill:") for v in agent.bound_skills)
    assert other.bound_skills == []
    # 幂等：第二次不再追加
    assert office.bind_office_skills_to_office_agents(db_session) == 0


def test_factory_office_framework_suggests_skills() -> None:
    brief = "帮我组建一个办公文档团队，负责写周报、做Excel台账和PPT汇报材料"
    items = heuristic_blueprint(brief)
    assert 3 <= len(items) <= 6
    codes = {code for item in items for code in item.get("suggested_skills") or []}
    assert {"office_excel_table", "office_word_report", "office_ppt_deck"} <= codes
    profile, picked = ff.compose_roles(brief)
    assert profile.matched[0] == "office_documents"
    assert [role.stage for _, role in picked][-1] == ff.STAGE_REVIEW
