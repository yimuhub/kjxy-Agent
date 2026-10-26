"""DocModel → Excel（openpyxl，MIT）。

口径：一份文档对应一个工作表；标题合并首行，标题块加粗放大，表格块写成真实表格
（表头加粗+底色），段落自动换行。复杂公式/多 sheet 不在本期范围。
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.modules.docs.model import DocBlock, DocModel

_HEADER_FILL = PatternFill("solid", fgColor="DCE6F1")
_WRAP = Alignment(wrap_text=True, vertical="top")
_TITLE_FONT = Font(bold=True, size=14)
_H2_FONT = Font(bold=True, size=12)
_H3_FONT = Font(bold=True, size=11)


def _write_table(ws, block: DocBlock, row: int) -> int:
    for i, value in enumerate(block.header, start=1):
        cell = ws.cell(row=row, column=i, value=value)
        cell.font = Font(bold=True)
        cell.fill = _HEADER_FILL
        cell.alignment = _WRAP
    row += 1
    for data_row in block.rows:
        for i, value in enumerate(data_row, start=1):
            ws.cell(row=row, column=i, value=value).alignment = _WRAP
        row += 1
    # 表格下方留一行空白，避免与下一块粘连
    return row + 1


def to_xlsx(model: DocModel) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "文档"
    max_cols = max([len(b.header) for b in model.blocks if b.type == "table"] or [4])
    for i in range(1, max_cols + 1):
        ws.column_dimensions[get_column_letter(i)].width = 22

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_cols)
    title_cell = ws.cell(row=1, column=1, value=model.title)
    title_cell.font = _TITLE_FONT
    row = 2
    if model.subject:
        ws.cell(row=row, column=1, value=model.subject).alignment = _WRAP
        row += 1
    if model.author:
        ws.cell(row=row, column=1, value=f"作者：{model.author}")
        row += 1
    row += 1

    for block in model.blocks:
        if block.type == "heading":
            cell = ws.cell(row=row, column=1, value=block.text)
            cell.font = _H2_FONT if block.level <= 1 else _H3_FONT
            row += 1
        elif block.type == "paragraph":
            ws.cell(row=row, column=1, value=block.text).alignment = _WRAP
            row += 2
        elif block.type in ("bullets", "numbered"):
            for idx, item in enumerate(block.items, start=1):
                prefix = "• " if block.type == "bullets" else f"{idx}. "
                ws.cell(row=row, column=1, value=f"{prefix}{item}").alignment = _WRAP
                row += 1
            row += 1
        elif block.type == "code":
            ws.cell(row=row, column=1, value=block.text).alignment = _WRAP
            row += 2
        elif block.type == "table":
            row = _write_table(ws, block, row)
        elif block.type == "pagebreak":
            row += 1

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
