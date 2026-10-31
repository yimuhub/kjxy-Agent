"""DocModel → Excel（openpyxl，MIT）。

口径：一份文档对应一个工作表；标题合并首行，标题块加粗放大，表格块写成真实表格。
另外做三件让表格「能继续用」的事：
- 数字/百分比/ISO 日期按真实类型写入（12、0.6、35%、2024-05-01），而不是全当字符串；
- 列宽按内容自适应（中文按 2 个字符宽度估算，10–40）；
- 单表工作簿自动冻结表头并加筛选，方便持续维护。
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from app.modules.docs.model import DocBlock, DocModel

_HEADER_FILL = PatternFill("solid", fgColor="DCE6F1")
_WRAP = Alignment(wrap_text=True, vertical="top")
_HEADER_ALIGN = Alignment(wrap_text=True, vertical="center", horizontal="center")
_TITLE_FONT = Font(name="微软雅黑", bold=True, size=14)
_HEAD_FONT = Font(name="微软雅黑", bold=True, size=11)
_BODY_FONT = Font(name="宋体", size=10.5)
_THIN = Side(style="thin", color="B7B7B7")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)

_NUMBER_RE = re.compile(r"^[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
_PERCENT_RE = re.compile(r"^([+-]?(?:\d+)(?:\.\d+)?)%$")
_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d")


def _coerce(value: str) -> tuple[Any, str | None]:
    """把明显是数字/百分比/日期的字符串转成真实类型；返回 (值, 数字格式)。"""
    text = (value or "").strip()
    if not text:
        return value, None
    percent = _PERCENT_RE.match(text)
    if percent:
        return float(percent.group(1)) / 100, "0.00%"
    if _NUMBER_RE.match(text):
        clean = text.replace(",", "")
        number = float(clean) if "." in clean else int(clean)
        return number, "0.00" if isinstance(number, float) else "0"
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        if isinstance(parsed, date):
            return parsed, "yyyy-mm-dd"
    return value, None


def _write_table(ws, block: DocBlock, row: int) -> tuple[int, int, int]:
    """写一张表；返回 (下一行, 表头行, 结束行)。"""
    header_row = row
    for i, value in enumerate(block.header, start=1):
        cell = ws.cell(row=row, column=i, value=value)
        cell.font = _HEAD_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = _HEADER_ALIGN
        cell.border = _BORDER
    row += 1
    for data_row in block.rows:
        for i, value in enumerate(data_row, start=1):
            parsed, number_format = _coerce(value)
            cell = ws.cell(row=row, column=i, value=parsed)
            cell.font = _BODY_FONT
            cell.alignment = _WRAP
            cell.border = _BORDER
            if number_format:
                cell.number_format = number_format
        row += 1
    return row + 1, header_row, row - 1


def _display_width(value: Any) -> int:
    text = "" if value is None else str(value)
    return sum(2 if ord(ch) > 127 else 1 for ch in text)


def _autosize(ws, max_cols: int) -> None:
    for col in range(1, max_cols + 1):
        width = 0
        for row in ws.iter_rows(min_col=col, max_col=col):
            for cell in row:
                width = max(width, _display_width(cell.value))
        ws.column_dimensions[get_column_letter(col)].width = max(10, min(40, width + 2))


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
    title_cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 24
    row = 2
    if model.subject:
        ws.cell(row=row, column=1, value=model.subject).alignment = _WRAP
        row += 1
    if model.author:
        ws.cell(row=row, column=1, value=f"作者：{model.author}")
        row += 1
    row += 1

    tables: list[tuple[int, int]] = []
    for block in model.blocks:
        if block.type == "heading":
            cell = ws.cell(row=row, column=1, value=block.text)
            cell.font = (
                _HEAD_FONT if block.level <= 1 else Font(name="微软雅黑", bold=True, size=10.5)
            )
            row += 1
        elif block.type == "paragraph":
            cell = ws.cell(row=row, column=1, value=block.text)
            cell.alignment = _WRAP
            cell.font = _BODY_FONT
            row += 2
        elif block.type in ("bullets", "numbered"):
            for idx, item in enumerate(block.items, start=1):
                prefix = "• " if block.type == "bullets" else f"{idx}. "
                cell = ws.cell(row=row, column=1, value=f"{prefix}{item}")
                cell.alignment = _WRAP
                cell.font = _BODY_FONT
                row += 1
            row += 1
        elif block.type == "code":
            cell = ws.cell(row=row, column=1, value=block.text)
            cell.alignment = _WRAP
            cell.font = Font(name="Consolas", size=9)
            row += 2
        elif block.type == "table":
            row, header_row, end_row = _write_table(ws, block, row)
            tables.append((header_row, end_row))
        elif block.type == "pagebreak":
            row += 1

    _autosize(ws, max_cols)
    if len(tables) == 1:
        header_row, end_row = tables[0]
        ws.freeze_panes = f"A{header_row + 1}"
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(max_cols)}{end_row}"
    ws.sheet_view.showGridLines = True

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
