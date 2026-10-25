"""知识库业务服务：文档上传/查询/软删除、向量检索，以及 knowledge.search 工具注册。"""

import hashlib
import math
import os
import threading
from dataclasses import dataclass
from datetime import UTC

from fastapi import UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import TenantContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.audit.service import log_audit
from app.modules.knowledge.embedding import get_embedding_provider
from app.modules.knowledge.models import KnowledgeChunk, KnowledgeDocument
from app.modules.knowledge.schemas import DocumentOut
from app.modules.knowledge.storage import LocalStorageBackend
from app.modules.tenants.permissions import has_permission as has_role_permission

log = get_logger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md", ".csv"}

MIME_BY_EXT = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
}

QUOTE_MAX_CHARS = 200
#: pgvector 路径候选过采样因子：DB 侧取 top_k × 因子 的候选
#: （下限 40，上限 settings.retrieval_scan_limit）
VECTOR_OVERSAMPLE_FACTOR = 4


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


def _normalize_mime(content_type: str | None, ext: str) -> str:
    ct = (content_type or "").split(";")[0].strip().lower()
    if not ct or ct == "application/octet-stream":
        return MIME_BY_EXT[ext]
    return ct


def upload_document(
    db: Session,
    ctx: TenantContext,
    file: UploadFile,
    title: str | None = None,
    request_id: str = "",
) -> DocumentOut:
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise AppError(
            "UNSUPPORTED_FILE_TYPE",
            f"不支持的文件类型：{ext or '未知'}，仅支持 PDF/DOCX/TXT/MD/CSV。",
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
        tenant_id=ctx.tenant_id,
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
        tenant_id=ctx.tenant_id,
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

    return DocumentOut.model_validate(doc)


def _dispatch_parse(document_id: str) -> str | None:
    """投递文档解析任务（上传与重解析共用的投递路径）。

    返回 broker 任务 ID；broker 不可用时记录告警并按 worker_sync_fallback
    配置降级为本机后台线程同步解析，此时返回 None。
    """
    try:
        from app.modules.knowledge.worker_tasks import parse_document

        result = parse_document.delay(document_id)
        return getattr(result, "id", None)
    except Exception as e:
        log.warning("解析任务投递失败 document_id=%s: %s", document_id, e)
        if settings.worker_sync_fallback:
            # 纯本机模式：无 Redis 时在后台线程同步解析，保证上传后可直接问答
            thread = threading.Thread(
                target=_parse_inline,
                args=(document_id,),
                name=f"parse-{document_id[:8]}",
                daemon=True,
            )
            thread.start()
        return None


def reparse_document(
    db: Session, ctx: TenantContext, document_id: str, request_id: str = ""
) -> dict[str, str]:
    """重新解析失败的文档（追加任务 A）：仅 status=failed 可调。

    权限 knowledge:write；不存在/跨租户 → DOCUMENT_NOT_FOUND(404)；
    非 failed 状态 → DOCUMENT_NOT_REPARSABLE(409)。
    动作：状态重置 queued、清空 error_message，并复用现有解析投递路径。
    """
    if not has_role_permission(ctx.roles, "knowledge:write"):
        raise AppError(
            "FORBIDDEN", "缺少 knowledge:write 权限，无法重新解析文档。", http_status=403
        )
    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == ctx.tenant_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .first()
    )
    if doc is None:
        # 跨租户与不存在统一 404，防资源枚举
        raise AppError("DOCUMENT_NOT_FOUND", "文档不存在或无权访问。", http_status=404)
    if doc.status != "failed":
        raise AppError(
            "DOCUMENT_NOT_REPARSABLE",
            "仅解析失败（failed）的文档可以重新解析。",
            http_status=409,
            details={"status": doc.status},
        )
    doc.status = "queued"
    doc.error_message = None
    log_audit(
        db,
        tenant_id=ctx.tenant_id,
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


def get_document(db: Session, ctx: TenantContext, document_id: str) -> DocumentOut:
    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == ctx.tenant_id,
            KnowledgeDocument.deleted_at.is_(None),
        )
        .first()
    )
    if doc is None:
        # 跨租户与不存在统一返回 404，防止资源枚举
        raise AppError("DOCUMENT_NOT_FOUND", "文档不存在或无权访问。", http_status=404)
    return DocumentOut.model_validate(doc)


def list_documents(
    db: Session, ctx: TenantContext, page: int, page_size: int
) -> tuple[list[DocumentOut], int]:
    base = db.query(KnowledgeDocument).filter(
        KnowledgeDocument.tenant_id == ctx.tenant_id,
        KnowledgeDocument.deleted_at.is_(None),
    )
    total = base.with_entities(func.count()).scalar() or 0
    docs = (
        base.order_by(KnowledgeDocument.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return [DocumentOut.model_validate(d) for d in docs], total


def delete_document(db: Session, ctx: TenantContext, document_id: str) -> DocumentOut:
    doc = (
        db.query(KnowledgeDocument)
        .filter(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.tenant_id == ctx.tenant_id,
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
        tenant_id=ctx.tenant_id,
        actor_id=ctx.user_id,
        action="knowledge.document.delete",
        resource_type="knowledge_document",
        resource_id=doc.id,
        result="success",
        request_id=ctx.request_id,
    )
    db.commit()
    db.refresh(doc)
    return DocumentOut.model_validate(doc)


def _cosine(a: list[float], b: list[float]) -> float:
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = sum(x * y for x, y in zip(a[:n], b[:n], strict=False))
    na = math.sqrt(sum(x * x for x in a[:n]))
    nb = math.sqrt(sum(y * y for y in b[:n]))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _char_bigrams(text: str) -> set[str]:
    """字符 bigram 集合：与 MockEmbeddingProvider 分词口径一致（lower、滑动窗口），不引入新依赖。"""
    t = (text or "").lower()
    if len(t) < 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def _keyword_overlap_score(query_bigrams: set[str], chunk_bigrams: set[str]) -> float:
    """关键词分数 ∈ [0,1]，采用 Overlap 系数 |A∩B| / min(|A|,|B|)。

    不用 Jaccard 的理由：查询通常远短于 chunk（|query| << |chunk|），Jaccard
    的分母含并集，会系统性压低完全包含查询词的长 chunk；Overlap 系数刻画
    "查询在多大程度上被 chunk 覆盖"，query 被逐字命中时得 1.0。
    """
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
    db: Session, ctx: TenantContext, query: str, top_k: int = 5, mode: str | None = None
) -> list[ChunkHit]:
    """租户内知识检索。

    mode: "vector"（纯向量，旧逻辑）| "hybrid"（向量+关键词混合）；None 时取
    settings.retrieval_mode。混合模式先把余弦裁到 [0,1] 再融合
    ``final = alpha*vector + (1-alpha)*keyword``，融合后仍用 retrieval_min_score
    阈值过滤（阈值语义不变）。
    """
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

    provider = get_embedding_provider()
    query_vec = provider.embed([query])[0]
    query_bigrams = _char_bigrams(query) if resolved_mode == "hybrid" else set()

    base_query = (
        db.query(KnowledgeChunk)
        .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .filter(
            KnowledgeChunk.tenant_id == ctx.tenant_id,
            KnowledgeDocument.status == "ready",
            KnowledgeDocument.deleted_at.is_(None),
        )
    )
    scan_limit = max(50, int(settings.retrieval_scan_limit))
    candidates: list[KnowledgeChunk]
    if settings.use_pgvector and _is_postgresql(db):
        # pgvector 索引路径（规范 §10.3）：DB 侧按余弦距离排序取候选，
        # 避免全量拉取后 Python 扫描；混合模式在候选集上做关键词融合。
        oversample = min(max(top_k * VECTOR_OVERSAMPLE_FACTOR, 40), scan_limit)
        candidates = (
            base_query.order_by(KnowledgeChunk.embedding.op("<=>")(query_vec).asc())
            .limit(oversample)
            .all()
        )
        if len(candidates) >= oversample:
            log.warning(
                "knowledge.search truncated(pgvector) t=%s lim=%d k=%d qlen=%d",
                ctx.tenant_id,
                oversample,
                top_k,
                len(query),
            )
    else:
        # SQLite/测试回退：按文档序取候选后 Python 侧计算余弦（确定性、可离线测试）
        candidates = (
            base_query.order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)
            .limit(scan_limit)
            .all()
        )
        if len(candidates) >= scan_limit:
            log.warning(
                "knowledge.search truncated(scan) t=%s lim=%d k=%d qlen=%d",
                ctx.tenant_id,
                scan_limit,
                top_k,
                len(query),
            )

    # chunk bigram 集合按需预计算（混合模式才算，纯向量路径零额外开销）
    # 最低相似度阈值：低于此分数视为知识不足，不返回（防"伪相关"引用）
    min_score = settings.retrieval_min_score
    chunk_bigrams_cache: dict[str, set[str]] = {}
    scored: list[tuple[float, float, float, KnowledgeChunk]] = []
    for chunk in candidates:
        vec = chunk.embedding
        if not vec:
            continue
        vector_score = _cosine(query_vec, list(vec))
        keyword_score = 0.0
        if resolved_mode == "hybrid":
            bigrams = chunk_bigrams_cache.get(chunk.id)
            if bigrams is None:
                bigrams = _char_bigrams(chunk.content or "")
                chunk_bigrams_cache[chunk.id] = bigrams
            keyword_score = _keyword_overlap_score(query_bigrams, bigrams)
        final_score = _fuse_score(vector_score, keyword_score, alpha)
        # 检索保底（A9）：用户问题的 token（字符 bigram）全部出现在 chunk 中
        # （keyword_score 达 1.0 的"完整覆盖"）时，最终得分保底为 min_score+0.01。
        # 理由：逐字命中的资料不应被低向量分稀释到阈值以下而"查不到"；保底只抬升
        # 不压低，且封顶 1.0，不影响其余候选的相对排序与阈值语义。
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
        hits.append(
            ChunkHit(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                title=doc.title if doc else "",
                source=doc.source if doc else "upload",
                page_number=chunk.page_number,
                quote=(chunk.content or "")[:QUOTE_MAX_CHARS],
                score=round(final_score, 4),
                trust_level=chunk.trust_level,
                score_detail=(
                    {"vector": round(_clip01(vector_score), 4), "keyword": round(keyword_score, 4)}
                    if resolved_mode == "hybrid"
                    else None
                ),
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


def _knowledge_search_tool_func(params: dict, ctx: TenantContext) -> dict:
    """knowledge.search 工具执行函数。

    签名遵循 tools.registry 的 ToolFunc 约定：``(params: dict, ctx: TenantContext)``。
    参数经 SearchRequest 校验；函数内部自行开关数据库会话。
    """
    from pydantic import ValidationError

    from app.db.session import SessionLocal
    from app.modules.knowledge.schemas import SearchRequest

    try:
        req = SearchRequest(**(params or {}))
    except ValidationError as e:
        raise AppError("TOOL_VALIDATION_FAILED", "工具参数校验失败。", http_status=400) from e
    if not ctx.tenant_id:
        raise AppError("TENANT_REQUIRED", "该操作需要租户上下文。", http_status=400)

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
    """向全局工具注册表注册 code=knowledge.search 的工具定义。

    注册表缺失时仅记录告警并返回 None，保证本模块 import 不失败。
    """
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
