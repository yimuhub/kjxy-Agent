"""DocModel → PowerPoint（python-pptx，MIT）。

版式保持最小集：封面页 +「标题/内容」页。一级标题开新页；段落与列表进本页正文；
表格单独成页；分页符强制新页。不做自由拖拽排版（后续版本）。
"""

from __future__ import annotations

import io

from pptx import Presentation
from pptx.util import Inches, Pt

from app.modules.docs.model import DocModel

_LAYOUT_TITLE = 0
_LAYOUT_CONTENT = 1
_MAX_BODY_CHARS = 1500


def _body_paragraph(body):
    """取正文占位符里可写的段落：空白占位符复用首段，否则追加（不覆盖已有内容）。"""
    if not body.text.strip():
        return body.paragraphs[0]
    return body.add_paragraph()


def _add_bullets(slide, items: list[str], numbered: bool) -> None:
    body = (
        slide.placeholders[1].text_frame
        if len(slide.placeholders) > 1
        else slide.shapes.add_textbox(0, 0, 0, 0).text_frame
    )
    body.word_wrap = True
    for idx, item in enumerate(items, start=1):
        text = f"{idx}. {item}" if numbered else item
        para = _body_paragraph(body)
        para.text = text[:500]
        para.font.size = Pt(18)


def _new_content_slide(prs, title: str):
    slide = prs.slides.add_slide(prs.slide_layouts[_LAYOUT_CONTENT])
    slide.shapes.title.text = title[:120]
    return slide


def to_pptx(model: DocModel) -> bytes:
    prs = Presentation()
    cover = prs.slides.add_slide(prs.slide_layouts[_LAYOUT_TITLE])
    cover.shapes.title.text = model.title
    subtitle = model.subject or model.author
    if subtitle and len(cover.placeholders) > 1:
        cover.placeholders[1].text = subtitle

    slide = None
    current_title = model.title
    body_chars = 0
    for block in model.blocks:
        if block.type == "heading":
            current_title = block.text
            slide = _new_content_slide(prs, block.text)
            body_chars = 0
        elif block.type in ("bullets", "numbered"):
            slide = slide or _new_content_slide(prs, current_title)
            _add_bullets(slide, block.items, numbered=block.type == "numbered")
            body_chars += sum(len(i) for i in block.items)
        elif block.type in ("paragraph", "code"):
            slide = slide or _new_content_slide(prs, current_title)
            body = slide.placeholders[1].text_frame
            body.word_wrap = True
            para = _body_paragraph(body)
            para.text = block.text[:_MAX_BODY_CHARS]
            para.font.size = Pt(16)
            body_chars += len(block.text)
        elif block.type == "table":
            slide = prs.slides.add_slide(prs.slide_layouts[_LAYOUT_CONTENT])
            slide.shapes.title.text = current_title[:120]
            rows, cols = len(block.rows) + 1, len(block.header)
            table = slide.shapes.add_table(
                rows,
                cols,
                Inches(0.6),
                Inches(1.4),
                prs.slide_width - Inches(1.2),
                Inches(0.45) * rows,
            ).table
            for i, value in enumerate(block.header):
                table.cell(0, i).text = value
            for r, row in enumerate(block.rows, start=1):
                for c, value in enumerate(row):
                    table.cell(r, c).text = value
        elif block.type == "pagebreak":
            slide = None
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()
