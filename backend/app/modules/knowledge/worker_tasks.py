"""文档解析后台任务：提取文本 → 切块 → 向量化 → 入库。

Celery 入口为 ``parse_document``（task name ``knowledge.parse_document``）；
核心逻辑在 ``parse_document_sync`` 中，便于测试与无 Celery 环境同步调用。
"""

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.knowledge.chunking import split_text
from app.modules.knowledge.embedding import get_embedding_provider
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.knowledge.parsers import extract_text
from app.workers.celery_app import celery_app

log = get_logger(__name__)

# 信任级映射（S4/A 审查项）：chunk 的 trust_level 不再硬编码 tenant_material，
# 而是按文档 source 区分知识可信级别——专家纠错高于普通租户资料，
# 官方/政策类来源视为已核实官方知识。
_EXPERT_SOURCE = "expert_correction"
_OFFICIAL_SOURCE_KEYS = ("official", "policy", "verified_official")


def _trust_level_for_source(source: str | None) -> str:
    """文档来源 → 切块信任级：expert_correction→expert_reviewed；
    官方/政策→verified_official；其余（upload/seed 等）→tenant_material。"""
    src = (source or "").strip().lower()
    if src == _EXPERT_SOURCE:
        return "expert_reviewed"
    if src in _OFFICIAL_SOURCE_KEYS or "官方" in src or "政策" in src:
        return "verified_official"
    return "tenant_material"


def parse_document_sync(db: Session, document_id: str) -> None:
    """同步执行文档解析全流程。异常时文档置 failed，error_message 仅面向用户（不含堆栈）。"""
    doc = db.get(KnowledgeDocument, document_id)
    if doc is None or doc.deleted_at is not None:
        log.warning("解析跳过：文档不存在或已删除 document_id=%s", document_id)
        return

    doc.status = "processing"
    doc.error_message = None
    db.commit()

    try:
        full_text, page_count, pages = extract_text(doc.storage_key, doc.mime_type)
        if not (full_text or "").strip():
            raise AppError(
                "DOCUMENT_PARSE_FAILED",
                "文档中未提取到可解析的文字（可能是扫描型 PDF 或文件已损坏），暂不支持解析。",
                http_status=422,
            )

        # 重新解析时先清理旧切块，保证幂等
        db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc.id).delete()

        pieces: list[tuple[str, int | None]] = []
        for page_idx, page_text in enumerate(pages):
            page_no = (page_idx + 1) if page_count else None
            for piece in split_text(page_text):
                pieces.append((piece, page_no))

        provider = get_embedding_provider()
        texts = [content for content, _ in pieces]
        vectors = provider.embed(texts) if texts else []
        for idx, ((content, page_no), vec) in enumerate(zip(pieces, vectors, strict=False)):
            db.add(
                KnowledgeChunk(
                    tenant_id=doc.tenant_id,
                    document_id=doc.id,
                    chunk_index=idx,
                    content=content,
                    page_number=page_no,
                    trust_level=_trust_level_for_source(doc.source),
                    visibility="tenant",
                    embedding=list(vec),
                )
            )

        doc.status = "ready"
        doc.page_count = page_count
        db.commit()
        log_audit(
            db,
            tenant_id=doc.tenant_id,
            actor_id=doc.uploaded_by,
            action="knowledge.document.parsed",
            resource_type="knowledge_document",
            resource_id=doc.id,
            result="success",
            request_id="worker",
            metadata={"chunks": len(pieces), "page_count": page_count},
        )
        db.commit()
    except AppError as e:
        db.rollback()
        log.warning("文档解析失败 document_id=%s code=%s", document_id, e.code)
        _mark_failed(db, document_id, e.message)
    except Exception as e:
        db.rollback()
        log.exception("文档解析异常 document_id=%s: %s", document_id, e)
        _mark_failed(db, document_id, "文档解析失败，请检查文件是否完好后重新上传。")


def _mark_failed(db: Session, document_id: str, message: str) -> None:
    doc = db.get(KnowledgeDocument, document_id)
    if doc is None:
        return
    doc.status = "failed"
    doc.error_message = message
    db.commit()


@celery_app.task(name="knowledge.parse_document", bind=True, task_ignore_result=True)
def parse_document(self, document_id: str) -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        parse_document_sync(db, document_id)
    finally:
        db.close()
