"""DocModel → Word（python-docx，MIT；项目原本就用于 docx 文本抽取）。"""

from __future__ import annotations

import io

from docx import Document
from docx.shared import Pt

from app.modules.docs.model import DocBlock, DocModel


def _add_table(doc, block: DocBlock) -> None:
    table = doc.add_table(rows=1, cols=len(block.header))
    table.style = "Table Grid"
    for i, cell in enumerate(table.rows[0].cells):
        cell.text = block.header[i]
        for run in cell.paragraphs[0].runs:
            run.bold = True
    for row in block.rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = value


def to_docx(model: DocModel) -> bytes:
    doc = Document()
    props = doc.core_properties
    props.title = model.title
    if model.subject:
        props.subject = model.subject
    if model.author:
        props.author = model.author

    doc.add_heading(model.title, level=0)
    if model.subject:
        doc.add_paragraph(model.subject)
    if model.author:
        doc.add_paragraph(f"作者：{model.author}")

    for block in model.blocks:
        if block.type == "heading":
            doc.add_heading(block.text, level=min(block.level + 1, 9))
        elif block.type == "paragraph":
            doc.add_paragraph(block.text)
        elif block.type == "bullets":
            for item in block.items:
                doc.add_paragraph(item, style="List Bullet")
        elif block.type == "numbered":
            for item in block.items:
                doc.add_paragraph(item, style="List Number")
        elif block.type == "code":
            para = doc.add_paragraph()
            run = para.add_run(block.text)
            run.font.name = "Courier New"
            run.font.size = Pt(9)
        elif block.type == "table":
            _add_table(doc, block)
            doc.add_paragraph()
        elif block.type == "pagebreak":
            doc.add_page_break()

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()
