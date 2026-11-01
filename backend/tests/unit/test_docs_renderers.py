"""文档模型与五种写出器单测：可打开、可嗅探、预览全量转义。

口径：DocModel 是唯一来源——五种格式与在线预览都从同一份模型渲染，
因此断言"预览里出现的文字，导出文件里也能读到"。
"""

import io

import pytest
from app.core.errors import AppError
from app.modules.docs import render_docx, render_md, render_pdf, render_pptx, render_xlsx
from app.modules.docs.model import parse_model
from app.modules.docs.preview import to_preview_html
from app.modules.docs.service import format_from_text, normalize_format
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from openpyxl import load_workbook
from pptx import Presentation

RAW_MODEL = {
    "title": "设备巡检报告",
    "subject": "一号泵站",
    "author": "巡检班组",
    "blocks": [
        {"type": "heading", "level": 1, "text": "巡检概况"},
        {"type": "paragraph", "text": "本次巡检覆盖一号泵站全部机组。"},
        {"type": "bullets", "items": ["油位正常", "皮带张紧度正常"]},
        {"type": "numbered", "items": ["停机检查", "记录数据"]},
        {"type": "table", "header": ["项目", "结论"], "rows": [["油位", "正常"], ["异响", "无"]]},
        {"type": "code", "text": "pressure=0.6MPa"},
        {"type": "pagebreak"},
        {"type": "heading", "level": 2, "text": "待办事项"},
        {"type": "paragraph", "text": "下周复检轴承温度。"},
    ],
}


@pytest.fixture()
def model():
    return parse_model(RAW_MODEL)


def test_markdown_contains_all_blocks(model):
    text = render_md.to_markdown(model).decode()
    assert text.startswith("# 设备巡检报告")
    assert "> 一号泵站" in text
    assert "## 巡检概况" in text
    assert "- 油位正常" in text
    assert "1. 停机检查" in text
    assert "| 项目 | 结论 |" in text
    assert "```" in text
    assert "\n---\n" in text


def test_docx_is_valid_and_readable(model):
    data = render_docx.to_docx(model)
    assert data.startswith(b"PK\x03\x04")
    doc = Document(io.BytesIO(data))
    text = "\n".join(p.text for p in doc.paragraphs)
    assert "设备巡检报告" in text
    assert "本次巡检覆盖一号泵站全部机组。" in text
    assert doc.tables and doc.tables[0].cell(1, 0).text == "油位"


def test_xlsx_is_valid_and_readable(model):
    data = render_xlsx.to_xlsx(model)
    assert data.startswith(b"PK\x03\x04")
    wb = load_workbook(io.BytesIO(data))
    ws = wb.active
    assert ws["A1"].value == "设备巡检报告"
    cells = [c.value for row in ws.iter_rows() for c in row if c.value]
    assert "项目" in cells and "油位" in cells


def test_pptx_is_valid_and_readable(model):
    data = render_pptx.to_pptx(model)
    assert data.startswith(b"PK\x03\x04")
    prs = Presentation(io.BytesIO(data))
    assert len(prs.slides) >= 2
    texts = []
    for slide in prs.slides:
        for shape in slide.shapes:
            if shape.has_text_frame:
                texts.append(shape.text_frame.text)
    joined = "\n".join(texts)
    assert "设备巡检报告" in joined
    assert "油位正常" in joined


def test_pdf_has_magic_and_reasonable_size(model):
    data = render_pdf.to_pdf(model)
    assert data.startswith(b"%PDF")
    assert len(data) > 1000


def test_docx_uses_chinese_typography(model):
    doc = Document(io.BytesIO(render_docx.to_docx(model)))
    section = doc.sections[0]
    assert section.left_margin.cm == pytest.approx(2.54, abs=0.01)
    assert section.top_margin.cm == pytest.approx(2.54, abs=0.01)
    assert doc.styles["Normal"].font.name == "宋体"
    assert doc.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER


def test_xlsx_coerces_numbers_and_freezes_header():
    raw = {
        "title": "泵站台账",
        "blocks": [
            {
                "type": "table",
                "header": ["设备", "压力(MPa)", "占比"],
                "rows": [["一号泵", "0.6", "35%"]],
            }
        ],
    }
    ws = load_workbook(io.BytesIO(render_xlsx.to_xlsx(parse_model(raw)))).active
    header_row = next(row[0].row for row in ws.iter_rows() if row and row[0].value == "设备")
    assert ws.cell(header_row + 1, 2).value == 0.6
    assert ws.cell(header_row + 1, 3).value == 0.35
    assert ws.freeze_panes == f"A{header_row + 1}"


def test_pptx_is_widescreen(model):
    prs = Presentation(io.BytesIO(render_pptx.to_pptx(model)))
    assert round(prs.slide_width / 914400, 3) == 13.333
    assert round(prs.slide_height / 914400, 3) == 7.5


def test_preview_escapes_user_content():
    raw = {
        "title": "标题<script>alert(1)</script>",
        "blocks": [{"type": "paragraph", "text": "<img src=x onerror=alert(1)>"}],
    }
    html = to_preview_html(parse_model(raw))
    assert "<script>" not in html
    assert "onerror=alert(1)>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_preview_renders_tables_and_lists(model):
    html = to_preview_html(model)
    assert "<table>" in html and "<th>项目</th>" in html
    assert "<ul>" in html and "<ol>" in html
    assert '<hr class="pb"/>' in html


def test_parse_model_rejects_bad_table_row():
    bad = {
        "title": "x",
        "blocks": [{"type": "table", "header": ["a", "b"], "rows": [["only-one"]]}],
    }
    with pytest.raises(AppError) as e:
        parse_model(bad)
    assert e.value.code == "DOC_MODEL_INVALID"
    assert e.value.http_status == 400


def test_parse_model_rejects_empty_blocks():
    with pytest.raises(AppError) as e:
        parse_model({"title": "x", "blocks": []})
    assert e.value.code == "DOC_MODEL_INVALID"


def test_format_hints():
    assert format_from_text("导出成 PPT 给我") == "pptx"
    assert format_from_text("做个 Excel 台账") == "xlsx"
    assert format_from_text("生成一份 PDF") == "pdf"
    assert format_from_text("写成 markdown") == "md"
    assert format_from_text("生成一份周报") == "docx"


def test_normalize_format_rejects_unknown():
    with pytest.raises(AppError) as e:
        normalize_format("doc")
    assert e.value.code == "DOC_FORMAT_UNSUPPORTED"
    assert normalize_format(".PPTX") == "pptx"
