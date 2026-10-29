"""内置增长技能种子（营销 6 + 活动 4 + 海报 2）的结构、契约与铺设校验。"""

from __future__ import annotations

import pytest
from app.modules.evolution.models import Skill
from app.modules.growth.skills import (
    _SPEC_EXTRAS,
    GROUP_CONTENT,
    GROUP_EVENT,
    GROUP_MARKETING,
    builtin_items,
    ensure_growth_skills,
    seed_growth_skills,
)
from app.modules.market.agri_skills import ensure_builtin_skills
from app.modules.market.installer import get_item
from app.modules.market.models import MarketItem
from app.modules.market.skill_template import SkillSpec
from app.modules.yards.repository import create_yard

EXPECTED_CODES = {
    "growth_brand_story",
    "growth_selling_points",
    "growth_pricing",
    "growth_channel_plan",
    "growth_content_calendar",
    "growth_repurchase_plan",
    "growth_event_plan",
    "growth_invitation",
    "growth_live_script",
    "growth_event_review",
    "growth_poster_prompt",
    "growth_poster_render",
}
MARKETING_CODES = {
    "growth_brand_story",
    "growth_selling_points",
    "growth_pricing",
    "growth_channel_plan",
    "growth_content_calendar",
    "growth_repurchase_plan",
}
CONTENT_CODES = {"growth_poster_prompt", "growth_poster_render"}
EVENT_CODES = EXPECTED_CODES - MARKETING_CODES - CONTENT_CODES
REGISTERED_TOOL_CODES = {
    "knowledge.search",
    "sensor.query",
    "web.search",
    "workorder.dispatch",
    "compliance.check_copy",
    "image.generate",
}
# 各技能对外承诺的边界：提示词必须写明不编造 / 不承诺 / 不自动发送之一
HONESTY_MARKERS = ("编造", "虚构", "不承诺", "不估算", "不得", "严禁", "不推断", "不自动发送")


@pytest.fixture(scope="module")
def items() -> list[dict]:
    return builtin_items()


def test_builtin_items_shape(items: list[dict]) -> None:
    assert len(items) == 12
    assert {item["code"] for item in items} == EXPECTED_CODES
    assert len({item["item_key"] for item in items}) == 12
    for item in items:
        assert item["item_type"] == "skill"
        assert item["source"] == "builtin"
        assert item["version"] == "1.0.0"
        assert item["status"] == "available"
        assert item["verified"] is True
        assert item["item_key"] == f"builtin:skill:{item['code']}"
        assert item["license"] == "内部原创"
        assert item["author"] == "科技小院平台"
        assert item["source_url"] is None

        definition = item["definition_json"]
        for field in (
            "name",
            "description",
            "prompt",
            "steps",
            "code",
            "version",
            "summary",
            "when_to_use",
            "inputs",
            "eval_cases",
            "guardrails",
        ):
            assert definition.get(field), (item["code"], field)
        # 既有链路：steps 必须仍是字符串列表，prompt 仍是字符串
        assert isinstance(definition["prompt"], str)
        assert isinstance(definition["steps"], list)
        assert all(isinstance(step, str) and step.strip() for step in definition["steps"])
        assert 3 <= len(definition["steps"]) <= 6
        assert any(marker in definition["prompt"] for marker in HONESTY_MARKERS), item["code"]


def test_groups_and_categories(items: list[dict]) -> None:
    by_code = {item["code"]: item["definition_json"] for item in items}
    marketing = {code for code, d in by_code.items() if d["skill_group"] == GROUP_MARKETING}
    event = {code for code, d in by_code.items() if d["skill_group"] == GROUP_EVENT}
    content = {code for code, d in by_code.items() if d["skill_group"] == GROUP_CONTENT}
    assert marketing == MARKETING_CODES
    assert event == EVENT_CODES
    assert content == CONTENT_CODES
    for code in MARKETING_CODES:
        assert by_code[code]["category"] == "marketing", code
    for code in EVENT_CODES:
        assert by_code[code]["category"] == "event", code
    for code in CONTENT_CODES:
        assert by_code[code]["category"] == "content", code
    assert {item["metadata_json"]["skill_group"] for item in items} == {
        GROUP_MARKETING,
        GROUP_EVENT,
        GROUP_CONTENT,
    }


def test_every_builtin_has_extras(items: list[dict]) -> None:
    assert {item["code"] for item in items} == set(_SPEC_EXTRAS)


def test_specs_satisfy_skill_spec_contract(items: list[dict]) -> None:
    """按 SkillSpec 的字段约束重验（步骤转成 prompt 步骤以适配严格模型）。"""
    spec_fields = set(SkillSpec.model_fields)
    for item in items:
        d = item["definition_json"]
        raw = {key: value for key, value in d.items() if key in spec_fields}
        raw["steps"] = [{"kind": "prompt", "text": step} for step in d["steps"]]
        spec = SkillSpec.model_validate(raw)
        assert spec.code == item["code"]
        assert spec.category in {"marketing", "event", "content"}
        assert spec.license == "内部原创"
        assert len(spec.eval_cases) >= 2


def test_allowed_tools_are_registered(items: list[dict]) -> None:
    for item in items:
        tools = item["definition_json"]["allowed_tools"]
        assert set(tools) <= REGISTERED_TOOL_CODES, (item["code"], tools)
        assert len(set(tools)) == len(tools)


def test_guardrails_match_risk(items: list[dict]) -> None:
    expected_risk = {
        "growth_brand_story": "medium",
        "growth_selling_points": "medium",
        "growth_pricing": "medium",
        "growth_channel_plan": "low",
        "growth_content_calendar": "low",
        "growth_repurchase_plan": "medium",
        "growth_event_plan": "low",
        "growth_invitation": "medium",
        "growth_live_script": "high",
        "growth_event_review": "low",
        "growth_poster_prompt": "low",
        "growth_poster_render": "medium",
    }
    for item in items:
        d = item["definition_json"]
        g = d["guardrails"]
        assert item["risk_level"] == expected_risk[item["code"]]
        assert g["risk_level"] == expected_risk[item["code"]]
        assert 1 <= g["max_steps"] <= 10
        # 平台原创方法论没有外部来源，因此不得声明「有依据生成」
        assert d["sources"] == []
        assert g["require_citation"] is False


def test_inputs_shape(items: list[dict]) -> None:
    for item in items:
        inputs = item["definition_json"]["inputs"]
        assert 1 <= len(inputs) <= 20
        keys = [i["key"] for i in inputs]
        assert len(set(keys)) == len(keys), f"{item['code']} 输入 key 重复"
        for i in inputs:
            assert i["type"] in {"string", "text", "number", "integer", "boolean", "enum"}
            assert isinstance(i["required"], bool)
            assert i["label"].strip()


def test_live_script_is_forced_through_compliance(items: list[dict]) -> None:
    d = {item["code"]: item["definition_json"] for item in items}["growth_live_script"]
    assert "compliance.check_copy" in d["allowed_tools"]
    assert "合规体检" in d["prompt"]
    assert any("合规" in step for step in d["steps"])


def test_repurchase_and_invitation_need_human_send(items: list[dict]) -> None:
    by_code = {item["code"]: item["definition_json"] for item in items}
    assert "不自动群发" in by_code["growth_repurchase_plan"]["prompt"]
    assert "由人确认" in by_code["growth_repurchase_plan"]["prompt"]
    assert "不得虚构领导出席" in by_code["growth_invitation"]["prompt"]


def test_seed_growth_skills_is_idempotent(db_session) -> None:
    assert seed_growth_skills(db_session) == 12
    assert db_session.query(MarketItem).count() == 12

    assert seed_growth_skills(db_session) == 0
    assert db_session.query(MarketItem).count() == 12

    row = (
        db_session.query(MarketItem)
        .filter(MarketItem.item_key == "builtin:skill:growth_pricing")
        .one()
    )
    assert row.verified is True
    assert row.definition_json["name"] == "定价与毛利"


def test_seed_keeps_installed_status(db_session) -> None:
    seed_growth_skills(db_session)
    row = db_session.query(MarketItem).first()
    row.status = "installed"
    db_session.flush()

    assert seed_growth_skills(db_session) == 0
    db_session.refresh(row)
    assert row.status == "installed"


def test_get_item_seeds_growth_item_on_demand(db_session) -> None:
    assert get_item(db_session, "builtin:skill:growth_event_review") is not None
    assert db_session.query(MarketItem).count() >= 12


def test_ensure_growth_skills_provisions_published_skills(db_session) -> None:
    yard = create_yard(db_session, name="增长技能铺设小院")
    db_session.flush()

    assert ensure_growth_skills(db_session, yard_id=yard.id, actor_id="u-growth") == 12
    skills = db_session.query(Skill).filter(Skill.yard_id == yard.id).all()
    assert {s.skill_code for s in skills} == EXPECTED_CODES
    assert {s.status for s in skills} == {"published"}
    assert {s.created_from for s in skills} == {"market_builtin"}
    assert all(s.approved_by == "u-growth" for s in skills)
    assert all(s.definition_json.get("market_item_key") for s in skills)

    assert ensure_growth_skills(db_session, yard_id=yard.id, actor_id="u-growth") == 0


def test_agri_and_growth_skills_coexist(db_session) -> None:
    yard = create_yard(db_session, name="农技与增长共存小院")
    db_session.flush()

    assert ensure_builtin_skills(db_session, yard_id=yard.id, actor_id="u-both") == 12
    assert ensure_growth_skills(db_session, yard_id=yard.id, actor_id="u-both") == 12
    assert db_session.query(Skill).filter(Skill.yard_id == yard.id).count() == 24
