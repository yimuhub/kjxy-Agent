"""演示质检单测：能抓出问题、能确定性修复、视觉跳过不冒充通过。"""

from __future__ import annotations

from pathlib import Path

from app.modules.docs import deck_qa
from app.modules.docs.deck_plan import (
    DeckChart,
    DeckChartSeries,
    DeckPlan,
    DeckSlide,
    parse_deck_plan,
)
from app.modules.docs.deck_qa import (
    inspect_presentation,
    qa_and_repair,
    qa_deck_plan,
    repair_deck_plan,
    visual_qa,
    visual_qa_available,
)
from pptx import Presentation
from pptx.util import Inches, Pt


def _deck() -> DeckPlan:
    return parse_deck_plan(
        {
            "theme": "tech_blue",
            "title": "智慧灌溉示范区建设汇报",
            "subtitle": "2026 年度进展",
            "audience": "合作社理事会",
            "storyline": "现状—问题—方案—行动",
            "slides": [
                {"type": "cover", "title": "智慧灌溉示范区建设汇报"},
                {"type": "agenda", "title": "目录", "bullets": ["建设现状", "关键指标", "下一步"]},
                {"type": "section", "title": "建设现状"},
                {
                    "type": "bullets",
                    "title": "建设现状",
                    "bullets": ["完成 12 个大棚滴灌改造", "培训农户 126 人次"],
                },
                {
                    "type": "chart",
                    "title": "月度用水量",
                    "chart": {
                        "type": "bar",
                        "categories": ["5月", "6月", "7月"],
                        "series": [{"name": "用水量", "values": [320, 280, 240]}],
                        "unit": "吨",
                    },
                },
                {"type": "closing", "title": "谢谢", "key_message": "欢迎提问"},
            ],
        }
    )


def _blank_presentation() -> Presentation:
    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    return prs


def _textbox(slide, left, top, width, height, text, size=18):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    frame = box.text_frame
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = text
    run.font.size = Pt(size)
    return box


def test_clean_deck_passes() -> None:
    report = qa_deck_plan(_deck())
    assert report.ok
    assert report.score == 100
    assert report.slide_count == 6
    assert report.visual["status"] == "not_requested"
    assert "通过结构质检" in report.summary()


def test_inspect_detects_out_of_bounds() -> None:
    prs = _blank_presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _textbox(slide, 13.0, 1.0, 2.0, 1.0, "越界的文本框")
    codes = {issue.code for issue in inspect_presentation(prs)}
    assert "SHAPE_OUT_OF_BOUNDS" in codes


def test_inspect_detects_overlap() -> None:
    prs = _blank_presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _textbox(slide, 1.0, 1.0, 6.0, 1.2, "第一块文字")
    _textbox(slide, 1.1, 1.1, 6.0, 1.2, "第二块文字压在同一位置")
    codes = {issue.code for issue in inspect_presentation(prs)}
    assert "TEXT_OVERLAP" in codes


def test_inspect_detects_small_font() -> None:
    prs = _blank_presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _textbox(slide, 1.0, 1.0, 4.0, 0.6, "9 号字", size=9)
    issues = inspect_presentation(prs)
    small = [issue for issue in issues if issue.code == "FONT_TOO_SMALL"]
    assert small and small[0].slide == 1


def test_detects_empty_chart_and_repairs_to_statement() -> None:
    plan = _deck()
    broken = plan.model_copy(deep=True)
    broken.slides[4].chart = DeckChart.model_construct(
        type="bar", categories=[], series=[], unit="", source=""
    )
    report = qa_deck_plan(broken)
    assert not report.ok
    assert any(issue.code == "CHART_EMPTY" and issue.slide == 5 for issue in report.issues)

    fixed = repair_deck_plan(broken, report)
    assert fixed.slides[4].type == "statement"
    assert not qa_deck_plan(fixed).errors


def test_detects_chart_series_length_mismatch() -> None:
    plan = _deck()
    broken = plan.model_copy(deep=True)
    broken.slides[4].chart = DeckChart.model_construct(
        type="bar",
        categories=["5月", "6月", "7月"],
        series=[DeckChartSeries.model_construct(name="用水量", values=[320])],
        unit="吨",
        source="",
    )
    report = qa_deck_plan(broken)
    assert any(issue.code == "CHART_SERIES_LENGTH" for issue in report.issues)


def test_detects_and_repairs_duplicate_titles() -> None:
    plan = _deck()
    broken = plan.model_copy(deep=True)
    broken.slides[4].title = "建设现状"
    report = qa_deck_plan(broken)
    duplicates = [issue for issue in report.issues if issue.code == "TITLE_DUPLICATE"]
    assert duplicates and duplicates[0].slide == 5

    fixed = repair_deck_plan(broken, report)
    assert fixed.slides[4].title == "建设现状（续）"
    assert not [issue for issue in qa_deck_plan(fixed).issues if issue.code == "TITLE_DUPLICATE"]


def test_repairs_long_title_and_too_many_bullets() -> None:
    plan = _deck()
    broken = plan.model_copy(deep=True)
    broken.slides[3].title = "这是一个明显超过六十个字的标题" * 8
    broken.slides[3].bullets = [f"第 {i} 条要点" * 12 for i in range(12)]
    report = qa_deck_plan(broken)
    codes = {issue.code for issue in report.issues}
    assert {"TITLE_TOO_LONG", "TOO_MANY_BULLETS"} <= codes

    fixed = repair_deck_plan(broken, report)
    assert len(fixed.slides[3].title) <= 60
    assert len(fixed.slides[3].bullets) <= 6
    assert DeckPlan.model_validate(fixed.model_dump())


def test_repairs_table_density() -> None:
    plan = _deck()
    broken = plan.model_copy(deep=True)
    table = broken.slides[3]
    table.bullets = []
    table.type = "table"
    from app.modules.docs.deck_plan import DeckTable

    table.table = DeckTable.model_construct(
        columns=["a", "b"],
        rows=[[f"r{i}-a", f"r{i}-b"] for i in range(12)],
        source="",
    )
    report = qa_deck_plan(broken)
    assert any(issue.code == "TABLE_DENSE" for issue in report.issues)
    fixed = repair_deck_plan(broken, report)
    assert len(fixed.slides[3].table.rows) <= 8


def test_qa_and_repair_loop_converges() -> None:
    plan = _deck()
    broken = plan.model_copy(deep=True)
    broken.slides[3].title = "超长标题" * 20
    broken.slides[3].bullets = ["要点" * 30] * 10
    fixed, report, rounds = qa_and_repair(broken, max_rounds=2)
    assert rounds <= 2
    assert report.ok
    assert fixed.slides[3].title != broken.slides[3].title


def test_repair_keeps_plan_valid_when_nothing_to_trim() -> None:
    plan = _deck()
    report = qa_deck_plan(plan)
    assert repair_deck_plan(plan, report).model_dump() == plan.model_dump()


def test_visual_qa_skipped_without_soffice(monkeypatch) -> None:
    monkeypatch.setattr(deck_qa.shutil, "which", lambda name: None)
    assert visual_qa_available() is False
    result = visual_qa(_deck())
    assert result["status"] == "skipped"
    assert "soffice" in result["reason"]
    assert result["images"] == []
    report = qa_deck_plan(_deck(), visual=True)
    assert report.visual["status"] == "skipped"
    # 跳到视觉不算通过，报告必须说清楚
    assert "未执行" in report.summary()


def test_visual_qa_rendered_is_not_a_pass(monkeypatch) -> None:
    monkeypatch.setattr(deck_qa.shutil, "which", lambda name: "/usr/bin/soffice")
    monkeypatch.setattr(deck_qa, "render_pngs", lambda *a, **k: [Path("/tmp/slide-1.png")])
    result = visual_qa(_deck())
    assert result["status"] == "rendered"
    assert "未做模型级视觉评分" in result["reason"]
    assert result["images"] == ["/tmp/slide-1.png"]


def test_slide_type_duplicate_section_allowed() -> None:
    plan = parse_deck_plan(
        {
            "title": "章节同名",
            "slides": [
                {"type": "cover", "title": "章节同名"},
                {"type": "agenda", "title": "目录", "bullets": ["一", "二"]},
                {"type": "section", "title": "现状"},
                {"type": "bullets", "title": "现状", "bullets": ["a"]},
                {"type": "closing", "title": "谢谢"},
            ],
        }
    )
    codes = {issue.code for issue in qa_deck_plan(plan).issues}
    assert "TITLE_DUPLICATE" not in codes


def test_empty_bullets_slide_is_shrunk() -> None:
    slide = DeckSlide(type="bullets", title="只有一条", bullets=["唯一要点"])
    assert deck_qa._shrink(slide) is False
