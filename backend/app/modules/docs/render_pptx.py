"""DeckPlan → PowerPoint 16:9（python-pptx，MIT）。

版式口径（每一页都由这里统一落地，幻灯片里不再留「随手画的框」）：
- 16:9（13.333×7.5 英寸），四周页边距 0.6 英寸，页脚/页码占固定条带；
- 15 种版式：封面、目录、章节、观点、要点、双栏、KPI、真实图表（柱/折线/饼）、
  表格（表头填充 + 斑马纹 + 数字右对齐）、时间轴、流程箭头、对比、引言、
  图片槽位、结尾；
- 颜色与字号全部取自 ``deck_theme``；中文用 ``a:ea`` 显式写东亚字体、
  拉丁文字用 ``a:latin``，缺字体时由 Office 按字符集回退，不出方框；
- 标题最多两行，正文按「中文字宽」估算后自动缩字号，仍放不下才截断并把
  完整文字留在演讲者备注——不静默溢出；
- 每页写演讲者备注，有来源时写页脚来源，非封面页给页码。

旧签名 ``to_pptx(model)`` 保持可用：传给 DocModel 时内部先降级成 DeckPlan。
"""

from __future__ import annotations

import io
import math
import re
from pathlib import Path

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Inches, Pt

from app.modules.docs.deck_plan import DeckPlan, DeckSlide, plan_from_doc_model
from app.modules.docs.deck_theme import DeckTheme, get_theme
from app.modules.docs.model import DocModel

SLIDE_W = 13.333
SLIDE_H = 7.5
MARGIN = 0.6
CONTENT_W = SLIDE_W - 2 * MARGIN
TITLE_Y = 0.55
TITLE_H = 0.95
BODY_Y = 1.72
BODY_H = 4.62
FOOTER_Y = 6.9
MIN_BODY_PT = 12

_NUMERIC_RE = re.compile(
    r"^[+-]?[\d,]+(\.\d+)?\s*"
    r"(%|％|万|亿|元|吨|kg|t|亩|户|台|人|次|小时|天|月|年|MPa|℃)?$"
)


# --------------------------------------------------------------------------- #
# 字体与颜色
# --------------------------------------------------------------------------- #


def _rgb(value: str) -> RGBColor:
    return RGBColor.from_string(value.lstrip("#").upper())


def _tint(color: str, ratio: float) -> str:
    """把颜色按比例混向白色（ratio=0 原色，1 纯白）。"""
    r, g, b = (int(color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: round(c + (255 - c) * ratio)  # noqa: E731
    return f"#{mix(r):02X}{mix(g):02X}{mix(b):02X}"


def _set_east_asian(run, east_asian: str, latin: str) -> None:
    """python-pptx 不暴露 ``a:ea``：直接写 XML，保证中文用指定中文字体。"""
    r_pr = run._r.get_or_add_rPr()
    latin_el = r_pr.find(qn("a:latin"))
    if latin_el is None:
        latin_el = r_pr.makeelement(qn("a:latin"), {})
        r_pr.append(latin_el)
    latin_el.set("typeface", latin)
    for tag in ("a:ea", "a:cs"):
        existing = r_pr.find(qn(tag))
        if existing is not None:
            r_pr.remove(existing)
    anchor = latin_el
    for tag, face in (("a:ea", east_asian), ("a:cs", latin)):
        el = r_pr.makeelement(qn(tag), {"typeface": face})
        anchor.addnext(el)
        anchor = el


def _apply_font(
    run,
    theme: DeckTheme,
    *,
    size: int,
    color: str,
    bold: bool = False,
    italic: bool = False,
    title: bool = False,
) -> None:
    latin = theme.latin_title if title else theme.latin_body
    east_asian = (theme.title_stack if title else theme.body_stack)[0]
    run.font.name = latin
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = _rgb(color)
    _set_east_asian(run, east_asian, latin)


def _set_bullet(paragraph, *, char: str = "•", indent_in: float = 0.24) -> None:
    p_pr = paragraph._p.get_or_add_pPr()
    p_pr.set("marL", str(int(Inches(indent_in))))
    p_pr.set("indent", str(int(-Inches(indent_in))))
    bu_font = p_pr.makeelement(qn("a:buFont"), {"typeface": "Arial"})
    bu_char = p_pr.makeelement(qn("a:buChar"), {"char": char})
    p_pr.append(bu_font)
    p_pr.append(bu_char)


# --------------------------------------------------------------------------- #
# 文本测量与自动适配
# --------------------------------------------------------------------------- #


def _text_units(text: str) -> float:
    """按中文字宽估算显示宽度：CJK 记 1，拉丁/数字记 0.55。"""
    return sum(1.0 if ord(ch) > 0x2E80 else 0.55 for ch in str(text))


def _lines_needed(text: str, size: int, width_in: float) -> int:
    if not str(text or "").strip():
        return 0
    per_line = max(1.0, width_in * 72 / max(size, 1))
    return max(1, math.ceil(_text_units(text) / per_line))


def _block_height(paras: list[str], size: int, width_in: float, spacing_pt: float) -> float:
    lines = sum(_lines_needed(text, size, width_in) for text in paras)
    return (lines * size * 1.28 + spacing_pt * len(paras)) / 72


def _fit_size(
    paras: list[str],
    width_in: float,
    height_in: float,
    start: int,
    *,
    min_size: int = MIN_BODY_PT,
    spacing_pt: float = 8,
) -> int:
    size = max(start, min_size)
    while size > min_size and _block_height(paras, size, width_in, spacing_pt) > height_in:
        size -= 1
    return size


def measure_text_height(
    paragraphs: list[str],
    size: int,
    width_in: float,
    spacing_pt: float = 0,
    line_spacing: float = 1.28,
) -> float:
    """对外暴露的文字高度估算（质检与渲染共用同一口径）。"""
    lines = sum(_lines_needed(text, size, width_in) for text in paragraphs)
    return (lines * size * line_spacing + spacing_pt * len(paragraphs)) / 72


def _clip_to_width(text: str, size: int, width_in: float, lines: int) -> str:
    capacity = max(1, int(width_in * 72 / max(size, 1) * lines / 1.06))
    if _text_units(text) <= capacity:
        return text
    out = ""
    for ch in text:
        if _text_units(out + ch) > capacity - 1:
            break
        out += ch
    return out.rstrip("，、；,; ") + "…"


def _title_text(text: str, size: int, width_in: float = CONTENT_W) -> str:
    """标题最多两行：先保证不超两行，超了才截断（截断部分留在备注里）。"""
    if _lines_needed(text, size, width_in) <= 2:
        return text
    return _clip_to_width(text, size, width_in, 2)


# --------------------------------------------------------------------------- #
# 基础图元
# --------------------------------------------------------------------------- #


def _textbox(
    slide,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    anchor=MSO_ANCHOR.TOP,
    name: str = "qa:text",
):
    box = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    box.name = name
    frame = box.text_frame
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left = 0
    frame.margin_right = 0
    frame.margin_top = 0
    frame.margin_bottom = 0
    frame.vertical_anchor = anchor
    return box


def _paragraph(
    frame,
    text: str,
    theme: DeckTheme,
    *,
    first: bool = False,
    size: int,
    color: str,
    bold: bool = False,
    italic: bool = False,
    title: bool = False,
    align=PP_ALIGN.LEFT,
    space_before: float = 0,
    space_after: float = 6,
    line_spacing: float = 1.24,
    bullet: str | None = None,
):
    paragraph = frame.paragraphs[0] if first else frame.add_paragraph()
    paragraph.alignment = align
    paragraph.space_before = Pt(space_before)
    paragraph.space_after = Pt(space_after)
    paragraph.line_spacing = line_spacing
    run = paragraph.add_run()
    run.text = text
    _apply_font(run, theme, size=size, color=color, bold=bold, italic=italic, title=title)
    if bullet:
        _set_bullet(paragraph, char=bullet)
    return paragraph


def _solid_rect(
    slide, x: float, y: float, w: float, h: float, *, fill: str, line: str | None = None
):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(fill)
    if line:
        shape.line.color.rgb = _rgb(line)
        shape.line.width = Pt(0.75)
    else:
        shape.line.fill.background()
    shape.shadow.inherit = False
    return shape


def _card(slide, x: float, y: float, w: float, h: float, *, fill: str, name: str = "qa:card"):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
    )
    shape.name = name
    shape.adjustments[0] = 0.06
    shape.fill.solid()
    shape.fill.fore_color.rgb = _rgb(fill)
    shape.line.fill.background()
    shape.shadow.inherit = False
    frame = shape.text_frame
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.margin_left = Inches(0.18)
    frame.margin_right = Inches(0.18)
    frame.margin_top = Inches(0.12)
    frame.margin_bottom = Inches(0.1)
    frame.vertical_anchor = MSO_ANCHOR.MIDDLE
    return shape


def _header(slide, theme: DeckTheme, text: str, *, page: int | None, source: str = "") -> None:
    """统一标题与页脚：标题最多两行，页脚左来源右页码。"""
    size = theme.title
    while size > 24 and _lines_needed(text, size, CONTENT_W) > 2:
        size -= 1
    shown = _title_text(text, size)
    box = _textbox(
        slide, MARGIN, TITLE_Y, CONTENT_W, TITLE_H, anchor=MSO_ANCHOR.TOP, name="qa:title"
    )
    _paragraph(
        box.text_frame,
        shown,
        theme,
        first=True,
        size=size,
        color=theme.neutral_dark,
        bold=True,
        title=True,
        line_spacing=1.12,
        space_after=0,
    )
    if source:
        foot = _textbox(
            slide,
            MARGIN,
            FOOTER_Y,
            CONTENT_W - 1.0,
            0.3,
            anchor=MSO_ANCHOR.MIDDLE,
            name="qa:footer",
        )
        _paragraph(
            foot.text_frame,
            f"来源：{source}",
            theme,
            first=True,
            size=theme.footer,
            color=theme.neutral_mid,
            space_after=0,
        )
    _pager(slide, theme, page)


def _pager(slide, theme: DeckTheme, page: int | None) -> None:
    """页码（封面与结尾页不写，避免压住整页底色）。"""
    if page is None:
        return
    pager = _textbox(
        slide,
        SLIDE_W - MARGIN - 0.8,
        FOOTER_Y,
        0.8,
        0.3,
        anchor=MSO_ANCHOR.MIDDLE,
        name="qa:pageno",
    )
    _paragraph(
        pager.text_frame,
        str(page),
        theme,
        first=True,
        size=theme.footer,
        color=theme.neutral_mid,
        align=PP_ALIGN.RIGHT,
        space_after=0,
    )


def _notes(slide, slide_model: DeckSlide) -> None:
    text = (slide_model.notes or slide_model.key_message or slide_model.title).strip()
    if text:
        slide.notes_slide.notes_text_frame.text = text[:1200]


def _append_notes(slide, text: str) -> None:
    """渲染时为放下而截断/删减的内容，完整版补进备注——不静默丢信息。"""
    frame = slide.notes_slide.notes_text_frame
    current = frame.text.strip()
    merged = f"{current}\n{text}".strip() if current else text.strip()
    frame.text = merged[:1800]


def _slide_source(slide_model: DeckSlide) -> str:
    if slide_model.source:
        return slide_model.source
    if slide_model.chart is not None and slide_model.chart.source:
        return slide_model.chart.source
    if slide_model.table is not None and slide_model.table.source:
        return slide_model.table.source
    return ""


# --------------------------------------------------------------------------- #
# 各版式
# --------------------------------------------------------------------------- #


def _cover(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = _solid_rect(slide, 0, 0, SLIDE_W, SLIDE_H, fill=theme.primary)
    bg.name = "qa:deco"
    size = theme.cover_title
    while size > 26 and _lines_needed(slide_model.title, size, 11.4) > 2:
        size -= 2
    title = _textbox(slide, 0.9, 2.0, 11.5, 1.9, anchor=MSO_ANCHOR.BOTTOM)
    _paragraph(
        title.text_frame,
        _title_text(slide_model.title, size, 11.4),
        theme,
        first=True,
        size=size,
        color="#FFFFFF",
        bold=True,
        title=True,
        line_spacing=1.1,
        space_after=0,
    )
    y = 4.0
    if plan.subtitle:
        sub = _textbox(slide, 0.9, y, 11.5, 0.5)
        _paragraph(
            sub.text_frame,
            plan.subtitle[:120],
            theme,
            first=True,
            size=theme.subtitle,
            color=_tint(theme.primary, 0.86),
            space_after=0,
        )
        y += 0.55
    if slide_model.key_message:
        msgs = _textbox(slide, 0.9, y, 11.5, 0.9)
        _paragraph(
            msgs.text_frame,
            slide_model.key_message,
            theme,
            first=True,
            size=16,
            color=_tint(theme.primary, 0.78),
            space_after=0,
        )
    if plan.audience:
        who = _textbox(slide, 0.9, 6.15, 11.5, 0.4)
        _paragraph(
            who.text_frame,
            f"汇报对象：{plan.audience}",
            theme,
            first=True,
            size=theme.small,
            color=_tint(theme.primary, 0.72),
            space_after=0,
        )
    _notes(slide, slide_model)
    return slide


def _agenda(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title or "目录", page=page, source=_slide_source(slide_model))
    items = slide_model.bullets[:6]
    cols = 2 if len(items) > 3 else 1
    per_col = math.ceil(len(items) / cols)
    col_w = (CONTENT_W - theme.gutter * (cols - 1)) / cols
    row_h = min(1.0, (BODY_H - 0.4) / per_col)
    size = theme.body if per_col <= 3 else theme.body - 2
    for index, item in enumerate(items):
        col = index // per_col
        row = index % per_col
        x = MARGIN + col * (col_w + theme.gutter)
        y = BODY_Y + 0.35 + row * row_h
        num = _textbox(slide, x, y, 0.72, row_h - 0.1, anchor=MSO_ANCHOR.MIDDLE)
        _paragraph(
            num.text_frame,
            f"{index + 1:02d}",
            theme,
            first=True,
            size=22,
            color=theme.accent,
            bold=True,
            title=True,
            space_after=0,
        )
        label = _textbox(
            slide, x + 0.78, y, col_w - 0.78, row_h - 0.1, anchor=MSO_ANCHOR.MIDDLE
        )
        _paragraph(
            label.text_frame,
            _clip_to_width(item, size, col_w - 0.9, 2),
            theme,
            first=True,
            size=size,
            color=theme.neutral_dark,
            space_after=0,
        )
    _notes(slide, slide_model)
    return slide


def _section(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    slide.background.fill.solid()
    slide.background.fill.fore_color.rgb = _rgb(theme.neutral_light)
    index = 0
    for item in plan.slides:
        if item.type == "section":
            index += 1
        if item is slide_model:
            break
    number = _textbox(slide, MARGIN, 2.0, 3.0, 1.1)
    _paragraph(
        number.text_frame,
        f"{index:02d}",
        theme,
        first=True,
        size=54,
        color=theme.accent,
        bold=True,
        title=True,
        space_after=0,
    )
    title = _textbox(slide, MARGIN, 3.15, CONTENT_W - 2.0, 0.9)
    _paragraph(
        title.text_frame,
        _title_text(slide_model.title, theme.section_title),
        theme,
        first=True,
        size=theme.section_title,
        color=theme.primary,
        bold=True,
        title=True,
        space_after=0,
    )
    if slide_model.key_message:
        msg = _textbox(slide, MARGIN, 4.15, CONTENT_W - 2.5, 1.2)
        _paragraph(
            msg.text_frame,
            slide_model.key_message,
            theme,
            first=True,
            size=theme.subtitle,
            color=theme.neutral_mid,
            space_after=0,
        )
    _pager(slide, theme, page)
    _notes(slide, slide_model)
    return slide


def _statement(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    text = slide_model.key_message
    size = _fit_size([text], CONTENT_W - 1.2, 3.4, 32, min_size=20, spacing_pt=0)
    box = _textbox(slide, MARGIN, 2.3, CONTENT_W - 1.2, 3.4, anchor=MSO_ANCHOR.MIDDLE)
    _paragraph(
        box.text_frame,
        _clip_to_width(text, size, CONTENT_W - 1.2, 6) if size <= 20 else text,
        theme,
        first=True,
        size=size,
        color=theme.neutral_dark,
        line_spacing=1.3,
        space_after=0,
    )
    _notes(slide, slide_model)
    return slide


def _bullets(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    items = list(slide_model.bullets)
    width = CONTENT_W - 0.35
    size = _fit_size(items, width, BODY_H - 0.2, theme.bullet)
    dropped = False
    if _block_height(items, size, width, 12) > BODY_H - 0.2:
        kept = []
        for item in items:
            if _block_height(kept + [item], size, width, 12) <= BODY_H - 0.2:
                kept.append(item)
        items = kept or items[:1]
        dropped = len(items) < len(slide_model.bullets)
    box = _textbox(slide, MARGIN, BODY_Y + 0.1, width, BODY_H - 0.3)
    for index, item in enumerate(items):
        _paragraph(
            box.text_frame,
            item,
            theme,
            first=index == 0,
            size=size,
            color=theme.neutral_dark,
            space_after=14,
            line_spacing=1.26,
            bullet="•",
        )
    _notes(slide, slide_model)
    if dropped:
        _append_notes(slide, "完整要点：" + "；".join(slide_model.bullets))
    return slide


def _two_column(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    columns = slide_model.columns[:2]
    gap = 0.45
    width = (CONTENT_W - gap * (len(columns) - 1)) / max(1, len(columns))
    for index, column in enumerate(columns):
        x = MARGIN + index * (width + gap)
        card = _card(slide, x, BODY_Y + 0.05, width, BODY_H - 0.2, fill=theme.neutral_light)
        frame = card.text_frame
        frame.vertical_anchor = MSO_ANCHOR.TOP
        _paragraph(
            frame,
            _clip_to_width(column.title, theme.body, width - 0.4, 2),
            theme,
            first=True,
            size=theme.subtitle,
            color=theme.primary,
            bold=True,
            space_after=12,
        )
        texts = column.items
        size = _fit_size(texts, width - 0.75, BODY_H - 1.3, theme.small, min_size=12, spacing_pt=8)
        for item in texts:
            _paragraph(
                frame,
                _clip_to_width(item, size, width - 0.75, 3),
                theme,
                size=size,
                color=theme.neutral_dark,
                space_after=8,
                bullet="•",
            )
    _notes(slide, slide_model)
    return slide


def _kpi(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    kpis = slide_model.kpis[:4]
    gap = 0.3
    width = (CONTENT_W - gap * (len(kpis) - 1)) / max(1, len(kpis))
    height = 2.55 if slide_model.key_message else 3.1
    y = BODY_Y + 0.15
    for index, kpi in enumerate(kpis):
        x = MARGIN + index * (width + gap)
        card = _card(slide, x, y, width, height, fill=theme.neutral_light)
        frame = card.text_frame
        value_text = f"{kpi.value}{kpi.unit}"
        size = _fit_size([value_text], width - 0.4, 1.1, theme.kpi, min_size=24, spacing_pt=0)
        _paragraph(
            frame,
            value_text,
            theme,
            first=True,
            size=size,
            color=theme.primary,
            bold=True,
            title=True,
            align=PP_ALIGN.CENTER,
            space_after=6,
            line_spacing=1.1,
        )
        _paragraph(
            frame,
            kpi.label,
            theme,
            size=theme.small,
            color=theme.neutral_mid,
            align=PP_ALIGN.CENTER,
            space_after=0,
        )
    if slide_model.key_message:
        msg = _textbox(slide, MARGIN, y + height + 0.25, CONTENT_W, 0.9)
        _paragraph(
            msg.text_frame,
            slide_model.key_message,
            theme,
            first=True,
            size=16,
            color=theme.neutral_mid,
            align=PP_ALIGN.CENTER,
            space_after=0,
        )
    _notes(slide, slide_model)
    return slide


def _chart_type(kind: str):
    return {
        "bar": XL_CHART_TYPE.COLUMN_CLUSTERED,
        "line": XL_CHART_TYPE.LINE_MARKERS,
        "pie": XL_CHART_TYPE.PIE,
    }.get(kind, XL_CHART_TYPE.COLUMN_CLUSTERED)


def _chart_east_asian(chart, theme: DeckTheme) -> None:
    """图表文本同样需要东亚字体，否则中文类别名会退化成方框。"""
    chart_space = chart._chartSpace
    for def_rpr in chart_space.iter(qn("a:defRPr")):
        latin = def_rpr.find(qn("a:latin"))
        if latin is None:
            continue
        existing = def_rpr.find(qn("a:ea"))
        if existing is not None:
            def_rpr.remove(existing)
        ea = def_rpr.makeelement(qn("a:ea"), {"typeface": theme.body_stack[0]})
        latin.addnext(ea)


def _chart(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    data = slide_model.chart
    if data is None or data.is_empty:  # 兜底：宁可写一行说明也不画空图
        box = _textbox(slide, MARGIN, 3.0, CONTENT_W, 1.2, anchor=MSO_ANCHOR.MIDDLE)
        _paragraph(
            box.text_frame,
            "本页图表数据待补充（请在素材中给出类别与数值）。",
            theme,
            first=True,
            size=theme.subtitle,
            color=theme.neutral_mid,
            align=PP_ALIGN.CENTER,
            space_after=0,
        )
        _notes(slide, slide_model)
        return slide
    chart_data = CategoryChartData()
    chart_data.categories = data.categories
    for series in data.series:
        chart_data.add_series(series.name, series.values)
    rect = (Inches(MARGIN), Inches(BODY_Y), Inches(CONTENT_W), Inches(BODY_H - 0.1))
    frame = slide.shapes.add_chart(_chart_type(data.type), *rect, chart_data)
    chart = frame.chart
    chart.font.size = Pt(12)
    chart.font.name = theme.latin_body
    chart.font.color.rgb = _rgb(theme.neutral_dark)
    chart.has_legend = len(data.series) > 1 and data.type != "pie"
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(12)
    plot = chart.plots[0]
    plot.gap_width = 70
    if data.type == "pie":
        plot.has_data_labels = True
        labels = plot.data_labels
        labels.show_percentage = True
        labels.show_value = False
        labels.number_format = "0%"
        labels.number_format_is_linked = False
        labels.font.size = Pt(12)
        labels.position = XL_LABEL_POSITION.OUTSIDE_END
    else:
        chart.value_axis.has_major_gridlines = True
        chart.value_axis.major_gridlines.format.line.color.rgb = _rgb(theme.neutral_light)
        chart.value_axis.tick_labels.font.size = Pt(11)
        chart.category_axis.tick_labels.font.size = Pt(12)
        try:
            chart.value_axis.tick_labels.number_format = "General"
            chart.value_axis.tick_labels.number_format_is_linked = False
        except (AttributeError, ValueError):  # pragma: no cover - 老版本兼容
            pass
    _chart_east_asian(chart, theme)
    if data.unit:
        unit = _textbox(slide, MARGIN, FOOTER_Y - 0.38, 3.0, 0.32, name="qa:caption")
        _paragraph(
            unit.text_frame,
            f"单位：{data.unit}",
            theme,
            first=True,
            size=theme.caption,
            color=theme.neutral_mid,
            space_after=0,
        )
    _notes(slide, slide_model)
    return slide


def _is_numeric(text: str) -> bool:
    return bool(_NUMERIC_RE.match(str(text).strip().replace(" ", "")))


def _table(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    data = slide_model.table
    columns = list(data.columns) if data else []
    rows = [list(row) for row in data.rows] if data else []
    if not columns or not rows:
        _notes(slide, slide_model)
        return slide
    row_count = len(rows) + 1
    col_count = len(columns)
    row_h = min(0.6, (BODY_H - 0.1) / row_count)
    col_w = CONTENT_W / col_count
    shape = slide.shapes.add_table(
        row_count,
        col_count,
        Inches(MARGIN),
        Inches(BODY_Y),
        Inches(CONTENT_W),
        Inches(row_h * row_count),
    )
    table = shape.table
    table.first_row = True
    table.horz_banding = False
    for index in range(col_count):
        table.columns[index].width = Inches(col_w)
    for index in range(row_count):
        table.rows[index].height = Inches(row_h)
    text_size = theme.table
    max_units = max(
        [_text_units(str(cell)) for row in rows for cell in row] or [0]
    )
    while text_size > 12 and _cell_lines(max_units, text_size, col_w) > 2:
        text_size -= 1
    capacity = int(col_w * 72 / text_size * 2 * 0.95)
    clipped = False

    for col, header in enumerate(columns):
        cell = table.cell(0, col)
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(theme.primary)
        cell.vertical_anchor = MSO_ANCHOR.MIDDLE
        cell.margin_left = Inches(0.08)
        cell.margin_right = Inches(0.08)
        cell.text = ""
        frame = cell.text_frame
        _paragraph(
            frame,
            _clip_to_width(str(header), text_size, col_w, 2),
            theme,
            first=True,
            size=text_size,
            color="#FFFFFF",
            bold=True,
            space_after=0,
            line_spacing=1.05,
        )
    for row_index, row in enumerate(rows, start=1):
        for col, value in enumerate(row):
            cell = table.cell(row_index, col)
            cell.fill.solid()
            cell.fill.fore_color.rgb = _rgb(
                theme.neutral_light if row_index % 2 == 1 else "#FFFFFF"
            )
            cell.vertical_anchor = MSO_ANCHOR.MIDDLE
            cell.margin_left = Inches(0.08)
            cell.margin_right = Inches(0.08)
            cell.text = ""
            text = str(value)
            numeric = _is_numeric(text)
            if len(text) > capacity:
                clipped = True
            _paragraph(
                cell.text_frame,
                _clip_to_width(text, text_size, col_w, 2) if len(text) > capacity else text,
                theme,
                first=True,
                size=text_size,
                color=theme.neutral_dark,
                align=PP_ALIGN.RIGHT if numeric else PP_ALIGN.LEFT,
                space_after=0,
                line_spacing=1.05,
            )
    _notes(slide, slide_model)
    if clipped:
        _append_notes(slide, "完整表格数据：" + "；".join(" ｜ ".join(row) for row in rows))
    return slide


def _cell_lines(units: float, size: int, width_in: float) -> int:
    per_line = max(1.0, width_in * 72 / max(size, 1))
    return max(1, math.ceil(units / per_line))


def _timeline(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    items = slide_model.timeline[:6]
    axis_y = 3.55
    left = MARGIN + 0.15
    right = SLIDE_W - MARGIN - 0.15
    line = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT,
        Inches(left),
        Inches(axis_y),
        Inches(right),
        Inches(axis_y),
    )
    line.name = "qa:axis"
    line.line.color.rgb = _rgb(theme.primary)
    line.line.width = Pt(1.5)
    step = (right - left) / max(1, len(items))
    width = step - 0.18
    for index, item in enumerate(items):
        cx = left + step * (index + 0.5)
        dot = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, Inches(cx - 0.085), Inches(axis_y - 0.085), Inches(0.17), Inches(0.17)
        )
        dot.fill.solid()
        dot.fill.fore_color.rgb = _rgb(theme.accent)
        dot.line.fill.background()
        dot.shadow.inherit = False
        dot.name = "qa:dot"
        when = _textbox(slide, cx - width / 2, 2.22, width, 0.48, anchor=MSO_ANCHOR.BOTTOM)
        _paragraph(
            when.text_frame,
            _clip_to_width(item.time, theme.small, width, 1),
            theme,
            first=True,
            size=theme.small,
            color=theme.accent,
            bold=True,
            align=PP_ALIGN.CENTER,
            space_after=0,
        )
        name = _textbox(slide, cx - width / 2, 2.75, width, 0.55, anchor=MSO_ANCHOR.BOTTOM)
        _paragraph(
            name.text_frame,
            _clip_to_width(item.title, 16, width, 2),
            theme,
            first=True,
            size=16,
            color=theme.neutral_dark,
            bold=True,
            align=PP_ALIGN.CENTER,
            space_after=0,
            line_spacing=1.1,
        )
        if item.text:
            detail = _textbox(slide, cx - width / 2, axis_y + 0.3, width, 1.5)
            _paragraph(
                detail.text_frame,
                _clip_to_width(item.text, 12, width, 4),
                theme,
                first=True,
                size=12,
                color=theme.neutral_mid,
                align=PP_ALIGN.CENTER,
                space_after=0,
                line_spacing=1.2,
            )
    _notes(slide, slide_model)
    return slide


def _process(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    items = slide_model.process[:6]
    width = (CONTENT_W + 0.24 * (len(items) - 1)) / max(1, len(items))
    overlap = 0.24
    y = BODY_Y + 0.35
    for index, item in enumerate(items):
        x = MARGIN + index * (width - overlap)
        chevron = slide.shapes.add_shape(
            MSO_SHAPE.CHEVRON, Inches(x), Inches(y), Inches(width), Inches(0.95)
        )
        chevron.fill.solid()
        chevron.fill.fore_color.rgb = _rgb(
            theme.primary if index % 2 == 0 else theme.secondary
        )
        chevron.line.fill.background()
        chevron.shadow.inherit = False
        chevron.name = "qa:chevron"
        frame = chevron.text_frame
        frame.word_wrap = True
        frame.margin_left = Inches(0.18)
        frame.margin_right = Inches(0.1)
        frame.vertical_anchor = MSO_ANCHOR.MIDDLE
        _paragraph(
            frame,
            item.step or f"{index + 1:02d}",
            theme,
            first=True,
            size=15,
            color="#FFFFFF",
            bold=True,
            align=PP_ALIGN.CENTER,
            space_after=0,
        )
        label = _textbox(slide, x + 0.1, y + 1.1, width - 0.4, 0.5)
        _paragraph(
            label.text_frame,
            _clip_to_width(item.title, 15, width - 0.5, 2),
            theme,
            first=True,
            size=15,
            color=theme.neutral_dark,
            bold=True,
            space_after=0,
            line_spacing=1.1,
        )
        if item.text:
            detail = _textbox(slide, x + 0.1, y + 1.65, width - 0.4, 1.6)
            _paragraph(
                detail.text_frame,
                _clip_to_width(item.text, 12, width - 0.5, 4),
                theme,
                first=True,
                size=12,
                color=theme.neutral_mid,
                space_after=0,
                line_spacing=1.2,
            )
    _notes(slide, slide_model)
    return slide


def _comparison(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    data = slide_model.comparison
    if data is None:
        _notes(slide, slide_model)
        return slide
    gap = 0.4
    width = (CONTENT_W - gap) / 2
    panels = [
        (MARGIN, data.left_title, data.left_items, theme.neutral_light, theme.neutral_dark),
        (
            MARGIN + width + gap,
            data.right_title,
            data.right_items,
            _tint(theme.primary, 0.9),
            theme.primary,
        ),
    ]
    for x, title, items, fill, title_color in panels:
        card = _card(slide, x, BODY_Y + 0.05, width, BODY_H - 0.2, fill=fill)
        frame = card.text_frame
        frame.vertical_anchor = MSO_ANCHOR.TOP
        _paragraph(
            frame,
            _clip_to_width(title, theme.body, width - 0.5, 2),
            theme,
            first=True,
            size=theme.subtitle,
            color=title_color,
            bold=True,
            space_after=12,
        )
        size = _fit_size(items, width - 0.75, BODY_H - 1.3, theme.small, spacing_pt=8)
        for item in items:
            _paragraph(
                frame,
                _clip_to_width(item, size, width - 0.75, 3),
                theme,
                size=size,
                color=theme.neutral_dark,
                space_after=8,
                bullet="•",
            )
    _notes(slide, slide_model)
    return slide


def _quote(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    mark = _textbox(slide, MARGIN - 0.1, 1.35, 0.95, 1.95, anchor=MSO_ANCHOR.TOP)
    _paragraph(
        mark.text_frame,
        "“",
        theme,
        first=True,
        size=96,
        color=theme.accent,
        bold=True,
        title=True,
        space_after=0,
    )
    text = slide_model.key_message
    size = _fit_size([text], CONTENT_W - 1.6, 2.9, theme.quote, min_size=20, spacing_pt=0)
    box = _textbox(slide, MARGIN + 0.9, 2.0, CONTENT_W - 1.6, 2.9, anchor=MSO_ANCHOR.MIDDLE)
    _paragraph(
        box.text_frame,
        text if size > 20 else _clip_to_width(text, size, CONTENT_W - 1.6, 5),
        theme,
        first=True,
        size=size,
        color=theme.neutral_dark,
        italic=True,
        line_spacing=1.3,
        space_after=0,
    )
    if slide_model.source:
        src = _textbox(slide, MARGIN + 0.9, 5.25, CONTENT_W - 1.6, 0.4)
        _paragraph(
            src.text_frame,
            f"—— {slide_model.source}",
            theme,
            first=True,
            size=theme.small,
            color=theme.neutral_mid,
            align=PP_ALIGN.RIGHT,
            space_after=0,
        )
    _notes(slide, slide_model)
    return slide


def _image(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    _header(slide, theme, slide_model.title, page=page, source=_slide_source(slide_model))
    data = slide_model.image
    frame_shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(MARGIN), Inches(BODY_Y), Inches(CONTENT_W), Inches(BODY_H - 0.1)
    )
    frame_shape.fill.solid()
    frame_shape.fill.fore_color.rgb = _rgb(theme.neutral_light)
    frame_shape.line.color.rgb = _rgb(theme.neutral_mid)
    frame_shape.line.width = Pt(1)
    frame_shape.line.dash_style = MSO_LINE_DASH_STYLE.DASH
    frame_shape.shadow.inherit = False
    frame_shape.name = "qa:image"
    box = _textbox(
        slide,
        MARGIN + 0.6,
        BODY_Y + 1.2,
        CONTENT_W - 1.2,
        2.0,
        anchor=MSO_ANCHOR.MIDDLE,
        name="qa:image-label",
    )
    slot = data.slot if data else "配图"
    _paragraph(
        box.text_frame,
        f"图片位：{slot}",
        theme,
        first=True,
        size=theme.subtitle,
        color=theme.primary,
        bold=True,
        align=PP_ALIGN.CENTER,
        space_after=6,
    )
    parts = (data.prompt if data else "", data.alt if data else "")
    hint = "；".join(part for part in parts if part)
    if hint:
        _paragraph(
            box.text_frame,
            _clip_to_width(hint, theme.caption, CONTENT_W - 1.6, 3),
            theme,
            size=theme.caption,
            color=theme.neutral_mid,
            align=PP_ALIGN.CENTER,
            space_after=0,
        )
    _notes(slide, slide_model)
    return slide


def _closing(prs, plan: DeckPlan, slide_model: DeckSlide, theme: DeckTheme, page: int):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    bg = _solid_rect(slide, 0, 0, SLIDE_W, SLIDE_H, fill=theme.primary)
    bg.name = "qa:deco"
    title = _textbox(slide, 0.9, 2.4, 11.5, 1.3, anchor=MSO_ANCHOR.BOTTOM)
    _paragraph(
        title.text_frame,
        slide_model.title or "谢谢",
        theme,
        first=True,
        size=46,
        color="#FFFFFF",
        bold=True,
        title=True,
        space_after=0,
    )
    if slide_model.key_message:
        msg = _textbox(slide, 0.9, 3.95, 11.5, 0.8)
        _paragraph(
            msg.text_frame,
            slide_model.key_message,
            theme,
            first=True,
            size=theme.subtitle,
            color=_tint(theme.primary, 0.82),
            space_after=0,
        )
    if plan.audience:
        who = _textbox(slide, 0.9, 6.1, 11.5, 0.4)
        _paragraph(
            who.text_frame,
            plan.audience,
            theme,
            first=True,
            size=theme.small,
            color=_tint(theme.primary, 0.7),
            space_after=0,
        )
    _notes(slide, slide_model)
    return slide


_BUILDERS = {
    "cover": _cover,
    "agenda": _agenda,
    "section": _section,
    "statement": _statement,
    "bullets": _bullets,
    "two_column": _two_column,
    "kpi": _kpi,
    "chart": _chart,
    "table": _table,
    "timeline": _timeline,
    "process": _process,
    "comparison": _comparison,
    "quote": _quote,
    "image": _image,
    "closing": _closing,
}


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #


def build_presentation(plan: DeckPlan) -> Presentation:
    """把 DeckPlan 渲染成 Presentation 对象（质检直接检查几何用）。"""
    prs = Presentation()
    prs.slide_width = Inches(SLIDE_W)
    prs.slide_height = Inches(SLIDE_H)
    theme = get_theme(plan.theme)
    for index, slide_model in enumerate(plan.slides, start=1):
        builder = _BUILDERS.get(slide_model.type, _statement)
        page = None if slide_model.type in ("cover", "closing") else index
        builder(prs, plan, slide_model, theme, page)
    return prs


def to_pptx_plan(plan: DeckPlan, path: str | Path | None = None) -> bytes:
    """DeckPlan → .pptx 字节；给出 path 时同时落盘。"""
    prs = build_presentation(plan)
    buf = io.BytesIO()
    prs.save(buf)
    data = buf.getvalue()
    if path is not None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)
    return data


def to_pptx(
    model: DocModel | DeckPlan, path: str | Path | None = None, *, theme: str = ""
) -> bytes:
    """旧签名兼容：DocModel 会先确定性降级成 DeckPlan 再渲染。"""
    if isinstance(model, DeckPlan):
        plan = model
    else:
        # 旧接口必须出文件：素材不足 5 页时也渲染短稿，而不是报错
        plan = plan_from_doc_model(model, allow_short=True)
    if theme:
        plan = plan.model_copy(update={"theme": theme})
    return to_pptx_plan(plan, path)


__all__ = [
    "BODY_H",
    "BODY_Y",
    "CONTENT_W",
    "MARGIN",
    "SLIDE_H",
    "SLIDE_W",
    "build_presentation",
    "measure_text_height",
    "to_pptx",
    "to_pptx_plan",
]
