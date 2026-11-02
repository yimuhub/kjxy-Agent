"""分批生成 PPT：页数必须按用户要求兑现。

背景：一次性让模型吐 30 页 JSON 会撞输出上限被截断（``Expecting ',' delimiter``），
整份降级成模板后用户只拿到 4-8 页。这里锁住三条行为：
1. 从需求里正确解析页数；
2. 分批链路产出与要求一致的页数；
3. 某一批写失败时用大纲骨架补齐，而不是退回只有几页的模板。
"""

from __future__ import annotations

import asyncio
import json

import pytest
from app.core.deps import YardContext
from app.modules.chat import document as document_service
from app.modules.market import office_skills, skill_router
from app.modules.yards.repository import create_yard


def _ctx(yard_id: str) -> YardContext:
    return YardContext(yard_id=yard_id, user_id="u-deck", roles=[], request_id="r-deck")


def _outline(count: int) -> dict:
    slides = [{"title": "封面：科技小院介绍", "type": "cover", "key_message": "介绍小院"}]
    for index in range(count - 2):
        slides.append(
            {
                "title": f"第{index + 1}个结论：小院在第{index + 1}方面的进展",
                "type": "bullets",
                "key_message": f"第{index + 1}页核心信息。第二条支撑信息。",
            }
        )
    slides.append({"title": "谢谢", "type": "closing", "key_message": ""})
    return {
        "theme": "agri_green",
        "title": "科技小院介绍",
        "subtitle": "2026",
        "audience": "评审",
        "storyline": "背景—做法—成效",
        "outline": slides,
    }


class _Resp:
    def __init__(self, content: str) -> None:
        self.content = content
        self.input_tokens = 10
        self.output_tokens = 20
        self.model = "stub"
        self.finish_reason = "stop"


def _make_fake(count: int, *, break_batches: bool = False):
    calls = {"outline": 0, "slides": 0}

    async def _fake(db, request):
        system = request.messages[0].content or ""
        if "只出大纲" in system:
            calls["outline"] += 1
            return _Resp(json.dumps(_outline(count), ensure_ascii=False))
        calls["slides"] += 1
        if break_batches:
            return _Resp("模型这次只回了半截 JSON {")
        payload = json.loads(request.messages[1].content)
        slides = []
        for item in payload["本批大纲"]:
            if item["type"] in ("cover", "closing"):
                slides.append({"type": item["type"], "title": item["title"]})
            else:
                slides.append(
                    {
                        "type": "bullets",
                        "title": item["title"],
                        "key_message": item["key_message"],
                        # 体量按真实要求写（每页 ≥3 条、每条 20-60 字），否则会触发过薄重写
                        "bullets": [
                            f"要点{i}：小院累计服务农户 {100 + i} 户，覆盖 {1000 + i} 亩耕地，"
                            "形成可复制的技术服务模式"
                            for i in (1, 2, 3)
                        ],
                        "notes": "讲解要点：先说结论，再补数据，最后过渡到下一页。",
                    }
                )
        return _Resp(json.dumps({"slides": slides}, ensure_ascii=False))

    return _fake, calls


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("创作一个不少于 30 页、精美的科技小院介绍 PPT", 30),
        ("给我 20 页的汇报 PPT", 20),
        ("做一份 10-15 页的演示文稿", 15),
        ("帮我制作一个二十页的ppt", 20),
        ("帮我做个PPT", None),
        ("就做 3 页吧", None),
        ("介绍下小院", None),
    ],
)
def test_requested_slides_parsing(text, expected):
    assert document_service.requested_slides(text) == expected


def test_batched_generation_meets_requested_pages(db_session, monkeypatch):
    yard = create_yard(db_session, name="分批 PPT 小院")
    db_session.flush()
    fake, calls = _make_fake(12)
    monkeypatch.setattr(document_service, "complete_with_logging", fake)

    plan, used, usage = asyncio.run(
        document_service._generate_deck_plan(
            db_session,
            _ctx(yard.id),
            request_text="帮我做一份 12 页的科技小院介绍 PPT",
            context="小院 2024 年成立，服务 36 户农户。",
        )
    )
    assert len(plan.slides) == 12
    assert used.startswith("deck_builder_batched")
    assert usage["target_slides"] == 12
    # 12 页 → 大纲 1 次 + 正文 3 批（每批 5 页）
    assert calls["outline"] == 1
    assert calls["slides"] == 3
    assert plan.slides[0].type == "cover"
    assert plan.slides[-1].type == "closing"


def test_batch_failure_still_produces_full_deck(db_session, monkeypatch):
    yard = create_yard(db_session, name="分批兜底小院")
    db_session.flush()
    fake, _calls = _make_fake(12, break_batches=True)
    monkeypatch.setattr(document_service, "complete_with_logging", fake)

    plan, used, _usage = asyncio.run(
        document_service._generate_deck_plan(
            db_session,
            _ctx(yard.id),
            request_text="帮我做一份 12 页的科技小院介绍 PPT",
            context="",
        )
    )
    # 正文全挂也不许退回 4-8 页模板：用大纲骨架把页数撑住
    assert len(plan.slides) == 12
    assert used in ("deck_builder_batched_partial", "deck_builder_skeleton")


def test_skill_router_recommends_ppt_family(db_session):
    yard = create_yard(db_session, name="技能检索小院")
    db_session.flush()
    office_skills.ensure_office_skills(db_session, yard_id=yard.id, actor_id="u-skill")
    db_session.flush()

    skills = skill_router.resolve(
        db_session,
        yard.id,
        text="创作一个不少于 30 页、精美的科技小院介绍 PPT",
        fmt="pptx",
        capabilities=["presentation"],
        limit=6,
    )
    codes = [skill.code for skill in skills]
    assert "office_ppt_deck" in codes
    # 30 页 + 精美，需要叙事/设计等方法论一起上场，而不是只给一条通用技能
    assert {"office_ppt_storyline", "office_ppt_design"}.issubset(set(codes))
    assert skill_router.guidance(skills)


def test_skill_router_reaches_other_domains(db_session):
    yard = create_yard(db_session, name="跨域技能小院")
    db_session.flush()
    office_skills.ensure_office_skills(db_session, yard_id=yard.id, actor_id="u-skill")
    db_session.flush()

    skills = skill_router.resolve(
        db_session,
        yard.id,
        text="帮我写一份市级专项申报书，重点写联农带农机制",
        fmt="docx",
        capabilities=["document"],
        limit=3,
    )
    codes = [skill.code for skill in skills]
    assert "office_word_report" in codes


def test_batched_generation_30_pages(db_session, monkeypatch):
    """用户点名 30 页时按 30 页产出（每批 5 页共 6 批）。"""
    yard = create_yard(db_session, name="30 页小院")
    db_session.flush()
    fake, calls = _make_fake(30)
    monkeypatch.setattr(document_service, "complete_with_logging", fake)

    plan, used, usage = asyncio.run(
        document_service._generate_deck_plan(
            db_session,
            _ctx(yard.id),
            request_text="创作一个不少于 30 页、精美的科技小院介绍 PPT",
            context="",
        )
    )
    assert len(plan.slides) == 30
    assert usage["target_slides"] == 30
    assert calls["slides"] == 6


def test_truncated_outline_is_salvaged(monkeypatch, db_session):
    """大纲输出被截断时，已写完整的条目要救回来，而不是退成模板。"""
    yard = create_yard(db_session, name="截断小院")
    db_session.flush()
    full = json.dumps(_outline(12), ensure_ascii=False)
    truncated = full[: int(len(full) * 0.75)]

    async def _fake(db, request):
        system = request.messages[0].content or ""
        if "只出大纲" in system:
            return _Resp(truncated)
        payload = json.loads(request.messages[1].content)
        slides = [
            {
                "type": "bullets",
                "title": item["title"],
                "bullets": ["要点一", "要点二"],
                "notes": "备注",
            }
            for item in payload["本批大纲"]
        ]
        return _Resp(json.dumps({"slides": slides}, ensure_ascii=False))

    monkeypatch.setattr(document_service, "complete_with_logging", _fake)
    plan, used, _usage = asyncio.run(
        document_service._generate_deck_plan(
            db_session,
            _ctx(yard.id),
            request_text="帮我做一份 12 页的科技小院介绍 PPT",
            context="",
        )
    )
    # 截断后仍能捞出大部分条目，页数接近目标而不是掉到个位数
    assert len(plan.slides) >= 9
    assert used.startswith("deck_builder")
