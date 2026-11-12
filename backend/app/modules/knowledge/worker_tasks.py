"""文档解析后台任务：MarkItDown 结构化提取 → 标题/表格感知切块 → 向量化 → Token 记账与入库。

Celery 入口为 ``parse_document``（task name ``knowledge.parse_document``）；
核心逻辑在 ``parse_document_sync`` 中，便于测试与无 Celery 环境同步调用。
"""

import time

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.knowledge.chunking import estimate_token_count, split_markdown_structured
from app.modules.knowledge.embedding import get_embedding_provider
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.knowledge.parsers import extract_text
from app.modules.llm.models import LLMTrace
from app.workers.celery_app import celery_app

log = get_logger(__name__)

# 信任级映射（S4/A 审查项）：chunk 的 trust_level 不再硬编码 yard_material，
# 而是按文档 source 区分知识可信级别——专家纠错高于普通小院资料，
# 官方/政策类来源视为已核实官方知识。
_EXPERT_SOURCE = "expert_correction"
_OFFICIAL_SOURCE_KEYS = ("official", "policy", "verified_official")


def _trust_level_for_source(source: str | None) -> str:
    """文档来源 → 切块信任级：expert_correction→expert_reviewed；
    官方/政策→verified_official；其余（upload/seed 等）→yard_material。"""
    src = (source or "").strip().lower()
    if src == _EXPERT_SOURCE:
        return "expert_reviewed"
    if src in _OFFICIAL_SOURCE_KEYS or "官方" in src or "政策" in src:
        return "verified_official"
    return "yard_material"


def claim_document(db: Session, document_id: str) -> int | None:
    """CAS 抢占解析权：把文档从 queued 抢到 processing，**解析代数 +1**。

    返回抢到的代数（``parse_attempt`` 新值）；None 表示已被别人抢走（看门狗
    兜底与真 worker 同时盯上同一份文档时，只有一方能继续）。

    用**条件 UPDATE**而不是"读-改-写"：``read status`` → ``write processing``
    之间存在竞态窗口，两个执行者会同时认为自己抢到了，导致同一份文档被解析两次
    （重复 embedding 消耗、重复 chunk、审计重复）。条件 UPDATE 由数据库保证
    原子性，是唯一可靠的抢占方式。

    代数的意义（2026-11-13 二次深审）：僵尸回收/人工重解析会把 processing 重置
    回 queued，**上一个执行者可能还活着**——它收尾时必须校验
    ``parse_attempt == 自己抢到的代数``，否则会与新一代解析并发写切块。
    抢占成功即自增，旧执行者的收尾/失败标记因此自然失效。
    """
    result = db.execute(
        update(KnowledgeDocument)
        .where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.status == "queued",
        )
        .values(
            status="processing",
            parse_attempt=KnowledgeDocument.parse_attempt + 1,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    if not result.rowcount:  # type: ignore[attr-defined]
        return None
    # CAS 是批量 UPDATE：代数新值需要回读（SQLite/PG 通用，不用 RETURNING）
    attempt = db.scalar(
        select(KnowledgeDocument.parse_attempt).where(KnowledgeDocument.id == document_id)
    )
    return int(attempt or 0)


def parse_document_sync(db: Session, document_id: str) -> None:
    """同步执行文档解析全流程。异常时文档置 failed，error_message 仅面向用户（不含堆栈）。

    并发语义（2026-11-13 二次深审）：claim 抢到的是**本次解析代数**；切块删除、
    切块写入、状态收尾统一放在带代数校验的条件 UPDATE 之后——被僵尸回收/
    人工重解析超越的旧执行者在收尾闸门处静默退出，绝不与新执行者并发写
    同一份文档的切块。
    """
    doc = db.get(KnowledgeDocument, document_id)
    if doc is None or doc.deleted_at is not None:
        log.warning("解析跳过：文档不存在或已删除 document_id=%s", document_id)
        return

    # CAS 抢占：看门狗兜底与真 worker 可能同时盯上同一份文档，只有一方能继续。
    # 抢不到说明对方已经在解析（或已解析完），直接返回，避免重复计费与重复 chunk。
    claimed_attempt = claim_document(db, document_id)
    if claimed_attempt is None:
        log.info("解析跳过：文档已被其他执行者抢占 document_id=%s", document_id)
        return

    # 重新取一次：CAS 是批量 UPDATE，会话里的对象可能仍是旧状态。
    doc = db.get(KnowledgeDocument, document_id)
    if doc is None:
        return
    doc.error_message = None
    db.commit()

    started = time.perf_counter()
    try:
        full_text, page_count, pages = extract_text(doc.storage_key, doc.mime_type)
        if not (full_text or "").strip():
            raise AppError(
                "DOCUMENT_PARSE_FAILED",
                "文档中未提取到可解析的文字（可能是扫描型 PDF 或文件已损坏），暂不支持解析。",
                http_status=422,
            )

        pieces: list[tuple[str, int | None, str | None, int]] = []
        for page_idx, page_text in enumerate(pages):
            page_no = (page_idx + 1) if page_count else None
            for sc in split_markdown_structured(page_text):
                tok = sc.token_count or estimate_token_count(sc.content)
                pieces.append((sc.content, page_no, sc.title_path, tok))

        # 按供应商链解析向量模型（审查 h）：小院可单独绑定向量模型
        provider = get_embedding_provider(db, doc.yard_id)
        texts = [content for content, _, _, _ in pieces]
        vectors = provider.embed(texts) if texts else []
        total_tokens = sum(tok for _, _, _, tok in pieces)

        # ---- 收尾闸门（解析代数校验）------------------------------------
        # 只有「仍是 processing 且代数未被超越」的一方才能写库。条件 UPDATE
        # 先行抢定收尾权，随后的旧切块清理、新切块写入、轨迹与状态变更都在
        # 同一事务里；任何一步失败整体回滚，文档回到 processing 交由看门狗
        # 僵尸回收兜底。被超越的旧执行者在此静默让位（外部已花的 embedding
        # 成本仅记录在日志，不写轨迹，避免赢家/输家双计费）。
        finalized = db.execute(
            update(KnowledgeDocument)
            .where(
                KnowledgeDocument.id == doc.id,
                KnowledgeDocument.parse_attempt == claimed_attempt,
                KnowledgeDocument.status == "processing",
            )
            .values(status="ready", page_count=page_count)
            .execution_options(synchronize_session=False)
        )
        if not finalized.rowcount:  # type: ignore[attr-defined]
            db.rollback()
            log.warning(
                "解析收尾让位：文档已被更高代数的解析超越 "
                "document_id=%s attempt=%s（本次 embedding 消耗约 %s token）",
                document_id,
                claimed_attempt,
                total_tokens,
            )
            return

        # 重新解析时清理旧切块，保证幂等（在收尾闸门之后，避免误删赢家的切块）
        db.query(KnowledgeChunk).filter(KnowledgeChunk.document_id == doc.id).delete()
        for idx, ((content, page_no, title_path, tok), vec) in enumerate(
            zip(pieces, vectors, strict=False)
        ):
            db.add(
                KnowledgeChunk(
                    yard_id=doc.yard_id,
                    document_id=doc.id,
                    chunk_index=idx,
                    content=content,
                    page_number=page_no,
                    title_path=title_path,
                    trust_level=_trust_level_for_source(doc.source),
                    visibility="yard",
                    metadata_json={
                        "token_count": tok,
                        "parser": "markitdown",
                    },
                    embedding=list(vec),
                )
            )

        latency_ms = max(1, int((time.perf_counter() - started) * 1000))
        # 记录向量化 Embedding Token 消耗至观测轨迹表
        if total_tokens > 0:
            db.add(
                LLMTrace(
                    yard_id=doc.yard_id,
                    provider_id=None,
                    model=getattr(provider, "model", "embedding-markitdown"),
                    purpose="embedding",
                    agent_ref=f"doc:{doc.id[:8]}",
                    run_id=doc.id,
                    status="ok",
                    input_tokens=total_tokens,
                    output_tokens=0,
                    latency_ms=latency_ms,
                    prompt_digest=f"kb_embed|{doc.title[:120]}",
                )
            )

        db.commit()
        # 切块内容已变化：本进程内的检索索引缓存立即失效
        from app.modules.knowledge.service import invalidate_search_cache

        invalidate_search_cache(doc.yard_id)
        log_audit(
            db,
            yard_id=doc.yard_id,
            actor_id=doc.uploaded_by,
            action="knowledge.document.parsed",
            resource_type="knowledge_document",
            resource_id=doc.id,
            result="success",
            request_id="worker",
            metadata={
                "chunks": len(pieces),
                "page_count": page_count,
                "total_tokens": total_tokens,
                "parser": "markitdown",
            },
        )
        db.commit()
    except AppError as e:
        db.rollback()
        log.warning("文档解析失败 document_id=%s code=%s", document_id, e.code)
        _mark_failed(db, document_id, e.message, claimed_attempt=claimed_attempt)
    except Exception as e:
        db.rollback()
        log.exception("文档解析异常 document_id=%s: %s", document_id, e)
        _mark_failed(
            db,
            document_id,
            "文档解析失败，请检查文件是否完好后重新上传。",
            claimed_attempt=claimed_attempt,
        )


def _mark_failed(
    db: Session, document_id: str, message: str, *, claimed_attempt: int | None = None
) -> None:
    """标记解析失败。

    带 ``claimed_attempt`` 时按**解析代数**条件更新：只有当前代数仍归属本次
    执行的失败才能落库——旧执行者晚到的失败标记不得覆盖新一代解析的
    ``processing``/``ready`` 状态（僵尸回收/重解析超越后，失败方静默退出）。
    未带代数的调用保持旧行为（无条件标记），仅供非竞态路径使用。
    """
    from app.modules.knowledge.service import invalidate_search_cache

    yard_id = db.scalar(
        select(KnowledgeDocument.yard_id).where(KnowledgeDocument.id == document_id)
    )
    if yard_id is None:
        return
    if claimed_attempt is not None:
        result = db.execute(
            update(KnowledgeDocument)
            .where(
                KnowledgeDocument.id == document_id,
                KnowledgeDocument.parse_attempt == claimed_attempt,
                KnowledgeDocument.status == "processing",
            )
            .values(status="failed", error_message=message)
            .execution_options(synchronize_session=False)
        )
        db.commit()
        if not result.rowcount:  # type: ignore[attr-defined]
            log.info(
                "失败标记让位：文档已被更高代数的解析超越 document_id=%s attempt=%s",
                document_id,
                claimed_attempt,
            )
            return
    else:
        doc = db.get(KnowledgeDocument, document_id)
        if doc is None:
            return
        doc.status = "failed"
        doc.error_message = message
        db.commit()
    invalidate_search_cache(yard_id)


@celery_app.task(name="knowledge.parse_document", bind=True, task_ignore_result=True)
def parse_document(self, document_id: str) -> None:
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        parse_document_sync(db, document_id)
    finally:
        db.close()
