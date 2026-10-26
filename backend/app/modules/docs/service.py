"""文档生成服务：DocModel → 文件 → 会话附件（可预览、可下载）。

与对话附件同一条管道（chat_attachments + LocalStorageBackend），因此天然复用：
小院/创建者边界、会话删除级联清理、鉴权下载（越权 404）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.chat import attachments as attachments_service
from app.modules.chat import service as chat_service
from app.modules.chat.models import ATT_DOCUMENT, ATT_READY, ChatAttachment
from app.modules.docs import (
    render_docx,
    render_md,
    render_pdf,
    render_pptx,
    render_xlsx,
)
from app.modules.docs.model import DOC_FORMATS, DocModel, parse_model
from app.modules.docs.preview import to_preview_html
from app.modules.knowledge.storage import LocalStorageBackend

log = get_logger(__name__)

#: 格式 → (扩展名, MIME, 渲染器)
_RENDERERS: dict[str, tuple[str, str, Callable[[DocModel], bytes]]] = {
    "md": (".md", "text/markdown; charset=utf-8", render_md.to_markdown),
    "docx": (
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        render_docx.to_docx,
    ),
    "xlsx": (
        ".xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        render_xlsx.to_xlsx,
    ),
    "pptx": (
        ".pptx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        render_pptx.to_pptx,
    ),
    "pdf": (".pdf", "application/pdf", render_pdf.to_pdf),
}

_FORMAT_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("ppt", "幻灯片", "演示文稿", "powerpoint"), "pptx"),
    (("excel", "xlsx", "工作表", "表格", "台账"), "xlsx"),
    (("pdf",), "pdf"),
    (("markdown", " md ", "md文档"), "md"),
    (("word", "docx", "文档"), "docx"),
)

_UNSAFE_FILENAME = re.compile(r"[\\/:*?\"<>|\r\n\t]+")


def normalize_format(fmt: str) -> str:
    clean = (fmt or "").strip().lower().lstrip(".")
    if clean not in DOC_FORMATS:
        raise AppError(
            "DOC_FORMAT_UNSUPPORTED",
            f"暂不支持的文档格式：{fmt or '空'}。",
            http_status=400,
            details={"supported": list(DOC_FORMATS)},
        )
    return clean


def format_from_text(text: str) -> str:
    """按用户原话猜格式（命中不到默认 Word，最通用）。"""
    lowered = f" {(text or '').lower()} "
    for words, fmt in _FORMAT_HINTS:
        if any(w in lowered for w in words):
            return fmt
    return "docx"


def _safe_filename(title: str, ext: str) -> str:
    base = _UNSAFE_FILENAME.sub("", (title or "").strip())[:60].strip() or "文档"
    return f"{base}{ext}"


def generate_document(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    fmt: str,
    raw_model: dict[str, Any],
) -> ChatAttachment:
    """生成文档并落为会话附件（不入消息表，由调用方决定是否发卡片）。"""
    chat_service.get_session(db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id)
    model = parse_model(raw_model)
    fmt = normalize_format(fmt)
    ext, mime, renderer = _RENDERERS[fmt]
    try:
        data = renderer(model)
    except Exception as e:  # noqa: BLE001 —— 渲染失败统一 500 但保留原因摘要
        log.warning("文档渲染失败 fmt=%s title=%s: %s", fmt, model.title, e)
        raise AppError(
            "DOC_RENDER_FAILED",
            "文档渲染失败，请稍后重试或换一种格式。",
            http_status=500,
            details={"format": fmt},
        ) from e
    max_bytes = settings.upload_max_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise AppError(
            "DOC_TOO_LARGE",
            f"生成的文档超过 {settings.upload_max_mb}MB 上限，请精简内容。",
            http_status=400,
            details={"size_bytes": len(data)},
        )

    storage = LocalStorageBackend()
    storage_key = storage.save(data, ext)
    att = ChatAttachment(
        session_id=session_id,
        yard_id=ctx.yard_id,
        created_by=ctx.user_id,
        filename=_safe_filename(model.title, ext),
        storage_key=storage_key,
        mime=mime,
        size_bytes=len(data),
        kind=ATT_DOCUMENT,
        status=ATT_READY,
        extracted_text="",
        error=None,
        doc_format=fmt,
        doc_model_json=model.model_dump(),
        generated=True,
    )
    db.add(att)
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="chat_document_generated",
        resource_type="chat_attachment",
        resource_id=att.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"format": fmt, "size": len(data), "title": model.title},
    )
    db.commit()
    return att


def document_out(att: ChatAttachment) -> dict[str, Any]:
    """文档卡片载荷（前端据此渲染 预览/下载）。"""
    actions: list[dict[str, Any]] = []
    if att.doc_model_json:
        actions.append({"key": "preview", "label": "预览"})
    actions.append({"key": "download", "label": "下载"})
    return {
        "type": "document",
        "attachment_id": att.id,
        "session_id": att.session_id,
        "filename": att.filename,
        "format": att.doc_format or "",
        "mime": att.mime,
        "size_bytes": att.size_bytes,
        "previewable": bool(att.doc_model_json),
        "actions": actions,
    }


def get_document(
    db: Session, *, ctx: YardContext, session_id: str, attachment_id: str
) -> ChatAttachment:
    try:
        att = attachments_service.get_attachment(
            db, yard_id=ctx.yard_id, created_by=ctx.user_id, attachment_id=attachment_id
        )
    except AppError as e:
        # 对外统一文档语义的错误码（附件内部码不外泄，防枚举形状漂移）
        raise AppError("DOC_NOT_FOUND", "文档不存在或无权访问。", http_status=404) from e
    if att.session_id != session_id:
        raise AppError("DOC_NOT_FOUND", "文档不存在或无权访问。", http_status=404)
    return att


def preview_html(db: Session, *, ctx: YardContext, session_id: str, attachment_id: str) -> str:
    att = get_document(db, ctx=ctx, session_id=session_id, attachment_id=attachment_id)
    raw = att.doc_model_json
    if not raw:
        raise AppError(
            "DOC_PREVIEW_UNSUPPORTED",
            "该附件不支持在线预览，请下载后查看。",
            http_status=415,
        )
    return to_preview_html(parse_model(raw))


def download_bytes(
    db: Session, *, ctx: YardContext, session_id: str, attachment_id: str
) -> tuple[bytes, str, str]:
    att = get_document(db, ctx=ctx, session_id=session_id, attachment_id=attachment_id)
    with LocalStorageBackend().open(att.storage_key) as fh:
        data = fh.read()
    return data, att.mime, att.filename
