"""内置农业技能种子：数量、字段、来源标注与幂等 upsert。"""

from __future__ import annotations

from app.modules.evolution.models import Skill
from app.modules.market.agri_skills import (
    builtin_items,
    ensure_builtin_skills,
    seed_builtin,
)
from app.modules.market.models import MarketItem
from app.modules.yards.repository import create_yard

EXPECTED_CODES = {
    "agri_soil_health",
    "agri_irrigation",
    "agri_pest_diagnosis",
    "agri_pesticide_safety",
    "agri_harvest_storage",
    "agri_machinery",
    "agri_field_log",
    "agri_farm_calendar",
    "agri_market_price",
    "agri_policy_search",
    "agri_crop_nutrition",
    "agri_climate_risk",
}


def test_builtin_items_shape():
    items = builtin_items()
    assert len(items) == 12
    assert {i["code"] for i in items} == EXPECTED_CODES
    assert len({i["item_key"] for i in items}) == 12
    for item in items:
        assert item["item_type"] == "skill"
        assert item["source"] == "builtin"
        assert item["version"] == "1.0.0"
        assert item["risk_level"] == "low"
        assert item["status"] == "available"
        assert item["verified"] is True
        assert item["item_key"] == f"builtin:skill:{item['code']}"
        assert item["license"] == "MIT"
        assert "farmfriend-labs/agent-skills-farming" in item["source_url"]

        definition = item["definition_json"]
        for field in (
            "name",
            "description",
            "prompt",
            "steps",
            "source_url",
            "license",
            "attribution",
        ):
            assert definition.get(field), (item["code"], field)
        assert definition["license"] == "MIT"
        assert "MIT" in definition["attribution"]
        assert 2 <= definition["prompt"].count("。") <= 5
        assert isinstance(definition["steps"], list) and 3 <= len(definition["steps"]) <= 6
        assert all(isinstance(step, str) and step.strip() for step in definition["steps"])
        # 不编造数据：提示词必须要求缺数据时说明或补测
        prompt = definition["prompt"]
        assert any(
            word in prompt
            for word in (
                "不编造",
                "不得编造",
                "不臆造",
                "不确诊",
                "只给出",
                "不虚构",
                "不得虚构",
                "不得臆造",
                "不替用户填写",
            )
        )


def test_seed_builtin_is_idempotent(db_session):
    first = seed_builtin(db_session)
    assert first == 12
    assert db_session.query(MarketItem).count() == 12

    second = seed_builtin(db_session)
    assert second == 0
    assert db_session.query(MarketItem).count() == 12

    row = (
        db_session.query(MarketItem)
        .filter(MarketItem.item_key == "builtin:skill:agri_soil_health")
        .one()
    )
    assert row.verified is True
    assert row.definition_json["name"] == "土壤健康管理"


def test_seed_builtin_keeps_installed_status(db_session):
    seed_builtin(db_session)
    row = db_session.query(MarketItem).first()
    row.status = "installed"
    db_session.flush()

    assert seed_builtin(db_session) == 0
    db_session.refresh(row)
    assert row.status == "installed"


def test_ensure_builtin_skills_provisions_published_skills_idempotently(db_session):
    yard = create_yard(db_session, name="内置技能铺设小院")
    db_session.flush()

    assert ensure_builtin_skills(db_session, yard_id=yard.id, actor_id="u-provision") == 12
    skills = (
        db_session.query(Skill).filter(Skill.yard_id == yard.id).order_by(Skill.skill_code).all()
    )
    assert {s.skill_code for s in skills} == EXPECTED_CODES
    assert {s.status for s in skills} == {"published"}
    assert {s.created_from for s in skills} == {"market_builtin"}
    assert all(s.approved_by == "u-provision" for s in skills)
    assert all(s.definition_json.get("market_item_key") for s in skills)

    # 幂等：再次铺设不新增
    assert ensure_builtin_skills(db_session, yard_id=yard.id, actor_id="u-provision") == 0
