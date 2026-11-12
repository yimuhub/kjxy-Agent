"""知识库 HTTP 路由。

端点只做身份上下文读取、输入输出转换与 service 调用，不写复杂业务逻辑。
挂载到 /api/v1 由集成阶段统一接入（见 app/api/v1/router.py）。
"""

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status

from app.core.deps import (
    CurrentUser,
    YardContext,
    get_db_session,
    get_yard_context,
    require_permission,
)
from app.core.pagination import pagination_params
from app.core.responses import ok, paged
from app.modules.knowledge import service
from app.modules.knowledge.schemas import (
    ChunkHitOut,
    DocumentOut,
    ReparseOut,
    SearchRequest,
    SearchResponse,
)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/documents", status_code=status.HTTP_201_CREATED)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    db=Depends(get_db_session),
    ctx: YardContext = Depends(get_yard_context),
    _user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    doc = service.upload_document(
        db, ctx, file, title=title, request_id=getattr(request.state, "request_id", "")
    )
    return ok(DocumentOut.model_validate(doc).model_dump(mode="json"), request)


@router.get("/documents")
def list_documents(
    request: Request,
    page_params=Depends(pagination_params),
    db=Depends(get_db_session),
    ctx: YardContext = Depends(get_yard_context),
):
    page, page_size = page_params
    items, total = service.list_documents(db, ctx, page, page_size)
    return paged(
        [DocumentOut.model_validate(d).model_dump(mode="json") for d in items],
        page,
        page_size,
        total,
        request,
    )


@router.get("/documents/{document_id}")
def get_document(
    document_id: str,
    request: Request,
    db=Depends(get_db_session),
    ctx: YardContext = Depends(get_yard_context),
):
    doc = service.get_document(db, ctx, document_id)
    return ok(DocumentOut.model_validate(doc).model_dump(mode="json"), request)


@router.get("/documents/{document_id}/chunks")
def get_document_chunks(
    document_id: str,
    request: Request,
    db=Depends(get_db_session),
    ctx: YardContext = Depends(get_yard_context),
):
    """获取文档经过 MarkItDown 转换与结构化切块后的分段预览列表（含每段 Token 计数）。"""
    chunks = service.list_document_chunks(db, ctx, document_id)
    return ok(
        {
            "document_id": document_id,
            "chunks": [c.model_dump(mode="json") for c in chunks],
            "total_tokens": sum(c.token_count for c in chunks),
        },
        request,
    )


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: str,
    request: Request,
    db=Depends(get_db_session),
    ctx: YardContext = Depends(get_yard_context),
    _user: CurrentUser = Depends(require_permission("knowledge:write")),
):
    service.delete_document(db, ctx, document_id)
    return ok({"id": document_id, "deleted": True}, request)


@router.post("/documents/{document_id}/reparse")
def reparse_document(
    document_id: str,
    request: Request,
    db=Depends(get_db_session),
    ctx: YardContext = Depends(get_yard_context),
):
    """重新解析文档（权限 knowledge:write；failed/queued/processing 可调，ready 拒绝）。"""
    data = service.reparse_document(
        db, ctx, document_id, request_id=getattr(request.state, "request_id", "")
    )
    return ok(ReparseOut(**data).model_dump(mode="json"), request)


@router.post("/search")
def search_knowledge(
    body: SearchRequest,
    request: Request,
    db=Depends(get_db_session),
    ctx: YardContext = Depends(get_yard_context),
):
    hits = service.search(db, ctx, body.query, body.top_k)
    resp = SearchResponse(
        hits=[
            ChunkHitOut(
                document_id=h.document_id,
                chunk_id=h.chunk_id,
                title=h.title,
                source=h.source,
                page_number=h.page_number,
                quote=h.quote,
                score=h.score,
                trust_level=h.trust_level,
                token_count=h.token_count,
                title_path=h.title_path,
            )
            for h in hits
        ]
    )
    return ok(resp.model_dump(mode="json"), request)
