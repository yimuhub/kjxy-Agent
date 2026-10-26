"""对话模式服务层：会话 CRUD + 消息持久化 + 复用 supervisor 执行 + 消息/会话级 Token 统计。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.progress import (
    PHASE_GENERATING,
    PHASE_READING,
    PHASE_ROUTING,
    STEP_DONE,
    STEP_FAILED,
    STEP_RUNNING,
    StepCallback,
    StepRecorder,
)
from app.modules.agents import declarative, supervisor
from app.modules.agents.models import YardAgent
from app.modules.audit.service import log_audit
from app.modules.chat import attachments as attachments_service
from app.modules.chat import capability as capability_service
from app.modules.chat import intent as intent_service
from app.modules.chat.models import (
    MSG_FAILED,
    MSG_OK,
    ROLE_ASSISTANT,
    ROLE_USER,
    ChatAttachment,
    ChatMessage,
    ChatParticipant,
    ChatSession,
)
from app.modules.knowledge.chunking import estimate_token_count

log = get_logger(__name__)

TITLE_CAP = 60
CONTENT_CAP = 8000
ROUTED_LOCAL = "local"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def derive_title(content: str) -> str:
    """首句 → 会话标题：取首个非空行，超长截断加省略号。"""
    for line in (content or "").splitlines():
        line = line.strip()
        if line:
            return line[:TITLE_CAP] + ("…" if len(line) > TITLE_CAP else "")
    return ""


def next_seq(db: Session, session_id: str) -> int:
    """会话内下一序号（max+1；会话为空从 1 开始）。"""
    cur = db.scalar(select(func.max(ChatMessage.seq)).where(ChatMessage.session_id == session_id))
    return int(cur or 0) + 1


def _session_token_summary(db: Session, session_id: str, yard_id: str) -> dict[str, int]:
    rows = db.scalars(
        select(ChatMessage).where(
            ChatMessage.session_id == session_id,
            ChatMessage.yard_id == yard_id,
        )
    ).all()
    in_tok = 0
    out_tok = 0
    for m in rows:
        tu = getattr(m, "token_usage_json", None)
        if isinstance(tu, dict):
            in_tok += int(tu.get("input_tokens") or 0)
            out_tok += int(tu.get("output_tokens") or 0)
        elif m.role == ROLE_ASSISTANT and m.content:
            out_tok += estimate_token_count(m.content)
        elif m.role == ROLE_USER and m.content:
            in_tok += estimate_token_count(m.content)
    return {
        "message_count": len(rows),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
    }


def session_out(db: Session, session: ChatSession) -> dict[str, Any]:
    tok_meta = _session_token_summary(db, session.id, session.yard_id)
    target = getattr(session, "target", None) or "auto"
    target_code = getattr(session, "target_code", None)
    target_name: str | None = None
    if target == "agent" and target_code:
        # 会话列表的「智能体」标识：按 agent_code 解析展示名，改名前的老会话也能显示
        target_name = (
            db.scalar(
                select(YardAgent.name).where(
                    YardAgent.yard_id == session.yard_id,
                    YardAgent.agent_code == target_code,
                )
            )
            or target_code
        )
    participant_count = int(
        db.scalar(
            select(func.count())
            .select_from(ChatParticipant)
            .where(ChatParticipant.session_id == session.id)
        )
        or 0
    )
    return {
        "id": session.id,
        "title": session.title,
        "created_by": session.created_by,
        "created_at": session.created_at.isoformat() if session.created_at else "",
        "updated_at": session.updated_at.isoformat() if session.updated_at else "",
        "message_count": tok_meta["message_count"],
        "input_tokens": tok_meta["input_tokens"],
        "output_tokens": tok_meta["output_tokens"],
        "total_tokens": tok_meta["total_tokens"],
        "target": target,
        "target_code": target_code,
        "target_name": target_name,
        "participant_count": participant_count,
    }


def message_out(msg: ChatMessage) -> dict[str, Any]:
    routed = msg.routed_by or ""
    tu = getattr(msg, "token_usage_json", None)
    if tu is None and msg.role == ROLE_ASSISTANT and msg.content:
        est_out = estimate_token_count(msg.content)
        tu = {
            "input_tokens": 0,
            "output_tokens": est_out,
            "total_tokens": est_out,
            "model": "system",
            "estimated": True,
        }
    elif tu is None and msg.role == ROLE_USER and msg.content:
        est_in = estimate_token_count(msg.content)
        tu = {
            "input_tokens": est_in,
            "output_tokens": 0,
            "total_tokens": est_in,
            "estimated": True,
        }
    return {
        "id": msg.id,
        "seq": msg.seq,
        "role": msg.role,
        "content": msg.content,
        "status": msg.status,
        "routed_by": msg.routed_by,
        "routed_label": supervisor.routed_label(routed),
        "routed_tone": supervisor.routed_tone(routed),
        "choice": msg.choice_json,
        "reason": msg.reason,
        "error_code": msg.error_code,
        "payload": msg.payload_json,
        "steps": msg.steps_json,
        "attachments": msg.attachments_json,
        "token_usage": tu,
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
    }


# ---------------------------------------------------------------------------
# 会话 CRUD（全部带小院 + 创建者边界）
# ---------------------------------------------------------------------------


def get_session(db: Session, *, yard_id: str, created_by: str, session_id: str) -> ChatSession:
    row = db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.yard_id == yard_id,
            ChatSession.created_by == created_by,
        )
    )
    if row is None:
        raise AppError("CHAT_SESSION_NOT_FOUND", "会话不存在或无权访问。", http_status=404)
    return row


def create_session(
    db: Session,
    *,
    yard_id: str,
    created_by: str,
    title: str | None = None,
    request_id: str = "",
    target: str = "auto",
    target_code: str | None = None,
) -> ChatSession:
    clean = (title or "").strip()[:200] or None
    session = ChatSession(
        yard_id=yard_id,
        created_by=created_by,
        title=clean,
        target=target or "auto",
        target_code=(target_code or None) if target == "agent" else None,
    )
    db.add(session)
    db.flush()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=created_by,
        action="chat_session_created",
        resource_type="chat_session",
        resource_id=session.id,
        result="success",
        request_id=request_id,
        metadata={"title": clean},
    )
    return session


def list_sessions(db: Session, *, yard_id: str, created_by: str) -> list[dict[str, Any]]:
    """本人会话列表（最近更新在前）+ 各自消息数与累计 Token。"""
    rows = list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.yard_id == yard_id, ChatSession.created_by == created_by)
            .order_by(ChatSession.updated_at.desc())
        )
    )
    return [session_out(db, r) for r in rows]


def rename_session(
    db: Session, *, yard_id: str, created_by: str, session_id: str, title: str
) -> ChatSession:
    clean = (title or "").strip()
    if not clean:
        raise AppError("INVALID_REQUEST", "会话标题不能为空。", http_status=400)
    session = get_session(db, yard_id=yard_id, created_by=created_by, session_id=session_id)
    session.title = clean[:200]
    session.updated_at = _utcnow()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=created_by,
        action="chat_session_updated",
        resource_type="chat_session",
        resource_id=session.id,
        result="success",
        request_id="",
        metadata={"title": session.title},
    )
    return session


def delete_session(db: Session, *, yard_id: str, created_by: str, session_id: str) -> None:
    session = get_session(db, yard_id=yard_id, created_by=created_by, session_id=session_id)
    db.execute(
        delete(ChatMessage).where(
            ChatMessage.session_id == session.id, ChatMessage.yard_id == yard_id
        )
    )
    attachments_service.delete_session_attachments(
        db, yard_id=yard_id, created_by=created_by, session_id=session.id
    )
    db.delete(session)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=created_by,
        action="chat_session_deleted",
        resource_type="chat_session",
        resource_id=session_id,
        result="success",
        request_id="",
        metadata=None,
    )


def list_messages(
    db: Session, *, yard_id: str, created_by: str, session_id: str, limit: int = 200
) -> list[ChatMessage]:
    get_session(db, yard_id=yard_id, created_by=created_by, session_id=session_id)
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.yard_id == yard_id)
            .order_by(ChatMessage.seq.asc())
            .limit(limit)
        )
    )


# ---------------------------------------------------------------------------
# 附件解析
# ---------------------------------------------------------------------------


def resolve_attachments(
    db: Session, *, ctx: YardContext, session_id: str, attachment_ids: list[str] | None
) -> list[ChatAttachment]:
    ids = [str(x).strip() for x in (attachment_ids or []) if str(x).strip()]
    if not ids:
        return []
    unique: list[str] = []
    for x in ids:
        if x not in unique:
            unique.append(x)
    rows = db.scalars(
        select(ChatAttachment).where(
            ChatAttachment.id.in_(unique),
            ChatAttachment.yard_id == ctx.yard_id,
            ChatAttachment.created_by == ctx.user_id,
            ChatAttachment.session_id == session_id,
        )
    )
    found = {row.id: row for row in rows}
    missing = [x for x in unique if x not in found]
    if missing:
        raise AppError(
            "CHAT_ATTACHMENT_NOT_FOUND",
            "附件不存在或无权访问（可能属于其他会话）。",
            http_status=404,
        )
    return [found[x] for x in unique]


# ---------------------------------------------------------------------------
# @ 点名候选（只读，不落库）
# ---------------------------------------------------------------------------


def mention_candidates(db: Session, *, ctx: YardContext) -> list[dict[str, Any]]:
    cands = supervisor.list_candidates(db, ctx.yard_id, user_id=ctx.user_id)
    agent_ids = [str(c["id"]) for c in cands if c["kind"] == "agent"]
    extras: dict[str, dict[str, Any]] = {}
    if agent_ids:
        rows = list(
            db.scalars(
                select(YardAgent).where(
                    YardAgent.id.in_(agent_ids), YardAgent.yard_id == ctx.yard_id
                )
            )
        )
        extras = {str(a["id"]): a for a in declarative.to_out_list(db, rows)}
    items: list[dict[str, Any]] = []
    for c in cands:
        extra = extras.get(str(c["id"]), {})
        items.append(
            {
                "kind": c["kind"],
                "id": c["id"],
                "code": c.get("code"),
                "name": c["name"],
                "display_name": extra.get("display_name") or c["name"],
                "visibility": extra.get("visibility"),
                "emoji": extra.get("emoji"),
                "blurb": c.get("blurb") or "",
            }
        )
    return items


# ---------------------------------------------------------------------------
# 消息持久化（用户消息 / 助手消息 / 失败消息）
# ---------------------------------------------------------------------------


def _attachment_ref(att: ChatAttachment) -> dict[str, Any]:
    return {
        "id": att.id,
        "filename": att.filename,
        "kind": att.kind,
        "mime": att.mime,
        "size_bytes": att.size_bytes,
        "status": att.status,
    }


def persist_user_message(
    db: Session,
    *,
    ctx: YardContext,
    session: ChatSession,
    content: str,
    attachments: list[ChatAttachment],
) -> ChatMessage:
    in_tok = estimate_token_count(content)
    msg = ChatMessage(
        session_id=session.id,
        yard_id=ctx.yard_id,
        seq=next_seq(db, session.id),
        role=ROLE_USER,
        content=content,
        status=MSG_OK,
        attachments_json=[_attachment_ref(a) for a in attachments] or None,
        token_usage_json={
            "input_tokens": in_tok,
            "output_tokens": 0,
            "total_tokens": in_tok,
            "estimated": True,
        },
    )
    db.add(msg)
    if not session.title:
        session.title = derive_title(content)
    session.updated_at = _utcnow()
    return msg


def persist_assistant_message(
    db: Session,
    *,
    ctx: YardContext,
    session: ChatSession,
    content: str,
    routed_by: str = "",
    choice: dict[str, Any] | None = None,
    reason: str = "",
    payload: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    status: str = MSG_OK,
    error_code: str = "",
    token_usage: dict[str, Any] | None = None,
) -> ChatMessage:
    """助手消息落库（路由存证 + 过程步骤 + 结构化载荷 + Token 统计）。"""
    if token_usage is None and content:
        out_tok = estimate_token_count(content)
        token_usage = {
            "input_tokens": 0,
            "output_tokens": out_tok,
            "total_tokens": out_tok,
            "model": "local",
            "estimated": True,
        }
    msg = ChatMessage(
        session_id=session.id,
        yard_id=ctx.yard_id,
        seq=next_seq(db, session.id),
        role=ROLE_ASSISTANT,
        content=content[:CONTENT_CAP],
        status=status,
        routed_by=routed_by[:10] or None,
        choice_json=choice or None,
        reason=reason[:200] or None,
        error_code=error_code[:50] or None,
        payload_json=payload,
        steps_json=steps or None,
        token_usage_json=token_usage,
    )
    db.add(msg)
    session.updated_at = _utcnow()
    return msg


def _persist_failure(
    db: Session,
    session: ChatSession,
    ctx: YardContext,
    *,
    code: str,
    message: str,
    steps: list[dict[str, Any]] | None = None,
) -> None:
    failed = ChatMessage(
        session_id=session.id,
        yard_id=ctx.yard_id,
        seq=next_seq(db, session.id),
        role=ROLE_ASSISTANT,
        content=message[:CONTENT_CAP],
        status=MSG_FAILED,
        error_code=code[:50],
        steps_json=steps or None,
    )
    db.add(failed)
    session.updated_at = _utcnow()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="chat_message_send",
        resource_type="chat_session",
        resource_id=session.id,
        result="failed",
        request_id=ctx.request_id,
        metadata={"user_seq": failed.seq - 1, "assistant_seq": failed.seq, "error_code": code},
    )
    db.commit()


# ---------------------------------------------------------------------------
# 一次对话的执行（问答链路 / 能力链路）
# ---------------------------------------------------------------------------


async def execute_turn(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachments: list[ChatAttachment],
    on_step: StepCallback | None = None,
    target_code: str | None = None,
) -> dict[str, Any]:
    """执行一轮对话，返回 {content, routed_by, choice, reason, payload, token_usage}。"""
    intent = intent_service.detect(content)

    if intent.kind == intent_service.INTENT_CREATE:
        if on_step:
            on_step(PHASE_GENERATING, "正在把你的需求整理成能力草稿", STEP_RUNNING)
        try:
            card, note = await capability_service.generate_capability(
                db, ctx=ctx, request_text=content
            )
        except AppError:
            if on_step:
                on_step(PHASE_GENERATING, "能力生成失败", STEP_FAILED)
            raise
        if on_step:
            on_step(PHASE_GENERATING, "能力草稿已生成", STEP_DONE)
        in_tok = estimate_token_count(content)
        out_tok = estimate_token_count(note) + estimate_token_count(str(card))
        return {
            "content": note,
            "routed_by": ROUTED_LOCAL,
            "choice": {"kind": "agent", "id": card.get("agent_id") or "", "name": card["name"]},
            "reason": intent.reason,
            "payload": card,
            "token_usage": {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "model": "capability_builder",
            },
        }

    if intent.kind == intent_service.INTENT_INSTALL:
        from app.modules.market.service import install_from_chat

        if on_step:
            on_step(PHASE_GENERATING, "正在从能力市场匹配并装载", STEP_RUNNING)
        install_result = install_from_chat(db, ctx=ctx, request_text=content)
        if on_step:
            on_step(PHASE_GENERATING, install_result["summary"], STEP_DONE)
        in_tok = estimate_token_count(content)
        out_tok = estimate_token_count(install_result["summary"])
        return {
            "content": install_result["summary"],
            "routed_by": ROUTED_LOCAL,
            "choice": {"kind": "agent", "id": "", "name": install_result["name"]},
            "reason": intent.reason,
            "payload": install_result["card"],
            "token_usage": {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "model": "market_installer",
                "estimated": True,
            },
        }

    # ---- 文档链路：产出 Word/Excel/PPT/PDF/Markdown 并落为会话附件 ----
    if intent.kind == intent_service.INTENT_DOC:
        from app.modules.chat import document as document_service

        doc_context = ""
        if attachments:
            if on_step:
                on_step(PHASE_READING, f"正在阅读 {len(attachments)} 个附件", STEP_RUNNING)
            doc_context = attachments_service.build_attachment_context(attachments)
            if on_step:
                on_step(PHASE_READING, f"已读取 {len(attachments)} 个附件", STEP_DONE)
        if on_step:
            on_step(PHASE_GENERATING, "正在整理文档内容", STEP_RUNNING)
        card, note, doc_usage = await document_service.generate_document_from_chat(
            db,
            ctx=ctx,
            session_id=session_id,
            request_text=content,
            context=doc_context,
        )
        if on_step:
            on_step(PHASE_GENERATING, f"文档已生成：{card['filename']}", STEP_DONE)
        return {
            "content": note,
            "routed_by": ROUTED_LOCAL,
            "choice": {"kind": "agent", "id": "", "name": "文档生成"},
            "reason": intent.reason,
            "payload": card,
            "token_usage": doc_usage,
        }

    # ---- 问答链路：附件只进上下文，不参与路由打分 ----
    context = ""
    if attachments:
        if on_step:
            on_step(PHASE_READING, f"正在阅读 {len(attachments)} 个附件", STEP_RUNNING)
        context = attachments_service.build_attachment_context(attachments)
        ready = sum(1 for a in attachments if a.status == "ready")
        if on_step:
            on_step(
                PHASE_READING,
                f"已读取 {len(attachments)} 个附件（{ready} 个可解析）",
                STEP_DONE,
            )

    if target_code:
        if on_step:
            on_step(PHASE_ROUTING, f"按 @ 指定定向执行「{target_code}」", STEP_RUNNING)
        question = f"{content}\n\n{context}" if context else content
        data = await supervisor.ask_directed(db, ctx=ctx, question=question, target=target_code)
        chosen_name = str((data.get("choice") or {}).get("name") or target_code)
        if on_step:
            on_step(PHASE_ROUTING, f"已定向「{chosen_name}」（定向执行）", STEP_DONE)
    else:
        data = await supervisor.ask(db, ctx=ctx, question=content, on_step=on_step, context=context)
    result: dict[str, Any] = dict(data.get("result") or {})
    choice: dict[str, Any] = dict(data.get("choice") or {})
    if result.get("run_id"):
        choice["run_id"] = result["run_id"]

    answer_text = str(result.get("answer") or "")
    token_usage = result.get("token_usage")
    if not isinstance(token_usage, dict):
        in_tok = estimate_token_count(f"{content}\n{context}")
        out_tok = estimate_token_count(answer_text)
        token_usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "estimated": True,
        }

    return {
        "content": answer_text,
        "routed_by": str(data.get("routed_by") or ""),
        "choice": choice,
        "reason": str(data.get("reason") or ""),
        "payload": None,
        "token_usage": token_usage,
    }


async def run_and_persist(
    db: Session,
    *,
    ctx: YardContext,
    session: ChatSession,
    content: str,
    attachments: list[ChatAttachment],
    on_step: StepCallback | None = None,
    recorder: StepRecorder | None = None,
    target_code: str | None = None,
) -> dict[str, Any]:
    steps = recorder or StepRecorder()

    def _cb(phase: str, detail: str, status: str) -> None:
        steps.emit(phase, detail, status)
        if on_step is not None:
            on_step(phase, detail, status)

    try:
        outcome = await execute_turn(
            db,
            ctx=ctx,
            session_id=session.id,
            content=content,
            attachments=attachments,
            on_step=_cb,
            target_code=target_code,
        )
    except AppError as e:
        steps.fail(PHASE_GENERATING, e.message)
        _persist_failure(db, session, ctx, code=e.code, message=e.message, steps=steps.steps)
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("chat send unexpected error: %s", e)
        steps.fail(PHASE_GENERATING, "服务暂时不可用")
        _persist_failure(
            db, session, ctx, code="INTERNAL_ERROR", message="服务暂时不可用。", steps=steps.steps
        )
        raise

    steps.finish()
    assistant_msg = persist_assistant_message(
        db,
        ctx=ctx,
        session=session,
        content=outcome["content"],
        routed_by=outcome["routed_by"],
        choice=outcome["choice"],
        reason=outcome["reason"],
        payload=outcome["payload"],
        steps=steps.steps or None,
        token_usage=outcome.get("token_usage"),
    )
    return {
        "assistant_message": assistant_msg,
        "content": outcome["content"],
        "routed_by": outcome["routed_by"],
        "choice": outcome["choice"],
        "reason": outcome["reason"],
        "payload": outcome["payload"],
        "steps": steps.steps,
        "token_usage": outcome.get("token_usage"),
    }


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------


async def send_message(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachment_ids: list[str] | None = None,
    on_step: StepCallback | None = None,
    target_code: str | None = None,
) -> dict[str, Any]:
    content = (content or "").strip()[:CONTENT_CAP]
    if not content:
        raise AppError("INVALID_REQUEST", "消息内容不能为空。", http_status=400)
    session = get_session(db, yard_id=ctx.yard_id, created_by=ctx.user_id, session_id=session_id)
    attachments = resolve_attachments(
        db, ctx=ctx, session_id=session.id, attachment_ids=attachment_ids
    )
    user_msg = persist_user_message(
        db, ctx=ctx, session=session, content=content, attachments=attachments
    )
    db.commit()

    outcome = await run_and_persist(
        db,
        ctx=ctx,
        session=session,
        content=content,
        attachments=attachments,
        on_step=on_step,
        target_code=target_code,
    )
    assistant_msg = outcome["assistant_message"]
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="chat_message_send",
        resource_type="chat_session",
        resource_id=session.id,
        result="success",
        request_id=ctx.request_id,
        metadata={
            "user_seq": user_msg.seq,
            "assistant_seq": assistant_msg.seq,
            "routed_by": assistant_msg.routed_by,
            "kind": (outcome["choice"] or {}).get("kind"),
            "name": (outcome["choice"] or {}).get("name"),
            "attachments": len(attachments),
            "tokens": (outcome.get("token_usage") or {}).get("total_tokens"),
        },
    )
    db.commit()
    return {
        "session": session_out(db, session),
        "user_message": message_out(user_msg),
        "assistant_message": message_out(assistant_msg),
    }


__all__ = [
    "CONTENT_CAP",
    "ROUTED_LOCAL",
    "create_session",
    "delete_session",
    "derive_title",
    "execute_turn",
    "get_session",
    "list_messages",
    "list_sessions",
    "mention_candidates",
    "message_out",
    "next_seq",
    "persist_assistant_message",
    "persist_user_message",
    "rename_session",
    "resolve_attachments",
    "run_and_persist",
    "send_message",
    "session_out",
]
