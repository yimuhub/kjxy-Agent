"""对话模式 REST API：``/chat/sessions*``（会话 + 消息 + 群聊名单 + 附件）。

另含集合资源 ``/chat/mention-candidates``（@ 点名候选，见 ``mention_router``）。

产品口径：普通对话入口——用户发消息（可带照片/文档附件），后端先判意图：
- 命中"创建能力/装载能力"→ 走能力链路，产出能力卡片（意图优先，不被 @ 点名劫持）；
- 带 ``target_code``（@ 点名）→ 交 ``supervisor.ask_directed`` 定向执行；
- 带 ``policy``（all|named|auto）→ 交 ``chat.group`` 群聊编排，每位在场助手各答一条；
- 其余 → 复用 supervisor 三级路由（规则→LLM→兜底）选最合适的助手/班组执行。

会话与消息全量持久化，历史可按 seq 回放，路由依据与过程步骤随行可查；
助手消息另发 ``routed_label``/``routed_tone`` 徽章文案（后端单点字典，前端只渲染）。

权限口径：任意小院成员可用（与班组运行同口径）；会话对创建者私有，
跨小院与访问他人会话统一 404 CHAT_SESSION_NOT_FOUND（防枚举）。
平台管理员无小院上下文时沿用既有 YARD_REQUIRED(400)。
"""

from __future__ import annotations

from typing import Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.deps import YardContext, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.chat import attachments as attachments_service
from app.modules.chat import group, multimodal, schemas, service
from app.modules.chat import stream as chat_stream
from app.modules.docs import service as docs_service

router = APIRouter(prefix="/chat/sessions", tags=["chat"])
#: 集合级多模态入口（不属于某个会话）：/chat/transcribe
media_router = APIRouter(prefix="/chat", tags=["chat"])

# @ 点名候选是**集合资源**（不属于任何会话），单挂一个 /chat 前缀的路由：
# 若挤进 /chat/sessions，会被 /chat/sessions/{session_id} 的位置参数吞掉。
mention_router = APIRouter(prefix="/chat", tags=["chat"])


class SessionCreateRequest(BaseModel):
    """建会话：title 可空（首条消息自动继承标题）。

    target/target_code 标记会话的固定执行对象（@ 点名弹窗/「使用」弹窗建会话时带），
    会话列表据此区分「普通对话 / 某个智能体 / 群聊」。
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    target: Literal["auto", "agent", "team"] = "auto"
    target_code: str | None = Field(default=None, max_length=100)


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


class DocumentCreateRequest(BaseModel):
    """按 DocModel 生成文档：format 缺省 word，model 为结构化文档内容。"""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    format: str = Field(default="docx", max_length=10)
    doc_model: dict[str, Any] = Field(alias="model")


# 发消息的请求模型在 schemas.py（含群聊 policy/target/target_code）：/messages 与
# /messages/group* 复用同一份，避免"两套形状"漂移——带 policy 走群聊、不带走单聊。


def _commit_group_turn(
    db: Session,
    ctx: YardContext,
    session: Any,
    payload: schemas.MessageSendRequest,
) -> tuple[str, list[str], list[dict[str, Any]], Any]:
    """群聊一轮的落库前置：名单/策略校验**在写之前**，随后发言落库并提交。

    校验失败（空名单 ``GROUP_NO_PARTICIPANTS``、点名不在名单
    ``GROUP_TARGET_NOT_PARTICIPANT``）在落库前 4xx 抛出，绝不留一条没人回答的用户
    发言。返回 ``(content, attachment_ids, participants, user_message)``——名单
    取成**纯数据**是流式链路的硬约束（请求级 Session 在端点返回时即关闭）。
    """
    content = payload.content.strip()[: service.CONTENT_CAP]
    participants = group.list_participants(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session.id
    )
    group.pick_members(payload.policy or "", participants, payload.group_target)
    atts = service.resolve_attachments(
        db, ctx=ctx, session_id=session.id, attachment_ids=payload.attachment_ids
    )
    user_msg = service.persist_user_message(
        db, ctx=ctx, session=session, content=content, attachments=atts
    )
    attachment_ids = [a.id for a in atts]
    db.commit()  # 发言先落库：后面执行失败也不丢发言痕迹
    return content, attachment_ids, participants, user_msg


def _group_response(db: Session, session: Any, user_msg: Any, outcome: dict[str, Any]) -> dict:
    """群聊一轮的同步响应数据（``/messages`` 带 policy 与 ``/messages/group`` 共用）。"""
    return {
        "session": service.session_out(db, session),
        "user_message": service.message_out(user_msg),
        "turn_id": outcome["turn_id"],
        "policy": outcome["policy"],
        "member_count": outcome["member_count"],
        "messages": outcome["messages"],
        "answered_names": outcome["answered_names"],
        "not_answered": outcome["not_answered"],
        "notice": outcome["notice"],
    }


@router.post("")
def create_session(
    payload: SessionCreateRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """创建会话（可空标题）。"""
    session = service.create_session(
        db,
        yard_id=ctx.yard_id,
        created_by=ctx.user_id,
        title=payload.title,
        target=payload.target,
        target_code=payload.target_code,
    )
    db.commit()
    return ok(service.session_out(db, session), request)


@router.get("")
def list_sessions(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """本人会话列表（最近更新在前，含消息数）。契约 §10：{items:[...]}。"""
    return ok(
        {"items": service.list_sessions(db, yard_id=ctx.yard_id, created_by=ctx.user_id)},
        request,
    )


@router.get("/{session_id}")
def get_session(
    session_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """会话详情（含消息数）。"""
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    return ok(service.session_out(db, session), request)


@router.patch("/{session_id}")
def rename_session(
    session_id: str,
    payload: SessionUpdateRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """改会话标题。"""
    session = service.rename_session(
        db,
        yard_id=ctx.yard_id,
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
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """删会话（连带删除全部消息与附件）。"""
    service.delete_session(db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id)
    db.commit()
    return ok({"deleted": True}, request)


@router.get("/{session_id}/messages", response_model=None)
def list_messages(
    session_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
    limit: int = Query(default=200, ge=1, le=500),
):
    """会话历史（seq 升序；assistant 行含路由存证与过程步骤）。"""
    rows = service.list_messages(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id, limit=limit
    )
    return ok({"items": [service.message_out(m) for m in rows]}, request)


@router.post("/{session_id}/messages")
async def send_message(
    session_id: str,
    payload: schemas.MessageSendRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """发消息并取回回复：不带 ``policy`` 走单聊（三级路由 / @ 定向），带则进群聊。

    群聊一支的响应比单聊多 turn_id/policy/member_count/messages/answered_names/
    not_answered/notice（每位应答者一条助手消息，未回答者一条诚实说明）；
    单聊一支的形状与历史完全一致：{session, user_message, assistant_message}。
    """
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    if payload.policy:
        content, attachment_ids, participants, user_msg = _commit_group_turn(
            db, ctx, session, payload
        )
        outcome = await group.run_group_turn(
            db,
            ctx=ctx,
            session_id=session.id,
            content=content,
            attachment_ids=attachment_ids,
            policy=payload.policy,
            target=payload.group_target,
            participants=participants,
        )
        return ok(_group_response(db, session, user_msg, outcome), request)
    data = await service.send_message(
        db,
        ctx=ctx,
        session_id=session_id,
        content=payload.content,
        attachment_ids=payload.attachment_ids,
        target_code=payload.target_code,
    )
    return ok(data, request)


@router.post("/{session_id}/messages/stream")
async def send_message_stream(
    session_id: str,
    payload: schemas.MessageSendRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """发消息并以 SSE 流式返回过程步骤与最终回答（带 policy 即群聊一轮）。

    这里只做"发言先落库"，随后把**纯数据**交给流：FastAPI 在端点返回 Response
    时就关闭了请求级 Session（``get_db`` 的 teardown），而响应体之后才被发送——
    流执行期间不能持有请求级 Session 的 ORM 对象（群聊名单同理必须先取成字典列表）。
    """
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    content = payload.content.strip()[: service.CONTENT_CAP]
    if payload.policy:
        content, attachment_ids, participants, user_msg = _commit_group_turn(
            db, ctx, session, payload
        )
        return chat_stream.build_group_stream_response(
            ctx=ctx,
            session_id=session.id,
            content=content,
            attachment_ids=attachment_ids,
            policy=payload.policy,
            target=payload.group_target,
            participants=participants,
            user_message=service.message_out(user_msg),
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
        target_code=payload.target_code,
    )


@router.get("/{session_id}/participants")
def get_participants(
    session_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """群聊成员名单（position 升序；会话私有边界同消息）。"""
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    items = group.list_participants(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session.id
    )
    return ok({"items": items, "max": group.MAX_PARTICIPANTS}, request)


@router.put("/{session_id}/participants")
def set_participants(
    session_id: str,
    payload: schemas.ParticipantSetRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """整表覆盖成员名单（PUT 幂等，顺序即 position；空数组=清空群聊名单）。

    成员必须是**本小院已发布助手**：跨小院/未发布/班组 id 一律
    400 GROUP_PARTICIPANT_INVALID（候选集复用 supervisor，不另写一份边界）。
    响应与 GET 同形状，前端覆盖一次即可刷新，不用再回读。
    """
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    items = group.replace_participants(
        db, ctx=ctx, session_id=session.id, agent_ids=payload.agent_ids
    )
    db.commit()
    return ok({"items": items, "max": group.MAX_PARTICIPANTS}, request)


@mention_router.get("/mention-candidates")
def mention_candidates(
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """@ 点名候选（本小院对当前用户可见的已发布助手 + 班组，id 升序）。

    可见性口径**完全复用** supervisor/declarative 那份过滤（公开/本院共享/
    自己的私有），不在 chat 模块另写一份 SQL——两份边界迟早漂移，漂移即越权。
    """
    return ok({"items": service.mention_candidates(db, ctx=ctx)}, request)


@router.post("/{session_id}/messages/group")
async def send_group_message(
    session_id: str,
    payload: schemas.GroupTurnRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """群聊一轮（同步版；与 ``/messages`` 带 policy 完全同链路，只是 policy 必有值）。

    策略/名单校验在发言落库**之前**——请求本身不合法（空名单、点名不在名单）
    就 4xx 返回，不留一条没人回答的用户消息。
    """
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    content, attachment_ids, participants, user_msg = _commit_group_turn(db, ctx, session, payload)
    outcome = await group.run_group_turn(
        db,
        ctx=ctx,
        session_id=session.id,
        content=content,
        attachment_ids=attachment_ids,
        policy=payload.policy,
        target=payload.group_target,
        participants=participants,
    )
    return ok(_group_response(db, session, user_msg, outcome), request)


@router.post("/{session_id}/messages/group/stream")
async def send_group_message_stream(
    session_id: str,
    payload: schemas.GroupTurnRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """群聊一轮的 SSE 版：chat-init → step/member-event* → message* → chat-end。

    与单聊流同一口径：端点只做"发言先落库 + 名单取成纯数据"，响应体流执行期间
    不再持有请求级 Session 的 ORM 对象（FastAPI 在返回 Response 时就关闭了它）。
    """
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    content, attachment_ids, participants, user_msg = _commit_group_turn(db, ctx, session, payload)
    return chat_stream.build_group_stream_response(
        ctx=ctx,
        session_id=session.id,
        content=content,
        attachment_ids=attachment_ids,
        policy=payload.policy,
        target=payload.group_target,
        participants=participants,
        user_message=service.message_out(user_msg),
    )


@router.post("/{session_id}/attachments", status_code=201)
def upload_attachment(
    session_id: str,
    request: Request,
    file: UploadFile = File(...),
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """上传会话附件（照片/文档）：校验 → 存储 → 抽取文本 → 入库。"""
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    att = attachments_service.upload_attachment(db, ctx=ctx, session_id=session.id, file=file)
    db.commit()
    return ok(attachments_service.attachment_out(att), request)


@router.get("/{session_id}/attachments")
def list_attachments(
    session_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """会话附件列表。"""
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    rows = attachments_service.list_attachments(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session.id
    )
    return ok({"items": [attachments_service.attachment_out(a) for a in rows]}, request)


@router.delete("/{session_id}/attachments/{attachment_id}")
def delete_attachment(
    session_id: str,
    attachment_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """删附件（连带删除存储文件）。"""
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    att = attachments_service.get_attachment(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, attachment_id=attachment_id
    )
    if att.session_id != session.id:
        raise AppError("CHAT_ATTACHMENT_NOT_FOUND", "附件不存在或无权访问。", http_status=404)
    attachments_service.delete_attachment(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, attachment_id=attachment_id
    )
    db.commit()
    return ok({"deleted": True}, request)


@router.post("/{session_id}/attachments/{attachment_id}/vision")
async def recognize_attachment_image(
    session_id: str,
    attachment_id: str,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """图片理解（用户手动触发，明确计费）：结果写回附件文本，随后随消息进入上下文。"""
    session = service.get_session(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id
    )
    att = attachments_service.get_attachment(
        db, yard_id=ctx.yard_id, created_by=ctx.user_id, attachment_id=attachment_id
    )
    if att.session_id != session.id:
        raise AppError("CHAT_ATTACHMENT_NOT_FOUND", "附件不存在或无权访问。", http_status=404)
    result = await multimodal.recognize_image(db, ctx, att)
    return ok({**result, "attachment": attachments_service.attachment_out(att)}, request)


@media_router.post("/transcribe")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """语音转写：音频只在内存中转发给上游，不落库（隐私最小化）。"""
    data = await multimodal.transcribe_audio(db, ctx, file)
    return ok(data, request)


def _document_urls(session_id: str, attachment_id: str) -> dict[str, str]:
    base = f"/chat/sessions/{session_id}/attachments/{attachment_id}"
    return {"preview_url": f"{base}/preview", "download_url": f"{base}/download"}


@router.post("/{session_id}/documents", status_code=201)
def create_document(
    session_id: str,
    payload: DocumentCreateRequest,
    request: Request,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """按 DocModel 生成文档（md/docx/xlsx/pptx/pdf），落为会话附件并返回卡片载荷。"""
    att = docs_service.generate_document(
        db,
        ctx=ctx,
        session_id=session_id,
        fmt=payload.format,
        raw_model=payload.doc_model,
    )
    document = docs_service.document_out(att)
    return ok(
        {**document, "document": document, **_document_urls(session_id, att.id)},
        request,
    )


@router.get("/{session_id}/attachments/{attachment_id}/preview", response_class=HTMLResponse)
def preview_document(
    session_id: str,
    attachment_id: str,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """在线预览：直接由 DocModel 快照渲染自包含 HTML（CSP sandbox，无脚本无外链）。

    上传附件没有 DocModel 快照 → 415 DOC_PREVIEW_UNSUPPORTED，请走下载。
    """
    html = docs_service.preview_html(
        db, ctx=ctx, session_id=session_id, attachment_id=attachment_id
    )
    return HTMLResponse(
        content=html,
        headers={
            "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/{session_id}/attachments/{attachment_id}/download")
def download_document(
    session_id: str,
    attachment_id: str,
    ctx: YardContext = Depends(get_yard_context),
    db: Session = Depends(get_db_session),
):
    """原文件下载（中文文件名按 RFC 5987 编码，越权与不存在统一 404）。"""
    data, mime, filename = docs_service.download_bytes(
        db, ctx=ctx, session_id=session_id, attachment_id=attachment_id
    )
    # 头部只放 ASCII 安全字符；中文原名走 RFC 5987 的 filename*（quote 全转义）
    ascii_name = (
        "".join(
            c if 32 <= ord(c) < 127 and c not in '"\\' else "_"
            for c in filename.encode("ascii", "ignore").decode()
        ).strip("_ ")
        or "document"
    )
    disposition = (
        f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename, safe="")}'
    )
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": disposition,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
