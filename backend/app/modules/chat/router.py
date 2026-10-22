"""对话模式 REST API：``/chat/sessions*``（会话 + 消息）。

产品口径：普通对话入口——用户发消息，后端复用 supervisor 三级路由
（规则→LLM→兜底）选最合适的助手/班组执行；会话与消息全量持久化，
历史可按 seq 回放，路由依据随行可查。

权限口径：任意租户成员可用（与班组运行同口径）；会话对创建者私有，
跨租户与访问他人会话统一 404 CHAT_SESSION_NOT_FOUND（防枚举）。
平台管理员无租户上下文时沿用既有 TENANT_REQUIRED(400)。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.deps import TenantContext, get_db_session, get_tenant_context
from app.core.responses import ok
from app.modules.chat import service

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
    """发消息：content 为问题原文（转发给 supervisor 三级路由执行）。"""

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=8000)

    @field_validator("content")
    @classmethod
    def _content_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content 不能为空白")
        return v.strip()


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
    """删会话（连带删除全部消息）。"""
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
    """会话历史（seq 升序；assistant 行含路由存证字段）。"""
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
    """发消息并取回回复：supervisor 三级路由选助手/班组执行（同步请求内完成）。"""
    data = await service.send_message(db, ctx=ctx, session_id=session_id, content=payload.content)
    return ok(data, request)
