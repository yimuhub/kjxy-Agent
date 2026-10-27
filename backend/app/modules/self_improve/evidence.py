"""自我总结的窗口证据聚合（只读，不落任何原文）。

隐私口径：
- 只取**主题级信号**——会话标题（截断）、反馈样本（截断）、工具与错误类别、
  token 消耗；不读取、不传递、不存储会话消息正文；
- 所有查询强制 ``yard_id`` 边界，跨小院不可见。
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.agents.run_steps import AgentRunStep
from app.modules.auth.models import User
from app.modules.chat.models import (
    MSG_FAILED,
    ROLE_ASSISTANT,
    ROLE_USER,
    ChatMessage,
    ChatSession,
)
from app.modules.evolution.models import Feedback
from app.modules.knowledge.chunking import estimate_token_count

#: 证据裁剪上限：控制 prompt 体积与隐私暴露面
MAX_MEMBERS = 30
MAX_TOPICS_PER_MEMBER = 8
MAX_FEEDBACK_SAMPLES = 5
TOPIC_CHARS = 40
SAMPLE_CHARS = 120


def message_tokens(msg: ChatMessage) -> tuple[int, int]:
    """单条消息 token：优先真实统计，缺失按角色估算（与驾驶舱同口径）。"""
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


def _short(text: str | None, limit: int) -> str:
    clean = " ".join((text or "").split())
    return clean[:limit]


def collect_evidence(db: Session, yard_id: str, start: datetime, end: datetime) -> dict[str, Any]:
    """聚合 ``[start, end)`` 窗口内的小院级证据。"""
    sessions = list(
        db.scalars(
            select(ChatSession).where(
                ChatSession.yard_id == yard_id,
                ChatSession.created_at >= start,
                ChatSession.created_at < end,
            )
        )
    )
    messages = list(
        db.scalars(
            select(ChatMessage).where(
                ChatMessage.yard_id == yard_id,
                ChatMessage.created_at >= start,
                ChatMessage.created_at < end,
            )
        )
    )
    session_owner = {s.id: s.created_by for s in sessions}
    users = {
        user.id: user
        for user in db.scalars(
            select(User).where(User.id.in_({s.created_by for s in sessions} or {""}))
        )
    }

    members: dict[str, dict[str, Any]] = {}
    for session in sessions:
        bucket = members.setdefault(
            session.created_by,
            {
                "user_id": session.created_by,
                "username": (
                    (users[session.created_by].display_name or users[session.created_by].username)
                    if session.created_by in users
                    else ""
                ),
                "sessions": 0,
                "messages": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "topics": [],
            },
        )
        bucket["sessions"] += 1
        title = _short(session.title, TOPIC_CHARS)
        at_capacity = len(bucket["topics"]) >= MAX_TOPICS_PER_MEMBER
        if title and not at_capacity and title not in bucket["topics"]:
            bucket["topics"].append(title)

    for msg in messages:
        owner = session_owner.get(msg.session_id)
        member = members.get(owner) if owner else None
        if member is None:
            continue
        msg_in, msg_out = message_tokens(msg)
        member["messages"] += 1
        member["input_tokens"] += msg_in
        member["output_tokens"] += msg_out

    member_items = sorted(members.values(), key=lambda item: (-item["messages"], item["username"]))[
        :MAX_MEMBERS
    ]
    for item in member_items:
        item["total_tokens"] = item["input_tokens"] + item["output_tokens"]

    feedback_rows = list(
        db.scalars(
            select(Feedback).where(
                Feedback.yard_id == yard_id,
                Feedback.created_at >= start,
                Feedback.created_at < end,
            )
        )
    )
    down_samples: list[str] = []
    for row in feedback_rows:
        if row.rating != "down":
            continue
        sample = _short(row.feedback_text or row.corrected_answer, SAMPLE_CHARS)
        if sample and sample not in down_samples and len(down_samples) < MAX_FEEDBACK_SAMPLES:
            down_samples.append(sample)

    steps = list(
        db.scalars(
            select(AgentRunStep).where(
                AgentRunStep.yard_id == yard_id,
                AgentRunStep.created_at >= start,
                AgentRunStep.created_at < end,
            )
        )
    )
    tool_calls: Counter[str] = Counter()
    tool_errors: Counter[str] = Counter()
    for step in steps:
        code = str(step.tool_code or "").strip()
        if not code:
            continue
        tool_calls[code] += 1
        if step.status == "error":
            tool_errors[code] += 1

    error_codes = Counter(
        str(msg.error_code or "").strip()
        for msg in messages
        if msg.status == MSG_FAILED and str(msg.error_code or "").strip()
    )

    return {
        "window": {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "days": max(1, round((end - start).total_seconds() / 86400, 2)),
        },
        "sessions_total": len(sessions),
        "messages_total": len(messages),
        "members": member_items,
        "feedback": {
            "up": sum(1 for row in feedback_rows if row.rating == "up"),
            "down": sum(1 for row in feedback_rows if row.rating == "down"),
            "samples": down_samples,
        },
        "tools": [
            {"code": code, "count": count, "errors": tool_errors.get(code, 0)}
            for code, count in tool_calls.most_common(8)
        ],
        "failures": {
            "failed_messages": sum(1 for msg in messages if msg.status == MSG_FAILED),
            "error_codes": [
                {"code": code, "count": count} for code, count in error_codes.most_common(5)
            ],
        },
        "knowledge_gaps": down_samples,
    }


__all__ = ["collect_evidence", "message_tokens"]
