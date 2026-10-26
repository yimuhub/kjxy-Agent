"""对话模式服务层：会话 CRUD + 消息持久化 + 复用 supervisor 执行。

执行口径（send_message 时序）：
1. 会话定位带 tenant_id + created_by 双重边界（跨租户/无权统一 404）；
2. 用户消息**先落库再执行**（独立 commit）：路由/执行失败不丢发言痕迹；
3. 执行分两条链路（意图识别后分流）：
   - 问答链路：复用 ``supervisor.ask``（规则→LLM→兜底三级路由 + 内部审计），
     附件上下文以 ``context`` 拼在执行问句后（**不参与路由打分**，避免资料
     文本把路由带偏）；
   - 能力链路：命中"创建/装载"意图时走能力中心 service，产出能力卡片；
4. 路由结果（routed_by/choice/reason）与过程步骤（steps）随 assistant 消息
   行存证——历史回放时能看出当时是谁、按什么依据接的话、走了哪几步；
5. 执行失败：assistant 行以 failed 状态入库（error_code 记错误码），commit
   后原样抛错——HTTP 仍是错误响应，但历史诚实记录失败，重试可见。

隐私口径：会话内容只对创建者可见（同租户成员亦不可见）；审计只记
session/seq/路由元数据，不复制消息全文（消息本体已入库，无需二次冗余）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.deps import TenantContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.progress import (
    PHASE_GENERATING,
    PHASE_READING,
    STEP_DONE,
    STEP_FAILED,
    STEP_RUNNING,
    StepCallback,
    StepRecorder,
)
from app.modules.agents import supervisor
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
    ChatSession,
)

log = get_logger(__name__)

#: 会话标题上限（列为 String(200)，取短保证列表展示友好；超出加省略号不虚报）
TITLE_CAP = 60
#: 单条消息内容上限（与班组运行 input 口径一致）
CONTENT_CAP = 8000
#: 对话内路由来源（非 supervisor 路由的能力链路）
ROUTED_LOCAL = "local"


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# 纯函数与小工具（可单测）
# ---------------------------------------------------------------------------


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


def session_out(db: Session, session: ChatSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "title": session.title,
        "created_by": session.created_by,
        "created_at": session.created_at.isoformat() if session.created_at else "",
        "updated_at": session.updated_at.isoformat() if session.updated_at else "",
        "message_count": int(
            db.scalar(
                select(func.count())
                .select_from(ChatMessage)
                .where(
                    ChatMessage.session_id == session.id,
                    ChatMessage.tenant_id == session.tenant_id,
                )
            )
            or 0
        ),
    }


def message_out(msg: ChatMessage) -> dict[str, Any]:
    return {
        "id": msg.id,
        "seq": msg.seq,
        "role": msg.role,
        "content": msg.content,
        "status": msg.status,
        "routed_by": msg.routed_by,
        "choice": msg.choice_json,
        "reason": msg.reason,
        "error_code": msg.error_code,
        "payload": msg.payload_json,
        "steps": msg.steps_json,
        "attachments": msg.attachments_json,
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
    }


# ---------------------------------------------------------------------------
# 会话 CRUD（全部带租户 + 创建者边界）
# ---------------------------------------------------------------------------


def get_session(db: Session, *, tenant_id: str, created_by: str, session_id: str) -> ChatSession:
    row = db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.tenant_id == tenant_id,
            ChatSession.created_by == created_by,
        )
    )
    if row is None:
        raise AppError("CHAT_SESSION_NOT_FOUND", "会话不存在或无权访问。", http_status=404)
    return row


def create_session(
    db: Session,
    *,
    tenant_id: str,
    created_by: str,
    title: str | None = None,
    request_id: str = "",
) -> ChatSession:
    clean = (title or "").strip()[:200] or None
    session = ChatSession(tenant_id=tenant_id, created_by=created_by, title=clean)
    db.add(session)
    db.flush()
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=created_by,
        action="chat_session_created",
        resource_type="chat_session",
        resource_id=session.id,
        result="success",
        request_id=request_id,
        metadata={"title": clean},
    )
    return session


def list_sessions(db: Session, *, tenant_id: str, created_by: str) -> list[dict[str, Any]]:
    """本人会话列表（最近更新在前）+ 各自消息数（一次分组统计，不在循环里查）。"""
    rows = list(
        db.scalars(
            select(ChatSession)
            .where(ChatSession.tenant_id == tenant_id, ChatSession.created_by == created_by)
            .order_by(ChatSession.updated_at.desc())
        )
    )
    counts: dict[str, int] = {}
    if rows:
        pairs = db.execute(
            select(ChatMessage.session_id, func.count())
            .where(
                ChatMessage.tenant_id == tenant_id,
                ChatMessage.session_id.in_([r.id for r in rows]),
            )
            .group_by(ChatMessage.session_id)
        ).all()
        counts = {str(p[0]): int(p[1]) for p in pairs}
    out = []
    for r in rows:
        item = session_out(db, r)
        item["message_count"] = counts.get(r.id, 0)
        out.append(item)
    return out


def rename_session(
    db: Session, *, tenant_id: str, created_by: str, session_id: str, title: str
) -> ChatSession:
    clean = (title or "").strip()
    if not clean:
        raise AppError("INVALID_REQUEST", "会话标题不能为空。", http_status=400)
    session = get_session(db, tenant_id=tenant_id, created_by=created_by, session_id=session_id)
    session.title = clean[:200]
    session.updated_at = _utcnow()
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=created_by,
        action="chat_session_updated",
        resource_type="chat_session",
        resource_id=session.id,
        result="success",
        request_id="",
        metadata={"title": session.title},
    )
    return session


def delete_session(db: Session, *, tenant_id: str, created_by: str, session_id: str) -> None:
    """删会话连带删消息与附件（消息查询同样带租户边界，双保险）。"""
    session = get_session(db, tenant_id=tenant_id, created_by=created_by, session_id=session_id)
    db.execute(
        delete(ChatMessage).where(
            ChatMessage.session_id == session.id, ChatMessage.tenant_id == tenant_id
        )
    )
    attachments_service.delete_session_attachments(
        db, tenant_id=tenant_id, created_by=created_by, session_id=session.id
    )
    db.delete(session)
    log_audit(
        db,
        tenant_id=tenant_id,
        actor_id=created_by,
        action="chat_session_deleted",
        resource_type="chat_session",
        resource_id=session_id,
        result="success",
        request_id="",
        metadata=None,
    )


def list_messages(
    db: Session, *, tenant_id: str, created_by: str, session_id: str, limit: int = 200
) -> list[ChatMessage]:
    get_session(db, tenant_id=tenant_id, created_by=created_by, session_id=session_id)
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.tenant_id == tenant_id)
            .order_by(ChatMessage.seq.asc())
            .limit(limit)
        )
    )


# ---------------------------------------------------------------------------
# 附件解析
# ---------------------------------------------------------------------------


def resolve_attachments(
    db: Session, *, ctx: TenantContext, session_id: str, attachment_ids: list[str] | None
) -> list[ChatAttachment]:
    """把消息引用的附件 id 解析成附件行（会话 + 创建者双边界）。"""
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
            ChatAttachment.tenant_id == ctx.tenant_id,
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
# 消息持久化（用户消息 / 助手消息 / 失败消息）
# ---------------------------------------------------------------------------


def _attachment_ref(att: ChatAttachment) -> dict[str, Any]:
    """附件引用（存进用户消息行）：文件名等事实，历史回放不必再查附件表。"""
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
    ctx: TenantContext,
    session: ChatSession,
    content: str,
    attachments: list[ChatAttachment],
) -> ChatMessage:
    """用户消息落库（含附件引用）；标题缺省继承首句。"""
    msg = ChatMessage(
        session_id=session.id,
        tenant_id=ctx.tenant_id,
        seq=next_seq(db, session.id),
        role=ROLE_USER,
        content=content,
        status=MSG_OK,
        attachments_json=[_attachment_ref(a) for a in attachments] or None,
    )
    db.add(msg)
    if not session.title:
        session.title = derive_title(content)
    session.updated_at = _utcnow()
    return msg


def persist_assistant_message(
    db: Session,
    *,
    ctx: TenantContext,
    session: ChatSession,
    content: str,
    routed_by: str = "",
    choice: dict[str, Any] | None = None,
    reason: str = "",
    payload: dict[str, Any] | None = None,
    steps: list[dict[str, Any]] | None = None,
    status: str = MSG_OK,
    error_code: str = "",
) -> ChatMessage:
    """助手消息落库（路由存证 + 过程步骤 + 结构化载荷）。"""
    msg = ChatMessage(
        session_id=session.id,
        tenant_id=ctx.tenant_id,
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
    )
    db.add(msg)
    session.updated_at = _utcnow()
    return msg


def _persist_failure(
    db: Session,
    session: ChatSession,
    ctx: TenantContext,
    *,
    code: str,
    message: str,
    steps: list[dict[str, Any]] | None = None,
) -> None:
    """执行失败：assistant 行以 failed 入库并审计，然后由调用方原样抛错。"""
    failed = ChatMessage(
        session_id=session.id,
        tenant_id=ctx.tenant_id,
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
        tenant_id=ctx.tenant_id,
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
    ctx: TenantContext,
    content: str,
    attachments: list[ChatAttachment],
    on_step: StepCallback | None = None,
) -> dict[str, Any]:
    """执行一轮对话，返回 {content, routed_by, choice, reason, payload}。

    意图分流：命中"创建能力"→ 能力链路（产出卡片）；命中"装载市场能力"→
    市场链路；其余 → 既有问答链路（supervisor 三级路由）。
    """
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
        return {
            "content": note,
            "routed_by": ROUTED_LOCAL,
            "choice": {"kind": "agent", "id": card.get("agent_id") or "", "name": card["name"]},
            "reason": intent.reason,
            "payload": card,
        }

    if intent.kind == intent_service.INTENT_INSTALL:
        from app.modules.market.service import install_from_chat

        if on_step:
            on_step(PHASE_GENERATING, "正在从能力市场匹配并装载", STEP_RUNNING)
        install_result = install_from_chat(db, ctx=ctx, request_text=content)
        if on_step:
            on_step(PHASE_GENERATING, install_result["summary"], STEP_DONE)
        return {
            "content": install_result["summary"],
            "routed_by": ROUTED_LOCAL,
            "choice": {"kind": "agent", "id": "", "name": install_result["name"]},
            "reason": intent.reason,
            "payload": install_result["card"],
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

    data = await supervisor.ask(db, ctx=ctx, question=content, on_step=on_step, context=context)
    result: dict[str, Any] = dict(data.get("result") or {})
    choice: dict[str, Any] = dict(data.get("choice") or {})
    if result.get("run_id"):
        choice["run_id"] = result["run_id"]
    return {
        "content": str(result.get("answer") or ""),
        "routed_by": str(data.get("routed_by") or ""),
        "choice": choice,
        "reason": str(data.get("reason") or ""),
        "payload": None,
    }


async def run_and_persist(
    db: Session,
    *,
    ctx: TenantContext,
    session: ChatSession,
    content: str,
    attachments: list[ChatAttachment],
    on_step: StepCallback | None = None,
    recorder: StepRecorder | None = None,
) -> dict[str, Any]:
    """执行一轮对话并落库助手消息（同步与流式两条链路共用）。

    步骤既进消息行（历史可回放）也通过 ``on_step`` 推给调用方（SSE 实时帧）；
    流式链路传入带 notify 的 ``recorder``，可直接拿到累计步骤快照。
    """
    steps = recorder or StepRecorder()

    def _cb(phase: str, detail: str, status: str) -> None:
        steps.emit(phase, detail, status)
        if on_step is not None:
            on_step(phase, detail, status)

    try:
        outcome = await execute_turn(
            db, ctx=ctx, content=content, attachments=attachments, on_step=_cb
        )
    except AppError as e:
        steps.fail(PHASE_GENERATING, e.message)
        _persist_failure(db, session, ctx, code=e.code, message=e.message, steps=steps.steps)
        raise
    except Exception as e:  # noqa: BLE001 —— 意外异常也要留下失败痕迹再抛
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
    )
    return {
        "assistant_message": assistant_msg,
        "content": outcome["content"],
        "routed_by": outcome["routed_by"],
        "choice": outcome["choice"],
        "reason": outcome["reason"],
        "payload": outcome["payload"],
        "steps": steps.steps,
    }


# ---------------------------------------------------------------------------
# 对外主入口
# ---------------------------------------------------------------------------


async def send_message(
    db: Session,
    *,
    ctx: TenantContext,
    session_id: str,
    content: str,
    attachment_ids: list[str] | None = None,
    on_step: StepCallback | None = None,
) -> dict[str, Any]:
    """发一条消息并取回回复（问答链路 + 能力链路）。"""
    content = (content or "").strip()[:CONTENT_CAP]
    if not content:
        raise AppError("INVALID_REQUEST", "消息内容不能为空。", http_status=400)
    session = get_session(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, session_id=session_id
    )
    attachments = resolve_attachments(
        db, ctx=ctx, session_id=session.id, attachment_ids=attachment_ids
    )
    user_msg = persist_user_message(
        db, ctx=ctx, session=session, content=content, attachments=attachments
    )
    db.commit()  # 发言先落库：后面的执行失败不能抹掉发言痕迹

    outcome = await run_and_persist(
        db,
        ctx=ctx,
        session=session,
        content=content,
        attachments=attachments,
        on_step=on_step,
    )
    assistant_msg = outcome["assistant_message"]
    log_audit(
        db,
        tenant_id=ctx.tenant_id,
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
