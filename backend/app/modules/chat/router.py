"""对话模式 REST API：``/chat/sessions*``（会话 + 消息 + 附件）。

产品口径：普通对话入口——用户发消息（可带照片/文档附件），后端先判意图：
- 命中"创建能力/装载能力"→ 走能力链路，产出能力卡片；
- 其余 → 复用 supervisor 三级路由（规则→LLM→兜底）选最合适的助手/班组执行。

会话与消息全量持久化，历史可按 seq 回放，路由依据与过程步骤随行可查。

权限口径：任意租户成员可用（与班组运行同口径）；会话对创建者私有，
跨租户与访问他人会话统一 404 CHAT_SESSION_NOT_FOUND（防枚举）。
平台管理员无租户上下文时沿用既有 TENANT_REQUIRED(400)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.chat import attachments as attachments_service
from app.modules.chat import service
from app.modules.chat import stream as chat_stream

router = APIRouter(prefix="/chat/sessions", tags=["chat"])


class SessionCreateRequest(BaseModel):
    """建会话：title 可空（首条消息自动继承标题）。"""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)


class SessionUpdateRequest(BaseModel):
    """改会话标题（非空）。"""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title 不能为空白")
        return v.strip()


class MessageSendRequest(BaseModel):
    """发消息：content 为问题原文；attachment_ids 为随消息引用的附件。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=8000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content 不能为空白")
        return v.strip()

    @field_validator("attachment_ids")
    @classmethod
    def _attachment_ids_clean(cls, v: list[str]) -> list[str]:
        return [str(x).strip() for x in v if str(x).strip()]


@router.post("")
def create_session(
    payload: SessionCreateRequest,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """创建会话（可空标题）。"""
    session = service.create_session(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, title=payload.title
    )
    db.commit()
    return ok(service.session_out(db, session), request)


@router.get("")
def list_sessions(
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """本人会话列表（最近更新在前，含消息数）。契约 §10：{items:[...]}。"""
    return ok(
        {"items": service.list_sessions(db, tenant_id=ctx.tenant_id, created_by=ctx.user_id)},
        request,
    )


@router.get("/{session_id}")
def get_session(
    session_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """会话详情（含消息数）。"""
    session = service.get_session(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, session_id=session_id
    )
    return ok(service.session_out(db, session), request)


@router.patch("/{session_id}")
def rename_session(
    session_id: str,
    payload: SessionUpdateRequest,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """改会话标题。"""
    session = service.rename_session(
        db,
        tenant_id=ctx.tenant_id,
        created_by=ctx.user_id,
        session_id=session_id,
        title=payload.title,
    )
    db.commit()
    return ok(service.session_out(db, session), request)


@router.delete("/{session_id}")
def delete_session(
    session_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """删会话（连带删除全部消息与附件）。"""
    service.delete_session(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, session_id=session_id
    )
    db.commit()
    return ok({"deleted": True}, request)


@router.get("/{session_id}/messages", response_model=None)
def list_messages(
    session_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
    limit: int = Query(default=200, ge=1, le=500),
):
    """会话历史（seq 升序；assistant 行含路由存证与过程步骤）。"""
    rows = service.list_messages(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, session_id=session_id, limit=limit
    )
    return ok({"items": [service.message_out(m) for m in rows]}, request)


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str,
    payload: MessageSendRequest,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """发消息并取回回复（同步一问一答；前端流式不可用时走此端点）。"""
    data = await service.send_message(
        db,
        ctx=ctx,
        session_id=session_id,
        content=payload.content,
        attachment_ids=payload.attachment_ids,
    )
    return ok(data, request)


@router.post("/{session_id}/messages/stream")
async def send_message_stream(
    session_id: str,
    payload: MessageSendRequest,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """发消息并以 SSE 流式返回过程步骤与最终回答。

    这里只做"发言先落库"，随后把**纯数据**交给流：FastAPI 在端点返回 Response
    时就关闭了请求级 Session（``get_db`` 的 teardown），而响应体之后才被发送——
    流执行期间不能持有请求级 Session 的 ORM 对象。
    """
    content = payload.content.strip()[: service.CONTENT_CAP]
    session = service.get_session(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, session_id=session_id
    )
    atts = service.resolve_attachments(
        db, ctx=ctx, session_id=session.id, attachment_ids=payload.attachment_ids
    )
    user_msg = service.persist_user_message(
        db, ctx=ctx, session=session, content=content, attachments=atts
    )
    stream_session_id = session.id
    stream_attachment_ids = [a.id for a in atts]
    db.commit()  # 发言先落库：执行失败也不丢发言痕迹
    return chat_stream.build_stream_response(
        ctx=ctx,
        session_id=stream_session_id,
        content=content,
        attachment_ids=stream_attachment_ids,
        user_message=service.message_out(user_msg),
    )


@router.post("/{session_id}/attachments", status_code=201)
def upload_attachment(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """上传会话附件（照片/文档）：校验 → 存储 → 抽取文本 → 入库。"""
    session = service.get_session(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, session_id=session_id
    )
    att = attachments_service.upload_attachment(db, ctx=ctx, session_id=session.id, file=file)
    db.commit()
    return ok(attachments_service.attachment_out(att), request)


@router.get("/{session_id}/attachments")
def list_attachments(
    session_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """会话附件列表。"""
    session = service.get_session(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, session_id=session_id
    )
    rows = attachments_service.list_attachments(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, session_id=session.id
    )
    return ok({"items": [attachments_service.attachment_out(a) for a in rows]}, request)


@router.delete("/{session_id}/attachments/{attachment_id}")
def delete_attachment(
    session_id: str,
    attachment_id: str,
    request: Request,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db_session),
):
    """删附件（连带删除存储文件）。"""
    session = service.get_session(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, session_id=session_id
    )
    att = attachments_service.get_attachment(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, attachment_id=attachment_id
    )
    if att.session_id != session.id:
        raise AppError("CHAT_ATTACHMENT_NOT_FOUND", "附件不存在或无权访问。", http_status=404)
    attachments_service.delete_attachment(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, attachment_id=attachment_id
    )
    db.commit()
    return ok({"deleted": True}, request)
