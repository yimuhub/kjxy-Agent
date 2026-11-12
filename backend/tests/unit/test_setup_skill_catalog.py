"""向导技能目录与「按产业画像推荐」单测（审查 D-13）。

护栏重点：
1. **不再全选 35 个**——任何画像的推荐数都显著小于全量；
2. **PPT 专项永不自动预选**（D-13 原文点名的问题技能）；
3. **新技能必须登记推荐元数据**——否则会静默地「永不预选」，
   属于把问题藏起来而不是解决；
4. **「通用」不参与自动推荐**（``initialize`` 会补general，但不能连带
   把通用办公技能预选上）。
"""

from __future__ import annotations

from app.modules.setup import service, skill_catalog


def _catalog() -> list[dict]:
    return service.builtin_skill_catalog()


def test_catalog_has_no_duplicate_codes():
    codes = [item["code"] for item in _catalog()]
    assert len(codes) == len(set(codes))
    assert len(codes) == 35


def test_every_builtin_skill_has_recommendation_metadata():
    """新增内置技能若不登记推荐元数据，会静默变成「永不预选」——此处卡住。"""
    known = set(skill_catalog.SKILL_META)
    for item in _catalog():
        assert item["code"] in known, f"{item['code']} 未登记推荐元数据"


def test_catalog_items_expose_group_and_keywords():
    for item in _catalog():
        assert item["group"] in {"agri", "growth", "office"}
        assert item["pack"]
        assert isinstance(item["keywords"], list) and item["keywords"]
        assert isinstance(item["scenes"], list)


def test_no_profile_selects_nothing():
    payload = skill_catalog.build_payload(_catalog(), [])
    assert payload["recommended_count"] == 0
    assert payload["recommended_codes"] == []
    assert payload["profile_labels"] == []


def test_agri_profile_recommends_only_agriculture_skills():
    payload = skill_catalog.build_payload(_catalog(), ["rice"])
    recommended = set(payload["recommended_codes"])
    assert payload["recommended_count"] == 12
    assert all(code.startswith("agri_") for code in recommended)


def test_fruit_profile_adds_business_skills():
    payload = skill_catalog.build_payload(_catalog(), ["fruit"])
    recommended = set(payload["recommended_codes"])
    assert payload["recommended_count"] == 17
    assert len(recommended) < payload["total"]
    assert any(code.startswith("growth_") for code in recommended)
    assert not any(code.startswith("office_") for code in recommended)


def test_general_profile_does_not_pull_in_ppt_skills():
    payload = skill_catalog.build_payload(_catalog(), ["general"])
    recommended = set(payload["recommended_codes"])
    assert payload["recommended_count"] > 0
    assert not any(code.startswith("office_ppt_") for code in recommended)


def test_ppt_skills_are_never_auto_recommended_for_any_profile():
    catalog = _catalog()
    all_profiles = list(skill_catalog.AG_PROFILE_TYPES) + [skill_catalog.GENERAL_PROFILE]
    for profile in all_profiles:
        recommended = set(skill_catalog.recommend_codes(catalog, [profile]))
        assert not any(code.startswith("office_ppt_") for code in recommended), profile
    # 多个画像叠加也不能把 PPT 拉进预选
    both = set(skill_catalog.recommend_codes(catalog, all_profiles))
    assert not any(code.startswith("office_ppt_") for code in both)


def test_recommendation_is_capped():
    catalog = _catalog()
    all_profiles = list(skill_catalog.AG_PROFILE_TYPES) + [skill_catalog.GENERAL_PROFILE]
    codes = skill_catalog.recommend_codes(catalog, all_profiles)
    assert len(codes) <= skill_catalog.RECOMMEND_CAP


def test_unknown_skill_is_not_recommended():
    meta = skill_catalog.meta_for("totally_unknown_skill")
    assert meta["auto"] is False
    assert skill_catalog.recommend_codes([{"code": "totally_unknown_skill"}], ["fruit"]) == []


def test_build_payload_groups_sum_to_total():
    payload = skill_catalog.build_payload(_catalog(), ["fruit"])
    assert sum(g["total"] for g in payload["groups"]) == payload["total"]
    assert sum(g["recommended"] for g in payload["groups"]) == payload["recommended_count"]
    assert {g["key"] for g in payload["groups"]} == {"agri", "growth", "office"}


def test_normalize_types_dedupes_and_keeps_order():
    assert skill_catalog.normalize_types(["fruit", " fruit ", "", "rice"]) == ["fruit", "rice"]
    assert skill_catalog.normalize_types(None) == []
