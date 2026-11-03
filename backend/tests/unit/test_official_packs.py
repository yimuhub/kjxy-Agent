"""官方技能包（v3.56）单测：目录完整性、同步幂等、画像推荐、越权。"""

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.market import official_catalog, official_packs
from app.modules.yards.repository import create_yard
from app.modules.yards.settings_service import OFFICIAL_YARD_TYPES, update_setting


def test_catalog_integrity():
    """目录完整性：每包技能 code 唯一、必带 sources 与 prompt、类型在白名单内。"""
    all_codes: set[str] = set()
    for pack in official_catalog.OFFICIAL_PACKS:
        assert pack["key"] and pack["name"]
        assert set(pack["yard_types"]) <= set(official_catalog.YARD_TYPES)
        for spec in pack["skills"]:
            assert spec["code"] not in all_codes, f"重复 code: {spec['code']}"
            all_codes.add(spec["code"])
            assert spec["prompt"] and len(spec["prompt"]) >= 80
            assert spec["sources"], f"{spec['code']} 必须带公开来源"
            assert all(s["url"].startswith("http") for s in spec["sources"])
    assert len(all_codes) >= 15  # 首批八包 19 条
    # 白名单对齐（设置校验与目录共用口径）
    assert set(OFFICIAL_YARD_TYPES) == set(official_catalog.YARD_TYPES)


def test_pack_skill_items_shape():
    items = official_catalog.pack_skill_items("rice")
    assert len(items) == 3
    for item in items:
        assert item["item_key"].startswith("official:skill:")
        assert item["source"] == "builtin" and item["status"] == "available"
        assert item["definition_json"]["official_pack"] == "rice"
        assert item["definition_json"]["allowed_tools"] == ["knowledge.search"]


def test_packs_for_yard_types_recommends():
    out = official_catalog.packs_for_yard_types(["rice"])
    keys = [p["key"] for p in out]
    assert keys[0] == "common" and "rice" in keys and "tea" not in keys
    # 未知类型只回通用
    assert [p["key"] for p in official_catalog.packs_for_yard_types(["nope"])] == ["common"]


def test_sync_official_pack_idempotent(db_session):
    yard = create_yard(db_session, name="官方包小院")
    db_session.commit()
    ctx = YardContext(yard_id=yard.id, user_id="u1", roles=["yard_admin"], request_id="req")
    first = official_packs.sync_official_pack(db_session, ctx, "rice")
    assert first["created"] == 3 and first["skipped"] == 0
    second = official_packs.sync_official_pack(db_session, ctx, "rice")
    assert second["created"] == 0 and second["skipped"] == 3
    # 列表视图反映已启用
    view = official_packs.list_official_packs(db_session, yard.id, ["rice"])
    rice = next(p for p in view["packs"] if p["key"] == "rice")
    assert rice["installed_count"] == 3 and rice["recommended"] is True
    common = next(p for p in view["packs"] if p["key"] == "common")
    assert common["installed_count"] == 0 and common["recommended"] is True  # common 恒推荐


def test_sync_unknown_pack_404(db_session):
    yard = create_yard(db_session, name="未知包小院")
    db_session.commit()
    ctx = YardContext(yard_id=yard.id, user_id="u1", roles=["yard_admin"], request_id="req")
    with pytest.raises(AppError) as e:
        official_packs.sync_official_pack(db_session, ctx, "nope")
    assert e.value.code == "OFFICIAL_PACK_NOT_FOUND"


def test_yard_profile_setting_roundtrip(db_session):
    yard = create_yard(db_session, name="画像小院")
    db_session.commit()
    out = update_setting(
        db_session,
        yard_id=yard.id,
        key="yard_profile",
        value={"types": ["rice", "aquaculture", "rice"]},
        actor_id="u1",
        request_id="req",
    )
    assert out["types"] == ["rice", "aquaculture"]  # 去重保序
    with pytest.raises(AppError):
        update_setting(
            db_session,
            yard_id=yard.id,
            key="yard_profile",
            value={"types": ["nope"]},
            actor_id="u1",
            request_id="req",
        )
