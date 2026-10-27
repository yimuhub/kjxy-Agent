"""从持久化班组答案生成可重复下载的文档。"""

from __future__ import annotations

import re
from typing import Any

from app.core.errors import AppError
from app.modules.agents.teams.models import RUN_COMPLETED, TeamRun
from app.modules.docs import render_docx, render_md
from app.modules.docs.model import MAX_TEXT, DocBlock, parse_model

_FORMATS = {
    "md": ("text/markdown; charset=utf-8", render_md.to_markdown),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        render_docx.to_docx,
    ),
}


def _answer_blocks(answer: str) -> list[DocBlock]:
    blocks: list[DocBlock] = []
    for paragraph in re.split(r"\n\s*\n", answer.strip()):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        lines = paragraph.splitlines()
        heading = re.fullmatch(r"#{1,6}\s+(.+)", lines[0])
        if heading:
            blocks.append(DocBlock(type="heading", level=1, text=heading[1]))
            paragraph = "\n".join(lines[1:]).strip()
            if not paragraph:
                continue
            lines = paragraph.splitlines()
        if len(lines) <= 200 and all(re.match(r"^[-*+]\s+", line) for line in lines):
            blocks.append(
                DocBlock(type="bullets", items=[re.sub(r"^[-*+]\s+", "", x) for x in lines])
            )
        elif len(lines) <= 200 and all(re.match(r"^\d+[.)]\s+", line) for line in lines):
            blocks.append(
                DocBlock(type="numbered", items=[re.sub(r"^\d+[.)]\s+", "", x) for x in lines])
            )
        else:
            for start in range(0, len(paragraph), MAX_TEXT):
                blocks.append(DocBlock(type="paragraph", text=paragraph[start : start + MAX_TEXT]))
    return blocks


def build_document(run: TeamRun) -> dict[str, Any]:
    if not (run.answer or "").strip():
        raise AppError("TEAM_DOCUMENT_NOT_READY", "本次运行尚无可导出的结果。", http_status=409)
    blocks = _answer_blocks(run.answer or "")
    research = run.research_json or {}
    sources: list[str] = []
    for i, item in enumerate(research.get("knowledge") or []):
        page = f"，第 {item['page_number']} 页" if item.get("page_number") is not None else ""
        sources.append(
            f"[K{i + 1}] {item.get('title') or '知识库资料'}{page}"
            f"（文档：{item.get('document_id') or ''}）"
        )
    for i, item in enumerate(research.get("web") or []):
        sources.append(f"[W{i + 1}] {item.get('title') or '联网资料'} — {item.get('url') or ''}")
    if sources:
        blocks.append(DocBlock(type="heading", level=1, text="检索来源"))
        blocks.extend(DocBlock(type="paragraph", text=s) for s in sources)
    warnings = research.get("warnings") or []
    if warnings:
        blocks.append(DocBlock(type="heading", level=1, text="资料限制"))
        blocks.extend(DocBlock(type="paragraph", text=str(w)) for w in warnings)
    model = parse_model(
        {
            "title": f"{run.team_name or '协作班组'}运行结果"[:100],
            "subject": run.input[:100],
            "blocks": [b.model_dump() for b in blocks],
        }
    )
    return model.model_dump()


def render_document(run: TeamRun, fmt: str) -> tuple[bytes, str, str]:
    if fmt not in _FORMATS:
        raise AppError(
            "DOC_FORMAT_UNSUPPORTED",
            "班组结果支持 Markdown 和 Word 格式。",
            http_status=400,
            details={"supported": ["md", "docx"]},
        )
    if run.status != RUN_COMPLETED or not (run.answer or "").strip():
        raise AppError("TEAM_DOCUMENT_NOT_READY", "运行完成后才能下载结果文档。", http_status=409)
    model = parse_model(run.document_json or build_document(run))
    mime, renderer = _FORMATS[fmt]
    base = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', "", model.title).strip()[:60] or "班组运行结果"
    return renderer(model), mime, f"{base}-{run.id[:8]}.{fmt}"
