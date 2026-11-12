"""工作日志/月报服务（v3.58，角色工作台 P0）。

对应全国科技小院平台的硬考核链路（日志+月报挂钩研究生实践考核与小院年度
评价，调研 docs/research-jiangsu-science-yards.md）。三条铁律：

1. **草稿是聚合不是编造**：证据来自平台真实发生的事（当天会话/任务/审计里
   的农事与设备操作），只汇总事实与数量，**不读消息原文**、不写评价性语言；
2. **人工确认才生效**：草稿必须由本人编辑后提交（submitted），审核者
   （logs:review）通过（approved）或驳回（回 draft 带意见）；
3. **导出兼容全国平台口径**：月报按日志聚合，Word 走 docs 渲染管线。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.modules.audit.models import AuditLog
from app.modules.audit.service import log_audit
from app.modules.chat.models import ChatMessage, ChatSession
from app.modules.journal.models import (
    KIND_DAILY,
    KIND_MONTHLY,
    STATUS_APPROVED,
    STATUS_DRAFT,
    STATUS_SUBMITTED,
    VALID_KINDS,
    YardJournal,
)
from app.modules.tasks.models import Task

#: 单份日志的素材采样上限（防极端日拉爆聚合查询）
_EVIDENCE_SAMPLE = 40

#: 年度考核锚点（三部门《质量监测标准（试行）》量化口径，docs/research-china-
#: science-yards-2026-11.md §2）：日志 100 篇/年、常驻 120 天/年。系统默认按此
#: 显示达标进度；小院可在设置中覆盖（预留）。
ANNUAL_LOG_TARGET = 100
ANNUAL_RESIDENCE_DAYS = 120


def _period_key(kind: str, day: date) -> str:
    return day.isoformat() if kind == KIND_DAILY else day.strftime("%Y-%m")


def _period_bounds(kind: str, day: date) -> tuple[datetime, datetime]:
    if kind == KIND_DAILY:
        start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        return start, start + timedelta(days=1)
    start = datetime(day.year, day.month, 1, tzinfo=UTC)
    nxt = date(day.year + (day.month == 12), (day.month % 12) + 1, 1)
    return start, datetime(nxt.year, nxt.month, 1, tzinfo=UTC)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    return _period_bounds(KIND_DAILY, day)


# ---------------------------------------------------------------------------
# 证据聚合（只取事实与数量，不读消息原文）
# ---------------------------------------------------------------------------


def collect_evidence(db: Session, yard_id: str, user_id: str, day: date) -> dict[str, Any]:
    """当天该成员的平台活动证据：会话/任务/审计事件的**计数与标题摘要**。"""
    start, end = _day_bounds(day)
    sessions = (
        db.scalars(
            select(ChatSession.id).where(
                ChatSession.yard_id == yard_id,
                ChatSession.created_by == user_id,
                ChatSession.created_at >= start,
                ChatSession.created_at < end,
            )
        ).all()
        if hasattr(ChatSession, "created_by")
        else []
    )
    msg_count = 0
    if sessions:
        msg_count = int(
            db.scalar(
                select(func.count())
                .select_from(ChatMessage)
                .where(
                    ChatMessage.session_id.in_(list(sessions)),
                    ChatMessage.created_at >= start,
                    ChatMessage.created_at < end,
                )
            )
            or 0
        )
    tasks = db.scalars(
        select(Task)
        .where(
            Task.yard_id == yard_id,
            Task.created_by == user_id,
            Task.created_at >= start,
            Task.created_at < end,
        )
        .limit(_EVIDENCE_SAMPLE)
    ).all()
    task_items = [{"type": str(t.task_type), "status": str(t.status)} for t in tasks]
    audits = db.scalars(
        select(AuditLog)
        .where(
            AuditLog.yard_id == yard_id,
            AuditLog.actor_id == user_id,
            AuditLog.created_at >= start,
            AuditLog.created_at < end,
        )
        .order_by(AuditLog.created_at.desc())
        .limit(_EVIDENCE_SAMPLE)
    ).all()
    actions: dict[str, int] = {}
    for row in audits:
        actions[str(row.action)] = actions.get(str(row.action), 0) + 1
    return {
        "date": day.isoformat(),
        "sessions": len(sessions),
        "messages": msg_count,
        "tasks": task_items,
        "audit_actions": actions,
        "audit_total": sum(actions.values()),
    }


def _render_daily_markdown(evidence: dict[str, Any], author_note: str = "") -> str:
    """证据 → 日志正文（全国平台口径：客观、可追溯、无评价性语言）。"""
    day = str(evidence.get("date") or "")
    lines = [f"# 工作日志 {day}", ""]
    sessions = int(evidence.get("sessions") or 0)
    messages = int(evidence.get("messages") or 0)
    if sessions or messages:
        lines.append(f"- 平台对话：{sessions} 个会话，{messages} 条消息（技术咨询/方案讨论）。")
    for task in evidence.get("tasks") or []:
        lines.append(f"- 平台任务：{task['type']}（{task['status']}）。")
    actions = evidence.get("audit_actions") or {}
    label_map = {
        "farm.record": "农事台账记录",
        "device.action": "设备操作",
        "task.create": "发起任务",
        "agent_generate_requested": "发起智能体生成",
        "official_pack_synced": "启用官方技能包",
        "skill_generated": "生成技能草稿",
        "plugin_generated": "生成插件草稿",
    }
    for action, count in sorted(actions.items(), key=lambda kv: -kv[1]):
        label = label_map.get(action, action)
        lines.append(f"- {label}：{count} 次。")
    total = int(evidence.get("audit_total") or 0)
    if total == 0 and not task_items_if_any(evidence):
        lines.append("- 本日无平台操作记录。")
    if author_note:
        lines += ["", author_note.strip()]
    lines += ["", "（本日志由平台活动自动汇总生成，经本人核对补充后提交。）"]
    return "\n".join(lines)


def task_items_if_any(evidence: dict[str, Any]) -> list[Any]:
    return list(evidence.get("tasks") or [])


# ---------------------------------------------------------------------------
# CRUD / 状态机
# ---------------------------------------------------------------------------


def get_or_none(db: Session, yard_id: str, journal_id: str) -> YardJournal | None:
    row = db.get(YardJournal, journal_id)
    if row is None or row.yard_id != yard_id:
        return None
    return row


def build_draft(db: Session, ctx: YardContext, kind: str, day: date) -> YardJournal:
    """生成（或取回）当日/当月日志草稿：幂等——已有草稿原样返回。"""
    if kind not in VALID_KINDS:
        raise AppError("INVALID_REQUEST", f"kind 只能是 {VALID_KINDS}。", http_status=400)
    period = _period_key(kind, day)
    existing = db.scalar(
        select(YardJournal).where(
            YardJournal.yard_id == ctx.yard_id,
            YardJournal.author_user_id == ctx.user_id,
            YardJournal.kind == kind,
            YardJournal.period_key == period,
        )
    )
    if existing is not None:
        return existing

    if kind == KIND_DAILY:
        evidence = collect_evidence(db, ctx.yard_id, ctx.user_id, day)
        content = _render_daily_markdown(evidence)
        title = f"工作日志 {period}"
        sources = {
            "evidence": {
                "sessions": evidence.get("sessions"),
                "messages": evidence.get("messages"),
                "audit_total": evidence.get("audit_total"),
            }
        }
    else:
        start, end = _period_bounds(kind, day)
        days = []
        cur = start.date()
        while cur < end.date():
            day_evidence = collect_evidence(db, ctx.yard_id, ctx.user_id, cur)
            if int(day_evidence.get("audit_total") or 0) or day_evidence.get("tasks"):
                days.append(cur.isoformat())
            cur += timedelta(days=1)
        content = _render_monthly_markdown(period, days)
        title = f"工作月报 {period}"
        sources = {"active_days": days}  # type: ignore[dict-item]
    row = YardJournal(
        id=uuid4().hex,
        yard_id=ctx.yard_id,
        author_user_id=ctx.user_id,
        kind=kind,
        period_key=period,
        entry_date=day if kind == KIND_DAILY else date(day.year, day.month, 1),
        title=title[:200],
        content_md=content,
        sources_json=sources,
        status=STATUS_DRAFT,
    )
    db.add(row)
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="journal_draft_created",
        resource_type="journal",
        resource_id=row.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"kind": kind, "period": period},
    )
    return row


def _render_monthly_markdown(period: str, active_days: list[str]) -> str:
    lines = [f"# 工作月报 {period}", "", f"- 本月有平台活动记录的天数：{len(active_days)} 天。"]
    if active_days:
        lines.append("- 活跃日期：" + "、".join(active_days[:31]))
    lines += [
        "- 月度工作内容：请按下方日志逐日核对后补充要点（试验进展/培训/推广）。",
        "",
        "（本月报由平台日志聚合生成，经本人核对补充后提交。）",
    ]
    return "\n".join(lines)


def save_content(
    db: Session, ctx: YardContext, journal_id: str, content_md: str, title: str | None = None
) -> YardJournal:
    row = get_or_none(db, ctx.yard_id, journal_id)
    if row is None:
        raise AppError("JOURNAL_NOT_FOUND", "日志不存在或无权访问。", http_status=404)
    if row.author_user_id != ctx.user_id:
        raise AppError("FORBIDDEN", "只能编辑自己的日志。", http_status=403)
    if row.status not in (STATUS_DRAFT,):
        raise AppError(
            "JOURNAL_NOT_EDITABLE", "已提交的日志不能编辑（可驳回后改）。", http_status=409
        )
    row.content_md = str(content_md or "")[:20000]
    if title is not None:
        row.title = str(title)[:200]
    db.flush()
    return row


def submit(db: Session, ctx: YardContext, journal_id: str) -> YardJournal:
    row = get_or_none(db, ctx.yard_id, journal_id)
    if row is None:
        raise AppError("JOURNAL_NOT_FOUND", "日志不存在或无权访问。", http_status=404)
    if row.author_user_id != ctx.user_id:
        raise AppError("FORBIDDEN", "只能提交自己的日志。", http_status=403)
    if row.status != STATUS_DRAFT:
        raise AppError(
            "INVALID_JOURNAL_TRANSITION", f"当前状态 {row.status} 不能提交。", http_status=409
        )
    if not row.content_md.strip():
        raise AppError("JOURNAL_EMPTY", "日志内容为空，先编辑再提交。", http_status=400)
    row.status = STATUS_SUBMITTED
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action="journal_submitted",
        resource_type="journal",
        resource_id=row.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"kind": row.kind, "period": row.period_key},
    )
    return row


def review(
    db: Session, ctx: YardContext, journal_id: str, action: str, note: str | None
) -> YardJournal:
    if action not in ("approve", "reject"):
        raise AppError("INVALID_REQUEST", "action 只能是 approve|reject。", http_status=400)
    row = get_or_none(db, ctx.yard_id, journal_id)
    if row is None:
        raise AppError("JOURNAL_NOT_FOUND", "日志不存在或无权访问。", http_status=404)
    if row.status != STATUS_SUBMITTED:
        raise AppError("INVALID_JOURNAL_TRANSITION", "只有已提交的日志可以审核。", http_status=409)
    if action == "approve":
        row.status = STATUS_APPROVED
    else:
        row.status = STATUS_DRAFT
    row.reviewed_by = ctx.user_id
    row.review_note = (str(note) if note else "")[:500]
    db.flush()
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action=f"journal_{action}",
        resource_type="journal",
        resource_id=row.id,
        result="success",
        request_id=ctx.request_id,
        metadata={"kind": row.kind, "period": row.period_key, "note": row.review_note},
    )
    return row


def list_journal(
    db: Session, ctx: YardContext, *, month: str | None = None, mine_only: bool = True
) -> list[YardJournal]:
    stmt = select(YardJournal).where(YardJournal.yard_id == ctx.yard_id)
    if mine_only:
        stmt = stmt.where(YardJournal.author_user_id == ctx.user_id)
    if month:
        stmt = stmt.where(YardJournal.period_key.like(f"{month}%"))
    stmt = stmt.order_by(YardJournal.period_key.desc(), YardJournal.created_at.desc()).limit(200)
    return list(db.scalars(stmt))


def journal_out(row: YardJournal, *, author_name: str | None = None) -> dict[str, Any]:
    """单条日志载荷。

    ``author_name``：作者显示名（优先昵称/姓名，回退用户名）。由列表/详情路由
    批量解析后传入——交付走查修正：此前「作者」列直接暴露 user UUID，
    对用户是不可读的内部标识。
    """
    return {
        "id": row.id,
        "kind": row.kind,
        "period_key": row.period_key,
        "entry_date": (
            row.entry_date.isoformat()  # type: ignore[attr-defined]
            if row.entry_date is not None
            else None
        ),
        "title": row.title,
        "content_md": row.content_md,
        "status": row.status,
        "author_user_id": row.author_user_id,
        "author_name": author_name,
        "reviewed_by": row.reviewed_by,
        "review_note": row.review_note,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def author_name_map(db: Session, user_ids: list[str]) -> dict[str, str]:
    """user_id → 展示名（display_name 优先，回退 username）；一次查询批量解析。"""
    ids = [u for u in {i for i in user_ids if i} if u]
    if not ids:
        return {}
    from app.modules.auth.models import User

    rows = db.execute(
        select(User.id, User.display_name, User.username).where(User.id.in_(ids))
    ).all()
    return {uid: (display or username or uid[:8]) for uid, display, username in rows}


# ---------------------------------------------------------------------------
# 统计与聚合
# ---------------------------------------------------------------------------


def stats(db: Session, ctx: YardContext, month: str) -> dict[str, Any]:
    """本人月度考核进度 + 年度达标牵引（对照官方 100 篇/120 天锚点）。"""
    rows = list_journal(db, ctx, month=month, mine_only=True)
    daily = [r for r in rows if r.kind == KIND_DAILY]
    monthly = [r for r in rows if r.kind == KIND_MONTHLY]
    year_prefix = month[:4]
    year_daily = [r for r in rows if r.period_key.startswith(year_prefix)]
    year_count = len(year_daily)
    now = datetime.now(UTC)
    year_start = date(now.year, 1, 1)
    days_elapsed = max(1, (now.date() - year_start).days + 1)
    days_left = (date(now.year, 12, 31) - now.date()).days
    weekly_pace = round(year_count / max(1, days_elapsed) * 7, 1)
    projected = year_count + int(weekly_pace * max(0, days_left) / 7)
    return {
        "month": month,
        "daily_total": len(daily),
        "daily_submitted": sum(1 for r in daily if r.status == STATUS_SUBMITTED),
        "daily_approved": sum(1 for r in daily if r.status == STATUS_APPROVED),
        "monthly_total": len(monthly),
        "active_days": len({r.period_key for r in daily}),
        # 年度达标牵引（对标质量监测标准）
        "year_total": year_count,
        "annual_target": ANNUAL_LOG_TARGET,
        "annual_progress": round(min(1.0, year_count / ANNUAL_LOG_TARGET), 3),
        "weekly_pace": weekly_pace,
        "projected_year_total": projected,
        "on_track": projected >= ANNUAL_LOG_TARGET,
    }


def aggregate_overview(db: Session, *, month: str, stale_days: int = 7) -> dict[str, Any]:
    """跨小院监管聚合（platform_admin）：各院日志量/活跃作者/断更预警。

    只出数字与时间，不含任何日志正文——跨院只做度量不做内容。
    """
    from app.modules.yards.models import Yard

    month_prefix = f"{month}%"
    rows = db.execute(
        select(
            YardJournal.yard_id,
            func.count(),
            func.count(func.distinct(YardJournal.author_user_id)),
            func.max(YardJournal.updated_at),
        )
        .where(YardJournal.period_key.like(month_prefix))
        .group_by(YardJournal.yard_id)
    ).all()
    stale_before = datetime.now(UTC) - timedelta(days=stale_days)
    yards = {y.id: y.name for y in db.scalars(select(Yard)).all()}
    items: list[dict[str, Any]] = []

    def _aware(value: datetime | None) -> datetime | None:
        # SQLite 返回 naive datetime，统一按 UTC 处理（与 TimestampMixin 写入口径一致）
        if value is None:
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    for yard_id, total, authors, last_at in rows:
        last_at = _aware(last_at)
        last_iso = last_at.isoformat() if last_at else None
        items.append(
            {
                "yard_id": yard_id,
                "yard_name": yards.get(yard_id, yard_id[:8]),
                "journal_count": int(total),
                "active_authors": int(authors),
                "last_updated_at": last_iso,
                "stale": last_at is None or last_at < stale_before,
            }
        )
    # 有院无日志（本月零填报）也进列表，标记 stale
    seen = {i["yard_id"] for i in items}
    for yard_id, name in yards.items():
        if yard_id not in seen:
            items.append(
                {
                    "yard_id": yard_id,
                    "yard_name": name,
                    "journal_count": 0,
                    "active_authors": 0,
                    "last_updated_at": None,
                    "stale": True,
                }
            )
    items.sort(key=lambda i: -i["journal_count"])
    return {"month": month, "stale_days": stale_days, "items": items}


def export_monthly_docx_raw(db: Session, ctx: YardContext, month: str) -> dict[str, Any] | None:
    """月报导出：按月聚合日志 → markdown → DocModel dict（docs 管线消费）。"""
    from app.modules.commander.export import markdown_to_doc_model

    rows = list_journal(db, ctx, month=month, mine_only=True)
    if not rows:
        return None
    lines = [f"# 工作月报 {month}", ""]
    daily = [r for r in rows if r.kind == KIND_DAILY]
    for row in sorted(daily, key=lambda r: r.period_key):
        lines.append(f"## {row.period_key}")
        for line in row.content_md.split("\n"):
            if line.startswith("# "):
                continue
            lines.append(line if line else "")
        lines.append("")
    monthly = [r for r in rows if r.kind == KIND_MONTHLY]
    for row in monthly:
        lines.append(f"## 月报要点（{row.period_key}）")
        lines += [line for line in row.content_md.split("\n") if line and not line.startswith("# ")]
    model = markdown_to_doc_model("\n".join(lines), title=f"工作月报 {month}")
    return model.model_dump()


__all__ = [
    "aggregate_overview",
    "build_draft",
    "collect_evidence",
    "export_monthly_docx_raw",
    "get_or_none",
    "journal_out",
    "list_journal",
    "review",
    "save_content",
    "stats",
    "submit",
]
