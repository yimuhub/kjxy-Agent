"""Skill 调用埋点与自动升级（v3.21）：只产草稿、门槛与冷却、失败不落脏数据。"""

from __future__ import annotations

import json

import pytest
from app.core.errors import AppError
from app.modules.evolution import auto_upgrade
from app.modules.evolution.models import Skill, SkillUpgradeRun
from app.modules.yards.repository import create_yard


def _skill(db, yard_id: str, code: str = "rice.fertilizer") -> Skill:
    skill = Skill(
        yard_id=yard_id,
        skill_code=code,
        version="1.2.3",
        definition_json={
            "title": "水稻施肥建议",
            "description": "按生育期给出施肥量",
            "prompt": "你是水稻施肥顾问。",
            "steps": ["确认生育期", "给出用量"],
        },
        status="published",
        created_from="manual",
        visibility="yard",
    )
    db.add(skill)
    db.flush()
    return skill


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content
        self.input_tokens = 10
        self.output_tokens = 20


def _fake_llm(monkeypatch, payload: dict | Exception):
    async def fake_complete(db, request):
        if isinstance(payload, Exception):
            raise payload
        return _Resp(json.dumps(payload, ensure_ascii=False))

    monkeypatch.setattr(auto_upgrade, "complete_with_logging", fake_complete)


def _enable(db, yard_id: str, code: str, *, min_calls: int = 10) -> None:
    row = auto_upgrade.ensure_setting(db, yard_id, code, "u1")
    row.enabled = True
    row.min_calls = min_calls
    row.cooldown_hours = 1
    db.flush()


def test_record_usage_and_stats(db_session):
    yard = create_yard(db_session, name="埋点小院")
    auto_upgrade.record_usage(
        db_session, yard_id=yard.id, codes=["a", "a", "b"], source="plan", session_id="s1"
    )
    auto_upgrade.record_usage(
        db_session, yard_id=yard.id, codes=["a"], source="declarative", session_id="s2"
    )
    stats = auto_upgrade.usage_stats(db_session, yard.id, "a")
    assert stats["calls"] == 2
    assert stats["sessions"] == 2
    mapped = auto_upgrade.usage_map(db_session, yard.id)
    assert mapped["a"]["calls"] == 2
    assert mapped["b"]["calls"] == 1


def test_disabled_skill_cannot_upgrade(db_session, monkeypatch):
    yard = create_yard(db_session, name="未开启小院")
    _skill(db_session, yard.id)
    _fake_llm(monkeypatch, {"needs_update": False})
    with pytest.raises(AppError) as e:
        auto_upgrade.upgrade_skill(
            db_session,
            yard_id=yard.id,
            code="rice.fertilizer",
            trigger="manual",
            actor_id="u1",
            request_id="r",
            manual=True,
        )
    assert e.value.code == "SKILL_AUTO_UPGRADE_DISABLED"


def test_insufficient_evidence_is_rejected(db_session, monkeypatch):
    yard = create_yard(db_session, name="证据不足小院")
    _skill(db_session, yard.id)
    _enable(db_session, yard.id, "rice.fertilizer")
    _fake_llm(monkeypatch, {"needs_update": True})
    with pytest.raises(AppError) as e:
        auto_upgrade.upgrade_skill(
            db_session,
            yard_id=yard.id,
            code="rice.fertilizer",
            trigger="schedule",
            actor_id=None,
            request_id="r",
        )
    assert e.value.code == "SKILL_UPGRADE_NO_EVIDENCE"


def test_upgrade_creates_pending_draft_and_bumps_version(db_session, monkeypatch):
    yard = create_yard(db_session, name="升级小院")
    _skill(db_session, yard.id)
    _enable(db_session, yard.id, "rice.fertilizer", min_calls=2)
    auto_upgrade.record_usage(
        db_session, yard_id=yard.id, codes=["rice.fertilizer"], source="plan", session_id="s1"
    )
    auto_upgrade.record_usage(
        db_session, yard_id=yard.id, codes=["rice.fertilizer"], source="plan", session_id="s2"
    )
    _fake_llm(
        monkeypatch,
        {
            "needs_update": True,
            "reason": "补充分蘖期用量区间",
            "description": "按生育期给出施肥量与禁忌",
            "prompt": "你是水稻施肥顾问，先问生育期，再给出亩用量区间。",
            "steps": ["确认生育期", "给出亩用量区间", "提示禁忌"],
        },
    )
    run = auto_upgrade.upgrade_skill(
        db_session,
        yard_id=yard.id,
        code="rice.fertilizer",
        trigger="manual",
        actor_id="u1",
        request_id="r",
        manual=True,
    )
    assert run.status == "success"
    draft = db_session.get(Skill, run.draft_skill_id)
    assert draft is not None
    assert draft.status == "pending_review"
    assert draft.version == "1.2.4"
    assert draft.created_from == "auto:upgrade"
    assert draft.definition_json["upgrade"]["calls"] == 2
    setting = auto_upgrade.get_setting(db_session, yard.id, "rice.fertilizer")
    assert setting is not None and setting.last_status == "success"


def test_model_says_no_update_records_skipped(db_session, monkeypatch):
    yard = create_yard(db_session, name="无需更新小院")
    _skill(db_session, yard.id)
    _enable(db_session, yard.id, "rice.fertilizer", min_calls=1)
    auto_upgrade.record_usage(db_session, yard_id=yard.id, codes=["rice.fertilizer"], source="plan")
    _fake_llm(monkeypatch, {"needs_update": False, "reason": "现有定义已够用"})
    run = auto_upgrade.upgrade_skill(
        db_session,
        yard_id=yard.id,
        code="rice.fertilizer",
        trigger="manual",
        actor_id="u1",
        request_id="r",
        manual=True,
    )
    assert run.status == "skipped"
    assert run.draft_skill_id is None
    assert db_session.query(Skill).count() == 1


def test_llm_failure_records_failed_without_draft(db_session, monkeypatch):
    yard = create_yard(db_session, name="模型失败小院")
    _skill(db_session, yard.id)
    _enable(db_session, yard.id, "rice.fertilizer", min_calls=1)
    auto_upgrade.record_usage(db_session, yard_id=yard.id, codes=["rice.fertilizer"], source="plan")
    _fake_llm(monkeypatch, RuntimeError("boom"))
    run = auto_upgrade.upgrade_skill(
        db_session,
        yard_id=yard.id,
        code="rice.fertilizer",
        trigger="manual",
        actor_id="u1",
        request_id="r",
        manual=True,
    )
    assert run.status == "failed"
    assert run.draft_skill_id is None
    assert db_session.query(Skill).count() == 1
    assert db_session.query(SkillUpgradeRun).count() == 1


def test_cooldown_blocks_scheduled_run(db_session, monkeypatch):
    yard = create_yard(db_session, name="冷却小院")
    _skill(db_session, yard.id)
    _enable(db_session, yard.id, "rice.fertilizer", min_calls=1)
    auto_upgrade.record_usage(db_session, yard_id=yard.id, codes=["rice.fertilizer"], source="plan")
    _fake_llm(monkeypatch, {"needs_update": False})
    setting = auto_upgrade.get_setting(db_session, yard.id, "rice.fertilizer")
    auto_upgrade.upgrade_skill(
        db_session,
        yard_id=yard.id,
        code="rice.fertilizer",
        trigger="manual",
        actor_id="u1",
        request_id="r",
        manual=True,
    )
    assert setting is not None
    with pytest.raises(AppError) as e:
        auto_upgrade.upgrade_skill(
            db_session,
            yard_id=yard.id,
            code="rice.fertilizer",
            trigger="schedule",
            actor_id=None,
            request_id="r",
        )
    assert e.value.code == "SKILL_UPGRADE_COOLDOWN"


def test_status_labels_are_chinese():
    assert auto_upgrade.status_label("pending_review") == "待审核"
    assert auto_upgrade.status_label("published") == "已发布"
    assert auto_upgrade.status_label("unknown") == "unknown"
