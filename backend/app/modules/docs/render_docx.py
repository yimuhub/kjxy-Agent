"""DocModel → Word（python-docx，MIT；项目原本就用于 docx 文本抽取）。

两种路径：
- ``doc_style="general"``（默认）：保持原有排版不变——A4、页边距 2.54cm、宋体
  10.5pt、1.5 倍行距、首行缩进 2 字符、黑体分级标题、网格表格、页脚 PAGE 域；
- 正式写作版式：按 ``doc_style`` 分派到公文（official_cn）/ 申报（grant）/
  投标（tender）/ 一页纸（brief），覆盖页边距、字体字号、固定行距、封面、
  目录占位、页眉、评分索引表、落款右对齐与红色分隔线。

中文字体口径：
- 每个 run 都同时写 ``w:rFonts`` 的 ``w:eastAsia``（Word 只认这个属性显示中文），
  并写 ``w:ascii``/``w:hAnsi``，避免中文回落到西文字体；
- ``FONT_STACKS`` 记录「首选 → 系统替代」回退链；DOCX 一个 run 只能写一个字体名，
  所以默认取通用可用的首位（宋体/仿宋/黑体/楷体），不假设机器装了方正字体；
  需要方正小标宋等商业字体时用环境变量覆盖，如
  ``DOCX_FONT_OFFICIAL_TITLE=方正小标宋简体``。

只做版式，不改内容：正文文字、块顺序、表格数据全部来自 DocModel。
"""

from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass, field
from datetime import date

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from app.modules.docs.model import DocBlock, DocModel

#: 字体回退链：首个是默认写入的 eastAsia 名，其后为同族替代字体。
#: 没有装首选字体的机器由 Word 按其字体替换表回退，默认值刻意选通用字体。
FONT_STACKS: dict[str, tuple[str, ...]] = {
    "song": ("宋体", "Songti SC", "SimSun", "STSong"),
    "official_title": ("宋体", "方正小标宋简体", "Songti SC", "SimSun"),
    "fangsong": ("仿宋", "STFangsong", "FangSong", "仿宋_GB2312"),
    "hei": ("黑体", "Heiti SC", "SimHei"),
    "kai": ("楷体", "Kaiti SC", "KaiTi", "STKaiti"),
}

#: 表格统一表头底纹（沿用原通用版式的浅蓝）
_HEADER_FILL = "DCE6F1"
_BODY_FONT = "宋体"
_HEAD_FONT = "黑体"
_BODY_SIZE = Pt(10.5)

#: 公文红色分隔线（红头文件的横线），用段落底边框实现，不引入新依赖
_RED_RULE_COLOR = "FF0000"

#: 落款/成文日期的识别（正式版式右对齐）：显式落款词、盖章标记或纯日期行
_SIGNATURE_PREFIX_RE = re.compile(
    r"^\s*(落款|署名|发文单位|申报单位|投标单位|编制单位|联系人[:：])"
)
_SIGNATURE_STAMP_RE = re.compile(r"^\s*.*[（(]\s*盖章\s*[)）]\s*$")
_SIGNATURE_DATE_RE = re.compile(r"^\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日\s*$")

#: 已有编号的标题不再重复编号
_EXISTING_NUMBER_RE = re.compile(
    r"^\s*(第[一二三四五六七八九十百\d]+[章节篇]"
    r"|[一二三四五六七八九十百]+、"
    r"|[（(][一二三四五六七八九十百\d]+[)）]"
    r"|\d+([.．、]\d+)*[.．、\s])"
)

_CN_DIGITS = "零一二三四五六七八九"

#: w:pBdr 之后允许出现的 pPr 子元素（按 OOXML 顺序插入，避免 Word 报文件损坏）
_PPR_SUCCESSORS_AFTER_BORDER: tuple[str, ...] = (
    "w:shd",
    "w:tabs",
    "w:suppressAutoHyphens",
    "w:kinsoku",
    "w:wordWrap",
    "w:overflowPunct",
    "w:topLinePunct",
    "w:autoSpaceDE",
    "w:autoSpaceDN",
    "w:bidi",
    "w:adjustRightInd",
    "w:snapToGrid",
    "w:spacing",
    "w:ind",
    "w:contextualSpacing",
    "w:mirrorIndents",
    "w:suppressOverlap",
    "w:jc",
    "w:textDirection",
    "w:textAlignment",
    "w:textboxTightWrap",
    "w:outlineLvl",
    "w:divId",
    "w:cnfStyle",
    "w:rPr",
    "w:sectPr",
    "w:pPrChange",
)


def _font(key: str) -> str:
    """取字体名：环境变量可覆盖（DOCX_FONT_<KEY>），否则用回退链首位。"""
    override = os.environ.get(f"DOCX_FONT_{key.upper()}")
    if override and override.strip():
        return override.strip()
    stack = FONT_STACKS.get(key) or FONT_STACKS["song"]
    return stack[0]


def _set_run_font(run, name: str, size: Pt, *, bold: bool = False) -> None:
    """run 级字体：同时写 ascii/hAnsi/eastAsia，保证中文不回落到西文字体。"""
    run.font.name = name
    run.font.size = size
    run.bold = bold
    run.font.color.rgb = RGBColor(0, 0, 0)
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    rfonts.set(qn("w:eastAsia"), name)
    rfonts.set(qn("w:hint"), "eastAsia")


def _apply_font(run, key: str, size_pt: float, *, bold: bool = False) -> None:
    """按字体链 key 设置 run 字体。"""
    _set_run_font(run, _font(key), Pt(size_pt), bold=bold)


def _set_style_font(style, name: str, size: Pt, *, bold: bool = False) -> None:
    style.font.name = name
    style.font.size = size
    style.font.bold = bold
    style.font.color.rgb = RGBColor(0, 0, 0)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), name)
    rfonts.set(qn("w:hAnsi"), name)
    rfonts.set(qn("w:eastAsia"), name)


def _shade_cell(cell, color: str) -> None:
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), color)
    tc_pr.append(shd)


def _add_field(paragraph, instruction: str, placeholder: str = "1"):
    """在段落里插入 Word 域（PAGE / TOC），返回承载域的 run。"""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for node in (begin, instr, separate, text, end):
        run._r.append(node)
    return run


def _add_page_number(doc, *, labeled: bool = False, font_size: float = 9.0) -> None:
    """页脚居中页码（PAGE 域）；labeled=True 时输出「第 X 页」。"""
    footer = doc.sections[0].footer
    para = footer.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if labeled:
        prefix = para.add_run("第 ")
        _set_run_font(prefix, _font("song"), Pt(font_size))
        _add_field(para, "PAGE")
        suffix = para.add_run(" 页")
        _set_run_font(suffix, _font("song"), Pt(font_size))
        for run in para.runs:
            _set_run_font(run, _font("song"), Pt(font_size))
        return
    run = _add_field(para, "PAGE")
    _set_run_font(run, _BODY_FONT, Pt(font_size))


def _add_red_rule(doc) -> None:
    """标题下的红色分隔线：空段落 + 红色下边框（不依赖图片/形状）。"""
    para = doc.add_paragraph()
    p_pr = para._p.get_or_add_pPr()
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "18")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), _RED_RULE_COLOR)
    borders.append(bottom)
    p_pr.insert_element_before(borders, *_PPR_SUCCESSORS_AFTER_BORDER)
    para.paragraph_format.space_before = Pt(6)
    para.paragraph_format.space_after = Pt(12)


def _add_toc_placeholder(doc, *, heading_font: str = "hei") -> None:
    """目录占位：标题 + TOC 域（在 Word 里「更新域」即生成真实目录）。"""
    para = doc.add_paragraph()
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run("目　录")
    _apply_font(run, heading_font, 16, bold=True)
    toc_para = doc.add_paragraph()
    _add_field(toc_para, 'TOC \\o "1-3" \\h \\z \\u', "（在 Word 中右键选择「更新域」生成目录）")
    for run in toc_para.runs:
        _apply_font(run, "song", 12)
    doc.add_page_break()


def _today_cn() -> str:
    today = date.today()
    return f"{today.year}年{today.month}月{today.day}日"


def _cn_number(value: int) -> str:
    """1-99 的中文数字（章节编号用；超出范围回落阿拉伯数字）。"""
    if value <= 0 or value >= 100:
        return str(value)
    if value < 10:
        return _CN_DIGITS[value]
    tens, ones = divmod(value, 10)
    head = "" if tens == 1 else _CN_DIGITS[tens]
    return f"{head}十{_CN_DIGITS[ones] if ones else ''}"


@dataclass
class _Numberer:
    """章节编号器：official=一、/（一）/1.；tender=第1章/1.1/1.1.1。"""

    mode: str = ""
    counters: list[int] = field(default_factory=lambda: [0, 0, 0])

    def label(self, level: int, text: str) -> str:
        if not self.mode:
            return text
        idx = max(0, min(level, 3) - 1)
        self.counters[idx] += 1
        for deeper in range(idx + 1, 3):
            self.counters[deeper] = 0
        if _EXISTING_NUMBER_RE.match(text):
            return text
        if self.mode == "official":
            if idx == 0:
                return f"{_cn_number(self.counters[0])}、{text}"
            if idx == 1:
                return f"（{_cn_number(self.counters[1])}）{text}"
            return f"{self.counters[2]}.{text}"
        if idx == 0:
            return f"第{self.counters[0]}章　{text}"
        if idx == 1:
            return f"{self.counters[0]}.{self.counters[1]} {text}"
        return f"{self.counters[0]}.{self.counters[1]}.{self.counters[2]} {text}"


@dataclass(frozen=True)
class _Profile:
    """一种 Word 版式的排版参数（general 也复用它，保持行为一致）。"""

    body_font: str
    body_size: float
    title_font: str
    title_size: float
    title_bold: bool = True
    heading_fonts: tuple[str, str, str] = ("hei", "hei", "hei")
    heading_sizes: tuple[float, float, float] = (16.0, 14.0, 12.0)
    line_exact: float | None = None
    line_multiple: float | None = 1.5
    space_after: float = 6.0
    indent_chars: int = 2
    table_size: float = 10.5
    table_header_fill: str = _HEADER_FILL
    numbering: str = ""
    signature_align: bool = False
    header_text: str = ""
    labeled_page: bool = False
    #: True=沿用旧行为（标题走 +1 级样式、正文不写 run 级字体、不强制 autofit）
    legacy: bool = False


_GENERAL = _Profile(
    body_font="song",
    body_size=10.5,
    title_font="hei",
    title_size=22.0,
    heading_fonts=("hei", "hei", "hei"),
    heading_sizes=(16.0, 14.0, 12.0),
    line_multiple=1.5,
    space_after=6.0,
    indent_chars=2,
    table_size=10.5,
    legacy=True,
)

_STYLES: dict[str, _Profile] = {
    "official_cn": _Profile(
        body_font="fangsong",
        body_size=16.0,
        title_font="official_title",
        title_size=22.0,
        heading_fonts=("hei", "kai", "fangsong"),
        heading_sizes=(16.0, 16.0, 16.0),
        line_exact=28.0,
        line_multiple=None,
        space_after=0.0,
        indent_chars=2,
        table_size=12.0,
        numbering="official",
        signature_align=True,
        labeled_page=True,
    ),
    "grant": _Profile(
        body_font="song",
        body_size=12.0,
        title_font="song",
        title_size=22.0,
        heading_fonts=("hei", "hei", "hei"),
        heading_sizes=(16.0, 14.0, 12.0),
        line_multiple=1.5,
        space_after=6.0,
        indent_chars=2,
        table_size=10.5,
        header_text="项目申报材料",
        labeled_page=True,
    ),
    "tender": _Profile(
        body_font="song",
        body_size=12.0,
        title_font="song",
        title_size=22.0,
        heading_fonts=("hei", "hei", "hei"),
        heading_sizes=(16.0, 14.0, 12.0),
        line_multiple=1.5,
        space_after=6.0,
        indent_chars=2,
        table_size=10.5,
        numbering="tender",
        header_text="投标文件",
        labeled_page=True,
    ),
    "brief": _Profile(
        body_font="song",
        body_size=12.0,
        title_font="hei",
        title_size=18.0,
        heading_fonts=("hei", "hei", "hei"),
        heading_sizes=(14.0, 12.0, 12.0),
        line_multiple=1.15,
        space_after=3.0,
        indent_chars=0,
        table_size=10.5,
        labeled_page=True,
    ),
}

#: 公文页边距（上/下/左/右 cm）；其余正式版式沿用 A4 + 2.54cm
_OFFICIAL_MARGINS: tuple[float, float, float, float] = (3.7, 3.5, 2.8, 2.6)
_DEFAULT_MARGINS: tuple[float, float, float, float] = (2.54, 2.54, 2.54, 2.54)

#: 带封面的版式（封面不显示页眉页码）
_COVER_STYLES = frozenset({"grant", "tender"})


def _setup_section(doc, style: str) -> None:
    section = doc.sections[0]
    section.page_height = Cm(29.7)
    section.page_width = Cm(21.0)
    top, bottom, left, right = (
        _OFFICIAL_MARGINS if style == "official_cn" else _DEFAULT_MARGINS
    )
    section.top_margin = Cm(top)
    section.bottom_margin = Cm(bottom)
    section.left_margin = Cm(left)
    section.right_margin = Cm(right)
    if style in _COVER_STYLES:
        section.different_first_page_header_footer = True


def _setup_styles(doc, profile: _Profile) -> None:
    _set_style_font(doc.styles["Normal"], _font(profile.body_font), Pt(profile.body_size))
    _set_style_font(
        doc.styles["Title"], _font(profile.title_font), Pt(profile.title_size), bold=True
    )
    for index, (font_key, size) in enumerate(
        zip(profile.heading_fonts, profile.heading_sizes, strict=False)
    ):
        name = f"Heading {index + 1}"
        _set_style_font(doc.styles[name], _font(font_key), Pt(size), bold=True)
        doc.styles[name].paragraph_format.space_before = Pt(12)
        doc.styles[name].paragraph_format.space_after = Pt(6)


def _set_props(doc, model: DocModel) -> None:
    props = doc.core_properties
    props.title = model.title
    if model.subject:
        props.subject = model.subject
    if model.author:
        props.author = model.author


def _set_header(doc, text: str, profile: _Profile) -> None:
    header = doc.sections[0].header
    para = header.paragraphs[0]
    para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = para.add_run(text)
    _set_run_font(run, _font(profile.body_font), Pt(9))


def _first_line_chars(para, chars: int, size_pt: float) -> None:
    """首行缩进 N 字符：写 firstLineChars（Word 按字符宽度换算）并给 twips 兜底。"""
    if chars <= 0:
        return
    p_pr = para._p.get_or_add_pPr()
    ind = p_pr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        p_pr.insert_element_before(
            ind,
            "w:contextualSpacing",
            "w:mirrorIndents",
            "w:suppressOverlap",
            "w:jc",
            "w:textDirection",
            "w:textAlignment",
            "w:textboxTightWrap",
            "w:outlineLvl",
            "w:divId",
            "w:cnfStyle",
            "w:rPr",
            "w:sectPr",
            "w:pPrChange",
        )
    ind.set(qn("w:firstLineChars"), str(chars * 100))
    ind.set(qn("w:firstLine"), str(int(chars * size_pt * 20)))


def _style_paragraph(para, profile: _Profile, *, indent: bool = True) -> None:
    fmt = para.paragraph_format
    if profile.line_exact is not None:
        fmt.line_spacing = Pt(profile.line_exact)
    elif profile.line_multiple is not None:
        fmt.line_spacing = profile.line_multiple
    fmt.space_after = Pt(profile.space_after)
    if indent and profile.indent_chars:
        fmt.first_line_indent = Pt(profile.body_size * profile.indent_chars)
        _first_line_chars(para, profile.indent_chars, profile.body_size)


def _is_signature(text: str) -> bool:
    value = (text or "").strip()
    if not value:
        return False
    return bool(
        _SIGNATURE_PREFIX_RE.match(value)
        or _SIGNATURE_STAMP_RE.match(value)
        or _SIGNATURE_DATE_RE.match(value)
    )


def _add_table(doc, block: DocBlock, profile: _Profile) -> None:
    """表格：网格线 + 表头底纹 + 列宽自适应 + 表头加粗居中。"""
    table = doc.add_table(rows=1, cols=len(block.header))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    if not profile.legacy:
        table.autofit = True
        table.allow_autofit = True
    for i, cell in enumerate(table.rows[0].cells):
        cell.text = block.header[i]
        _shade_cell(cell, profile.table_header_fill)
        para = cell.paragraphs[0]
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        if not profile.legacy:
            para.paragraph_format.space_after = Pt(0)
        for run in para.runs:
            _set_run_font(run, _font("hei"), Pt(profile.table_size), bold=True)
    for row in block.rows:
        cells = table.add_row().cells
        for i, value in enumerate(row):
            cells[i].text = value
            if not profile.legacy:
                cells[i].paragraphs[0].paragraph_format.space_after = Pt(0)
            for run in cells[i].paragraphs[0].runs:
                _set_run_font(run, _font(profile.body_font), Pt(profile.table_size))


def _render_blocks(
    doc,
    model: DocModel,
    profile: _Profile,
    *,
    numberer: _Numberer | None = None,
) -> None:
    """按块类型渲染正文（general 与正式版式共用，靠 profile 控制差异）。"""
    heading_offset = 1 if profile.legacy else 0
    for block in model.blocks:
        if block.type == "heading":
            text = (
                numberer.label(block.level, block.text) if numberer is not None else block.text
            )
            heading = doc.add_heading(text, level=min(block.level + heading_offset, 9))
            if not profile.legacy:
                font_key = profile.heading_fonts[min(block.level, 3) - 1]
                size = profile.heading_sizes[min(block.level, 3) - 1]
                for run in heading.runs:
                    _apply_font(run, font_key, size, bold=True)
        elif block.type == "paragraph":
            para = doc.add_paragraph(block.text)
            signature = profile.signature_align and _is_signature(block.text)
            _style_paragraph(para, profile, indent=not signature)
            if signature:
                para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            if not profile.legacy:
                for run in para.runs:
                    _apply_font(run, profile.body_font, profile.body_size)
        elif block.type == "bullets":
            for item in block.items:
                para = doc.add_paragraph(item, style="List Bullet")
                _style_paragraph(para, profile, indent=False)
                if not profile.legacy:
                    for run in para.runs:
                        _apply_font(run, profile.body_font, profile.body_size)
        elif block.type == "numbered":
            for item in block.items:
                para = doc.add_paragraph(item, style="List Number")
                _style_paragraph(para, profile, indent=False)
                if not profile.legacy:
                    for run in para.runs:
                        _apply_font(run, profile.body_font, profile.body_size)
        elif block.type == "code":
            para = doc.add_paragraph()
            run = para.add_run(block.text)
            _set_run_font(run, "Courier New", Pt(9))
        elif block.type == "table":
            _add_table(doc, block, profile)
            doc.add_paragraph()
        elif block.type == "pagebreak":
            doc.add_page_break()


def _render_formal_title(doc, model: DocModel, profile: _Profile) -> None:
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.paragraph_format.space_before = Pt(6)
    title.paragraph_format.space_after = Pt(12)
    run = title.add_run(model.title)
    _apply_font(run, profile.title_font, profile.title_size, bold=profile.title_bold)
    if model.subject:
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        subject = para.add_run(model.subject)
        _apply_font(subject, profile.body_font, profile.body_size)
    if model.author:
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        author = para.add_run(model.author)
        _apply_font(author, profile.body_font, profile.body_size)


def _render_cover(doc, model: DocModel, *, label: str) -> None:
    """封面：标题（居中）+ 单位 + 编制日期，之后分页进入目录/正文。"""
    for _ in range(4):
        doc.add_paragraph()
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = title.add_run(model.title)
    _apply_font(run, "hei", 26, bold=True)
    if model.subject:
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        subject = para.add_run(model.subject)
        _apply_font(subject, "song", 16)
    for _ in range(3):
        doc.add_paragraph()
    unit = doc.add_paragraph()
    unit.alignment = WD_ALIGN_PARAGRAPH.CENTER
    unit_text = model.author.strip() or "（待填写）"
    prefix = unit.add_run(f"{label}：")
    _apply_font(prefix, "song", 14)
    unit_name = unit.add_run(unit_text)
    _apply_font(unit_name, "song", 14)
    made = doc.add_paragraph()
    made.alignment = WD_ALIGN_PARAGRAPH.CENTER
    made_text = made.add_run(f"编制日期：{_today_cn()}")
    _apply_font(made_text, "song", 14)
    doc.add_page_break()


def _add_scoring_index(doc, profile: _Profile) -> None:
    """投标版式：评分索引占位表（只给结构，不预填评分标准与分值）。"""
    heading = doc.add_heading("评分索引表", level=1)
    for run in heading.runs:
        _apply_font(run, profile.heading_fonts[0], profile.heading_sizes[0], bold=True)
    block = DocBlock(
        type="table",
        header=["序号", "评分项（按招标文件填写）", "分值", "响应章节", "页码", "备注"],
        rows=[["1", "（待按招标文件评分办法逐条填写）", "", "", "", ""]],
    )
    _add_table(doc, block, profile)
    doc.add_paragraph()


def _render_general(doc, model: DocModel) -> None:
    """generic 版式：与 3.34 及之前完全一致的排版路径（回归基线）。"""
    _setup_styles(doc, _GENERAL)
    title = doc.add_heading(model.title, level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if model.subject:
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = para.add_run(model.subject)
        _set_run_font(run, _HEAD_FONT, Pt(14))
    if model.author:
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = para.add_run(f"作者：{model.author}")
        _set_run_font(run, _BODY_FONT, Pt(10.5))
    _render_blocks(doc, model, _GENERAL)
    _add_page_number(doc)


def _render_formal(doc, model: DocModel, style: str, profile: _Profile) -> None:
    _setup_styles(doc, profile)
    if style in _COVER_STYLES:
        _render_cover(doc, model, label="申报单位" if style == "grant" else "投标单位")
        _add_toc_placeholder(doc)
    else:
        _render_formal_title(doc, model, profile)
        if style == "official_cn":
            # 公文红头分隔线：标题（含副题）之下的红色横线
            _add_red_rule(doc)
    if profile.header_text:
        _set_header(doc, profile.header_text, profile)
    if style == "tender":
        _add_scoring_index(doc, profile)
    _render_blocks(doc, model, profile, numberer=_Numberer(profile.numbering))
    _add_page_number(doc, labeled=profile.labeled_page, font_size=9)


def to_docx(model: DocModel) -> bytes:
    """DocModel → docx 字节流；按 ``model.doc_style`` 分派版式。"""
    style = str(getattr(model, "doc_style", "general") or "general")
    if style not in _STYLES:
        style = "general"
    doc = Document()
    _setup_section(doc, style)
    _set_props(doc, model)
    if style == "general":
        _render_general(doc, model)
    else:
        _render_formal(doc, model, style, _STYLES[style])
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


__all__ = ["FONT_STACKS", "to_docx"]
