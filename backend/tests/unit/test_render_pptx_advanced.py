"""进阶 PPT 渲染单测：真实版式、真实图表、东亚字体、结构质检。

所有产物都写到 pytest 的 tmp_path，不落项目目录。
"""

from __future__ import annotations

import io

from app.modules.docs import render_pptx
from app.modules.docs.deck_plan import parse_deck_plan, plan_from_doc_model
from app.modules.docs.deck_qa import qa_deck_plan
from app.modules.docs.model import parse_model
from pptx import Presentation
from pptx.enum.chart import XL_CHART_TYPE

EMU = 914400


def _full_deck(theme: str = "tech_blue"):
    return parse_deck_plan(
        {
            "theme": theme,
            "title": "智慧灌溉示范区建设汇报",
            "subtitle": "2026 年度进展与下一步",
            "audience": "合作社理事会",
            "storyline": "现状—问题—方案—行动",
            "slides": [
                {
                    "type": "cover",
                    "title": "智慧灌溉示范区建设汇报",
                    "key_message": "2026 年度进展",
                },
                {
                    "type": "agenda",
                    "title": "目录",
                    "bullets": ["建设现状", "关键指标", "下一步行动"],
                },
                {"type": "section", "title": "建设现状", "key_message": "三条主线同步推进"},
                {
                    "type": "bullets",
                    "title": "建设现状",
                    "bullets": [
                        "完成 12 个大棚滴灌改造",
                        "铺设主干管网 3.6 公里",
                        "上线水肥一体机 8 台",
                        "培训农户 126 人次",
                    ],
                },
                {
                    "type": "kpi",
                    "title": "关键指标",
                    "kpis": [
                        {"value": "12", "unit": "个", "label": "改造大棚"},
                        {"value": "36", "unit": "%", "label": "节水率"},
                        {"value": "126", "unit": "人", "label": "培训人次"},
                    ],
                },
                {
                    "type": "chart",
                    "title": "月度用水量",
                    "chart": {
                        "type": "bar",
                        "categories": ["5月", "6月", "7月", "8月"],
                        "series": [{"name": "用水量", "values": [320, 280, 240, 210]}],
                        "unit": "吨",
                        "source": "灌溉台账",
                    },
                },
                {
                    "type": "table",
                    "title": "设备清单",
                    "table": {
                        "columns": ["设备", "数量", "状态"],
                        "rows": [["水肥一体机", "8", "在用"], ["电磁阀", "24", "在用"]],
                        "source": "设备台账",
                    },
                },
                {
                    "type": "timeline",
                    "title": "推进节奏",
                    "timeline": [
                        {"time": "Q1", "title": "勘察设计", "text": "完成测绘"},
                        {"time": "Q2", "title": "管网施工", "text": "主干贯通"},
                        {"time": "Q3", "title": "设备联调", "text": "水肥上线"},
                        {"time": "Q4", "title": "验收评估", "text": "节水复核"},
                    ],
                },
                {
                    "type": "process",
                    "title": "实施流程",
                    "process": [
                        {"step": "01", "title": "勘察", "text": "测绘取样"},
                        {"step": "02", "title": "设计", "text": "选型定案"},
                        {"step": "03", "title": "施工", "text": "分区推进"},
                        {"step": "04", "title": "验收", "text": "指标复核"},
                    ],
                },
                {
                    "type": "comparison",
                    "title": "改造前后",
                    "comparison": {
                        "left_title": "改造前",
                        "left_items": ["人工开阀", "亩均用水 420 吨"],
                        "right_title": "改造后",
                        "right_items": ["定时定量", "亩均用水 268 吨"],
                    },
                },
                {
                    "type": "quote",
                    "title": "核心判断",
                    "key_message": "节水不是少浇，而是把水浇在需要的地方。",
                    "source": "项目组",
                },
                {
                    "type": "image",
                    "title": "现场照片",
                    "image": {"slot": "田间管网", "prompt": "滴灌管网照片", "alt": "田间滴灌"},
                },
                {"type": "closing", "title": "谢谢", "key_message": "欢迎提问与交流"},
            ],
        }
    )


def _render(plan, path):
    data = render_pptx.to_pptx_plan(plan, path)
    return data, Presentation(io.BytesIO(data))


def _all_shape_names(prs):
    return [shape.name for slide in prs.slides for shape in slide.shapes]


def test_advanced_deck_structure(tmp_path) -> None:
    plan = _full_deck()
    target = tmp_path / "deck.pptx"
    data, prs = _render(plan, target)

    assert target.read_bytes() == data
    assert data.startswith(b"PK\x03\x04")
    assert round(prs.slide_width / EMU, 3) == 13.333
    assert round(prs.slide_height / EMU, 3) == 7.5
    assert len(prs.slides) == len(plan.slides)

    charts = [shape for slide in prs.slides for shape in slide.shapes if shape.has_chart]
    tables = [shape for slide in prs.slides for shape in slide.shapes if shape.has_table]
    assert len(charts) == 1 and len(tables) == 1
    assert charts[0].chart.chart_type == XL_CHART_TYPE.COLUMN_CLUSTERED
    assert len(tables[0].table.rows) == 3

    names = _all_shape_names(prs)
    assert "qa:chevron" in names  # 流程箭头
    assert "qa:dot" in names  # 时间轴节点
    assert "qa:image" in names  # 图片槽位


def test_every_slide_has_notes_and_page_numbers(tmp_path) -> None:
    plan = _full_deck()
    _, prs = _render(plan, tmp_path / "deck.pptx")
    for index, slide in enumerate(prs.slides, start=1):
        assert slide.has_notes_slide
        assert slide.notes_slide.notes_text_frame.text.strip()
        pagers = [shape for shape in slide.shapes if shape.name == "qa:pageno"]
        if index in (1, len(prs.slides)):
            assert not pagers
        else:
            assert pagers and pagers[0].text_frame.text == str(index)


def test_footer_keeps_source(tmp_path) -> None:
    plan = _full_deck()
    _, prs = _render(plan, tmp_path / "deck.pptx")
    footers = [
        shape.text_frame.text
        for slide in prs.slides
        for shape in slide.shapes
        if shape.name == "qa:footer" and "来源" in shape.text_frame.text
    ]
    assert any("来源：灌溉台账" in text for text in footers)
    assert any("来源：设备台账" in text for text in footers)


def test_east_asian_font_is_written_to_xml(tmp_path) -> None:
    plan = _full_deck()
    _, prs = _render(plan, tmp_path / "deck.pptx")
    title_xmls = [
        shape._element.xml
        for slide in prs.slides
        for shape in slide.shapes
        if shape.has_text_frame and "建设现状" in shape.text_frame.text
    ]
    assert title_xmls
    assert any("<a:ea" in xml and "思源黑体" in xml for xml in title_xmls)

    chart = next(
        shape.chart for slide in prs.slides for shape in slide.shapes if shape.has_chart
    )
    chart_xml = chart._chartSpace.xml
    assert "<a:ea" in chart_xml and "思源黑体" in chart_xml
    # 拉丁字体同时设置，避免中文回退时英文变方框
    assert 'typeface="Arial"' in title_xmls[0]


def test_no_shape_out_of_bounds(tmp_path) -> None:
    _, prs = _render(_full_deck(), tmp_path / "deck.pptx")
    width = prs.slide_width
    height = prs.slide_height
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.left is None:
                continue
            assert shape.left >= -1000
            assert shape.top >= -1000
            assert shape.left + shape.width <= width + 1000
            assert shape.top + shape.height <= height + 1000


def test_generated_deck_passes_structure_qa(tmp_path) -> None:
    plan = _full_deck()
    report = qa_deck_plan(plan)
    assert report.ok, [issue.model_dump() for issue in report.errors]
    assert report.score == 100
    _, prs = _render(plan, tmp_path / "deck.pptx")
    assert len(prs.slides) == 13


def test_chart_kinds_render(tmp_path) -> None:
    base = _full_deck().model_dump()
    cases = {
        "line": XL_CHART_TYPE.LINE_MARKERS,
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
    }
    for kind, expected in cases.items():
        payload = _full_deck().model_dump()
        for slide in payload["slides"]:
            if slide["type"] == "chart":
                slide["chart"]["type"] = kind
                slide["chart"]["series"] = [
                    {"name": "用水量", "values": [320, 280, 240, 210]},
                    {"name": "计划", "values": [330, 300, 270, 240]},
                ]
        plan = parse_deck_plan(payload)
        _, prs = _render(plan, tmp_path / f"{kind}.pptx")
        chart = next(s.chart for sl in prs.slides for s in sl.shapes if s.has_chart)
        assert chart.chart_type == expected

    pie = parse_deck_plan(base)
    payload = pie.model_dump()
    for slide in payload["slides"]:
        if slide["type"] == "chart":
            slide["chart"]["type"] = "pie"
            slide["chart"]["series"] = [{"name": "用水量", "values": [320, 280, 240, 210]}]
    _, prs = _render(parse_deck_plan(payload), tmp_path / "pie.pptx")
    chart = next(s.chart for sl in prs.slides for s in sl.shapes if s.has_chart)
    assert chart.chart_type == XL_CHART_TYPE.PIE
    assert chart.plots[0].has_data_labels


def test_theme_switch_changes_cover_color(tmp_path) -> None:
    _, red = _render(_full_deck("gov_red"), tmp_path / "red.pptx")
    _, blue = _render(_full_deck("tech_blue"), tmp_path / "blue.pptx")

    def cover_fill(prs):
        shape = prs.slides[0].shapes[0]
        return str(shape.fill.fore_color.rgb)

    assert cover_fill(red) == "9E1B22"
    assert cover_fill(blue) == "0F4C81"


def test_long_title_stays_within_two_lines(tmp_path) -> None:
    plan = _full_deck()
    long_title = "关于推进智慧灌溉示范区建设并落实下一期预算安排的总体情况说明与工作部署"
    plan.slides[3].title = long_title[:60]
    report = qa_deck_plan(plan)
    assert report.ok, [issue.model_dump() for issue in report.errors]
    _, prs = _render(plan, tmp_path / "long.pptx")
    assert len(prs.slides) == len(plan.slides)


def test_legacy_to_pptx_accepts_doc_model() -> None:
    model = parse_model(
        {
            "title": "设备巡检报告",
            "subject": "一号泵站",
            "author": "巡检班组",
            "blocks": [
                {"type": "heading", "level": 1, "text": "巡检概况"},
                {"type": "paragraph", "text": "本次巡检覆盖一号泵站全部机组。"},
                {"type": "bullets", "items": ["油位正常", "皮带张紧度正常"]},
                {"type": "table", "header": ["项目", "结论"], "rows": [["油位", "正常"]]},
            ],
        }
    )
    data = render_pptx.to_pptx(model)
    prs = Presentation(io.BytesIO(data))
    assert round(prs.slide_width / EMU, 3) == 13.333
    assert len(prs.slides) >= 5
    texts = "\n".join(
        shape.text_frame.text
        for slide in prs.slides
        for shape in slide.shapes
        if shape.has_text_frame
    )
    assert "设备巡检报告" in texts and "油位正常" in texts


def test_to_pptx_plan_accepts_doc_model_via_plan_from_doc_model(tmp_path) -> None:
    model = parse_model(
        {
            "title": "降级链路",
            "blocks": [
                {"type": "heading", "text": "一"},
                {"type": "bullets", "items": ["a", "b", "c"]},
                {"type": "heading", "text": "二"},
                {"type": "paragraph", "text": "结论先行。"},
            ],
        }
    )
    plan = plan_from_doc_model(model)
    data = render_pptx.to_pptx_plan(plan, tmp_path / "plan.pptx")
    assert Presentation(io.BytesIO(data)).slides[0].shapes[0].name == "qa:deco"


def test_legacy_to_pptx_renders_short_doc_model() -> None:
    """旧接口必须出文件：素材不足 5 页也渲染短稿，而不是 500。"""
    model = parse_model(
        {"title": "一句话", "blocks": [{"type": "paragraph", "text": "只有一句话。"}]}
    )
    prs = Presentation(io.BytesIO(render_pptx.to_pptx(model)))
    assert round(prs.slide_width / EMU, 3) == 13.333
    assert len(prs.slides) >= 2
    texts = [
        shape.text_frame.text
        for slide in prs.slides
        for shape in slide.shapes
        if shape.has_text_frame
    ]
    assert any("一句话" in text for text in texts)
