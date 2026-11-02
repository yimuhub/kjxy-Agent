"""文档结构化 Markdown 转换器（MarkItDown 兼容架构）：
支持 PDF / DOCX / XLSX / PPTX / CSV / HTML / TXT / MD 直接转化为 LLM 友好的结构化 Markdown。

核心策略：
1. 若运行环境安装了 ``markitdown``，优先使用 MarkItDown 引擎转换并做农业知识库去噪；
2. 内置零外部网络依赖的 MarkItDown 兼容结构化转换器（保留标题层级 #/##/###、Markdown 表格
   ``| col |``、列表结构，并自动剔除重复页眉页脚、乱码控制符与多余空行），大幅降低无效 Token 消耗。

返回 ``(full_text, page_count, pages)``。未知类型抛 ``UNSUPPORTED_FILE_TYPE(415)``。
扫描型/无文字 PDF 返回空文本（page_count 保留），由调用方决定如何处理。
"""

import csv
import html.parser
import io
import re
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from io import BytesIO

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.knowledge.storage import LocalStorageBackend

log = get_logger(__name__)

PDF_MIMES = {"application/pdf"}
DOCX_MIMES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
XLSX_MIMES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
PPTX_MIMES = {
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
CSV_MIMES = {
    "text/csv",
    "text/x-csv",
    "application/csv",
}
HTML_MIMES = {
    "text/html",
    "application/xhtml+xml",
}
TEXT_MIMES = {
    "text/plain",
    "text/markdown",
    *CSV_MIMES,
    *HTML_MIMES,
}


def _get_backend() -> LocalStorageBackend:
    return LocalStorageBackend()


def _read_text_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def clean_markdown_noise(text: str) -> str:
    """清洗文档噪点以降低 Token 消耗：
    - 移除不可见控制字符与目录前导点线（``...... 12``）；
    - 压缩连续空白与 3 行以上空行；
    - 保留段落双换行（``\\n\\n``）与 Markdown 表格/标题结构。
    """
    if not text:
        return ""
    # 统一换行符并剔除零宽字符与控制符
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\u200b-\u200f\ufeff]", "", cleaned)
    # 压缩目录点线（如 "第一章 概述 ................ 1" -> "第一章 概述 1"）
    cleaned = re.sub(r"(\.{4,}|…{3,}|-{5,})\s*(\d+)", r" \2", cleaned)
    lines: list[str] = []
    for raw_line in cleaned.split("\n"):
        line = re.sub(r"[ \t]{2,}", " ", raw_line).strip()
        # 剔除纯页码行（如 "- 12 -"、"Page 3 of 10"、"第 5 页"）
        if re.match(r"^[-—–]?\s*\d+\s*[-—–]?$", line):
            continue
        page_pat = r"^(第\s*\d+\s*页(\s*/\s*共\s*\d+\s*页)?|page\s+\d+(\s+of\s+\d+)?)$"
        if re.match(page_pat, line, re.I):
            continue
        lines.append(line)
    result = "\n".join(lines)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def _rows_to_markdown_table(rows: list[list[str]]) -> str:
    """二维单元格数组 → 紧凑 Markdown 表格（自动对齐列数、转义竖线与换行）。"""
    cleaned_rows: list[list[str]] = []
    max_cols = 0
    for row in rows:
        cells = [re.sub(r"\s+", " ", (c or "").replace("|", "\\|")).strip() for c in row]
        if any(cells):
            cleaned_rows.append(cells)
            max_cols = max(max_cols, len(cells))
    if not cleaned_rows or max_cols == 0:
        return ""
    norm = [r + [""] * (max_cols - len(r)) for r in cleaned_rows]
    header = "| " + " | ".join(norm[0]) + " |"
    sep = "| " + " | ".join(["---"] * max_cols) + " |"
    body = ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join([header, sep, *body])


def _infer_pdf_line_markdown(line: str) -> str:
    """启发式识别中文/通用文档章节标题并转为 Markdown 标题层级。"""
    s = line.strip()
    if not s or len(s) > 65:
        return s
    if s.startswith("#"):
        return s
    # 一级标题：第X章 / 第X部分 / 一、二、三、
    if re.match(r"^(第[一二三四五六七八九十百0-9]+[章篇部]|[一二三四五六七八九十]+、)\s*\S+", s):
        return f"# {s}"
    # 二级标题：第X节 / （一）（二） / 1.1 / 2.1（不含句号结尾的长句）
    if not s.endswith(("。", "；", "！", "？", ".", ";")) and re.match(
        r"^(第[一二三四五六七八九十0-9]+节|（[一二三四五六七八九十0-9]+）|\([一二三四五六七八九十0-9]+\)|\d+\.\d+(?!\.\d))\s*\S+",
        s,
    ):
        return f"## {s}"
    # 三级标题：1.1.1 / 2.3.4
    if not s.endswith(("。", "；", "！", "？", ".", ";")) and re.match(r"^\d+\.\d+\.\d+\s*\S+", s):
        return f"### {s}"
    return s


def _extract_pdf(data: bytes) -> tuple[str, int | None, list[str]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(BytesIO(data))
    except Exception as e:
        raise AppError(
            "DOCUMENT_PARSE_FAILED",
            "PDF 文档解析失败，请检查文件是否完好后重新上传。",
            http_status=422,
        ) from e

    raw_pages = [(page.extract_text() or "").strip() for page in reader.pages]
    page_count: int | None = len(raw_pages)

    # 识别并剔除跨页重复出现的页眉/页脚（>=3 页且出现率 >=60% 的首尾短行）
    repeated_headers: set[str] = set()
    if len(raw_pages) >= 3:
        edge_counter: Counter[str] = Counter()
        for p in raw_pages:
            plines = [ln.strip() for ln in p.splitlines() if ln.strip()]
            if plines:
                if len(plines[0]) <= 60:
                    edge_counter[plines[0]] += 1
                if len(plines) > 1 and len(plines[-1]) <= 60:
                    edge_counter[plines[-1]] += 1
        threshold = max(2, int(len(raw_pages) * 0.6))
        repeated_headers = {k for k, v in edge_counter.items() if v >= threshold}

    pages: list[str] = []
    for p in raw_pages:
        if not p:
            pages.append("")
            continue
        md_lines: list[str] = []
        for raw_ln in p.splitlines():
            ln = raw_ln.strip()
            if not ln or ln in repeated_headers:
                continue
            md_lines.append(_infer_pdf_line_markdown(ln))
        page_md = clean_markdown_noise("\n\n".join(md_lines))
        pages.append(page_md)

    full_text = "\n\n".join(p for p in pages if p).strip()
    if not full_text:
        log.info("pdf 提取结果为空，可能是扫描型 PDF")
    return full_text, page_count, pages


def _extract_docx(data: bytes) -> tuple[str, int | None, list[str]]:
    """MarkItDown 风格 DOCX → 结构化 Markdown（保留 Heading 层级、列表与表格）。"""
    from docx import Document
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        doc = Document(BytesIO(data))
    except Exception as e:
        raise AppError(
            "DOCUMENT_PARSE_FAILED",
            "Word 文档解析失败，请检查文件是否完好后重新上传。",
            http_status=422,
        ) from e

    blocks: list[str] = []
    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            para = Paragraph(child, doc)
            txt = (para.text or "").strip()
            if not txt:
                continue
            style_name = (para.style.name if para.style else "") or ""
            lower_style = style_name.lower()
            if lower_style.startswith("heading"):
                m = re.search(r"(\d+)", lower_style)
                level = min(max(int(m.group(1)), 1), 4) if m else 2
                blocks.append(f"{'#' * level} {txt}")
            elif lower_style == "title":
                blocks.append(f"# {txt}")
            elif "list" in lower_style:
                blocks.append(f"- {txt}")
            else:
                blocks.append(_infer_pdf_line_markdown(txt))
        elif isinstance(child, CT_Tbl):
            tbl = Table(child, doc)
            rows = [[cell.text.strip() for cell in row.cells] for row in tbl.rows]
            md_table = _rows_to_markdown_table(rows)
            if md_table:
                blocks.append(md_table)

    if not blocks:
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
        blocks = [_infer_pdf_line_markdown(p) for p in paragraphs]

    full_text = clean_markdown_noise("\n\n".join(blocks))
    return full_text, None, [full_text]


def _extract_csv(data: bytes) -> tuple[str, int | None, list[str]]:
    """CSV → Markdown 表格（第一行为表头，便于后续按行切块保留表头）。"""
    raw = _read_text_bytes(data).strip()
    if not raw:
        return "", None, [""]
    reader = csv.reader(io.StringIO(raw))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if len(rows) >= 2:
        md = _rows_to_markdown_table(rows)
        if md:
            return md, None, [md]
    return clean_markdown_noise(raw), None, [clean_markdown_noise(raw)]


class _HtmlToMarkdownParser(html.parser.HTMLParser):
    """轻量 HTML → Markdown 转换器（忽略 script/style，保留 h1-h4/p/li/table）。"""

    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._blocks: list[str] = []
        self._buf: list[str] = []
        self._heading = 0
        self._in_li = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg"):
            self._skip += 1
        elif t in ("h1", "h2", "h3", "h4"):
            self._flush()
            self._heading = int(t[1])
        elif t == "li":
            self._flush()
            self._in_li = True
        elif t in ("p", "div", "section", "article", "tr", "br"):
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("script", "style", "noscript", "svg") and self._skip > 0:
            self._skip -= 1
        elif t in ("h1", "h2", "h3", "h4", "li", "p", "div", "tr"):
            self._flush()
            self._heading = 0
            self._in_li = False

    def handle_data(self, data: str) -> None:
        if self._skip > 0:
            return
        s = data.strip()
        if s:
            self._buf.append(s)

    def _flush(self) -> None:
        if not self._buf:
            return
        text = " ".join(self._buf).strip()
        self._buf.clear()
        if not text:
            return
        if self._heading > 0:
            self._blocks.append(f"{'#' * self._heading} {text}")
        elif self._in_li:
            self._blocks.append(f"- {text}")
        else:
            self._blocks.append(text)

    def get_markdown(self) -> str:
        self._flush()
        return clean_markdown_noise("\n\n".join(self._blocks))


# 上传文档里的 XML 只允许纯数据：出现 DTD/实体声明即拒绝解析
_XML_FORBIDDEN_MARKUP = re.compile(rb"<\s*!?DOCTYPE|<\s*!?ENTITY", re.IGNORECASE)


def _xml_root(raw: bytes) -> ET.Element:
    """拒绝带 DTD/实体声明的 XML：OOXML 部件本是纯数据，实体展开是 billion laughs 类 DoS 的前提。"""
    if _XML_FORBIDDEN_MARKUP.search(raw):
        raise AppError(
            "DOCUMENT_PARSE_FAILED",
            "文档内含 XML 实体声明，出于安全限制不予解析，请另存为标准文件后重新上传。",
            http_status=422,
        )
    return ET.fromstring(raw)


def _extract_xlsx(data: bytes) -> tuple[str, int | None, list[str]]:
    """无第三方重依赖解析 .xlsx 为各工作表 Markdown 表格。"""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in zf.namelist():
                root = _xml_root(zf.read("xl/sharedStrings.xml"))
                for si in root.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}si"
                ):
                    texts = [
                        t.text or ""
                        for t in si.iter(
                            "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                        )
                    ]
                    shared.append("".join(texts))
            sheet_files = sorted(
                n
                for n in zf.namelist()
                if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
            )
            pages: list[str] = []
            for idx, sf in enumerate(sheet_files, start=1):
                sroot = _xml_root(zf.read(sf))
                rows: list[list[str]] = []
                for row_el in sroot.iter(
                    "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}row"
                ):
                    row_vals: list[str] = []
                    for c_el in row_el.iter(
                        "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"
                    ):
                        c_type = c_el.attrib.get("t", "")
                        v_el = c_el.find(
                            "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v"
                        )
                        val = v_el.text if v_el is not None and v_el.text else ""
                        if c_type == "s" and val.isdigit() and int(val) < len(shared):
                            val = shared[int(val)]
                        row_vals.append(val)
                    if any(row_vals):
                        rows.append(row_vals)
                md_table = _rows_to_markdown_table(rows)
                if md_table:
                    pages.append(f"## 工作表 {idx}\n\n{md_table}")
            full_text = "\n\n".join(pages).strip()
            return full_text, len(pages) or None, pages or [""]
    except AppError:
        raise
    except Exception as e:
        raise AppError(
            "DOCUMENT_PARSE_FAILED",
            "Excel 表格解析失败，请检查文件是否完好后重新上传。",
            http_status=422,
        ) from e


def _extract_pptx(data: bytes) -> tuple[str, int | None, list[str]]:
    """无第三方重依赖解析 .pptx 为按幻灯片分节的 Markdown。"""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            slide_files = sorted(
                n for n in zf.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            )
            pages: list[str] = []
            for idx, sf in enumerate(slide_files, start=1):
                root = _xml_root(zf.read(sf))
                texts = [
                    (t.text or "").strip()
                    for t in root.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t")
                    if (t.text or "").strip()
                ]
                if texts:
                    title = texts[0]
                    body = "\n".join(f"- {line}" for line in texts[1:])
                    slide_md = f"## 幻灯片 {idx}：{title}\n\n{body}".strip()
                    pages.append(slide_md)
            full_text = "\n\n".join(pages).strip()
            return full_text, len(pages) or None, pages or [""]
    except AppError:
        raise
    except Exception as e:
        raise AppError(
            "DOCUMENT_PARSE_FAILED",
            "PPT 演示文稿解析失败，请检查文件是否完好后重新上传。",
            http_status=422,
        ) from e


def _try_external_markitdown(data: bytes, ext: str) -> str | None:
    """若环境安装了微软官方 markitdown 包，优先尝试调用其转换流。"""
    try:
        from markitdown import MarkItDown  # type: ignore[import-not-found]

        md = MarkItDown()
        if hasattr(md, "convert_stream"):
            res = md.convert_stream(BytesIO(data), file_extension=ext)
            text = getattr(res, "text_content", None) or ""
            if text.strip():
                return clean_markdown_noise(text)
    except Exception:
        pass
    return None


def extract_text(storage_key: str, mime: str) -> tuple[str, int | None, list[str]]:
    data = _get_backend().open(storage_key).read()
    mime = (mime or "").split(";")[0].strip().lower()

    if mime in PDF_MIMES:
        return _extract_pdf(data)
    if mime in DOCX_MIMES:
        ext_md = _try_external_markitdown(data, ".docx")
        if ext_md:
            return ext_md, None, [ext_md]
        return _extract_docx(data)
    if mime in XLSX_MIMES:
        ext_md = _try_external_markitdown(data, ".xlsx")
        if ext_md:
            return ext_md, None, [ext_md]
        return _extract_xlsx(data)
    if mime in PPTX_MIMES:
        ext_md = _try_external_markitdown(data, ".pptx")
        if ext_md:
            return ext_md, None, [ext_md]
        return _extract_pptx(data)
    if mime in CSV_MIMES:
        return _extract_csv(data)
    if mime in HTML_MIMES:
        raw_html = _read_text_bytes(data)
        parser = _HtmlToMarkdownParser()
        parser.feed(raw_html)
        md = parser.get_markdown()
        return md, None, [md]
    if mime in TEXT_MIMES or mime.startswith("text/"):
        full_text = clean_markdown_noise(_read_text_bytes(data))
        return full_text, None, [full_text]
    raise AppError(
        "UNSUPPORTED_FILE_TYPE", f"不支持的文件类型：{mime or '未知'}。", http_status=415
    )
