"""SKILL.md 文本工具：frontmatter 轻量解析、段落抽取、slug。

不引入 PyYAML：市场只消费 name/description/metadata.version 等少量字段。
第三方内容一律按纯文本处理，绝不执行其中的脚本。
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
_LIST_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)、])\s+(.+)$")


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_scalar(value: str) -> str:
    # 去掉行尾注释（简单口径：不在引号内的 #）
    if "#" in value and not value.startswith(("'", '"')):
        value = value.split("#", 1)[0]
    value = value.strip()
    if value in {"true", "True"}:
        return "true"
    if value in {"false", "False"}:
        return "false"
    return _strip_quotes(value)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """解析 ``---`` 包裹的 frontmatter，返回 (meta, body)。

    仅支持 ``key: value`` 与一级缩进嵌套（如 metadata.version）；解析失败时
    返回空 meta 且原文整体作为 body，不抛异常。
    """
    if not text:
        return {}, ""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, normalized
    end = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() in {"---", "..."}:
            end = idx
            break
    if end == -1:
        return {}, normalized

    meta: dict = {}
    current_key: str | None = None
    for raw in lines[1:end]:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indented = raw[:1].isspace()
        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        if not key:
            continue
        if indented and current_key:
            bucket = meta.get(current_key)
            if not isinstance(bucket, dict):
                bucket = {}
                meta[current_key] = bucket
            bucket[key] = _parse_scalar(value)
            continue
        current_key = key
        if value.strip():
            meta[key] = _parse_scalar(value)
        else:
            meta[key] = {}
    body = "\n".join(lines[end + 1 :]).lstrip("\n")
    return meta, body


def extract_sections(body: str) -> dict[str, str]:
    """按 Markdown 标题切段，key 为小写标题。"""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in body.split("\n"):
        match = _HEADING_RE.match(line)
        if match:
            current = match.group(2).strip().lower()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items()}


def section_text(sections: dict[str, str], keywords: tuple[str, ...]) -> str:
    """取标题包含任一关键词的段落（按出现顺序拼接）。"""
    picked: list[str] = []
    for title, text in sections.items():
        if not text:
            continue
        if any(k in title for k in keywords):
            picked.append(text)
    return "\n\n".join(picked).strip()


def extract_list_items(text: str, limit: int = 6) -> list[str]:
    """从 Markdown 列表/编号行抽取条目；无列表时取非空短句。"""
    items: list[str] = []
    for line in text.split("\n"):
        match = _LIST_RE.match(line)
        if match:
            value = match.group(1).strip()
            if value and value not in items:
                items.append(value)
        if len(items) >= limit:
            break
    return items[:limit]


def first_heading(body: str) -> str:
    for line in body.split("\n"):
        match = _HEADING_RE.match(line)
        if match:
            return match.group(2).strip()
    return ""


def first_paragraph(body: str) -> str:
    """首个非标题、非列表的正文段落（截断 300 字）。"""
    chunks: list[str] = []
    for line in body.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or _LIST_RE.match(stripped):
            if chunks:
                break
            continue
        chunks.append(stripped)
    return " ".join(chunks)[:300]


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def slugify(value: str, fallback: str = "imported") -> str:
    """转小写 ASCII slug；中文等非 ASCII 字符被丢弃，全空时用 fallback。"""
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_text).strip("-").lower()
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:80] or fallback


def filename_stem(filename: str) -> str:
    return Path(filename or "").stem
