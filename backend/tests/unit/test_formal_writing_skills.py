"""内置正式写作技能（28 项）的种子、契约、SOP 注入与自动绑定单测（3.35）。"""

from __future__ import annotations

from collections import Counter

import pytest
from app.modules.agents.models import YardAgent
from app.modules.evolution.models import Skill
from app.modules.evolution.prepublish import static_checks
from app.modules.market import formal_writing_skills as formal
from app.modules.market.models import MarketItem
from app.modules.market.skill_template import SkillSpec, validate_skill_spec
from app.modules.yards.repository import create_yard

EXPECTED_CODES = {
    "gov_notice",
    "gov_request",
    "gov_report",
    "gov_letter",
    "gov_minutes",
    "gov_work_plan",
    "gov_summary",
    "gov_reply",
    "gov_speech",
    "gov_briefing",
    "grant_project_application",
    "grant_tech_program",
    "grant_subsidy",
    "grant_honor",
    "grant_midterm",
    "grant_acceptance",
    "grant_budget",
    "tender_document",
    "bid_document",
    "bid_response_matrix",
    "bid_technical",
    "bid_commercial",
    "bid_risk_review",
    "bid_clarification",
    "formal_longform",
    "exec_brief",
    "policy_interpretation",
    "meeting_pack",
}

EXPECTED_GROUP_COUNTS = {
    "formal_official": 10,
    "grant_application": 7,
    "tender_bid": 7,
    "formal_writing": 4,
}

MEDIUM_GROUPS = {"grant_application", "tender_bid"}


@pytest.fixture(scope="module")
def items() -> list[dict]:
    return formal.builtin_items()


def _agent(
    db,
    yard_id: str,
    code: str,
    name: str,
    *,
    description: str | None = None,
    persona: str = "你是正式写作助手。",
    status: str = "published",
) -> YardAgent:
    agent = YardAgent(
        yard_id=yard_id,
        agent_code=code,
        name=name,
        description=description if description is not None else f"{name}负责正式写作",
        emoji="📄",
        persona_prompt=persona,
        task_prompt_template="{{question}}",
        allowed_tools=["knowledge.search"],
        bound_skills=[],
        knowledge_scopes=["yard"],
        status=status,
        visibility="public",
        version="1.0.0",
        created_from="manual",
    )
    db.add(agent)
    db.flush()
    return agent


def test_codes_are_unique_and_grouped(items: list[dict]) -> None:
    assert len(items) == 28
    codes = [item["code"] for item in items]
    assert len(set(codes)) == 28
    assert set(codes) == EXPECTED_CODES
    assert tuple(codes) == formal.FORMAL_SKILL_CODES
    counts = Counter(item["definition_json"]["skill_group"] for item in items)
    assert dict(counts) == EXPECTED_GROUP_COUNTS
    assert set(formal.SKILL_GROUPS) == set(EXPECTED_GROUP_COUNTS)


def test_definitions_pass_skill_spec_and_static_checks(items: list[dict]) -> None:
    for item in items:
        assert item["item_key"] == f"builtin:skill:{item['code']}"
        assert item["item_type"] == "skill"
        definition = item["definition_json"]
        assert definition["category"] == "office"
        assert definition["skill_group"] in EXPECTED_GROUP_COUNTS
        assert definition["summary"] == definition["description"]
        assert definition["allowed_tools"] == ["knowledge.search"]
        assert definition["knowledge_scopes"] == ["yard"]
        assert definition["guardrails"]["require_citation"] is False
        assert definition["license"] == "平台原创"
        assert definition["author"] == "科技小院平台"
        assert definition["sources"] == []
        assert len(definition["tags"]) >= 3
        assert len(definition["eval_cases"]) >= 2
        assert len(definition["steps"]) >= 4
        assert definition["when_to_use"]
        # 写作纪律：待核实清单/附件清单/风险提示都写进提示词
        assert "待核实清单" in definition["prompt"]
        assert "附件清单" in definition["prompt"]
        assert "风险提示" in definition["prompt"]
        assert "严禁编造" in definition["prompt"]
        # 契约校验：SkillSpec 结构 + 平台跨字段校验
        payload = formal.spec_payload(definition)
        spec = SkillSpec.model_validate(payload)
        assert spec.code == item["code"]
        validate_skill_spec(payload, available_tools={"knowledge.search": True})
        # 上架静态检查：不允许出现 error
        errors = [
            check
            for check in static_checks(definition)
            if check["severity"] == "error" and not check["passed"]
        ]
        assert errors == []
        # 风险口径：申报/招投标 medium，其余 low
        expected_risk = "medium" if definition["skill_group"] in MEDIUM_GROUPS else "low"
        assert definition["guardrails"]["risk_level"] == expected_risk
        assert item["risk_level"] == expected_risk
        if expected_risk == "medium":
            assert "人工核稿" in definition["prompt"]
            assert formal.doc_style_for_code(item["code"]) in {"grant", "tender"}


def test_seed_and_ensure_are_idempotent(db_session) -> None:
    assert formal.seed_formal_writing_skills(db_session) == 28
    assert formal.seed_formal_writing_skills(db_session) == 0
    yard = create_yard(db_session, name="正式写作技能单测小院")
    db_session.flush()
    assert (
        formal.ensure_formal_writing_skills(
            db_session, yard_id=yard.id, actor_id="u-formal", request_id="test"
        )
        == 28
    )
    assert (
        formal.ensure_formal_writing_skills(
            db_session, yard_id=yard.id, actor_id="u-formal", request_id="test"
        )
        == 0
    )
    ids = formal.formal_skill_ids(db_session, yard.id)
    assert set(ids) == EXPECTED_CODES
    assert db_session.query(Skill).filter(Skill.yard_id == yard.id).count() == 28
    market_rows = db_session.query(MarketItem).filter(
        MarketItem.item_key.like("builtin:skill:gov%")
    )
    assert market_rows.count() == 10
    assert (
        db_session.query(MarketItem)
        .filter(MarketItem.item_key == "builtin:skill:grant_project_application")
        .one()
        .risk_level
        == "medium"
    )


def test_formal_skill_ids_ignore_third_party_rows(db_session) -> None:
    yard = create_yard(db_session, name="同名技能注入单测小院")
    db_session.flush()
    db_session.add(
        Skill(
            yard_id=yard.id,
            skill_code="gov_notice",
            version="1.0.0",
            definition_json={"category": "office", "name": "第三方通知技能"},
            status="published",
            created_from="user_generated",
            approved_by="u-x",
        )
    )
    db_session.flush()
    assert formal.formal_skill_ids(db_session, yard.id) == {}


def test_guidance_only_uses_builtin_formal_skills(db_session) -> None:
    yard = create_yard(db_session, name="正式写作 SOP 单测小院")
    db_session.flush()
    formal.ensure_formal_writing_skills(db_session, yard_id=yard.id, actor_id="u-formal")
    ids = formal.formal_skill_ids(db_session, yard.id)

    bound = formal.formal_guidance(
        db_session,
        yard.id,
        "帮我写个材料",
        bound_skills=[f"skill:{ids['gov_request']}"],
    )
    assert "请示写作" in bound
    assert "待核实清单" in bound
    assert "办公技能规范" not in bound

    auto = formal.formal_guidance(db_session, yard.id, "写一份请示，请上级批经费")
    assert "请示写作" in auto
    assert "doc_style=official_cn" in auto

    miss = formal.formal_guidance(db_session, yard.id, "今天天气不错")
    assert miss == ""


def test_bind_formal_skills_is_idempotent(db_session) -> None:
    yard = create_yard(db_session, name="正式写作绑定单测小院")
    db_session.flush()
    formal.ensure_formal_writing_skills(db_session, yard_id=yard.id, actor_id="u-formal")
    agent = _agent(db_session, yard.id, "gov_writer", "公文写作助手")
    draft = _agent(db_session, yard.id, "gov_draft", "投标草稿助手", status="draft")
    other = _agent(
        db_session,
        yard.id,
        "irrigation_advisor",
        "灌溉顾问",
        description="负责水肥与灌溉建议",
        persona="你是灌溉顾问，负责水肥与灌溉建议。",
    )

    assert formal.bind_formal_skills_to_agents(db_session) == 1
    assert agent.bound_skills
    assert all(str(value).startswith("skill:") for value in agent.bound_skills)
    # 只补绑已发布 Agent：草稿与非写作助手都不动
    assert draft.bound_skills == []
    assert other.bound_skills == []
    bound_before = list(agent.bound_skills)
    assert formal.bind_formal_skills_to_agents(db_session) == 0
    assert agent.bound_skills == bound_before


def test_matching_and_doc_style_mapping() -> None:
    assert formal.match_formal_skill_codes("写一份项目申报书")[0] == "grant_project_application"
    assert "bid_document" in formal.match_formal_skill_codes("做一份投标文件")
    assert "gov_minutes" in formal.match_formal_skill_codes("整理会议纪要")
    assert formal.match_formal_skill_codes("今天天气不错") == []
    assert formal.is_formal_request("帮我写个申报书")
    assert not formal.is_formal_request("水泵压力不够")
    assert formal.doc_style_for_code("gov_notice") == "official_cn"
    assert formal.doc_style_for_code("grant_budget") == "grant"
    assert formal.doc_style_for_code("bid_technical") == "tender"
    assert formal.doc_style_for_code("exec_brief") == "brief"
    assert formal.doc_style_for_code("unknown_code") == "general"
