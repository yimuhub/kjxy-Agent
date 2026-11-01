"""DeckPlan 形状修复单测：宽松修形状，但硬约束一条都不放松。"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.chat import document as document_service
from app.modules.docs.deck_plan import (
    MAX_SLIDES,
    parse_deck_plan,
    repair_deck_plan,
)


def _deck() -> dict:
    """一份最小合法计划；用例按需改坏某一页。"""
    return {
        "theme": "tech_blue",
        "title": "智慧灌溉示范区汇报",
        "slides": [
            {"type": "cover", "title": "智慧灌溉示范区汇报"},
            {"type": "agenda", "title": "目录", "bullets": ["建设现状", "下一步"]},
            {"type": "bullets", "title": "建设现状", "bullets": ["改造 12 个大棚"]},
            {
                "type": "timeline",
                "title": "推进节奏",
                "timeline": [{"time": "5 月", "title": "开工"}],
            },
            {"type": "closing", "title": "谢谢"},
        ],
    }


def test_null_string_and_object_shapes_are_repaired() -> None:
    raw = _deck()
    raw["slides"][0]["timeline"] = None
    raw["slides"][0]["process"] = None
    raw["slides"][1]["bullets"] = "建设现状、下一步"
    raw["slides"][3]["timeline"] = {"time": "5 月", "title": "开工"}

    repaired, fields = repair_deck_plan(raw)

    assert fields == [
        "slides.0.timeline",
        "slides.0.process",
        "slides.1.bullets",
        "slides.3.timeline",
    ]
    plan = parse_deck_plan(repaired)
    assert plan.slides[0].timeline == []
    assert plan.slides[0].process == []
    assert plan.slides[1].bullets == ["建设现状、下一步"]
    assert plan.slides[3].timeline[0].time == "5 月"
    # 入参不被原地修改，避免调用方缓存被悄悄改写
    assert raw["slides"][0]["timeline"] is None
    assert raw["slides"][1]["bullets"] == "建设现状、下一步"


def test_missing_optional_fields_need_no_repair() -> None:
    raw = _deck()
    repaired, fields = repair_deck_plan(raw)
    assert fields == []
    assert parse_deck_plan(repaired)


def test_numeric_text_fields_become_strings() -> None:
    raw = _deck()
    raw["title"] = 2026
    raw["slides"][2] = {
        "type": "kpi",
        "title": 2026,
        "kpis": [{"value": 36, "unit": "%", "label": 123}],
        "notes": 456,
    }

    repaired, fields = repair_deck_plan(raw)

    assert "title" in fields
    assert "slides.2.title" in fields
    assert "slides.2.kpis[0].value" in fields
    assert "slides.2.kpis[0].label" in fields
    assert "slides.2.notes" in fields
    plan = parse_deck_plan(repaired)
    assert plan.title == "2026"
    assert plan.slides[2].title == "2026"
    assert plan.slides[2].kpis[0].value == "36"
    assert plan.slides[2].kpis[0].label == "123"
    assert plan.slides[2].notes == "456"


def test_nested_chart_and_table_shapes_are_repaired() -> None:
    raw = _deck()
    raw["slides"][2] = {
        "type": "chart",
        "title": "月度用水量",
        "chart": {
            "type": "bar",
            "categories": "5 月",
            "series": {"name": "用水量", "values": "320"},
            "unit": 0,
            "source": None,
        },
    }
    raw["slides"][3] = {
        "type": "table",
        "title": "指标",
        "table": {"columns": "用水量", "rows": [[12]]},
    }

    repaired, fields = repair_deck_plan(raw)

    assert "slides.2.chart.categories" in fields
    assert "slides.2.chart.series" in fields
    assert "slides.2.chart.series[0].values" in fields
    assert "slides.2.chart.unit" in fields
    assert "slides.2.chart.source" in fields
    assert "slides.3.table.columns" in fields
    assert "slides.3.table.rows[0][0]" in fields
    plan = parse_deck_plan(repaired)
    chart = plan.slides[2].chart
    assert chart is not None and chart.categories == ["5 月"]
    assert chart.series[0].values == [320.0]
    table = plan.slides[3].table
    assert table is not None and table.columns == ["用水量"] and table.rows == [["12"]]


def test_repair_does_not_truncate_too_many_slides() -> None:
    raw = _deck()
    raw["slides"] = [{"type": "cover", "title": f"封面{i}"} for i in range(MAX_SLIDES + 1)]

    repaired, _fields = repair_deck_plan(raw)

    assert len(repaired["slides"]) == MAX_SLIDES + 1
    with pytest.raises(AppError) as e:
        parse_deck_plan(repaired)
    assert e.value.code == "DECK_PLAN_INVALID"
    assert "30" in str(e.value.details["errors"][0]["message"])


def test_repair_does_not_relax_slide_type_enum() -> None:
    raw = _deck()
    raw["slides"][2]["type"] = "bulletz"

    repaired, fields = repair_deck_plan(raw)

    assert fields == []
    assert repaired["slides"][2]["type"] == "bulletz"
    with pytest.raises(AppError) as e:
        parse_deck_plan(repaired)
    assert e.value.code == "DECK_PLAN_INVALID"


def test_repair_does_not_relax_required_title() -> None:
    raw = _deck()
    raw["slides"][0]["title"] = None
    repaired, _fields = repair_deck_plan(raw)
    with pytest.raises(AppError):
        parse_deck_plan(repaired)

    raw = _deck()
    raw["slides"][0]["title"] = "   "
    repaired, _fields = repair_deck_plan(raw)
    with pytest.raises(AppError):
        parse_deck_plan(repaired)


def test_repair_does_not_relax_one_layout_per_slide() -> None:
    raw = _deck()
    raw["slides"][2]["columns"] = [{"title": "左栏", "items": ["a"]}]

    repaired, fields = repair_deck_plan(raw)

    assert fields == []
    with pytest.raises(AppError) as e:
        parse_deck_plan(repaired)
    assert "一种内容版式" in str(e.value.details["errors"][0]["message"])


def test_repair_is_idempotent_on_valid_plan() -> None:
    raw = _deck()
    repaired, _fields = repair_deck_plan(raw)
    repaired_again, fields = repair_deck_plan(repaired)
    assert fields == []
    assert repaired_again == repaired
    assert parse_deck_plan(repaired_again).model_dump() == parse_deck_plan(raw).model_dump()


class _Resp:
    content = json.dumps(
        {
            "theme": "agri_green",
            "title": "智慧灌溉汇报",
            "slides": [
                {"type": "cover", "title": "智慧灌溉汇报", "timeline": None},
                {"type": "agenda", "title": "目录", "bullets": "现状、方案、行动"},
                {"type": "bullets", "title": "现状", "bullets": ["改造 12 个大棚"]},
                {
                    "type": "timeline",
                    "title": "推进节奏",
                    "timeline": {"time": "5 月", "title": "开工"},
                },
                {"type": "closing", "title": "谢谢"},
            ],
        },
        ensure_ascii=False,
    )
    input_tokens = 11
    output_tokens = 22
    model = "stub"


async def _fake_complete(db, request):
    return _Resp()


def test_malformed_plan_repaired_without_degrade(db_session, monkeypatch, caplog) -> None:
    """修复后能过严格校验：不再降级为模板（used == deck_builder）。"""
    monkeypatch.setattr(document_service, "complete_with_logging", _fake_complete)
    ctx = YardContext(yard_id="yard-deck", user_id="u-deck", roles=[], request_id="r-deck")
    with caplog.at_level(logging.INFO, logger="app.modules.chat.document"):
        plan, used, _usage = asyncio.run(
            document_service._generate_deck_plan(
                db_session,
                ctx,
                request_text="做个智慧灌溉汇报 PPT",
                context="",
            )
        )
    assert used == "deck_builder"
    assert plan.slides[0].timeline == []
    assert plan.slides[1].bullets == ["现状、方案、行动"]
    assert plan.slides[3].timeline[0].time == "5 月"
    # 日志只记录字段路径，不记录正文
    assert "slides.0.timeline" in caplog.text
    assert "智慧灌溉" not in caplog.text
