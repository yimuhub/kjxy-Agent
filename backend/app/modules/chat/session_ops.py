"""对话会话 CRUD、消息持久化、批量查询与分支复制（从 chat/service.py 拆分，保持对外重导出兼容）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.output_sanitizer import strip_internal_markup
from app.core.timezone import utc_now as _utcnow
from app.modules.agents import declarative, supervisor
from app.modules.agents.models import YardAgent
from app.modules.audit.service import log_audit
from app.modules.chat import attachments as attachments_service
from app.modules.chat.models import (
    APPROVAL_ASK,
    APPROVAL_ESCALATION_ROLES,
    MSG_FAILED,
    MSG_OK,
    ROLE_ASSISTANT,
    ROLE_USER,
    VALID_APPROVAL_MODES,
    ChatAttachment,
    ChatMessage,
    ChatParticipant,
    ChatSession,
)
from app.modules.knowledge.chunking import estimate_token_count

TITLE_CAP = 60
CONTENT_CAP = 8000
ASSISTANT_CONTENT_CAP = 20000


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
        elif tu is not None:
            continue
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


def _chunks(items: list[str], size: int = 500) -> list[list[str]]:
    """IN 子句分批。"""
    return [items[i : i + size] for i in range(0, len(items), size)]


def _bulk_message_stats(
    db: Session, session_ids: list[str], yard_id: str
) -> dict[str, dict[str, int]]:
    """整页会话的消息数与 Token 统计，一条查询搞定。"""
    stats = {
        sid: {"message_count": 0, "input_tokens": 0, "output_tokens": 0} for sid in session_ids
    }
    if not session_ids:
        return stats
    missing: set[str] = set()
    for group in _chunks(session_ids):
        rows = db.execute(
            select(ChatMessage.session_id, ChatMessage.token_usage_json).where(
                ChatMessage.session_id.in_(group), ChatMessage.yard_id == yard_id
            )
        ).all()
        for sid, tu in rows:
            bucket = stats.get(sid)
            if bucket is None:
                continue
            bucket["message_count"] += 1
            if isinstance(tu, dict):
                bucket["input_tokens"] += int(tu.get("input_tokens") or 0)
                bucket["output_tokens"] += int(tu.get("output_tokens") or 0)
            elif tu is None:
                missing.add(sid)
    if missing:
        for group in _chunks(sorted(missing)):
            legacy = db.execute(
                select(ChatMessage.session_id, ChatMessage.role, ChatMessage.content).where(
                    ChatMessage.session_id.in_(group),
                    ChatMessage.yard_id == yard_id,
                    ChatMessage.token_usage_json.is_(None),
                )
            ).all()
            for sid, role, content in legacy:
                if not content:
                    continue
                bucket = stats[sid]
                if role == ROLE_ASSISTANT:
                    bucket["output_tokens"] += estimate_token_count(content)
                elif role == ROLE_USER:
                    bucket["input_tokens"] += estimate_token_count(content)
    for bucket in stats.values():
        bucket["total_tokens"] = bucket["input_tokens"] + bucket["output_tokens"]
    return stats


def sessions_out_bulk(db: Session, rows: list[ChatSession]) -> list[dict[str, Any]]:
    """会话列表的批量序列化：统计/群聊人数/助手名各一条查询。"""
    if not rows:
        return []
    ids = [r.id for r in rows]
    yard_id = rows[0].yard_id
    stats = _bulk_message_stats(db, ids, yard_id)
    counts: dict[str, int] = {}
    for group in _chunks(ids):
        for sid, num in db.execute(
            select(ChatParticipant.session_id, func.count())
            .where(ChatParticipant.session_id.in_(group))
            .group_by(ChatParticipant.session_id)
        ).all():
            counts[str(sid)] = int(num or 0)
    codes = {r.target_code for r in rows if r.target == "agent" and r.target_code}
    names: dict[str, str] = {}
    for group in _chunks(sorted(codes)):
        names.update(
            {
                str(code): str(name)
                for code, name in db.execute(
                    select(YardAgent.agent_code, YardAgent.name).where(
                        YardAgent.yard_id == yard_id, YardAgent.agent_code.in_(group)
                    )
                ).all()
            }
        )
    return [
        session_out(
            db,
            r,
            stats=stats.get(r.id),
            participant_count=counts.get(r.id, 0),
            target_name=names.get(r.target_code or "") or r.target_code,
        )
        for r in rows
    ]


def session_out(
    db: Session,
    session: ChatSession,
    *,
    stats: dict[str, int] | None = None,
    participant_count: int | None = None,
    target_name: str | None = None,
) -> dict[str, Any]:
    """会话序列化。批量列表可传入预统计值跳过逐行查询（三条路径同一份字段）。"""
    tok_meta = (
        stats if stats is not None else _session_token_summary(db, session.id, session.yard_id)
    )
    target = getattr(session, "target", None) or "auto"
    target_code = getattr(session, "target_code", None)
    if target_name is None and target == "agent" and target_code:
        target_name = (
            db.scalar(
                select(YardAgent.name).where(
                    YardAgent.yard_id == session.yard_id,
                    YardAgent.agent_code == target_code,
                )
            )
            or target_code
        )
    if participant_count is None:
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
        "pinned": session.pinned_at is not None,
        "pinned_at": session.pinned_at.isoformat() if session.pinned_at else None,
        "archived": session.archived_at is not None,
        "archived_at": session.archived_at.isoformat() if session.archived_at else None,
        "unread": bool(session.unread),
        "approval_mode": session.approval_mode or APPROVAL_ASK,
        "execution_mode": getattr(session, "execution_mode", None) or "standard",
        "parent_session_id": session.parent_session_id,
        "parent_message_id": session.parent_message_id,
    }


_CITATION_KEYS = (
    "document_id",
    "chunk_id",
    "title",
    "source",
    "page_number",
    "quote",
    "score",
    "trust_level",
    "url",
    "snippet",
    "reference",
    "kind",
)
_CITATION_LIMIT = 20


def _normalize_citations(items: Any) -> list[dict[str, Any]]:
    """只保留可展示的引用字段，限制条数与长度，避免把任意模型输出塞进消息。"""
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for item in items[:_CITATION_LIMIT]:
        if not isinstance(item, dict):
            continue
        row = {k: item.get(k) for k in _CITATION_KEYS if item.get(k) not in (None, "")}
        if not row.get("quote") and row.get("snippet"):
            row["quote"] = row["snippet"]
        if not row.get("source") and row.get("url"):
            row["source"] = row["url"]
        for key in ("title", "source", "quote", "snippet", "url", "reference"):
            if row.get(key) is not None:
                row[key] = str(row[key])[:600]
        if row:
            out.append(row)
    return out


def citations_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """从单聊/班组执行结果提取引用：助手用 raw.citations，班组用 research 快照。"""
    raw = result.get("raw")
    if isinstance(raw, dict):
        found = _normalize_citations(raw.get("citations"))
        if found:
            return found
    research = result.get("research")
    if not isinstance(research, dict):
        return []
    merged: list[dict[str, Any]] = []
    for kind in ("knowledge", "web"):
        for item in research.get(kind) or []:
            if isinstance(item, dict):
                merged.append({**item, "kind": kind})
    return _normalize_citations(merged)


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
        "citations": msg.citations_json or [],
        "attachments": msg.attachments_json,
        "token_usage": tu,
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
    }


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


def _commander_settings(db: Session, yard_id: str) -> dict[str, Any] | None:
    try:
        from app.modules.commander import settings as commander_settings

        return commander_settings.settings_for(db, yard_id)
    except Exception:  # noqa: BLE001
        return None


def _commander_enabled(db: Session, yard_id: str) -> bool:
    cfg = _commander_settings(db, yard_id)
    return bool(cfg and cfg.get("enabled"))


def _default_execution_mode(db: Session, yard_id: str) -> str:
    cfg = _commander_settings(db, yard_id)
    if cfg and cfg.get("enabled") and cfg.get("default_mode"):
        return "commander"
    return "standard"


def apply_execution_mode(db: Session, session: ChatSession, mode: str | None) -> str:
    current = getattr(session, "execution_mode", None) or "standard"
    requested = mode if mode in ("standard", "commander") else current
    effective = requested
    if requested == "commander" and not _commander_enabled(db, session.yard_id):
        effective = "standard"
    if effective != current:
        session.execution_mode = effective
    return effective


def create_session(
    db: Session,
    *,
    yard_id: str,
    created_by: str,
    title: str | None = None,
    request_id: str = "",
    target: str = "auto",
    target_code: str | None = None,
    execution_mode: str | None = None,
) -> ChatSession:
    clean = (title or "").strip()[:200] or None
    mode = (
        execution_mode
        if execution_mode in ("standard", "commander")
        else _default_execution_mode(db, yard_id)
    )
    session = ChatSession(
        yard_id=yard_id,
        created_by=created_by,
        title=clean,
        target=target or "auto",
        target_code=(target_code or None) if target in {"agent", "team"} else None,
        execution_mode=mode,
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


def list_sessions(
    db: Session, *, yard_id: str, created_by: str, archived: bool = False
) -> list[dict[str, Any]]:
    cond = ChatSession.archived_at.is_not(None) if archived else ChatSession.archived_at.is_(None)
    rows = list(
        db.scalars(
            select(ChatSession)
            .where(
                ChatSession.yard_id == yard_id,
                ChatSession.created_by == created_by,
                cond,
            )
            .order_by(
                ChatSession.pinned_at.is_(None).asc(),
                ChatSession.pinned_at.desc(),
                ChatSession.updated_at.desc(),
            )
        )
    )
    return sessions_out_bulk(db, rows)


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


def update_session_flags(
    db: Session,
    *,
    yard_id: str,
    created_by: str,
    session_id: str,
    title: str | None = None,
    pinned: bool | None = None,
    archived: bool | None = None,
    unread: bool | None = None,
    approval_mode: str | None = None,
    execution_mode: str | None = None,
    roles: tuple[str, ...] | list[str] = (),
    request_id: str = "",
) -> ChatSession:
    session = get_session(db, yard_id=yard_id, created_by=created_by, session_id=session_id)
    changed: dict[str, Any] = {}

    if title is not None:
        clean = title.strip()
        if not clean:
            raise AppError("INVALID_REQUEST", "会话标题不能为空。", http_status=400)
        session.title = clean[:200]
        changed["title"] = session.title

    if pinned is not None:
        session.pinned_at = _utcnow() if pinned else None
        changed["pinned"] = bool(pinned)

    if archived is not None:
        session.archived_at = _utcnow() if archived else None
        changed["archived"] = bool(archived)

    if unread is not None:
        session.unread = bool(unread)
        changed["unread"] = bool(unread)

    if approval_mode is not None:
        if approval_mode not in VALID_APPROVAL_MODES:
            raise AppError(
                "INVALID_REQUEST",
                f"非法的授权模式：{approval_mode}。",
                http_status=400,
                details={"allowed": list(VALID_APPROVAL_MODES)},
            )
        requested = approval_mode
        if requested != APPROVAL_ASK and not set(roles) & set(APPROVAL_ESCALATION_ROLES):
            requested = APPROVAL_ASK
            changed["approval_mode_downgraded"] = {"from": approval_mode, "to": APPROVAL_ASK}
        session.approval_mode = requested
        changed["approval_mode"] = requested

    if execution_mode is not None:
        if execution_mode not in ("standard", "commander"):
            raise AppError(
                "INVALID_REQUEST",
                f"非法的执行模式：{execution_mode}。",
                http_status=400,
                details={"allowed": ["standard", "commander"]},
            )
        requested_mode = execution_mode
        if requested_mode == "commander" and not _commander_enabled(db, yard_id):
            requested_mode = "standard"
            changed["execution_mode_downgraded"] = {"from": execution_mode, "to": "standard"}
        session.execution_mode = requested_mode
        changed["execution_mode"] = requested_mode

    if changed:
        session.updated_at = _utcnow()
        log_audit(
            db,
            yard_id=yard_id,
            actor_id=created_by,
            action="chat_session_flags_updated",
            resource_type="chat_session",
            resource_id=session.id,
            result="success",
            request_id=request_id,
            metadata=changed,
        )
    return session


BATCH_ACTIONS = ("pin", "unpin", "archive", "unarchive", "read", "unread", "delete")


def batch_sessions(
    db: Session,
    *,
    yard_id: str,
    created_by: str,
    session_ids: list[str],
    action: str,
    request_id: str = "",
    file_sink: list[str] | None = None,
) -> dict[str, Any]:
    if action not in BATCH_ACTIONS:
        raise AppError(
            "INVALID_REQUEST",
            f"不支持的批量动作：{action}。",
            http_status=400,
            details={"allowed": list(BATCH_ACTIONS)},
        )
    ids = [sid for sid in dict.fromkeys(session_ids) if sid]
    if not ids:
        raise AppError("INVALID_REQUEST", "请至少选择一个会话。", http_status=400)
    if len(ids) > 200:
        raise AppError("INVALID_REQUEST", "单次批量操作最多 200 个会话。", http_status=400)

    rows = list(
        db.scalars(
            select(ChatSession).where(
                ChatSession.yard_id == yard_id,
                ChatSession.created_by == created_by,
                ChatSession.id.in_(ids),
            )
        )
    )
    found = {r.id for r in rows}
    affected: list[str] = []
    deleted = 0
    for session in rows:
        if action == "delete":
            delete_session(
                db,
                yard_id=yard_id,
                created_by=created_by,
                session_id=session.id,
                audit=False,
                file_sink=file_sink,
            )
            deleted += 1
            affected.append(session.id)
            continue
        if action == "pin":
            session.pinned_at = _utcnow()
        elif action == "unpin":
            session.pinned_at = None
        elif action == "archive":
            session.archived_at = _utcnow()
        elif action == "unarchive":
            session.archived_at = None
        elif action == "read":
            session.unread = False
        else:
            session.unread = True
        session.updated_at = _utcnow()
        affected.append(session.id)

    log_audit(
        db,
        yard_id=yard_id,
        actor_id=created_by,
        action="chat_session_batch",
        resource_type="chat_session",
        resource_id="",
        result="success",
        request_id=request_id,
        metadata={"action": action, "count": len(affected), "skipped": len(ids) - len(found)},
    )
    return {
        "action": action,
        "affected": affected,
        "deleted": deleted,
        "missing": len(ids) - len(found),
    }


def fork_session(
    db: Session,
    *,
    yard_id: str,
    created_by: str,
    session_id: str,
    message_id: str,
    request_id: str = "",
) -> ChatSession:
    source = get_session(db, yard_id=yard_id, created_by=created_by, session_id=session_id)
    anchor = db.scalar(
        select(ChatMessage).where(
            ChatMessage.id == message_id,
            ChatMessage.session_id == session_id,
            ChatMessage.yard_id == yard_id,
        )
    )
    if anchor is None:
        raise AppError("CHAT_MESSAGE_NOT_FOUND", "消息不存在或不属于该会话。", http_status=404)

    history = list(
        db.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.session_id == session_id,
                ChatMessage.yard_id == yard_id,
                ChatMessage.seq <= anchor.seq,
            )
            .order_by(ChatMessage.seq.asc())
        )
    )
    referenced = {_attachment_ref_id(a) for m in history for a in (m.attachments_json or [])}
    referenced.discard("")

    branch = ChatSession(
        yard_id=yard_id,
        created_by=created_by,
        title=f"{source.title or '新会话'} · 分支"[:200],
        target=source.target or "auto",
        target_code=source.target_code,
        approval_mode=source.approval_mode or APPROVAL_ASK,
        execution_mode=getattr(source, "execution_mode", None) or "standard",
        parent_session_id=source.id,
        parent_message_id=anchor.id,
    )
    db.add(branch)
    db.flush()

    id_map = attachments_service.clone_attachments_for_branch(
        db,
        yard_id=yard_id,
        created_by=created_by,
        source_session_id=source.id,
        target_session_id=branch.id,
        attachment_ids=referenced,
    )
    for m in history:
        db.add(
            ChatMessage(
                session_id=branch.id,
                yard_id=yard_id,
                seq=m.seq,
                role=m.role,
                content=m.content,
                status=m.status,
                routed_by=m.routed_by,
                choice_json=m.choice_json,
                reason=m.reason,
                error_code=m.error_code,
                payload_json=m.payload_json,
                steps_json=m.steps_json,
                attachments_json=[
                    _remap_attachment_ref(a, id_map) for a in (m.attachments_json or [])
                ]
                or None,
                token_usage_json=m.token_usage_json,
                citations_json=m.citations_json,
            )
        )

    for p in db.scalars(
        select(ChatParticipant)
        .where(ChatParticipant.session_id == source.id, ChatParticipant.yard_id == yard_id)
        .order_by(ChatParticipant.position.asc())
    ):
        db.add(
            ChatParticipant(
                session_id=branch.id,
                yard_id=yard_id,
                created_by=created_by,
                agent_id=p.agent_id,
                agent_code=p.agent_code,
                agent_name=p.agent_name,
                position=p.position,
            )
        )

    db.flush()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=created_by,
        action="chat_session_forked",
        resource_type="chat_session",
        resource_id=branch.id,
        result="success",
        request_id=request_id,
        metadata={
            "parent_session_id": source.id,
            "parent_message_id": anchor.id,
            "message_count": len(history),
        },
    )
    return branch


def delete_session(
    db: Session,
    *,
    yard_id: str,
    created_by: str,
    session_id: str,
    audit: bool = True,
    file_sink: list[str] | None = None,
) -> None:
    session = get_session(db, yard_id=yard_id, created_by=created_by, session_id=session_id)
    db.execute(
        delete(ChatMessage).where(
            ChatMessage.session_id == session.id, ChatMessage.yard_id == yard_id
        )
    )
    attachments_service.delete_session_attachments(
        db,
        yard_id=yard_id,
        created_by=created_by,
        session_id=session.id,
        file_sink=file_sink,
    )
    db.execute(delete(ChatParticipant).where(ChatParticipant.session_id == session.id))
    db.delete(session)
    if audit:
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


def _attachment_ref(att: ChatAttachment) -> dict[str, Any]:
    return {
        "id": att.id,
        "filename": att.filename,
        "kind": att.kind,
        "mime": att.mime,
        "size_bytes": att.size_bytes,
        "status": att.status,
    }


def _attachment_ref_id(ref: Any) -> str:
    if isinstance(ref, dict):
        return str(ref.get("id") or "")
    return str(ref or "")


def _remap_attachment_ref(ref: Any, id_map: dict[str, str]) -> Any:
    if not isinstance(ref, dict):
        return id_map.get(str(ref), ref)
    rid = str(ref.get("id") or "")
    return {**ref, "id": id_map[rid]} if rid in id_map else ref


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
    citations: list[dict[str, Any]] | None = None,
    token_usage: dict[str, Any] | None = None,
) -> ChatMessage:
    content = strip_internal_markup(content)
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
        content=content[:ASSISTANT_CONTENT_CAP],
        status=status,
        routed_by=routed_by[:10] or None,
        choice_json=choice or None,
        reason=reason[:200] or None,
        error_code=error_code[:50] or None,
        payload_json=payload,
        steps_json=steps or None,
        citations_json=citations or None,
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
    payload: dict[str, Any] | None = None,
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
        payload_json=payload,
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


__all__ = [
    "ASSISTANT_CONTENT_CAP",
    "BATCH_ACTIONS",
    "CONTENT_CAP",
    "TITLE_CAP",
    "_CITATION_KEYS",
    "_CITATION_LIMIT",
    "_attachment_ref",
    "_attachment_ref_id",
    "_bulk_message_stats",
    "_chunks",
    "_commander_enabled",
    "_commander_settings",
    "_default_execution_mode",
    "_normalize_citations",
    "_persist_failure",
    "_remap_attachment_ref",
    "_session_token_summary",
    "apply_execution_mode",
    "batch_sessions",
    "citations_from_result",
    "create_session",
    "delete_session",
    "derive_title",
    "fork_session",
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
    "session_out",
    "sessions_out_bulk",
    "update_session_flags",
]
