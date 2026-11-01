"""能力边界判定：能做的一律不许降级，只有现实办不到才允许。"""

from __future__ import annotations

import pytest
from app.modules.commander import feasibility, planner


@pytest.mark.parametrize(
    ("text", "status"),
    [
        ("帮我做一份春耕动员的PPT", "doable"),
        ("帮我做一份给果树施肥的PPT", "doable"),
        ("分析一下今年产量趋势并出Excel", "doable"),
        ("帮我生成一张海报", "doable"),
        ("帮我做个短视频", "doable"),
        ("查一下附近有没有卖苹果的", "doable"),
        ("你去把香蕉吃了", "blocked"),
        ("帮我吃掉它", "blocked"),
        ("帮我浇一下水", "blocked"),
        ("给我买10个苹果回来", "partial"),
    ],
)
def test_assess_status(text, status):
    assert feasibility.assess(text).status == status


def test_consultation_is_not_physical():
    """「吃了这个药要注意什么」是咨询，不是线下动作。"""
    assert feasibility.assess("吃了这个药要注意什么").status == "doable"


def test_explicit_format_wins_over_physical_verb():
    """要文件时正文里的线下动词不应把目标判成办不到。"""
    check = feasibility.assess("帮我做一份怎么给果树施肥的PPT")
    assert check.doable
    assert "presentation" in check.capabilities


def test_skill_codes_expand_from_capabilities():
    codes = feasibility.skill_codes_for(["presentation"])
    assert "office_ppt_deck" in codes
    assert "office_ppt_design" in codes


def test_capability_plan_for_pptx_has_two_steps():
    plan = planner.capability_plan(
        "帮我做一份销售提案 PPT", fmt="pptx", capabilities=["presentation"]
    )
    assert plan["fallback"] is False
    assert plan["assumed"] is True
    assert plan["deliverable"]["format"] == "pptx"
    assert [task["title"] for task in plan["subtasks"]] == ["梳理交付内容", "生成PPT交付物"]
    assert plan["subtasks"][1]["depends_on"] == [1]
    assert "doc.generate" in plan["subtasks"][1]["instruction"]


def test_blocked_plan_asks_for_alternative_not_execution():
    check = feasibility.assess("你去把香蕉吃了")
    assert check.blocked
    assert check.guidance
