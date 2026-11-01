"""DeckPlan 严格校验与 DocModel 降级单测。"""

from __future__ import annotations

import json

import pytest
from app.core.errors import AppError
from app.modules.docs.deck_plan import (
    MAX_SLIDES,
    DeckChart,
    DeckPlan,
    DeckSlide,
    doc_model_from_plan,
    guess_theme,
    parse_deck_plan,
    plan_from_doc_model,
)
from app.modules.docs.model import parse_model


def _deck(**overrides) -> dict:
    payload = {
        "theme": "tech_blue",
        "title": "智慧灌溉示范区建设汇报",
        "subtitle": "2026 年度进展",
        "audience": "合作社理事会",
        "storyline": "现状—问题—方案—行动",
        "slides": [
            {"type": "cover", "title": "智慧灌溉示范区建设汇报"},
            {"type": "agenda", "title": "目录", "bullets": ["建设现状", "下一步"]},
            {"type": "section", "title": "建设现状", "key_message": "三条主线同步推进"},
            {
                "type": "bullets",
                "title": "建设现状",
                "bullets": ["完成 12 个大棚改造", "培训农户 126 人次"],
            },
            {"type": "closing", "title": "谢谢", "key_message": "欢迎提问"},
        ],
    }
    payload.update(overrides)
    return payload


def test_parse_valid_plan_from_dict_and_text() -> None:
    plan = parse_deck_plan(_deck())
    assert isinstance(plan, DeckPlan)
    assert plan.theme == "tech_blue"
    assert len(plan.slides) == 5
    fenced = "```json\n" + json.dumps(_deck(), ensure_ascii=False) + "\n```"
    assert parse_deck_plan(fenced).title == "智慧灌溉示范区建设汇报"


def test_rejects_unknown_field() -> None:
    payload = _deck()
    payload["slides"][0]["sparkle"] = True
    with pytest.raises(AppError) as e:
        parse_deck_plan(payload)
    assert e.value.code == "DECK_PLAN_INVALID"
    assert e.value.http_status == 422
    assert e.value.details["errors"][0]["loc"].startswith("slides.0")


def test_rejects_bad_json() -> None:
    with pytest.raises(AppError) as e:
        parse_deck_plan("这不是 JSON")
    assert e.value.code == "DECK_PLAN_INVALID"
    assert e.value.details["errors"][0]["loc"] == "json"


def test_rejects_too_many_slides() -> None:
    slides = [{"type": "cover", "title": f"封面{i}"} for i in range(MAX_SLIDES + 1)]
    with pytest.raises(AppError) as e:
        parse_deck_plan(_deck(slides=slides))
    assert e.value.code == "DECK_PLAN_INVALID"
    assert "30" in str(e.value.details["errors"][0]["message"])


def test_rejects_too_few_slides() -> None:
    with pytest.raises(AppError):
        parse_deck_plan(_deck(slides=[{"type": "cover", "title": "只有一页"}]))


def test_chart_series_must_match_categories() -> None:
    slides = _deck()["slides"]
    slides[3] = {
        "type": "chart",
        "title": "月度用水量",
        "chart": {
            "type": "bar",
            "categories": ["5月", "6月", "7月"],
            "series": [{"name": "用水量", "values": [320, 280]}],
            "unit": "吨",
        },
    }
    with pytest.raises(AppError) as e:
        parse_deck_plan(_deck(slides=slides))
    assert "长度" in str(e.value.details["errors"][0]["message"]) or "一致" in str(
        e.value.details["errors"][0]["message"]
    )


def test_pie_allows_only_one_series() -> None:
    with pytest.raises(ValueError):
        DeckChart(
            type="pie",
            categories=["a", "b"],
            series=[{"name": "x", "values": [1, 2]}, {"name": "y", "values": [3, 4]}],
        )


def test_table_limits() -> None:
    slides = _deck()["slides"]
    slides[3] = {
        "type": "table",
        "title": "七列表",
        "table": {"columns": [f"c{i}" for i in range(7)], "rows": [["x"] * 7]},
    }
    with pytest.raises(AppError):
        parse_deck_plan(_deck(slides=slides))

    slides[3] = {
        "type": "table",
        "title": "九行表",
        "table": {"columns": ["a", "b"], "rows": [["1", "2"]] * 9},
    }
    with pytest.raises(AppError):
        parse_deck_plan(_deck(slides=slides))


def test_table_rows_must_align_with_columns() -> None:
    with pytest.raises(AppError):
        parse_deck_plan(
            _deck(
                slides=[
                    *_deck()["slides"][:3],
                    {
                        "type": "table",
                        "title": "错行",
                        "table": {"columns": ["a", "b"], "rows": [["only-one"]]},
                    },
                    {"type": "closing", "title": "谢谢"},
                ]
            )
        )


def test_bullets_and_columns_conflict() -> None:
    slides = _deck()["slides"]
    slides[3] = {
        "type": "bullets",
        "title": "冲突页",
        "bullets": ["要点"],
        "columns": [{"title": "左", "items": ["a"]}],
    }
    with pytest.raises(AppError) as e:
        parse_deck_plan(_deck(slides=slides))
    assert "一种内容版式" in str(e.value.details["errors"][0]["message"])


def test_title_must_not_be_empty() -> None:
    with pytest.raises(AppError):
        parse_deck_plan(_deck(slides=[{"type": "cover", "title": "  "}, *_deck()["slides"][1:]]))
    with pytest.raises(AppError):
        parse_deck_plan(
            _deck(slides=[{"type": "cover", "title": "长" * 61}, *_deck()["slides"][1:]])
        )


def test_statement_needs_core_message() -> None:
    slides = _deck()["slides"]
    slides[2] = {"type": "statement", "title": "空观点"}
    with pytest.raises(AppError):
        parse_deck_plan(_deck(slides=slides))


def test_first_slide_must_be_cover() -> None:
    slides = [{"type": "section", "title": "开场"}]
    slides += [{"type": "cover", "title": "封面"}] * 4
    with pytest.raises(AppError):
        parse_deck_plan(_deck(slides=slides))


RAW_DOC = {
    "title": "设备巡检报告",
    "subject": "一号泵站",
    "author": "巡检班组",
    "blocks": [
        {"type": "heading", "level": 1, "text": "巡检概况"},
        {"type": "paragraph", "text": "本次巡检覆盖一号泵站全部机组。油位与皮带张紧度均正常。"},
        {"type": "bullets", "items": ["油位正常", "皮带张紧度正常", "二号机组轴承温度偏高"]},
        {"type": "heading", "level": 2, "text": "待办事项"},
        {"type": "table", "header": ["项目", "结论"], "rows": [["油位", "正常"], ["轴承", "复查"]]},
    ],
}


def test_doc_model_degrade_is_deterministic() -> None:
    model = parse_model(RAW_DOC)
    plan = plan_from_doc_model(model)
    assert plan.title == "设备巡检报告"
    assert plan.slides[0].type == "cover"
    assert plan.slides[-1].type == "closing"
    assert plan.slides[1].type == "agenda"
    types = [slide.type for slide in plan.slides]
    assert "section" in types and "bullets" in types and "table" in types
    assert plan_from_doc_model(model).model_dump() == plan.model_dump()


def test_doc_model_degrade_splits_long_bullets() -> None:
    model = parse_model(
        {
            "title": "长列表",
            "blocks": [
                {"type": "heading", "text": "要点"},
                {"type": "bullets", "items": [f"第{i}条要点" for i in range(12)]},
            ],
        }
    )
    plan = plan_from_doc_model(model)
    bullet_slides = [slide for slide in plan.slides if slide.type == "bullets"]
    assert len(bullet_slides) >= 3
    assert all(len(slide.bullets) <= 5 for slide in bullet_slides)
    assert any(slide.title.endswith("（续）") for slide in bullet_slides)


def test_empty_doc_model_raises() -> None:
    model = parse_model({"title": "空文档", "blocks": [{"type": "pagebreak"}]})
    with pytest.raises(AppError) as e:
        plan_from_doc_model(model)
    assert e.value.code == "DECK_PLAN_EMPTY"
    assert e.value.http_status == 422


def test_too_thin_doc_model_raises() -> None:
    model = parse_model(
        {"title": "一句话", "blocks": [{"type": "paragraph", "text": "只有一句话。"}]}
    )
    with pytest.raises(AppError) as e:
        plan_from_doc_model(model)
    assert e.value.code == "DECK_PLAN_TOO_THIN"


def test_doc_model_from_plan_keeps_text() -> None:
    plan = parse_deck_plan(_deck())
    model = doc_model_from_plan(plan)
    assert model.title == plan.title
    text = " ".join(block.text + " ".join(block.items) for block in model.blocks)
    assert "培训农户 126 人次" in text
    assert "目录" in text


def test_guess_theme_prefers_scene() -> None:
    assert guess_theme("党建年度工作汇报") == "gov_red"
    assert guess_theme("合作社灌溉项目") == "agri_green"
    assert guess_theme("随便聊聊") == "tech_blue"


def test_slide_type_literal_covers_all_builders() -> None:
    from app.modules.docs.render_pptx import _BUILDERS

    for slide_type in DeckSlide.model_fields["type"].annotation.__args__:  # type: ignore[union-attr]
        assert slide_type in _BUILDERS
