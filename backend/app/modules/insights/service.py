"""负责人驾驶舱统计：全小院只读聚合。

口径：
- 所有查询强制 ``yard_id`` 边界，不复用 chat 模块的「创建者私有」查询；
- Token 优先取 ``ChatMessage.token_usage_json``，缺失按角色回退
  ``estimate_token_count``（user → input，assistant → output）；
- 成员活跃取 ``User.last_login_at`` 或 membership 的 ``updated_at``；
- 窗口 ``days`` 含今天（今天 + 前 days-1 天，UTC 自然日）。
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.agents.models import STATUS_DELETED, YardAgent
from app.modules.auth.models import User
from app.modules.chat.models import (
    MSG_FAILED,
    ROLE_ASSISTANT,
    ROLE_USER,
    ChatMessage,
    ChatSession,
)
from app.modules.chat.service import message_out, session_out
from app.modules.evolution.models import KnowledgeCandidate, Skill
from app.modules.knowledge.chunking import estimate_token_count
from app.modules.llm.models import LLMCall
from app.modules.plugins.models import PluginManifest
from app.modules.yards.models import YardMembership

MAX_DAYS = 90


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _day_start(moment: datetime | None = None) -> datetime:
    current = _as_utc(moment) or _utcnow()
    return datetime.combine(current.date(), time.min, tzinfo=UTC)


def _clamp_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        value = 7
    return min(max(value, 1), MAX_DAYS)


def _window_start(days: int) -> datetime:
    return _day_start() - timedelta(days=_clamp_days(days) - 1)


def _message_tokens(msg: ChatMessage) -> tuple[int, int]:
    usage = msg.token_usage_json
    if isinstance(usage, dict):
        raw_in = usage.get("input_tokens")
        raw_out = usage.get("output_tokens")
        if raw_in is not None or raw_out is not None:
            return int(raw_in or 0), int(raw_out or 0)
    content = msg.content or ""
    if msg.role == ROLE_ASSISTANT:
        return 0, estimate_token_count(content)
    if msg.role == ROLE_USER:
        return estimate_token_count(content), 0
    return 0, 0


def _choice_agent(choice: Any) -> tuple[str, str] | None:
    """choice_json → (code, name)；非 Agent 选择返回 None。"""
    if not isinstance(choice, dict):
        return None
    code = str(choice.get("code") or choice.get("agent_code") or choice.get("id") or "").strip()
    name = str(choice.get("name") or code).strip()
    if not code and not name:
        return None
    return code, name or code


def _active_members(db: Session, yard_id: str) -> list[tuple[YardMembership, User]]:
    rows = db.execute(
        select(YardMembership, User)
        .join(User, User.id == YardMembership.user_id)
        .where(
            YardMembership.yard_id == yard_id,
            YardMembership.status == "active",
        )
        .order_by(YardMembership.created_at)
    ).all()
    return [(m, u) for m, u in rows]


def mask_email(value: str | None) -> str | None:
    if not value:
        return value
    local, sep, domain = value.partition("@")
    if not sep:
        return value[:2] + "***"
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}***@{domain}"


def mask_phone(value: str | None) -> str | None:
    if not value:
        return value
    if len(value) <= 4:
        return "***"
    if len(value) <= 7:
        return value[:2] + "***" + value[-2:]
    return value[:3] + "****" + value[-4:]


# ---------------------------------------------------------------------------
# 概览
# ---------------------------------------------------------------------------


def overview(db: Session, yard_id: str) -> dict[str, Any]:
    today = _day_start()
    week_start = _window_start(7)

    members = _active_members(db, yard_id)
    active_today = 0
    for membership, user in members:
        login = _as_utc(user.last_login_at)
        joined = _as_utc(membership.updated_at)
        if (login is not None and login >= today) or (joined is not None and joined >= today):
            active_today += 1

    agents_count = int(
        db.scalar(
            select(func.count())
            .select_from(YardAgent)
            .where(
                YardAgent.yard_id == yard_id,
                YardAgent.status != STATUS_DELETED,
            )
        )
        or 0
    )
    sessions_today = int(
        db.scalar(
            select(func.count())
            .select_from(ChatSession)
            .where(ChatSession.yard_id == yard_id, ChatSession.created_at >= today)
        )
        or 0
    )
    today_messages = db.scalars(
        select(ChatMessage).where(
            ChatMessage.yard_id == yard_id,
            ChatMessage.created_at >= today,
        )
    ).all()
    week_messages = db.scalars(
        select(ChatMessage).where(
            ChatMessage.yard_id == yard_id,
            ChatMessage.created_at >= week_start,
        )
    ).all()

    in_today = out_today = 0
    for msg in today_messages:
        msg_in, msg_out = _message_tokens(msg)
        in_today += msg_in
        out_today += msg_out
    in_week = out_week = 0
    for msg in week_messages:
        msg_in, msg_out = _message_tokens(msg)
        in_week += msg_in
        out_week += msg_out

    failed_today = sum(1 for msg in today_messages if msg.status == MSG_FAILED)

    candidates_pending = int(
        db.scalar(
            select(func.count())
            .select_from(KnowledgeCandidate)
            .where(
                KnowledgeCandidate.yard_id == yard_id,
                KnowledgeCandidate.status == "pending_review",
            )
        )
        or 0
    )
    skills_pending = int(
        db.scalar(
            select(func.count())
            .select_from(Skill)
            .where(Skill.yard_id == yard_id, Skill.status == "pending_review")
        )
        or 0
    )
    plugins_pending = int(
        db.scalar(
            select(func.count())
            .select_from(PluginManifest)
            .where(
                PluginManifest.yard_id == yard_id,
                PluginManifest.status == "pending_review",
            )
        )
        or 0
    )
    pending_total = candidates_pending + skills_pending + plugins_pending

    # 可信度与成本（v3.7）：引用覆盖率 + 7 日模型成功/成本，供负责人判断
    # “回答是否有依据、钱花在哪”。引用数量取 citations_json（历史消息为 null）。
    assistant_week = [m for m in week_messages if m.role == ROLE_ASSISTANT and m.content]
    cited_week = sum(
        1 for m in assistant_week if isinstance(m.citations_json, list) and m.citations_json
    )
    failed_week = sum(1 for m in week_messages if m.status == MSG_FAILED)
    llm_rows = db.execute(
        select(
            LLMCall.status,
            func.count(),
            func.coalesce(func.sum(LLMCall.estimated_cost), 0.0),
        )
        .where(LLMCall.yard_id == yard_id, LLMCall.created_at >= week_start)
        .group_by(LLMCall.status)
    ).all()
    llm_calls_7d = sum(int(row[1]) for row in llm_rows)
    llm_ok_7d = sum(int(row[1]) for row in llm_rows if row[0] == "success")
    llm_cost_7d = round(sum(float(row[2]) for row in llm_rows), 4)

    return {
        "yard_id": yard_id,
        "generated_at": _utcnow().isoformat(),
        "members_total": len(members),
        "active_today": active_today,
        "agents_count": agents_count,
        "sessions_today": sessions_today,
        "messages_today": len(today_messages),
        "tokens_today": in_today + out_today,
        "tokens_today_input": in_today,
        "tokens_today_output": out_today,
        "tokens_7d": in_week + out_week,
        "tokens_7d_input": in_week,
        "tokens_7d_output": out_week,
        "failed_messages_today": failed_today,
        "trust": {
            "assistant_messages_7d": len(assistant_week),
            "cited_messages_7d": cited_week,
            # 没有回答样本时返回 None，避免把“无数据”误读成 0% 覆盖
            "citation_coverage_7d": (
                round(cited_week / len(assistant_week), 4) if assistant_week else None
            ),
            "failed_messages_7d": failed_week,
            "llm_calls_7d": llm_calls_7d,
            "llm_success_rate_7d": (
                round(llm_ok_7d / llm_calls_7d, 4) if llm_calls_7d else None
            ),
            "llm_cost_7d": llm_cost_7d,
        },
        "pending_reviews": {
            "total": pending_total,
            "knowledge_candidates": candidates_pending,
            "skills": skills_pending,
            "plugins": plugins_pending,
        },
        # 外部检索/网络信号本轮不接入，保持字段稳定
        "network": None,
    }


# ---------------------------------------------------------------------------
# 成员用量
# ---------------------------------------------------------------------------


def _empty_member_stats() -> dict[str, Any]:
    return {
        "sessions": 0,
        "messages": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "agent_names": set(),
    }


def member_usage(db: Session, yard_id: str, days: int = 7) -> dict[str, Any]:
    days = _clamp_days(days)
    start = _window_start(days)
    members = _active_members(db, yard_id)
    stats: dict[str, dict[str, Any]] = {user.id: _empty_member_stats() for _, user in members}

    for session in db.scalars(
        select(ChatSession).where(
            ChatSession.yard_id == yard_id,
            ChatSession.created_at >= start,
        )
    ).all():
        bucket = stats.get(session.created_by)
        if bucket is not None:
            bucket["sessions"] += 1

    message_rows = db.execute(
        select(ChatMessage, ChatSession.created_by)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatMessage.yard_id == yard_id,
            ChatSession.yard_id == yard_id,
            ChatMessage.created_at >= start,
        )
    ).all()
    for msg, created_by in message_rows:
        bucket = stats.get(created_by)
        if bucket is None:
            continue
        msg_in, msg_out = _message_tokens(msg)
        bucket["messages"] += 1
        bucket["input_tokens"] += msg_in
        bucket["output_tokens"] += msg_out
        bucket["total_tokens"] += msg_in + msg_out
        picked = _choice_agent(msg.choice_json)
        if picked is not None:
            bucket["agent_names"].add(picked[1])

    items: list[dict[str, Any]] = []
    totals = {
        "members_total": len(members),
        "sessions": 0,
        "messages": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    for membership, user in members:
        bucket = stats[user.id]
        agents_used = sorted(bucket["agent_names"])
        last_active = _as_utc(user.last_login_at)
        items.append(
            {
                "user_id": user.id,
                "username": user.username,
                "display_name": user.display_name or user.username,
                "role": membership.role,
                "email": user.email,
                "phone": user.phone,
                "sessions": bucket["sessions"],
                "messages": bucket["messages"],
                "input_tokens": bucket["input_tokens"],
                "output_tokens": bucket["output_tokens"],
                "total_tokens": bucket["total_tokens"],
                "last_active": last_active.isoformat() if last_active else None,
                "agents_used": agents_used,
                "agents_used_count": len(agents_used),
            }
        )
        for key in ("sessions", "messages", "input_tokens", "output_tokens", "total_tokens"):
            totals[key] += bucket[key]

    items.sort(key=lambda item: (-item["total_tokens"], item["username"]))
    return {
        "days": days,
        "from": start.date().isoformat(),
        "to": _day_start().date().isoformat(),
        "items": items,
        "summary": totals,
    }


# ---------------------------------------------------------------------------
# 按日 / 按 Agent 用量
# ---------------------------------------------------------------------------


def _day_keys(start: datetime, days: int) -> list[str]:
    return [(start + timedelta(days=offset)).date().isoformat() for offset in range(days)]


def daily_usage(db: Session, yard_id: str, days: int = 7) -> dict[str, Any]:
    days = _clamp_days(days)
    start = _window_start(days)
    keys = _day_keys(start, days)
    users = {user.id: user for _, user in _active_members(db, yard_id)}

    buckets: dict[str, dict[str, Any]] = {
        key: {"sessions": 0, "messages": 0, "input_tokens": 0, "output_tokens": 0, "members": {}}
        for key in keys
    }

    for session in db.scalars(
        select(ChatSession).where(
            ChatSession.yard_id == yard_id,
            ChatSession.created_at >= start,
        )
    ).all():
        stamp_key = _as_utc(session.created_at)
        if stamp_key is None:
            continue
        bucket = buckets.get(stamp_key.date().isoformat())
        if bucket is not None:
            bucket["sessions"] += 1

    message_rows = db.execute(
        select(ChatMessage, ChatSession.created_by)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatMessage.yard_id == yard_id,
            ChatSession.yard_id == yard_id,
            ChatMessage.created_at >= start,
        )
    ).all()
    for msg, created_by in message_rows:
        stamp = _as_utc(msg.created_at)
        if stamp is None:
            continue
        bucket = buckets.get(stamp.date().isoformat())
        if bucket is None:
            continue
        msg_in, msg_out = _message_tokens(msg)
        bucket["messages"] += 1
        bucket["input_tokens"] += msg_in
        bucket["output_tokens"] += msg_out
        member = bucket["members"].setdefault(
            created_by,
            {
                "user_id": created_by,
                "username": users[created_by].username if created_by in users else "",
                "messages": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        )
        member["messages"] += 1
        member["input_tokens"] += msg_in
        member["output_tokens"] += msg_out
        member["total_tokens"] += msg_in + msg_out

    items: list[dict[str, Any]] = []
    totals = {
        "sessions": 0,
        "messages": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    for key in keys:
        bucket = buckets[key]
        by_member = sorted(
            bucket["members"].values(), key=lambda item: (-item["total_tokens"], item["username"])
        )
        total_tokens = bucket["input_tokens"] + bucket["output_tokens"]
        items.append(
            {
                "date": key,
                "sessions": bucket["sessions"],
                "messages": bucket["messages"],
                "input_tokens": bucket["input_tokens"],
                "output_tokens": bucket["output_tokens"],
                "total_tokens": total_tokens,
                "active_members": sum(1 for member in by_member if member["messages"]),
                "by_member": by_member,
            }
        )
        for name in ("sessions", "messages", "input_tokens", "output_tokens"):
            totals[name] += bucket[name]
        totals["total_tokens"] += total_tokens

    return {"days": days, "from": keys[0], "to": keys[-1], "items": items, "totals": totals}


def agent_usage(db: Session, yard_id: str, days: int = 7) -> dict[str, Any]:
    days = _clamp_days(days)
    start = _window_start(days)
    messages = db.scalars(
        select(ChatMessage).where(
            ChatMessage.yard_id == yard_id,
            ChatMessage.created_at >= start,
        )
    ).all()

    buckets: dict[tuple[str, str], dict[str, Any]] = {}
    for msg in messages:
        picked = _choice_agent(msg.choice_json)
        if picked is None:
            continue
        code, name = picked
        key = (code, name)
        bucket = buckets.setdefault(
            key,
            {
                "code": code,
                "name": name,
                "calls": 0,
                "failed": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        )
        msg_in, msg_out = _message_tokens(msg)
        bucket["calls"] += 1
        if msg.status == MSG_FAILED:
            bucket["failed"] += 1
        bucket["input_tokens"] += msg_in
        bucket["output_tokens"] += msg_out
        bucket["total_tokens"] += msg_in + msg_out

    items = sorted(buckets.values(), key=lambda item: (-item["calls"], item["name"]))
    totals = {
        "agents": len(items),
        "calls": sum(item["calls"] for item in items),
        "failed": sum(item["failed"] for item in items),
        "total_tokens": sum(item["total_tokens"] for item in items),
    }
    return {"days": days, "from": start.date().isoformat(), "items": items, "totals": totals}


# ---------------------------------------------------------------------------
# 全小院会话（负责人视角）
# ---------------------------------------------------------------------------


def list_sessions(
    db: Session,
    yard_id: str,
    page: int = 1,
    page_size: int = 20,
    user_id: str | None = None,
    keyword: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    page = max(int(page or 1), 1)
    page_size = min(max(int(page_size or 20), 1), 100)

    conditions = [ChatSession.yard_id == yard_id]
    if user_id:
        conditions.append(ChatSession.created_by == user_id)
    if keyword:
        pattern = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                ChatSession.title.ilike(pattern),
                User.username.ilike(pattern),
                User.display_name.ilike(pattern),
            )
        )

    total = int(
        db.scalar(
            select(func.count())
            .select_from(ChatSession)
            .outerjoin(User, User.id == ChatSession.created_by)
            .where(*conditions)
        )
        or 0
    )
    rows = db.execute(
        select(ChatSession, User)
        .outerjoin(User, User.id == ChatSession.created_by)
        .where(*conditions)
        .order_by(ChatSession.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()

    items: list[dict[str, Any]] = []
    for session, user in rows:
        item = session_out(db, session)
        item["created_by_username"] = user.username if user else None
        item["created_by_display_name"] = (user.display_name or user.username) if user else None
        items.append(item)
    return items, total


def session_messages(db: Session, yard_id: str, session_id: str) -> dict[str, Any]:
    session = db.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.yard_id == yard_id,
        )
    )
    if session is None:
        raise AppError("CHAT_SESSION_NOT_FOUND", "会话不存在或无权访问。", http_status=404)
    rows = db.scalars(
        select(ChatMessage)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.yard_id == yard_id,
        )
        .order_by(ChatMessage.seq)
    ).all()
    return {
        "session": {
            "id": session.id,
            "title": session.title,
            "created_by": session.created_by,
            "created_at": session.created_at.isoformat() if session.created_at else "",
            "updated_at": session.updated_at.isoformat() if session.updated_at else "",
        },
        "items": [message_out(msg) for msg in rows],
    }


__all__ = [
    "agent_usage",
    "daily_usage",
    "list_sessions",
    "mask_email",
    "mask_phone",
    "member_usage",
    "overview",
    "session_messages",
]
