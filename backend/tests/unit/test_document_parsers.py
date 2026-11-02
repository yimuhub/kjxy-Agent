"""上传文档解析的 XML 安全闸（F-1：实体炸弹）。"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest
from app.core.errors import AppError
from app.modules.knowledge.parsers import _extract_pptx, _extract_xlsx

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

# billion laughs：内部实体递归展开，标准库解析会打满内存/CPU
_ENTITY_BOMB = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE sst [
  <!ENTITY lol "lol">
  <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
] >
<sst xmlns="{NS}"><si><t>&lol3;</t></si></sst>
"""


def _zip(entries: dict[str, str]) -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return buf.getvalue()


def test_xlsx_with_entity_bomb_is_rejected_before_expansion() -> None:
    data = _zip(
        {
            "xl/sharedStrings.xml": _ENTITY_BOMB,
            "xl/worksheets/sheet1.xml": (
                f'<worksheet xmlns="{NS}"><row r="1">'
                '<c r="A1" t="s"><v>0</v></c></row></worksheet>'
            ),
        }
    )
    with pytest.raises(AppError) as caught:
        _extract_xlsx(data)
    assert caught.value.code == "DOCUMENT_PARSE_FAILED"
    assert "实体声明" in caught.value.message


def test_pptx_with_entity_bomb_is_rejected() -> None:
    data = _zip(
        {
            "ppt/slides/slide1.xml": _ENTITY_BOMB.replace(
                f'xmlns="{NS}"', 'xmlns="http://schemas.openxmlformats.org/drawingml/2006/main"'
            )
        }
    )
    with pytest.raises(AppError) as caught:
        _extract_pptx(data)
    assert "实体声明" in caught.value.message


def test_plain_cell_text_mentioning_doctype_still_parses() -> None:
    """单元格里的转义文本不是标记，不能误伤正常文件。"""
    data = _zip(
        {
            "xl/sharedStrings.xml": (
                f'<sst xmlns="{NS}"><si><t>&lt;!DOCTYPE 样例&lt;/t&gt;</t></si>'
                f"<si><t>产量</t></si></sst>"
            ),
            "xl/worksheets/sheet1.xml": (
                f'<worksheet xmlns="{NS}"><row r="1">'
                '<c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c>'
                '<c r="C1"><v>320</v></c></row></worksheet>'
            ),
        }
    )
    text, pages, _ = _extract_xlsx(data)
    assert pages == 1
    assert "产量" in text
    assert "320" in text
    assert "!DOCTYPE" in text
