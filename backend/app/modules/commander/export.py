"""指挥官月度迭代报告导出（v3.56）：report_md → Word（复用 docs 渲染管线）。

不做通用 Markdown 解析器：只处理月报生成时约定的结构（`#`/`##` 标题、
`-` 列表、`1.` 编号列表、普通段落），未知行按段落兜底——报告是我们自己
生成的，结构可控；解析失败宁可全变段落也不能丢内容。
"""

from __future__ import annotations

import re
from typing import Any

from app.modules.docs.model import DocBlock, DocModel

_BULLET_RE = re.compile(r"^[-*]\s+(.*)$")
_NUMBERED_RE = re.compile(r"^\d+[.、)]\s+(.*)$")

#: 单块文本上限，超长段落切分为多个段落块（DocBlock.text 有长度上限）
_MAX_BLOCK_CHARS = 4000


def _split_long(text: str) -> list[str]:
    if len(text) <= _MAX_BLOCK_CHARS:
        return [text] if text else []
    return [text[i : i + _MAX_BLOCK_CHARS] for i in range(0, len(text), _MAX_BLOCK_CHARS)]


def _flush_bullets(items: list[str], numbered: bool, blocks: list[DocBlock]) -> None:
    if not items:
        return
    blocks.append(DocBlock(type="numbered" if numbered else "bullets", items=items))


def markdown_to_doc_model(
    markdown: str, *, title: str, subject: str = "", author: str = ""
) -> DocModel:
    """把指挥官报告 markdown 转为 DocModel（heading/bullets/numbered/paragraph）。"""
    blocks: list[DocBlock] = []
    bullet_items: list[str] = []
    bullet_numbered = False

    def flush() -> None:
        nonlocal bullet_items
        _flush_bullets(bullet_items, bullet_numbered, blocks)
        bullet_items = []

    for raw_line in (markdown or "").split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush()
            level = min(3, max(1, len(heading.group(1))))
            blocks.append(DocBlock(type="heading", level=level, text=heading.group(2).strip()))
            continue
        bullet = _BULLET_RE.match(stripped)
        if bullet:
            is_numbered = bool(_NUMBERED_RE.match(bullet.group(1)))
            if bullet_items and is_numbered != bullet_numbered:
                flush()
            bullet_numbered = is_numbered
            text = _NUMBERED_RE.sub(r"\1", bullet.group(1)) if is_numbered else bullet.group(1)
            bullet_items.append(text)
            continue
        numbered = _NUMBERED_RE.match(stripped)
        if numbered:
            if bullet_items and not bullet_numbered:
                flush()
            bullet_numbered = True
            bullet_items.append(numbered.group(1))
            continue
        flush()
        for chunk in _split_long(stripped):
            blocks.append(DocBlock(type="paragraph", text=chunk))
    flush()

    if not blocks:
        blocks.append(DocBlock(type="paragraph", text="（报告内容为空）"))
    return DocModel(title=title[:200], subject=subject[:200], author=author[:200], blocks=blocks)


def iteration_report_doc(
    report_md: str, *, month_key: str, version_label: str, yard_name: str = ""
) -> dict[str, Any]:
    """月度迭代报告 → DocModel dict（供 docs 渲染管线消费）。"""
    model = markdown_to_doc_model(
        report_md,
        title=f"指挥官月度迭代报告 {month_key}",
        subject=f"{version_label} · {yard_name}".strip(" ·"),
        author=yard_name or "科技小院平台",
    )
    return model.model_dump()


__all__ = ["iteration_report_doc", "markdown_to_doc_model"]
