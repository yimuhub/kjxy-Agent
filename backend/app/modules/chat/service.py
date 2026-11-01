"""对话模式服务层：会话 CRUD + 消息持久化 + 复用 supervisor 执行 + 消息/会话级 Token 统计。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
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
    PHASE_RETRIEVING,
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
    APPROVAL_ASK,
    APPROVAL_ESCALATION_ROLES,
    APPROVAL_FULL,
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

log = get_logger(__name__)

TITLE_CAP = 60
CONTENT_CAP = 8000
#: 指挥官重规划的会话上下文：最近几条消息 + 总字符上限，避免把整段历史塞进规划提示
COMMANDER_HISTORY_LIMIT = 6
COMMANDER_HISTORY_CHARS = 2500
# 助手侧单独放宽：一份完整方案（八章 + 分步细则）常超 8000 字，按输入上限切它会静默丢章节
ASSISTANT_CONTENT_CAP = 20000
ROUTED_LOCAL = "local"

#: 指挥官事件回调：(event, payload)，event ∈ commander-plan|subtask-status|commander-summary
CommanderEventCallback = Callable[[str, dict[str, Any]], None]


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
        elif tu is not None:
            # 存坏了的计量（非 dict 也非空）：与批量统计同口径按 0 计——按字数估会把
            # 一条已有计量记录算成两份，列表和详情就会出现两个总数。
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
    """IN 子句分批。

    会话列表不分页，几百上千条时把 id 一次性塞进 IN，SQLite 的绑定变量上限
    （老版本 999）会直接让查询报错——分批比指望数据库宽松更可靠。
    """
    return [items[i : i + size] for i in range(0, len(items), size)]


def _bulk_message_stats(
    db: Session, session_ids: list[str], yard_id: str
) -> dict[str, dict[str, int]]:
    """整页会话的消息数与 Token 统计，一条查询搞定（列表原本每行各跑一遍）。

    只取 (session_id, token_usage_json) 两列，正文不进这次查询；
    早期消息没带 token_usage_json 时，仅对**缺计量的那部分**回读正文按字数估，
    与 ``_session_token_summary`` 单条口径一致。
    """
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
    """会话列表的批量序列化：统计/群聊人数/助手名各一条查询。

    几百条会话时逐行查询要发近千条 SQL，点一下「置顶」都要等回读，慢到不能用；
    这里压成四条，序列化形状仍复用 ``session_out``，避免列表与详情两份字段漂移。
    """
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
        # ---- 0043 会话列表管理与授权模式 ----
        "pinned": session.pinned_at is not None,
        "pinned_at": session.pinned_at.isoformat() if session.pinned_at else None,
        "archived": session.archived_at is not None,
        "archived_at": session.archived_at.isoformat() if session.archived_at else None,
        "unread": bool(session.unread),
        "approval_mode": session.approval_mode or APPROVAL_ASK,
        # 0058：standard=普通对话；commander=指挥官编排（前端据此切换输入区提示）
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


def _commander_settings(db: Session, yard_id: str) -> dict[str, Any] | None:
    """读指挥官设置；老库未迁移/脏值时返回 None（调用方按关闭处理）。"""
    try:
        from app.modules.commander import settings as commander_settings

        return commander_settings.settings_for(db, yard_id)
    except Exception:  # noqa: BLE001
        return None


def _commander_enabled(db: Session, yard_id: str) -> bool:
    cfg = _commander_settings(db, yard_id)
    return bool(cfg and cfg.get("enabled"))


def _default_execution_mode(db: Session, yard_id: str) -> str:
    """新会话的默认执行模式：指挥官可用且设为默认时进 commander，否则 standard。

    读设置失败（未迁移的老库/脏值）一律回落 standard——建会话不能因为一个
    可选项读不出来就 500。
    """
    cfg = _commander_settings(db, yard_id)
    if cfg and cfg.get("enabled") and cfg.get("default_mode"):
        return "commander"
    return "standard"


def apply_execution_mode(db: Session, session: ChatSession, mode: str | None) -> str:
    """把请求带的执行模式落到会话上并返回真实生效值。

    - 只认 standard/commander（schema 已校验，这里兜底防内部调用传脏值）；
    - 指挥官被关闭时 commander 静默降级为 standard（响应体里就是降级后的值）；
    - 未传则沿用会话当前值；但当前值是 commander 而指挥官已被关掉时同样降级，
      否则用户关掉开关后旧会话每次发消息都撞 403。
    """
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
    """本人会话列表：置顶恒在最前（按置顶时间倒序），其余最近更新在前。

    ``archived=False`` 只给进行中，``True`` 只给已归档——前端顶部「进行中/已归档」
    切换用的就是这一个参数，不做混合返回。
    """
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
    """会话标记位局部更新（PATCH 语义：只改显式传入的字段）。

    授权模式是权限动作：``auto``/``full`` 只有能批准工具的人（小院管理员/负责人/
    专家/平台管理员）能开，其余角色请求这两个值时静默降级为 ``ask`` 并留痕，
    不因为一个前端误传就把高风险工具免批准。
    """
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
            # 指挥官被关闭时不报错、降级为普通：前端误传不能让整个 PATCH 失败，
            # 但响应必须是降级后的真实值（响应不撒谎）
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


#: 批量动作 → (标记位, 是否删除)；删除单独走 delete_session 以复用附件级联清理
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
    """批量操作：一次请求处理多个会话，越权/不存在的 id 直接跳过（不泄露存在性）。

    ``action=delete`` 时被删会话的附件存储键进了 ``file_sink`` 就交给调用方，
    必须等 ``db.commit()`` 成功之后再删文件。
    """
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
        else:  # unread
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
    """分支：把「到这条回答为止」的历史复制进一个全新会话。

    口径（产品定稿）：
    1. 复制该回答及其之前的全部消息（含卡片/步骤/引用/Token 存证），原会话不动；
    2. 新会话标题为「原会话标题 · 分支」，并记 ``parent_session_id``/``parent_message_id``；
    3. 附件只复制被复制消息引用到的行，文件本体与原件共用（见
       ``attachments.clone_attachments_for_branch``），删分支不会弄坏原会话附件；
    4. 群聊名单与授权模式一并继承——分支是同一场景的另一条思路，不该改协作对象。
    """
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
    """删除会话及其消息/成员/附件行。

    ``file_sink`` 非空时，附件的**物理文件键只登记不删**，由调用方在 commit 之后统一
    清理——事务回滚不能把文件一起带走（见 attachments.delete_session_attachments）。
    """
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


def _attachment_ref_id(ref: Any) -> str:
    """从消息的附件引用里取附件 id（历史行既可能是 dict 也可能是裸 id）。"""
    if isinstance(ref, dict):
        return str(ref.get("id") or "")
    return str(ref or "")


def _remap_attachment_ref(ref: Any, id_map: dict[str, str]) -> Any:
    """分支复制时把附件引用指向分支自己那份行；查不到的原样保留。"""
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


# ---------------------------------------------------------------------------
# 一次对话的执行（问答链路 / 能力链路）
# ---------------------------------------------------------------------------


def _ensure_generate_permission(db: Session, ctx: YardContext) -> None:
    """对话里的技能/插件生成与 HTTP /market/agent/* 同口径，需要 skills:generate。

    默认角色全员都有该权限；自定义角色可以裁掉——裁掉后对话里同样不可用，
    不能因为走聊天入口就绕过权限矩阵。
    """
    from app.modules.yards.authz import has_permission

    if not has_permission(
        db,
        yard_id=ctx.yard_id,
        user_id=ctx.user_id,
        jwt_roles=ctx.roles or [],
        permission="skills:generate",
    ):
        raise AppError(
            "FORBIDDEN",
            "缺少 skills:generate 权限，无法生成技能或插件。",
            http_status=403,
        )


#: 会话绑定的最近对话注入上限（条数 / 字符数）
CHAT_HISTORY_LIMIT = 8
CHAT_HISTORY_MAX_CHARS = 3000


def session_binding(session: ChatSession | None) -> tuple[str, str] | None:
    """会话级执行对象：``target=agent/team`` 且有 ``target_code`` 才算锁定。

    这是「与某个助手单独对话」的硬边界：锁定后每轮都直达该对象，
    不再进入总控自动路由，也不允许被单条消息的 ``target_code`` 改道。
    """
    if session is None:
        return None
    target = (getattr(session, "target", None) or "auto").strip().lower()
    code = (getattr(session, "target_code", None) or "").strip()
    if target in {"agent", "team"} and code:
        return target, code
    return None


def _recent_conversation(
    db: Session,
    session_id: str,
    current: str,
    *,
    limit: int = CHAT_HISTORY_LIMIT,
    max_chars: int = CHAT_HISTORY_MAX_CHARS,
) -> str:
    """把本会话最近几轮对话压成一段上下文，供锁定助手承接前文。

    当前轮的用户消息在调用前已落库，取数时跳过最新一条，避免原样重复。
    """
    rows = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq.desc())
            .limit(limit + 1)
        )
    )
    if (
        rows
        and rows[0].role == ROLE_USER
        and (rows[0].content or "").strip() == (current or "").strip()
    ):
        rows = rows[1:]
    lines: list[str] = []
    for row in reversed(rows):
        if row.role == ROLE_ASSISTANT and row.status != MSG_OK:
            # 失败/中止的回答不进入上下文，避免把「服务暂时不可用」当成事实
            continue
        text = " ".join((row.content or "").split())
        if not text:
            continue
        prefix = "用户" if row.role == ROLE_USER else "助手"
        lines.append(f"{prefix}：{text[:600]}")
    block = "\n".join(lines)
    if not block:
        return ""
    if len(block) > max_chars:
        block = block[-max_chars:]
    return "【本会话此前的对话（仅用于承接上下文）】\n" + block


async def _run_bound_turn(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachments: list[ChatAttachment],
    binding: tuple[str, str],
    on_step: StepCallback | None,
) -> dict[str, Any]:
    """锁定会话的一轮执行：直达绑定对象，只带本会话历史与附件上下文。"""
    kind, target = binding
    label = "班组" if kind == "team" else "助手"
    context_parts: list[str] = []
    if attachments:
        if on_step:
            on_step(PHASE_READING, f"正在阅读 {len(attachments)} 个附件", STEP_RUNNING)
        context_parts.append(attachments_service.build_attachment_context(attachments))
        ready = sum(1 for a in attachments if a.status == "ready")
        if on_step:
            on_step(
                PHASE_READING,
                f"已读取 {len(attachments)} 个附件（{ready} 个可解析）",
                STEP_DONE,
            )
    history = _recent_conversation(db, session_id, content)
    if history:
        context_parts.append(history)
    context = "\n\n".join(part for part in context_parts if part)
    question = f"{content}\n\n{context}" if context else content

    # 锁定助手同样要能用办公技能产出文档：在定向执行前先识别文档意图。
    intent = intent_service.detect(content)
    if intent.kind == intent_service.INTENT_DOC:
        from app.modules.chat import document as document_service

        agent = None
        if kind == "agent":
            agent = declarative.get_published_by_code(
                db, yard_id=ctx.yard_id, agent_code=target, user_id=ctx.user_id
            )
        agent_name = str(getattr(agent, "name", "") or "") if agent else ""
        chosen_name = agent_name or (target if kind == "agent" else "")
        if on_step:
            on_step(PHASE_GENERATING, "正在整理文档内容", STEP_RUNNING)
        card, note, doc_usage = await document_service.generate_document_from_chat(
            db,
            ctx=ctx,
            session_id=session_id,
            request_text=content,
            context=context,
            agent_name=chosen_name,
            agent_persona=str(getattr(agent, "persona_prompt", "") or "") if agent else "",
            agent_bound_skills=list(getattr(agent, "bound_skills", None) or []) if agent else [],
        )
        if on_step:
            on_step(PHASE_GENERATING, f"文档已生成：{card['filename']}", STEP_DONE)
        return {
            "content": note,
            "routed_by": "bound",
            "choice": {
                "kind": kind,
                "id": str(getattr(agent, "id", "") or target),
                "name": chosen_name or "文档生成",
            },
            "reason": f"会话已绑定：{chosen_name or target}（文档生成）",
            "payload": card,
            "citations": [],
            "token_usage": doc_usage,
        }

    if on_step:
        on_step(PHASE_ROUTING, f"本会话已绑定{label}「{target}」，直接执行", STEP_RUNNING)
    data = await supervisor.ask_directed(
        db,
        ctx=ctx,
        question=question,
        target=target,
        prefer_team=kind == "team",
    )
    result = dict(data.get("result") or {})
    choice = dict(data.get("choice") or {})
    chosen_name = str(choice.get("name") or target)
    if on_step:
        on_step(PHASE_ROUTING, f"已由「{chosen_name}」回答（会话绑定）", STEP_DONE)

    answer_text = str(result.get("answer") or "")
    token_usage = result.get("token_usage")
    if not isinstance(token_usage, dict):
        in_tok = estimate_token_count(question)
        out_tok = estimate_token_count(answer_text)
        token_usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
            "estimated": True,
        }
    return {
        "content": answer_text,
        "routed_by": str(data.get("routed_by") or "bound"),
        "choice": choice,
        "reason": str(data.get("reason") or f"会话已绑定：{chosen_name}"),
        "payload": None,
        "citations": citations_from_result(result),
        "token_usage": token_usage,
    }


async def execute_turn(
    db: Session,
    *,
    ctx: YardContext,
    session_id: str,
    content: str,
    attachments: list[ChatAttachment],
    on_step: StepCallback | None = None,
    target_code: str | None = None,
    session: ChatSession | None = None,
) -> dict[str, Any]:
    """执行一轮对话，返回 {content, routed_by, choice, reason, payload, token_usage}。"""
    # 会话级锁定优先于一切自动分流：与某个助手/班组单独对话时，整轮只由它回答。
    binding = session_binding(session)
    if binding is not None:
        return await _run_bound_turn(
            db,
            ctx=ctx,
            session_id=session_id,
            content=content,
            attachments=attachments,
            binding=binding,
            on_step=on_step,
        )

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

    # ---- 技能链路：一句话要技能（先检索匹配，无匹配再生成私有草稿）----
    if intent.kind == intent_service.INTENT_SKILL:
        # 与 HTTP /market/agent/* 同口径：技能检索/生成需要 skills:generate
        # （默认全员都有；自定义角色裁掉后对话里同样不可用）
        _ensure_generate_permission(db, ctx)
        from app.modules.market.skill_agent import handle_skill_request

        if on_step:
            on_step(PHASE_RETRIEVING, "正在检索已有技能", STEP_RUNNING)
        try:
            skill_card: dict[str, Any] = await handle_skill_request(
                db, ctx=ctx, utterance=content, on_step=on_step
            )
        except AppError:
            # 与能力链路同口径：错误继续上抛（run_and_persist 负责落失败行），
            # 这里只把最后一步标成 failed，让过程可见。
            if on_step:
                on_step(PHASE_GENERATING, "技能检索/生成失败，请稍后重试", STEP_FAILED)
            raise

        state = str(skill_card.get("state") or "")
        raw_skill = skill_card.get("skill")
        skill: dict[str, Any] = raw_skill if isinstance(raw_skill, dict) else {}
        skill_name = str(skill.get("name") or "")
        note = str(skill_card.get("note") or "").strip()
        if state == "failed":
            fail_reason = note or "未生成可用技能草稿"
            if on_step:
                on_step(PHASE_GENERATING, f"技能生成失败：{fail_reason}", STEP_FAILED)
            summary = note or "技能生成失败，请稍后重试或换个说法再试。"
        elif state == "created":
            if on_step:
                on_step(PHASE_GENERATING, "未找到匹配，正在学习并生成技能", STEP_DONE)
                on_step(PHASE_GENERATING, "技能草稿已生成", STEP_DONE)
            summary = note or "已生成技能草稿，可在技能库中查看并绑定到助手。"
        else:
            if on_step:
                matched = len(skill_card.get("matches") or [])
                label = f"「{skill_name}」" if skill_name else ""
                on_step(PHASE_GENERATING, f"已找到 {matched} 个匹配技能{label}", STEP_DONE)
            summary = note or (
                f"已找到匹配技能「{skill_name}」，可在卡片中安装。"
                if skill_name
                else "已找到可用的匹配技能，请在卡片中确认。"
            )

        usage = skill_card.get("token_usage")
        if isinstance(usage, dict):
            skill_token_usage = usage
        else:
            in_tok = estimate_token_count(content)
            out_tok = estimate_token_count(summary) + estimate_token_count(str(skill_card))
            skill_token_usage = {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "model": "skill_generator",
                "estimated": True,
            }
        return {
            "content": summary,
            "routed_by": ROUTED_LOCAL,
            "choice": {
                "kind": "skill",
                "id": str(skill.get("id") or ""),
                "name": skill_name or "技能生成",
            },
            "reason": intent.reason,
            "payload": skill_card,
            "token_usage": skill_token_usage,
        }

    # ---- 插件链路：一句话要插件（v3.3：生成 workflow manifest，一律待审）----
    if intent.kind == intent_service.INTENT_PLUGIN:
        _ensure_generate_permission(db, ctx)
        from app.modules.market import plugin_agent

        if on_step:
            on_step(PHASE_GENERATING, "正在设计工作流插件", STEP_RUNNING)
        try:
            plugin_card: dict[str, Any] = await plugin_agent.handle_plugin_request(
                db, ctx=ctx, utterance=content, on_step=on_step
            )
        except AppError:
            # 与能力/技能链路同口径：错误继续上抛（run_and_persist 负责落失败行），
            # 这里只把最后一步标成 failed，让过程可见。
            if on_step:
                on_step(PHASE_GENERATING, "插件生成失败，请稍后重试", STEP_FAILED)
            raise

        state = str(plugin_card.get("state") or "")
        raw_plugin = plugin_card.get("plugin")
        plugin: dict[str, Any] = raw_plugin if isinstance(raw_plugin, dict) else {}
        plugin_name = str(plugin.get("name") or "")
        note = str(plugin_card.get("note") or "").strip()
        if state == "failed":
            fail_reason = note or "未生成可用插件草稿"
            if on_step:
                on_step(PHASE_GENERATING, f"插件生成失败：{fail_reason}", STEP_FAILED)
            summary = note or "插件生成失败，请稍后重试或换个说法再试。"
        else:
            if on_step:
                on_step(PHASE_GENERATING, "插件草稿已生成，等待审核", STEP_DONE)
            summary = note or (
                f"已生成工作流插件「{plugin_name}」，审核通过后即可运行。"
                if plugin_name
                else "已生成工作流插件草稿，审核通过后即可运行。"
            )

        usage = plugin_card.get("token_usage")
        if isinstance(usage, dict):
            plugin_token_usage = usage
        else:
            in_tok = estimate_token_count(content)
            out_tok = estimate_token_count(summary) + estimate_token_count(str(plugin_card))
            plugin_token_usage = {
                "input_tokens": in_tok,
                "output_tokens": out_tok,
                "total_tokens": in_tok + out_tok,
                "model": "plugin_generator",
                "estimated": True,
            }
        return {
            "content": summary,
            "routed_by": ROUTED_LOCAL,
            "choice": {
                "kind": "plugin",
                "id": str(plugin.get("plugin_id") or ""),
                "name": plugin_name or "插件生成",
            },
            "reason": intent.reason,
            "payload": plugin_card,
            "token_usage": plugin_token_usage,
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
        agent = (
            declarative.get_published_by_code(
                db, yard_id=ctx.yard_id, agent_code=target_code, user_id=ctx.user_id
            )
            if target_code
            else None
        )
        card, note, doc_usage = await document_service.generate_document_from_chat(
            db,
            ctx=ctx,
            session_id=session_id,
            request_text=content,
            context=doc_context,
            agent_name=str(getattr(agent, "name", "") or "") if agent else "",
            agent_persona=str(getattr(agent, "persona_prompt", "") or "") if agent else "",
            agent_bound_skills=(list(getattr(agent, "bound_skills", None) or []) if agent else []),
        )
        if on_step:
            on_step(PHASE_GENERATING, f"文档已生成：{card['filename']}", STEP_DONE)
        return {
            "content": note,
            "routed_by": ROUTED_LOCAL,
            "choice": {
                "kind": "agent",
                "id": str(getattr(agent, "id", "") or ""),
                "name": str(getattr(agent, "name", "") or "") or "文档生成",
            },
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

    # ---- 方案链路：多源取证合成完整方案（v3.14）----
    if intent.kind == intent_service.INTENT_PLAN and not target_code:
        from app.modules.chat import plan as plan_service

        if on_step:
            on_step(PHASE_ROUTING, "按方案链路合成完整方案", STEP_RUNNING)
        outcome = await plan_service.build_plan(
            db,
            ctx=ctx,
            session_id=session_id,
            question=content,
            context=context,
            on_step=on_step,
        )
        return {**outcome, "citations": _normalize_citations(outcome.get("citations"))}

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
        "citations": citations_from_result(result),
        "token_usage": token_usage,
    }


def scoped_ctx(ctx: YardContext, session: ChatSession) -> YardContext:
    """把「本会话的授权模式 + 会话 id」带进本轮执行上下文。

    工具风险闸（``tools/registry``）据此在 ask/auto/full 之间分流；不带会话 id 时
    一律退回 ask——任务、机器人、脚本等非对话入口的行为逐字不变。
    """
    return replace(
        ctx,
        approval_mode=session.approval_mode or APPROVAL_ASK,
        session_id=session.id,
        approval_requests=[],
    )


def approval_card(requests: list[dict[str, Any]]) -> dict[str, Any]:
    """被风险闸拦下的调用 → 对话里的「去批准」卡片（前端只渲染，不自行拼 id）。"""
    return {
        "type": "approval",
        "title": "高风险操作待批准",
        "hint": "本轮有工具需人工批准后才会执行；批准后可重新提问。",
        "items": [
            {
                "tool_code": str(r.get("tool_code") or ""),
                "approval_id": str(r.get("approval_id") or ""),
            }
            for r in requests
            if r.get("approval_id")
        ],
    }


def _bridge_commander_events(
    cb: CommanderEventCallback | None,
    run_id: str,
) -> Callable[[str, dict[str, Any]], None] | None:
    """把 commander 服务的事件名（plan/subtask/summary）映射成 SSE 帧名。

    服务层的 payload 是「运行/子任务对象」本身（plan/subtask 不带 run_id、
    子任务主键叫 id），这里补齐帧契约字段，前端才能按 run_id 归档。
    """
    if cb is None:
        return None
    mapping = {
        "plan": "commander-plan",
        "subtask": "subtask-status",
        "summary": "commander-summary",
    }

    def _bridge(kind: str, payload: dict[str, Any]) -> None:
        event = mapping.get(kind)
        if not event:
            return
        data = {"run_id": run_id, **payload}
        if kind == "subtask":
            data["subtask_id"] = str(payload.get("id") or payload.get("subtask_id") or "")
        cb(event, data)

    return _bridge


def _commander_token_usage(tokens: int) -> dict[str, Any]:
    total = max(0, int(tokens or 0))
    return {
        "input_tokens": 0,
        "output_tokens": total,
        "total_tokens": total,
        "model": "commander",
        "estimated": False,
    }


def _commander_history_context(db: Session, session: ChatSession, current: str) -> str:
    """把最近几条对话压成规划上下文。

    「第 3 页排版不好」这类反馈必须能对上上一版成品，否则指挥官会当成新任务重做。
    只取最近 ``COMMANDER_HISTORY_LIMIT`` 条（排除本轮刚落的用户消息），
    并把上一条答复里的产物标题一并带上，总长限制在 ``COMMANDER_HISTORY_CHARS``。
    """
    rows = list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session.id)
            .order_by(ChatMessage.seq.desc())
            .limit(COMMANDER_HISTORY_LIMIT + 1)
        )
    )
    rows.reverse()
    if rows and rows[-1].role == ROLE_USER and (rows[-1].content or "").strip() == current.strip():
        rows.pop()
    lines: list[str] = []
    for msg in rows[-COMMANDER_HISTORY_LIMIT:]:
        text = (msg.content or "").strip()
        if not text:
            continue
        if msg.role == ROLE_USER:
            label = "用户"
        elif msg.routed_by == "commander":
            label = "指挥官"
        else:
            label = "助手"
        item = f"[{label}] {text[:600]}"
        payload = msg.payload_json if isinstance(msg.payload_json, dict) else {}
        artifacts = payload.get("artifacts")
        if isinstance(artifacts, list):
            titles = [
                str(art.get("title") or art.get("name") or "").strip()
                for art in artifacts
                if isinstance(art, dict)
            ]
            titles = [title for title in titles if title][:5]
            if titles:
                item += f"\n（上一版产物：{'、'.join(titles)}）"
        lines.append(item)
    if not lines:
        return ""
    return (
        "最近对话（用于理解用户对上一版成品的修改意见；只作上下文，不要重复交付）：\n"
        + "\n".join(lines)
    )[:COMMANDER_HISTORY_CHARS]


async def _run_commander_turn(
    db: Session,
    *,
    ctx: YardContext,
    session: ChatSession,
    content: str,
    attachments: list[ChatAttachment],
    steps: StepRecorder,
    on_step: StepCallback | None,
    on_commander_event: CommanderEventCallback | None,
) -> dict[str, Any]:
    """指挥官一轮：拆解计划 →（预览或）并行执行 → 汇总落库。

    commander 模块全部懒导入：它是可选增强，模块自身出问题不能连普通单聊一起挂。
    """
    from app.modules.commander import service as commander_service
    from app.modules.commander import settings as commander_settings

    cfg = commander_settings.settings_for(db, session.yard_id)
    display_name = str(cfg.get("display_name") or "指挥官")
    context = attachments_service.build_attachment_context(attachments) if attachments else ""
    history = _commander_history_context(db, session, content)
    if history:
        context = f"{context}\n\n{history}".strip() if context else history
    emit = on_step or steps.emit

    emit(PHASE_ROUTING, f"{display_name}正在拆解任务", STEP_RUNNING)
    run = await commander_service.create_plan_run(
        db, ctx=ctx, session_id=session.id, user_content=content, context=context
    )
    plan_json = getattr(run, "plan_json", None) or {}
    clarify = [str(q).strip() for q in (plan_json.get("clarify") or []) if str(q).strip()]
    # 完全控制 = 不问：连澄清也跳过，按最合理假设直接开跑；只有轮尽才回报缺口
    if (
        clarify
        and cfg.get("clarify_enabled", False)
        and (session.approval_mode or APPROVAL_ASK) != APPROVAL_FULL
    ):
        # 目标太笼统：先问 1–2 个问题再拆，避免拆出一堆空转子任务
        text = f"为了把任务拆准，{display_name}需要先确认：\n" + "\n".join(
            f"{i}. {q}" for i, q in enumerate(clarify, start=1)
        )
        emit(PHASE_ROUTING, f"信息不足，先确认 {len(clarify)} 个问题", STEP_DONE)
        choice = {"kind": "commander", "id": run.id, "name": display_name}
        plan_tokens = _commander_token_usage(run.tokens_total)
        assistant_msg = persist_assistant_message(
            db,
            ctx=ctx,
            session=session,
            content=text,
            routed_by="commander",
            choice=choice,
            reason="指挥官澄清",
            payload=None,
            steps=steps.steps or None,
            token_usage=plan_tokens,
        )
        return {
            "assistant_message": assistant_msg,
            "content": text,
            "routed_by": "commander",
            "choice": choice,
            "reason": "指挥官澄清",
            "payload": None,
            "steps": steps.steps,
            "citations": [],
            "token_usage": plan_tokens,
        }

    _, planned_subtasks = commander_service.get_run(db, ctx.yard_id, run.id)
    plan = commander_service.plan_message_payload(run, planned_subtasks)
    if on_commander_event is not None:
        on_commander_event("commander-plan", plan)
    count = len(plan.get("subtasks") or [])
    emit(PHASE_ROUTING, f"计划已生成：{count} 个子任务", STEP_DONE)

    choice = {"kind": "commander", "id": run.id, "name": display_name}
    plan_tokens = _commander_token_usage(run.tokens_total)

    if cfg.get("preview_plan") and (session.approval_mode or APPROVAL_ASK) != APPROVAL_FULL:
        notice = f"已生成计划：共 {count} 个子任务，请确认后开始执行。"
        payload = {**plan, "run_id": run.id, "status": run.status}
        assistant_msg = persist_assistant_message(
            db,
            ctx=ctx,
            session=session,
            content=notice,
            routed_by="commander",
            choice=choice,
            reason="指挥官计划待确认",
            payload=payload,
            steps=steps.steps or None,
            token_usage=plan_tokens,
        )
        return {
            "assistant_message": assistant_msg,
            "content": notice,
            "routed_by": "commander",
            "choice": choice,
            "reason": "指挥官计划待确认",
            "payload": payload,
            "steps": steps.steps,
            "citations": [],
            "token_usage": plan_tokens,
        }

    # create_plan_run 内部已 commit；执行器自开短连接，本会话不能持有写事务
    emit(PHASE_GENERATING, f"{display_name}正在调度子任务并行执行", STEP_RUNNING)
    summary = await commander_service.execute_run(
        run.id, on_event=_bridge_commander_events(on_commander_event, run.id)
    )
    emit(PHASE_GENERATING, "执行完成，正在汇总", STEP_DONE)

    # 执行器在另一条连接上提交，本会话必须清缓存再读，否则拿到的是计划阶段的旧状态
    db.expire_all()
    latest_run, latest_subtasks = commander_service.get_run(db, ctx.yard_id, run.id)
    payload = {
        "type": "commander_summary",
        **commander_service.run_out(latest_run, latest_subtasks),
        "artifacts": summary.get("artifacts") or [],
        "failed": summary.get("failed") or [],
    }
    text = str(summary.get("answer") or f"{display_name}已完成本轮编排。")
    token_usage = _commander_token_usage(summary.get("tokens_total"))
    assistant_msg = persist_assistant_message(
        db,
        ctx=ctx,
        session=session,
        content=text,
        routed_by="commander",
        choice=choice,
        reason="指挥官编排",
        payload=payload,
        steps=steps.steps or None,
        token_usage=token_usage,
    )
    return {
        "assistant_message": assistant_msg,
        "content": text,
        "routed_by": "commander",
        "choice": choice,
        "reason": "指挥官编排",
        "payload": payload,
        "steps": steps.steps,
        "citations": [],
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
    execution_mode: str | None = None,
    on_commander_event: CommanderEventCallback | None = None,
) -> dict[str, Any]:
    ctx = scoped_ctx(ctx, session)
    steps = recorder or StepRecorder()

    def _cb(phase: str, detail: str, status: str) -> None:
        steps.emit(phase, detail, status)
        if on_step is not None:
            on_step(phase, detail, status)

    mode = apply_execution_mode(db, session, execution_mode)

    try:
        if mode == "commander" and target_code is None:
            return await _run_commander_turn(
                db,
                ctx=ctx,
                session=session,
                content=content,
                attachments=attachments,
                steps=steps,
                on_step=_cb,
                on_commander_event=on_commander_event,
            )
        outcome = await execute_turn(
            db,
            ctx=ctx,
            session_id=session.id,
            content=content,
            attachments=attachments,
            on_step=_cb,
            target_code=target_code,
            session=session,
        )
    except AppError as e:
        steps.fail(PHASE_GENERATING, e.message)
        blocked = (e.details or {}).get("approval_id") if e.code == "TOOL_RISK_BLOCKED" else None
        _persist_failure(
            db,
            session,
            ctx,
            code=e.code,
            message=e.message,
            steps=steps.steps,
            payload=approval_card(ctx.approval_requests)
            if blocked or ctx.approval_requests
            else None,
        )
        raise
    except Exception as e:  # noqa: BLE001
        log.warning("chat send unexpected error: %s", e)
        steps.fail(PHASE_GENERATING, "服务暂时不可用")
        _persist_failure(
            db, session, ctx, code="INTERNAL_ERROR", message="服务暂时不可用。", steps=steps.steps
        )
        raise

    steps.finish()
    payload = outcome["payload"]
    if ctx.approval_requests:
        # 模型可能已把「需要审批」写进回答，但卡片给出可点的审批单号，
        # 用户不必自己去找审批页；已有其他卡片时不覆盖，只补进步骤痕迹。
        if payload is None:
            payload = approval_card(ctx.approval_requests)
        else:
            # 本轮同时产出了别的卡片（方案/文档/技能…）：不能为了塞审批卡把它顶掉，
            # 但被拦下的调用也必须可点——把同一份 items 附在原卡片上。
            payload = {
                **payload,
                "approvals": approval_card(ctx.approval_requests)["items"],
            }
            steps.emit(
                PHASE_GENERATING,
                f"本轮有 {len(ctx.approval_requests)} 个高风险调用待批准",
                STEP_DONE,
            )
    assistant_msg = persist_assistant_message(
        db,
        ctx=ctx,
        session=session,
        content=outcome["content"],
        routed_by=outcome["routed_by"],
        choice=outcome["choice"],
        reason=outcome["reason"],
        payload=payload,
        steps=steps.steps or None,
        citations=outcome.get("citations"),
        token_usage=outcome.get("token_usage"),
    )
    return {
        "assistant_message": assistant_msg,
        "content": outcome["content"],
        "routed_by": outcome["routed_by"],
        "choice": outcome["choice"],
        "reason": outcome["reason"],
        "payload": payload,
        "steps": steps.steps,
        "citations": outcome.get("citations") or [],
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
    execution_mode: str | None = None,
    on_commander_event: CommanderEventCallback | None = None,
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
        execution_mode=execution_mode,
        on_commander_event=on_commander_event,
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
    "BATCH_ACTIONS",
    "CONTENT_CAP",
    "ROUTED_LOCAL",
    "approval_card",
    "batch_sessions",
    "create_session",
    "delete_session",
    "derive_title",
    "execute_turn",
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
    "run_and_persist",
    "scoped_ctx",
    "send_message",
    "session_out",
    "sessions_out_bulk",
    "update_session_flags",
]
