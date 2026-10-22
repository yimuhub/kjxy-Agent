"""对话模式服务层：会话 CRUD +消息持久化 + 复用 supervisor 执行。

执行口径（send_message 时序）：
1. 会话定位带 tenant_id + created_by 双重边界（跨租户/无权统一 404）；
2. 用户消息**先落库再执行**（独立 commit）：路由/执行失败不丢发言痕迹；
3. 执行复用 ``supervisor.ask``（规则→LLM→兜底三级路由 + 内部审计与 commit），
   本层不重复路由逻辑；路由结果（routed_by/choice/reason）存进 assistant
   消息行——历史回放时能看出当时是谁、按什么依据接的话；
4. 执行失败：assistant 行以 failed 状态入库（error_code 记错误码），commit
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
from app.modules.agents import supervisor
from app.modules.audit.service import log_audit
from app.modules.chat.models import (
    MSG_FAILED,
    MSG_OK,
    ROLE_ASSISTANT,
    ROLE_USER,
    ChatMessage,
    ChatSession,
)

log = get_logger(__name__)

#: 会话标题上限（列为 String(200)，取短保证列表展示友好；超出加省略号不虚报）
TITLE_CAP = 60
#: 单条消息内容上限（与班组运行 input 口径一致）
CONTENT_CAP = 8000


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
    """删会话连带删消息（消息查询同样带租户边界，双保险）。"""
    session = get_session(db, tenant_id=tenant_id, created_by=created_by, session_id=session_id)
    db.execute(
        delete(ChatMessage).where(
            ChatMessage.session_id == session.id, ChatMessage.tenant_id == tenant_id
        )
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
# 消息发送：先落用户发言 → supervisor 路由执行 → 落 assistant 回复/失败
# ---------------------------------------------------------------------------


async def send_message(
    db: Session, *, ctx: TenantContext, session_id: str, content: str
) -> dict[str, Any]:
    """发一条消息并取回回复（执行复用 supervisor 三级路由）。"""
    content = (content or "").strip()[:CONTENT_CAP]
    if not content:
        raise AppError("INVALID_REQUEST", "消息内容不能为空。", http_status=400)
    session = get_session(
        db, tenant_id=ctx.tenant_id, created_by=ctx.user_id, session_id=session_id
    )
    user_msg = ChatMessage(
        session_id=session.id,
        tenant_id=ctx.tenant_id,
        seq=next_seq(db, session.id),
        role=ROLE_USER,
        content=content,
        status=MSG_OK,
    )
    db.add(user_msg)
    if not session.title:
        session.title = derive_title(content)
    session.updated_at = _utcnow()
    db.commit()  # 发言先落库：后面的执行失败不能抹掉发言痕迹

    try:
        data = await supervisor.ask(db, ctx=ctx, question=content)
    except AppError as e:
        _persist_failure(db, session, ctx, code=e.code, message=e.message)
        raise
    except Exception as e:  # noqa: BLE001 —— 意外异常也要留下失败痕迹再抛
        log.warning("chat send unexpected error: %s", e)
        _persist_failure(db, session, ctx, code="INTERNAL_ERROR", message="服务暂时不可用。")
        raise

    result: dict[str, Any] = dict(data.get("result") or {})
    choice: dict[str, Any] = dict(data.get("choice") or {})
    if result.get("run_id"):
        choice["run_id"] = result["run_id"]
    assistant_msg = ChatMessage(
        session_id=session.id,
        tenant_id=ctx.tenant_id,
        seq=next_seq(db, session.id),
        role=ROLE_ASSISTANT,
        content=str(result.get("answer") or ""),
        status=MSG_OK,
        routed_by=str(data.get("routed_by") or ""),
        choice_json=choice,
        reason=str(data.get("reason") or "")[:200],
    )
    db.add(assistant_msg)
    session.updated_at = _utcnow()
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
            "kind": choice.get("kind"),
            "name": choice.get("name"),
        },
    )
    db.commit()
    return {
        "session": session_out(db, session),
        "user_message": message_out(user_msg),
        "assistant_message": message_out(assistant_msg),
    }


def _persist_failure(
    db: Session, session: ChatSession, ctx: TenantContext, *, code: str, message: str
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


__all__ = [
    "create_session",
    "delete_session",
    "derive_title",
    "get_session",
    "list_messages",
    "list_sessions",
    "message_out",
    "next_seq",
    "rename_session",
    "send_message",
    "session_out",
]
