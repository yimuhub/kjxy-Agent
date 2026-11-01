"""正式写作 Word 版式：公文/申报/投标/简报与 general 回归。"""

from __future__ import annotations

import io
import zipfile

from app.modules.docs.model import parse_model
from app.modules.docs.render_docx import to_docx
from docx import Document

EMU_PER_CM = 360_000


def _model(style: str):
    return parse_model(
        {
            "title": "关于开展春季农田水利建设工作的通知",
            "subject": "春季农田水利建设",
            "author": "科技小院",
            "doc_style": style,
            "blocks": [
                {"type": "heading", "level": 1, "text": "一、总体要求"},
                {
                    "type": "paragraph",
                    "text": "为做好春季农田水利建设，现将有关事项通知如下。",
                },
                {
                    "type": "table",
                    "header": ["序号", "事项", "完成时限"],
                    "rows": [["1", "检修泵站", "3 月底"], ["2", "清理渠道", "4 月初"]],
                },
                {"type": "heading", "level": 2, "text": "（一）责任分工"},
                {"type": "bullets", "items": ["农业农村部门牵头", "各小院具体落实"]},
            ],
        }
    )


def _xml(data: bytes, name: str = "word/document.xml") -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(name).decode("utf-8")


def test_all_doc_styles_open_and_keep_a4() -> None:
    for style in ("general", "official_cn", "grant", "tender", "brief"):
        data = to_docx(_model(style))
        assert len(data) > 2000
        doc = Document(io.BytesIO(data))
        section = doc.sections[0]
        # A4：21cm × 29.7cm，允许 0.1cm 误差
        assert abs(section.page_width - 21 * EMU_PER_CM) < 0.1 * EMU_PER_CM
        assert abs(section.page_height - 29.7 * EMU_PER_CM) < 0.1 * EMU_PER_CM
        assert section.top_margin > 0
        assert section.left_margin > 0


def test_official_cn_uses_official_fonts_and_page_number() -> None:
    data = to_docx(_model("official_cn"))
    body = _xml(data)
    footer = _xml(data, "word/footer1.xml")
    assert 'w:eastAsia="仿宋"' in body or "w:eastAsia='仿宋'" in body
    assert 'w:eastAsia="黑体"' in body or "w:eastAsia='黑体'" in body
    assert "PAGE" in footer
    # 公文版心比普通文档窄：左右页边距至少 2.6cm
    doc = Document(io.BytesIO(data))
    assert doc.sections[0].left_margin >= 2.5 * EMU_PER_CM
    assert doc.sections[0].right_margin >= 2.5 * EMU_PER_CM


def test_grant_and_tender_add_cover_and_header() -> None:
    for style in ("grant", "tender"):
        data = to_docx(_model(style))
        body = _xml(data)
        assert "宋体" in body
        # 正式版式必须有封面/目录或分页结构，且至少一个表格
        assert "w:tbl" in body
        assert "w:br" in body or "w:lastRenderedPageBreak" in body
        footer = _xml(data, "word/footer1.xml")
        assert "PAGE" in footer


def test_general_style_unchanged() -> None:
    data = to_docx(_model("general"))
    body = _xml(data)
    assert "宋体" in body
    assert "PAGE" in _xml(data, "word/footer1.xml")
