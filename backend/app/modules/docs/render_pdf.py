"""DocModel → PDF（reportlab，BSD-3；中文走 CID 字体 STSong-Light，不打包字体文件）。"""

from __future__ import annotations

import io
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.modules.docs.model import DocBlock, DocModel

_FONT = "STSong-Light"


def _ensure_font() -> None:
    if _FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(_FONT))


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle("doc-body", fontName=_FONT, fontSize=10.5, leading=16)
    return {
        "title": ParagraphStyle("doc-title", parent=base, fontSize=20, leading=28, spaceAfter=10),
        "meta": ParagraphStyle("doc-meta", parent=base, textColor=colors.HexColor("#666666")),
        "h1": ParagraphStyle(
            "doc-h1", parent=base, fontSize=15, leading=22, spaceBefore=12, spaceAfter=4
        ),
        "h2": ParagraphStyle(
            "doc-h2", parent=base, fontSize=13, leading=20, spaceBefore=10, spaceAfter=3
        ),
        "h3": ParagraphStyle(
            "doc-h3", parent=base, fontSize=11.5, leading=18, spaceBefore=8, spaceAfter=2
        ),
        "body": base,
        "bullet": ParagraphStyle("doc-bullet", parent=base, leftIndent=14, bulletIndent=2),
        "code": ParagraphStyle("doc-code", parent=base, fontSize=9.5, leading=14, leftIndent=8),
    }


def _table_flowable(block: DocBlock) -> Table:
    data = [list(block.header)] + [list(row) for row in block.rows]
    table = Table(data, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), _FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EDF5")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#B9C2D0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


def to_pdf(model: DocModel) -> bytes:
    _ensure_font()
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=18 * mm,
        title=model.title,
        author=model.author or "",
    )
    story: list = [Paragraph(escape(model.title), styles["title"])]
    if model.subject:
        story.append(Paragraph(escape(model.subject), styles["meta"]))
    if model.author:
        story.append(Paragraph(f"作者：{escape(model.author)}", styles["meta"]))
    story.append(Spacer(1, 8))

    for block in model.blocks:
        if block.type == "heading":
            key = "h1" if block.level <= 1 else ("h2" if block.level == 2 else "h3")
            story.append(Paragraph(escape(block.text), styles[key]))
        elif block.type == "paragraph":
            story.append(Paragraph(escape(block.text).replace("\n", "<br/>"), styles["body"]))
            story.append(Spacer(1, 4))
        elif block.type == "bullets":
            for item in block.items:
                story.append(Paragraph(f"• {escape(item)}", styles["bullet"]))
            story.append(Spacer(1, 4))
        elif block.type == "numbered":
            for idx, item in enumerate(block.items, start=1):
                story.append(Paragraph(f"{idx}. {escape(item)}", styles["bullet"]))
            story.append(Spacer(1, 4))
        elif block.type == "code":
            story.append(Paragraph(escape(block.text).replace("\n", "<br/>"), styles["code"]))
            story.append(Spacer(1, 4))
        elif block.type == "table":
            story.append(_table_flowable(block))
            story.append(Spacer(1, 6))
        elif block.type == "pagebreak":
            story.append(PageBreak())

    doc.build(story)
    return buf.getvalue()
