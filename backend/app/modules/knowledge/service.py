"""知识库业务服务：文档上传/查询/软删除、向量检索，以及 knowledge.search 工具注册。"""

import hashlib
import math
import os
import threading
import time
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session, contains_eager

from app.core.config import settings
from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.db.base import is_empty_vector
from app.modules.audit.service import log_audit
from app.modules.knowledge.chunking import estimate_token_count, prune_chunk_for_query
from app.modules.knowledge.embedding import get_embedding_provider
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.knowledge.schemas import ChunkPreviewOut, DocumentOut
from app.modules.knowledge.storage import LocalStorageBackend
from app.modules.yards.permissions import has_permission as has_role_permission

log = get_logger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv", ".html", ".htm"}

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
}

QUOTE_MAX_CHARS = 200
#: pgvector 路径候选过采样因子：DB 侧取 top_k × 因子 的候选
#: （下限 40，上限 settings.retrieval_scan_limit）
VECTOR_OVERSAMPLE_FACTOR = 4

#: 允许自助重新解析的文档状态。
#: - failed：解析明确失败；
#: - queued：任务已投递但无人消费（桌面单机未启 worker / Redis 队列滞留）的卡死态；
#: - processing：解析进程中断，同样属于可恢复的悬挂态。
#: ready / uploaded 不在其中：前者已成功入库，无需重试；后者尚未进入解析队列。
REPARSEABLE_STATUSES = frozenset({"failed", "queued", "processing"})


def _is_postgresql(db) -> bool:
    """当前会话是否运行在 PostgreSQL 上（pgvector 索引路径开关）。"""
    bind = getattr(db, "bind", None)
    dialect = getattr(bind, "dialect", None)
    return dialect is not None and dialect.name == "postgresql"


@dataclass
class ChunkHit:
    chunk_id: str
    document_id: str
    title: str
    source: str
    page_number: int | None
    quote: str
    score: float
    trust_level: str
    # 混合检索时各通道的原始分数（便于调参与诊断）；纯向量模式为 None
    score_detail: dict | None = None
    token_count: int | None = None
    title_path: str | None = None


def _normalize_mime(content_type: str | None, ext: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if not ct or ct == "application/octet-stream":
        return MIME_BY_EXT[ext]
    return ct


def _enrich_doc_out(db: Session, docs: list[KnowledgeDocument]) -> list[DocumentOut]:
    if not docs:
        return []
    doc_ids = [d.id for d in docs]
    chunks = (
        db.query(
            KnowledgeChunk.document_id,
            KnowledgeChunk.content,
            KnowledgeChunk.metadata_json,
        )
        .filter(KnowledgeChunk.document_id.in_(doc_ids))
        .all()
    )
    chunk_counts: dict[str, int] = {}
    token_sums: dict[str, int] = {}
    for doc_id, content, meta in chunks:
        chunk_counts[doc_id] = chunk_counts.get(doc_id, 0) + 1
        token_count = meta.get("token_count") if isinstance(meta, dict) else None
        tok = token_count if isinstance(token_count, int) else estimate_token_count(content)
        token_sums[doc_id] = token_sums.get(doc_id, 0) + tok

    out: list[DocumentOut] = []
    for d in docs:
        item = DocumentOut.model_validate(d)
        item.chunk_count = chunk_counts.get(d.id, 0)
        item.token_count = token_sums.get(d.id, 0)
        out.append(item)
    return out


def upload_document(
    db: Session,
    ctx: YardContext,
    file: UploadFile,
    title: str | None = None,
    request_id: str = "",
) -> DocumentOut:
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AppError(
            "UNSUPPORTED_FILE_TYPE",
            f"不支持的文件类型：{ext or '未知'}，仅支持 PDF/DOCX/XLSX/PPTX/TXT/MD/CSV/HTML。",
            http_status=415,
        )
    data = file.file.read()
    max_bytes = settings.upload_max_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise AppError(
            "FILE_TOO_LARGE",
            f"文件过大，单文件上限为 {settings.upload_max_mb}MB。",
            http_status=413,
        )
    if not data:
        raise AppError("INVALID_REQUEST", "上传的文件为空。", http_status=400)

    checksum = hashlib.sha256(data).hexdigest()
    storage_key = LocalStorageBackend().save(data, ext)
    doc = KnowledgeDocument(
        yard_id=ctx.yard_id,
        title=(title or filename or "未命名文档").strip()[:255],
        storage_key=storage_key,
        mime_type=_normalize_mime(file.content_type, ext),
        source="upload",
        status="queued",
        checksum=checksum,
        uploaded_by=ctx.user_id,
    )
    db.add(doc)
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="knowledge.document.upload",
        resource_type="knowledge_document",
        resource_id=doc.id,
        result="success",
        request_id=request_id or ctx.request_id,
        metadata={"title": doc.title, "mime_type": doc.mime_type, "size": len(data)},
    )
    db.commit()
    db.refresh(doc)

    # 创建解析任务；broker 不可用时不阻塞上传，解析可稍后重试/手动触发
    _dispatch_parse(doc.id)

    return _enrich_doc_out(db, [doc])[0]


def _dispatch_parse(document_id: str) -> str | None:
    """投递文档解析任务（上传与重解析共用的投递路径）。

    返回 broker 任务 ID。以下两种情况返回 None 并改在本机后台线程同步解析：

    1. **桌面单机模式**（``TASKS_INLINE_MODE=true``）：桌面端按设计不启动
       Celery worker，此时 ``.delay()`` 会投递成功、消息却永远躺在队列里，
       文档永久卡在 ``queued`` 且无任何报错（生产事故 D-03）。
    2. broker 不可用且开启 ``worker_sync_fallback``。

    注意：本函数是**全后端唯一的 Celery 投递点**，knowledge 也是唯一没有
    ``tasks_inline_mode`` 分支的异步模块——修复必须落在这里。
    """
    if settings.tasks_inline_mode:
        _run_parse_in_thread(document_id)
        return None
    try:
        from app.modules.knowledge.worker_tasks import parse_document

        result = parse_document.delay(document_id)
        return getattr(result, "id", None)
    except Exception as e:
        log.warning("解析任务投递失败 document_id=%s: %s", document_id, e)
        if settings.worker_sync_fallback:
            # 纯本机模式：无 Redis 时在后台线程同步解析，保证上传后可直接问答
            _run_parse_in_thread(document_id)
        return None


def _run_parse_in_thread(document_id: str) -> None:
    """在后台线程同步解析文档，不阻塞 API 响应。"""
    thread = threading.Thread(
        target=_parse_inline,
        args=(document_id,),
        name=f"parse-{document_id[:8]}",
        daemon=True,
    )
    thread.start()


def reparse_document(
    db: Session, ctx: YardContext, document_id: str, request_id: str = ""
) -> dict[str, str]:
    """重新解析文档（追加任务 A）：failed / queued / processing 可调。

    权限 knowledge:write；不存在/跨小院 → DOCUMENT_NOT_FOUND(404)；
    ready / uploaded → DOCUMENT_NOT_REPARSABLE(409)。
    动作：状态重置 queued、清空 error_message，并复用现有解析投递路径。

    说明：queued/processing 属于"任务已投递但无人消费"的卡死态（桌面单机模式下
    worker 未启动、Redis 队列消息滞留等）。此前仅允许 failed 重解析，导致此类
    文档永久卡在 queued 且无任何恢复入口，形成死路；现允许自助重试。
    """
    if not has_role_permission(ctx.roles, "knowledge:write"):
        raise AppError(
            "FORBIDDEN", "缺少 knowledge:write 权限，无法重新解析文档。", http_status=403
        )
    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.yard_id == ctx.yard_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .first()
    )
    if doc is None:
        # 跨小院与不存在统一 404，防资源枚举
        raise AppError("DOCUMENT_NOT_FOUND", "文档不存在或无权访问。", http_status=404)
    if doc.status not in REPARSEABLE_STATUSES:
        raise AppError(
            "DOCUMENT_NOT_REPARSABLE",
            "仅解析失败（failed）或卡在排队/解析中（queued/processing）的文档可以重新解析；"
            "已就绪（ready）的文档无需重试。",
            http_status=409,
            details={"status": doc.status},
        )
    doc.status = "queued"
    doc.error_message = None
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="knowledge.document.reparse",
        resource_type="knowledge_document",
        resource_id=doc.id,
        result="success",
        request_id=request_id or ctx.request_id,
        metadata={"title": doc.title},
    )
    db.commit()
    task_id = _dispatch_parse(doc.id)
    return {"task_id": task_id or "", "document_id": doc.id, "status": "queued"}


def _parse_inline(document_id: str) -> None:
    """纯本机模式的同步解析入口：自建会话，不依赖请求上下文。"""
    from app.db.session import SessionLocal
    from app.modules.knowledge.worker_tasks import parse_document_sync

    db = SessionLocal()
    try:
        parse_document_sync(db, document_id)
    except Exception:
        log.exception("本机同步解析异常 document_id=%s", document_id)
    finally:
        db.close()


def get_document(db: Session, ctx: YardContext, document_id: str) -> DocumentOut:
    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.yard_id == ctx.yard_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .first()
    )
    if doc is None:
        # 跨小院与不存在统一返回 404，防止资源枚举
        raise AppError("DOCUMENT_NOT_FOUND", "文档不存在或无权访问。", http_status=404)
    return _enrich_doc_out(db, [doc])[0]


def list_document_chunks(db: Session, ctx: YardContext, document_id: str) -> list[ChunkPreviewOut]:
    """查看文档经过 MarkItDown 转换与结构化切块后的分段列表及各段 Token 数。"""
    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.yard_id == ctx.yard_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .first()
    )
    if doc is None:
        raise AppError("DOCUMENT_NOT_FOUND", "文档不存在或无权访问。", http_status=404)
    chunks = (
        db.query(KnowledgeChunk)
        .filter(KnowledgeChunk.document_id == doc.id, KnowledgeChunk.yard_id == ctx.yard_id)
        .order_by(KnowledgeChunk.chunk_index.asc())
        .all()
    )
    out: list[ChunkPreviewOut] = []
    for c in chunks:
        meta = c.metadata_json if isinstance(c.metadata_json, dict) else {}
        tok = (
            int(meta["token_count"])
            if isinstance(meta.get("token_count"), int)
            else estimate_token_count(c.content)
        )
        out.append(
            ChunkPreviewOut(
                id=c.id,
                chunk_index=c.chunk_index,
                content=c.content,
                title_path=c.title_path,
                page_number=c.page_number,
                token_count=tok,
                trust_level=c.trust_level,
            )
        )
    return out


def list_documents(
    db: Session, ctx: YardContext, page: int, page_size: int
) -> tuple[list[DocumentOut], int]:
    base = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.yard_id == ctx.yard_id,
        KnowledgeDocument.deleted_at.is_(None),
    )
    total = base.with_entities(func.count()).scalar() or 0
    docs = (
        base.order_by(KnowledgeDocument.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return _enrich_doc_out(db, docs), total


def delete_document(db: Session, ctx: YardContext, document_id: str) -> DocumentOut:
    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.yard_id == ctx.yard_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .first()
    )
    if doc is None:
        raise AppError("DOCUMENT_NOT_FOUND", "文档不存在或无权访问。", http_status=404)
    from datetime import datetime

    doc.deleted_at = datetime.now(UTC)
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="knowledge.document.delete",
        resource_type="knowledge_document",
        resource_id=doc.id,
        result="success",
        request_id=ctx.request_id,
    )
    db.commit()
    invalidate_search_cache(ctx.yard_id)
    db.refresh(doc)
    return _enrich_doc_out(db, [doc])[0]


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """余弦相似度；不切片拷贝，按最短长度一次遍历算完点积与模长。"""
    if not a or not b:
        return 0.0
    dot = 0.0
    na2 = 0.0
    nb2 = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na2 += x * x
        nb2 += y * y
    if na2 == 0.0 or nb2 == 0.0:
        return 0.0
    return dot / math.sqrt(na2 * nb2)


# ---------------------------------------------------------------------------
# 检索索引缓存（SQLite / 无 pgvector 路径）
#
# 每次检索若都把全部切块的 embedding JSON 反序列化再逐条算余弦，知识库一大
# 就会把请求拖成秒级。这里把「已解析的向量 + 关键词 bigram」按小院缓存在
# 进程内，切块写入/删除/文档状态变化时显式失效；另有 TTL 兜底跨进程变更。
# ---------------------------------------------------------------------------

_SEARCH_CACHE_TTL = 60.0
#: 超过该切块数不常驻内存（向量 + bigram 集按块线性增长，宁可逐次扫描也不吃爆进程）
_SEARCH_CACHE_MAX_CHUNKS = 1200
_search_cache: dict[str, tuple[float, list["_CachedChunk"]]] = {}
_search_cache_lock = threading.Lock()
_query_embed_cache: dict[tuple[str, str, int, str], tuple[float, ...]] = {}


@dataclass(frozen=True)
class _CachedChunk:
    """检索缓存条目：与 KnowledgeChunk 同名字段，供评分循环直接复用。"""

    chunk_id: str
    document_id: str
    content: str
    page_number: int | None
    title_path: str | None
    trust_level: str
    embedding: array
    bigrams: frozenset[str]
    title: str
    source: str

    @property
    def id(self) -> str:
        return self.chunk_id

    @property
    def document(self) -> None:
        return None


def invalidate_search_cache(yard_id: str | None = None) -> None:
    """切块/文档状态变化后失效缓存；yard_id 为空时清空全部。"""
    with _search_cache_lock:
        if yard_id is None:
            _search_cache.clear()
        else:
            _search_cache.pop(yard_id, None)


def _load_scan_index(db: Session, yard_id: str) -> list[_CachedChunk] | None:
    now = time.monotonic()
    with _search_cache_lock:
        cached = _search_cache.get(yard_id)
    if cached is not None and now - cached[0] < _SEARCH_CACHE_TTL:
        return cached[1]

    total = int(
        db.scalar(
            select(func.count(KnowledgeChunk.id))
            .select_from(KnowledgeChunk)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .where(
                KnowledgeChunk.yard_id == yard_id,
                KnowledgeDocument.status == "ready",
                KnowledgeDocument.deleted_at.is_(None),
            )
        )
        or 0
    )
    if total > _SEARCH_CACHE_MAX_CHUNKS:
        # 超出内存预算时退回逐次扫描，避免把大库的向量常驻内存
        return None

    rows = (
        db.query(KnowledgeChunk)
        # contains_eager：复用下面的显式 join，不再额外连一次文档表
        .options(contains_eager(KnowledgeChunk.document))
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .filter(
            KnowledgeChunk.yard_id == yard_id,
            KnowledgeDocument.status == "ready",
            KnowledgeDocument.deleted_at.is_(None),
        )
        .order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
        .all()
    )
    index: list[_CachedChunk] = []
    for row in rows:
        vector = row.embedding
        if is_empty_vector(vector):
            continue
        doc = row.document
        index.append(
            _CachedChunk(
                chunk_id=row.id,
                document_id=row.document_id,
                content=row.content or "",
                page_number=row.page_number,
                title_path=row.title_path,
                trust_level=row.trust_level,
                embedding=array("f", vector),
                bigrams=frozenset(_char_bigrams(row.content or "")),
                title=doc.title if doc else "",
                source=doc.source if doc else "upload",
            )
        )
    with _search_cache_lock:
        _search_cache[yard_id] = (now, index)
    return index


def _embed_query(
    query: str,
    db: Session | None = None,
    yard_id: str | None = None,
) -> tuple[float, ...]:
    """查询向量：同一查询在进程内复用（Mock 嵌入按 bigram 逐维生成，重复计算很贵）。

    带小院上下文时按供应商链解析向量模型（审查 h）；缓存键带上模型标识，
    避免换模型后命中旧向量。
    """
    provider = get_embedding_provider(db, yard_id)
    model_key = str(getattr(provider, "model", "") or settings.embedding_model or "")
    key = (
        settings.embedding_provider,
        settings.embedding_model,
        model_key,
        int(settings.embedding_dim),
        query,
    )
    with _search_cache_lock:
        cached = _query_embed_cache.get(key)
    if cached is not None:
        return cached
    vector = tuple(provider.embed([query])[0])
    with _search_cache_lock:
        if len(_query_embed_cache) >= 512:
            _query_embed_cache.clear()
        _query_embed_cache[key] = vector
    return vector


def _char_bigrams(text: str) -> set[str]:
    """字符 bigram 集合：与 MockEmbeddingProvider 分词口径一致（lower、滑动窗口），不引入新依赖。"""
    t = (text or "").lower()
    if len(t) < 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _keyword_overlap_score(query_bigrams: set[str], chunk_bigrams: set[str]) -> float:
    """关键词分数 ∈ [0,1]，采用 Overlap 系数 |A∩B| / min(|A|,|B|)。"""
    if not query_bigrams:
        return 0.0
    denom = min(len(query_bigrams), len(chunk_bigrams)) or 1
    return len(query_bigrams & chunk_bigrams) / denom


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _fuse_score(vector_score: float, keyword_score: float, alpha: float) -> float:
    """融合：final = alpha*vector + (1-alpha)*keyword，余弦先裁到 [0,1]。"""
    alpha = max(0.0, min(1.0, alpha))
    return alpha * _clip01(vector_score) + (1.0 - alpha) * keyword_score


def search(
    db: Session, ctx: YardContext, query: str, top_k: int = 5, mode: str | None = None
) -> list[ChunkHit]:
    """小院内知识检索（含检索端 Context Pruning 降 Token）。"""
    top_k = max(1, min(int(top_k or 5), 20))
    query = (query or "").strip()
    if not query:
        raise AppError("INVALID_REQUEST", "检索关键词不能为空。", http_status=400)

    resolved_mode = mode or settings.retrieval_mode
    if resolved_mode not in ("vector", "hybrid"):
        raise AppError(
            "INVALID_REQUEST",
            f"未知的检索模式：{resolved_mode}，可选 vector/hybrid。",
            http_status=400,
        )
    alpha = settings.retrieval_alpha if resolved_mode == "hybrid" else 1.0

    query_vec = list(_embed_query(query, db, ctx.yard_id))
    query_bigrams = _char_bigrams(query) if resolved_mode == "hybrid" else set()

    base_query = (
        db.query(KnowledgeChunk)
        .options(contains_eager(KnowledgeChunk.document))
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .filter(
            KnowledgeChunk.yard_id == ctx.yard_id,
            KnowledgeDocument.status == "ready",
            KnowledgeDocument.deleted_at.is_(None),
        )
    )
    scan_limit = max(50, int(settings.retrieval_scan_limit))
    candidates: list[KnowledgeChunk] | list[_CachedChunk]
    if settings.use_pgvector and _is_postgresql(db):
        oversample = min(max(top_k * VECTOR_OVERSAMPLE_FACTOR, 40), scan_limit)
        candidates = (
            base_query.order_by(KnowledgeChunk.embedding.op("<=>")(query_vec).asc())
            .limit(oversample)
            .all()
        )
        if len(candidates) >= oversample:
            log.warning(
                "knowledge.search truncated(pgvector) t=%s lim=%d k=%d qlen=%d",
                ctx.yard_id,
                oversample,
                top_k,
                len(query),
            )
    else:
        cached_index = _load_scan_index(db, ctx.yard_id)
        if cached_index is not None and len(cached_index) <= scan_limit:
            # 命中进程内索引：不再反序列化 embedding JSON，也不再逐条算 bigram
            candidates = cached_index
        else:
            candidates = (
                base_query.order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
                .limit(scan_limit)
                .all()
            )
            if len(candidates) >= scan_limit:
                log.warning(
                    "knowledge.search truncated(scan) t=%s lim=%d k=%d qlen=%d",
                    ctx.yard_id,
                    scan_limit,
                    top_k,
                    len(query),
                )

    min_score = settings.effective_retrieval_min_score
    chunk_bigrams_cache: dict[str, set[str]] = {}
    scored: list[tuple[float, float, float, KnowledgeChunk | _CachedChunk]] = []
    for chunk in candidates:
        vec = chunk.embedding
        if is_empty_vector(vec):
            continue
        vector_score = _cosine(query_vec, list(vec))
        keyword_score = 0.0
        if resolved_mode == "hybrid":
            bigrams = getattr(chunk, "bigrams", None)
            if bigrams is None:
                bigrams = chunk_bigrams_cache.get(chunk.id)
                if bigrams is None:
                    bigrams = _char_bigrams(chunk.content or "")
                    chunk_bigrams_cache[chunk.id] = bigrams
            keyword_score = _keyword_overlap_score(query_bigrams, bigrams)
        final_score = _fuse_score(vector_score, keyword_score, alpha)
        if resolved_mode == "hybrid" and keyword_score >= 1.0:
            floor = min(min_score + 0.01, 1.0)
            if final_score < floor:
                final_score = floor
        scored.append((final_score, vector_score, keyword_score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)

    hits: list[ChunkHit] = []
    for final_score, vector_score, keyword_score, chunk in scored[:top_k]:
        if final_score < min_score:
            continue
        doc = chunk.document
        pruned_quote = prune_chunk_for_query(chunk.content or "", query, max_chars=QUOTE_MAX_CHARS)
        quote_str = pruned_quote[:QUOTE_MAX_CHARS]
        hits.append(
            ChunkHit(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                title=doc.title if doc else getattr(chunk, "title", ""),
                source=doc.source if doc else getattr(chunk, "source", "upload"),
                page_number=chunk.page_number,
                quote=quote_str,
                score=round(final_score, 4),
                trust_level=chunk.trust_level,
                score_detail=(
                    {"vector": round(_clip01(vector_score), 4), "keyword": round(keyword_score, 4)}
                    if resolved_mode == "hybrid"
                    else None
                ),
                token_count=estimate_token_count(quote_str),
                title_path=chunk.title_path,
            )
        )
    return hits


# ---------------------------------------------------------------------------
# knowledge.search 工具注册（供 tools 模块 / Agent 调用）
# ---------------------------------------------------------------------------

KNOWLEDGE_SEARCH_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string", "minLength": 1, "maxLength": 2000},
        "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
    },
    "required": ["query"],
    "additionalProperties": False,
}


def _knowledge_search_tool_func(params: dict, ctx: YardContext) -> dict:
    """knowledge.search 工具执行函数。"""
    from pydantic import ValidationError

    from app.db.session import SessionLocal
    from app.modules.knowledge.schemas import SearchRequest

    try:
        req = SearchRequest(**(params or {}))
    except ValidationError as e:
        raise AppError("TOOL_VALIDATION_FAILED", "工具参数校验失败。", http_status=400) from e
    if not ctx.yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)

    db = SessionLocal()
    try:
        hits = search(db, ctx, req.query, req.top_k)
    finally:
        db.close()
    return {
        "hits": [
            {
                "chunk_id": h.chunk_id,
                "document_id": h.document_id,
                "title": h.title,
                "source": h.source,
                "page_number": h.page_number,
                "quote": h.quote,
                "score": h.score,
                "trust_level": h.trust_level,
            }
            for h in hits
        ]
    }


def register_knowledge_search_tool():
    """向全局工具注册表注册 code=knowledge.search 的工具定义。"""
    try:
        from app.modules.tools.registry import ToolDef, tool_registry
    except Exception as e:
        log.warning("工具注册表不可用，knowledge.search 未注册: %s", e)
        return None

    tool_def = ToolDef(
        code="knowledge.search",
        version="1.0.0",
        name="知识检索",
        description="在当前用户有权访问的知识范围内执行检索",
        input_schema=KNOWLEDGE_SEARCH_INPUT_SCHEMA,
        required_permissions=["knowledge:read"],
        risk_level="read_only",
        side_effect=False,
        timeout_seconds=10,
        func=_knowledge_search_tool_func,
    )
    try:
        tool_registry.register(tool_def)
    except Exception as e:
        log.warning("knowledge.search 注册失败: %s", e)
        return None
    log.info("knowledge.search 工具已注册")
    return tool_def
