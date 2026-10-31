"""正式写作工厂框架：角色齐备、关键需求优先整套正式写作角色。"""

from __future__ import annotations

from app.modules.agents import factory_frameworks as frames
from app.modules.market import formal_writing_skills as formal


def test_formal_framework_roles_and_skills() -> None:
    framework = next(f for f in frames.FRAMEWORKS if f.code == "formal_writing")
    assert framework.label == "正式写作"
    codes = {role.code for role in framework.roles}
    assert {"official_writer", "grant_specialist", "tender_specialist", "formal_writer"} <= codes
    all_skills = {code for role in framework.roles for code in role.skills}
    assert "gov_notice" in all_skills
    assert "grant_project_application" in all_skills
    assert "bid_document" in all_skills
    assert all(code in formal.FORMAL_SKILL_CODES for code in all_skills)


def test_formal_request_prefers_formal_roles() -> None:
    _profile, roles = frames.compose_roles("帮我写一份项目申报书，申请专项资金")
    framework_codes = [code for code, _role in roles]
    assert "formal_writing" in framework_codes
    role_codes = {role.code for _code, role in roles}
    assert "grant_specialist" in role_codes
    assert any(role.stage == frames.STAGE_REVIEW for _code, role in roles)


def test_plain_office_request_does_not_force_formal_roles() -> None:
    _profile, roles = frames.compose_roles("做一份设备巡检 Excel 台账")
    role_codes = {role.code for _code, role in roles}
    assert "grant_specialist" not in role_codes
    assert "tender_specialist" not in role_codes
