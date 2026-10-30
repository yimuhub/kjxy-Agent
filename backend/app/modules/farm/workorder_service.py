"""告警与工单闭环（v3.10）：告警转单、指派、状态流转、回执与逾期升级。

- 告警 critical 自动建单；其余可人工转单，避免把噪声全部变成工单。
- 状态流转由服务端白名单约束，事件流只追加，回执写入结论与关闭时间。
- 逾期升级由 beat 任务 ``workorders.escalate_overdue`` 每日巡检。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.notify import send_alert
from app.modules.audit.service import log_audit
from app.modules.auth.models import User
from app.modules.farm.models import (
    ALERT_SEVERITIES,
    TERMINAL_WORK_ORDER_STATUSES,
    WORK_ORDER_PRIORITIES,
    WORK_ORDER_STATUSES,
    FarmAlert,
    FarmPlot,
    WorkOrder,
    WorkOrderEvent,
)
from app.modules.farm.schemas import (
    AlertCreate,
    WorkOrderCreate,
    WorkOrderReceipt,
    WorkOrderUpdate,
)
from app.modules.yards.models import YardMembership

_SEVERITY_PRIORITY = {"low": "low", "normal": "normal", "high": "high", "critical": "urgent"}
_PRIORITY_ORDER = ["low", "normal", "high", "urgent"]
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "open": {"assigned", "in_progress", "cancelled"},
    "assigned": {"open", "in_progress", "done", "cancelled"},
    "in_progress": {"done", "cancelled"},
    "done": {"in_progress", "closed"},
    "closed": set(),
    "cancelled": set(),
}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _ensure_plot(db: Session, yard_id: str, plot_id: str | None) -> None:
    if not plot_id:
        return
    exists = db.scalar(
        select(FarmPlot.id).where(FarmPlot.yard_id == yard_id, FarmPlot.id == plot_id)
    )
    if exists is None:
        raise AppError("FARM_PLOT_NOT_FOUND", "地块不存在或无权访问。", http_status=404)


def _member_name(db: Session, yard_id: str, user_id: str) -> str:
    row = db.execute(
        select(User.display_name, User.username)
        .join(YardMembership, YardMembership.user_id == User.id)
        .where(
            YardMembership.yard_id == yard_id,
            YardMembership.user_id == user_id,
            YardMembership.status == "active",
        )
    ).first()
    if row is None:
        raise AppError("INVALID_ASSIGNEE", "被指派人不是本院在职成员。", http_status=400)
    return row[0] or row[1]


def _event(
    db: Session,
    *,
    yard_id: str,
    order: WorkOrder,
    kind: str,
    note: str,
    actor_id: str | None,
    from_status: str | None = None,
    to_status: str | None = None,
    client_op_id: str | None = None,
) -> None:
    db.add(
        WorkOrderEvent(
            yard_id=yard_id,
            work_order_id=order.id,
            kind=kind,
            note=note,
            actor_id=actor_id,
            from_status=from_status,
            to_status=to_status,
            client_op_id=client_op_id,
        )
    )


def _new_order(
    db: Session,
    *,
    yard_id: str,
    user_id: str | None,
    title: str,
    description: str,
    priority: str,
    source: str,
    source_ref: str | None,
    plot_id: str | None,
    assignee_id: str | None,
    due_on: date | None,
    client_op_id: str | None = None,
) -> WorkOrder:
    if priority not in WORK_ORDER_PRIORITIES:
        raise AppError("INVALID_REQUEST", "未知工单优先级。", http_status=400)
    assignee_name = ""
    if assignee_id:
        assignee_name = _member_name(db, yard_id, assignee_id)
    order = WorkOrder(
        yard_id=yard_id,
        title=title.strip(),
        description=description.strip(),
        priority=priority,
        status="assigned" if assignee_id else "open",
        source=source,
        source_ref=source_ref,
        plot_id=plot_id,
        assignee_id=assignee_id,
        assignee_name=assignee_name,
        due_on=due_on,
        created_by=user_id,
        client_op_id=client_op_id,
    )
    db.add(order)
    db.flush()
    _event(
        db,
        yard_id=yard_id,
        order=order,
        kind="created",
        note=f"工单创建（{source}）",
        actor_id=user_id,
        to_status=order.status,
    )
    if priority == "urgent":
        send_alert(
            title="work_order",
            message=f"小院 {yard_id} 出现紧急工单：{order.title}",
            level="warning",
            dedupe_key=f"workorder:{order.id}:urgent",
        )
    return order


def _alert_out(row: FarmAlert) -> dict:
    return {
        "alert_id": row.id,
        "title": row.title,
        "detail": row.detail,
        "severity": row.severity,
        "source": row.source,
        "plot_id": row.plot_id,
        "status": row.status,
        "work_order_id": row.work_order_id,
        "agent_run_id": getattr(row, "agent_run_id", None),
        "created_at": row.created_at,
    }


def _event_out(row: WorkOrderEvent) -> dict:
    return {
        "event_id": row.id,
        "kind": row.kind,
        "note": row.note,
        "actor_id": row.actor_id,
        "from_status": row.from_status,
        "to_status": row.to_status,
        "created_at": row.created_at,
    }


def _order_out(row: WorkOrder, events: Sequence[WorkOrderEvent] | None = None) -> dict:
    return {
        "work_order_id": row.id,
        "title": row.title,
        "description": row.description,
        "priority": row.priority,
        "status": row.status,
        "source": row.source,
        "source_ref": row.source_ref,
        "plot_id": row.plot_id,
        "assignee_id": row.assignee_id,
        "assignee_name": row.assignee_name,
        "due_on": row.due_on,
        "escalation_level": row.escalation_level,
        "resolution": row.resolution,
        "closed_at": row.closed_at,
        "agent_run_id": getattr(row, "agent_run_id", None),
        "revision": int(row.revision or 1),
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "events": [_event_out(e) for e in (events or [])],
    }


# ---------------------------------------------------------------------------
# 告警
# ---------------------------------------------------------------------------


def create_alert(db: Session, yard_id: str, user_id: str, req: AlertCreate) -> dict:
    if req.severity not in ALERT_SEVERITIES:
        raise AppError("INVALID_REQUEST", "未知告警级别。", http_status=400)
    _ensure_plot(db, yard_id, req.plot_id)
    alert = FarmAlert(
        yard_id=yard_id,
        title=req.title.strip(),
        detail=req.detail.strip(),
        severity=req.severity,
        source=req.source.strip() or "manual",
        plot_id=req.plot_id,
        status="new",
        created_by=user_id,
    )
    db.add(alert)
    db.flush()
    # critical 自动转单：高等级告警不允许停留在“仅提示”
    if req.severity == "critical":
        order = _new_order(
            db,
            yard_id=yard_id,
            user_id=user_id,
            title=alert.title,
            description=alert.detail,
            priority="urgent",
            source="alert",
            source_ref=alert.id,
            plot_id=alert.plot_id,
            assignee_id=None,
            due_on=None,
        )
        alert.status = "converted"
        alert.work_order_id = order.id
        _event(
            db,
            yard_id=yard_id,
            order=order,
            kind="converted",
            note=f"由告警「{alert.title}」自动转单",
            actor_id=user_id,
        )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_alert_created",
        resource_type="farm_alert",
        resource_id=alert.id,
        result="success",
        request_id="",
        metadata={"severity": req.severity, "work_order_id": alert.work_order_id},
    )
    db.commit()
    return _alert_out(alert)


def list_alerts(
    db: Session,
    yard_id: str,
    *,
    status: str | None = None,
    severity: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    q = select(FarmAlert).where(FarmAlert.yard_id == yard_id)
    if status:
        q = q.where(FarmAlert.status == status)
    if severity:
        if severity not in ALERT_SEVERITIES:
            raise AppError("INVALID_REQUEST", "未知告警级别。", http_status=400)
        q = q.where(FarmAlert.severity == severity)
    total = int(db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0)
    rows = db.scalars(
        q.order_by(FarmAlert.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_alert_out(r) for r in rows], total


def convert_alert(db: Session, yard_id: str, alert_id: str, user_id: str) -> dict:
    alert = db.scalar(
        select(FarmAlert).where(FarmAlert.yard_id == yard_id, FarmAlert.id == alert_id)
    )
    if alert is None:
        raise AppError("ALERT_NOT_FOUND", "告警不存在或无权访问。", http_status=404)
    if alert.work_order_id:
        raise AppError("ALERT_ALREADY_CONVERTED", "该告警已转工单。", http_status=409)
    order = _new_order(
        db,
        yard_id=yard_id,
        user_id=user_id,
        title=alert.title,
        description=alert.detail,
        priority=_SEVERITY_PRIORITY.get(alert.severity, "normal"),
        source="alert",
        source_ref=alert.id,
        plot_id=alert.plot_id,
        assignee_id=None,
        due_on=None,
    )
    alert.status = "converted"
    alert.work_order_id = order.id
    _event(
        db,
        yard_id=yard_id,
        order=order,
        kind="converted",
        note=f"由告警「{alert.title}」转单",
        actor_id=user_id,
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_alert_converted",
        resource_type="farm_alert",
        resource_id=alert.id,
        result="success",
        request_id="",
        metadata={"work_order_id": order.id},
    )
    db.commit()
    return _order_out(order)


# ---------------------------------------------------------------------------
# 工单
# ---------------------------------------------------------------------------


def create_work_order(db: Session, yard_id: str, user_id: str, req: WorkOrderCreate) -> dict:
    if req.client_op_id:
        existing = db.scalar(
            select(WorkOrder).where(
                WorkOrder.yard_id == yard_id, WorkOrder.client_op_id == req.client_op_id
            )
        )
        if existing is not None:  # 离线重放：同一操作只落一条
            return _order_out(existing)
    _ensure_plot(db, yard_id, req.plot_id)
    order = _new_order(
        db,
        yard_id=yard_id,
        user_id=user_id,
        title=req.title,
        description=req.description,
        priority=req.priority,
        source=req.source.strip() or "manual",
        source_ref=req.source_ref,
        plot_id=req.plot_id,
        assignee_id=req.assignee_id,
        due_on=req.due_on,
        client_op_id=req.client_op_id,
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="work_order_created",
        resource_type="work_order",
        resource_id=order.id,
        result="success",
        request_id="",
        metadata={"priority": order.priority, "source": order.source},
    )
    db.commit()
    return _order_out(order)


def list_work_orders(
    db: Session,
    yard_id: str,
    *,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    q = select(WorkOrder).where(WorkOrder.yard_id == yard_id)
    if status:
        if status not in WORK_ORDER_STATUSES:
            raise AppError("INVALID_REQUEST", "未知工单状态。", http_status=400)
        q = q.where(WorkOrder.status == status)
    if priority:
        if priority not in WORK_ORDER_PRIORITIES:
            raise AppError("INVALID_REQUEST", "未知工单优先级。", http_status=400)
        q = q.where(WorkOrder.priority == priority)
    if assignee_id:
        q = q.where(WorkOrder.assignee_id == assignee_id)
    total = int(db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0)
    rows = db.scalars(
        q.order_by(WorkOrder.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [_order_out(r) for r in rows], total


def _get_order(db: Session, yard_id: str, order_id: str) -> WorkOrder:
    row = db.scalar(select(WorkOrder).where(WorkOrder.yard_id == yard_id, WorkOrder.id == order_id))
    if row is None:
        raise AppError("WORK_ORDER_NOT_FOUND", "工单不存在或无权访问。", http_status=404)
    return row


def _ensure_revision(row: WorkOrder, expected: int | None) -> None:
    if expected is None:
        return
    actual = int(row.revision or 1)
    if actual != expected:
        raise AppError(
            "FARM_REVISION_CONFLICT",
            "该工单已被他人修改，请查看最新状态后再提交。",
            http_status=409,
            details={
                "expected_revision": expected,
                "current_revision": actual,
                "current": _order_out(row),
            },
        )


def get_work_order(db: Session, yard_id: str, order_id: str) -> dict:
    row = _get_order(db, yard_id, order_id)
    events = db.scalars(
        select(WorkOrderEvent)
        .where(
            WorkOrderEvent.yard_id == yard_id,
            WorkOrderEvent.work_order_id == row.id,
        )
        .order_by(WorkOrderEvent.created_at.asc())
    ).all()
    return _order_out(row, events)


def update_work_order(
    db: Session, yard_id: str, order_id: str, user_id: str, req: WorkOrderUpdate
) -> dict:
    row = _get_order(db, yard_id, order_id)
    changes = req.model_dump(exclude_unset=True)
    _ensure_revision(row, changes.pop("expected_revision", None))
    if "priority" in changes and changes["priority"] is not None:
        if changes["priority"] not in WORK_ORDER_PRIORITIES:
            raise AppError("INVALID_REQUEST", "未知工单优先级。", http_status=400)
    if "status" in changes and changes["status"] is not None:
        new_status = changes["status"]
        if new_status not in WORK_ORDER_STATUSES:
            raise AppError("INVALID_REQUEST", "未知工单状态。", http_status=400)
        if new_status != row.status:
            if new_status not in _ALLOWED_TRANSITIONS.get(row.status, set()):
                raise AppError(
                    "INVALID_STATUS_TRANSITION",
                    f"工单不能从 {row.status} 变更为 {new_status}。",
                    http_status=409,
                )
            _event(
                db,
                yard_id=yard_id,
                order=row,
                kind="status",
                note="状态变更",
                actor_id=user_id,
                from_status=row.status,
                to_status=new_status,
            )
            row.status = new_status
            if new_status in TERMINAL_WORK_ORDER_STATUSES and row.closed_at is None:
                row.closed_at = _utcnow()
    if "assignee_id" in changes:
        assignee_id = changes["assignee_id"]
        if assignee_id:
            name = _member_name(db, yard_id, assignee_id)
            row.assignee_id = assignee_id
            row.assignee_name = name
            if row.status == "open":
                row.status = "assigned"
            _event(
                db,
                yard_id=yard_id,
                order=row,
                kind="assigned",
                note=f"指派给 {name}",
                actor_id=user_id,
            )
        else:
            row.assignee_id = None
            row.assignee_name = ""
    if "due_on" in changes:
        row.due_on = changes["due_on"]
    if "description" in changes and changes["description"] is not None:
        row.description = changes["description"].strip()
    if "priority" in changes and changes["priority"] is not None:
        row.priority = changes["priority"]
    row.revision = int(row.revision or 1) + 1
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="work_order_updated",
        resource_type="work_order",
        resource_id=row.id,
        result="success",
        request_id="",
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    return get_work_order(db, yard_id, row.id)


def add_comment(db: Session, yard_id: str, order_id: str, user_id: str, note: str) -> dict:
    row = _get_order(db, yard_id, order_id)
    _event(
        db,
        yard_id=yard_id,
        order=row,
        kind="comment",
        note=note.strip(),
        actor_id=user_id,
    )
    row.revision = int(row.revision or 1) + 1
    db.commit()
    return get_work_order(db, yard_id, row.id)


def submit_receipt(
    db: Session, yard_id: str, order_id: str, user_id: str, req: WorkOrderReceipt
) -> dict:
    row = _get_order(db, yard_id, order_id)
    if req.client_op_id:
        replay = db.scalar(
            select(WorkOrderEvent).where(
                WorkOrderEvent.yard_id == yard_id,
                WorkOrderEvent.client_op_id == req.client_op_id,
            )
        )
        if replay is not None:  # 离线重放：回执只生效一次
            return get_work_order(db, yard_id, row.id)
    if row.status in ("closed", "cancelled"):
        raise AppError("WORK_ORDER_CLOSED", "工单已关闭或取消，不能再回执。", http_status=409)
    from_status = row.status
    row.resolution = req.resolution.strip()
    row.status = "closed" if req.close else "done"
    row.closed_at = _utcnow()
    _event(
        db,
        yard_id=yard_id,
        order=row,
        kind="receipt",
        note=row.resolution,
        actor_id=user_id,
        from_status=from_status,
        to_status=row.status,
        client_op_id=req.client_op_id,
    )
    row.revision = int(row.revision or 1) + 1
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="work_order_receipt",
        resource_type="work_order",
        resource_id=row.id,
        result="success",
        request_id="",
        metadata={"status": row.status},
    )
    db.commit()
    return get_work_order(db, yard_id, row.id)


def escalate(db: Session, yard_id: str, order_id: str, user_id: str | None) -> dict:
    row = _get_order(db, yard_id, order_id)
    if row.status in TERMINAL_WORK_ORDER_STATUSES:
        raise AppError("WORK_ORDER_CLOSED", "工单已结束，无需升级。", http_status=409)
    idx = _PRIORITY_ORDER.index(row.priority) if row.priority in _PRIORITY_ORDER else 1
    row.priority = _PRIORITY_ORDER[min(idx + 1, len(_PRIORITY_ORDER) - 1)]
    row.escalation_level += 1
    row.revision = int(row.revision or 1) + 1
    _event(
        db,
        yard_id=yard_id,
        order=row,
        kind="escalated",
        note=f"升级为 {row.priority}（第 {row.escalation_level} 次）",
        actor_id=user_id,
    )
    if row.priority == "urgent":
        send_alert(
            title="work_order",
            message=f"小院 {yard_id} 的工单已升级为紧急：{row.title}",
            level="warning",
            dedupe_key=f"workorder:{row.id}:urgent",
        )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="work_order_escalated",
        resource_type="work_order",
        resource_id=row.id,
        result="success",
        request_id="",
        metadata={"priority": row.priority, "level": row.escalation_level},
    )
    db.commit()
    return get_work_order(db, yard_id, row.id)


def escalate_overdue(db: Session, *, today: date | None = None) -> dict[str, int]:
    """逾期未结束的工单自动升级一级；返回 {"scanned": n, "escalated": m}。"""
    day = today or datetime.now(UTC).date()
    rows = db.scalars(
        select(WorkOrder).where(
            WorkOrder.due_on.is_not(None),
            WorkOrder.due_on < day,
            WorkOrder.status.not_in(TERMINAL_WORK_ORDER_STATUSES),
        )
    ).all()
    escalated = 0
    for row in rows:
        idx = _PRIORITY_ORDER.index(row.priority) if row.priority in _PRIORITY_ORDER else 1
        new_priority = _PRIORITY_ORDER[min(idx + 1, len(_PRIORITY_ORDER) - 1)]
        # 已紧急的工单不再重复升级，避免每天刷事件
        if new_priority == row.priority and row.escalation_level > 0:
            continue
        row.priority = new_priority
        row.escalation_level += 1
        row.revision = int(row.revision or 1) + 1
        _event(
            db,
            yard_id=row.yard_id,
            order=row,
            kind="escalated",
            note=f"逾期未完成，自动升级为 {new_priority}",
            actor_id=None,
        )
        escalated += 1
    if escalated:
        db.commit()
    return {"scanned": len(rows), "escalated": escalated}


def list_assignable_members(db: Session, yard_id: str) -> list[dict]:
    """可指派成员：本院在职成员（id/显示名/角色）。"""
    rows = db.execute(
        select(User.id, User.display_name, User.username, YardMembership.role)
        .join(YardMembership, YardMembership.user_id == User.id)
        .where(YardMembership.yard_id == yard_id, YardMembership.status == "active")
        .order_by(User.display_name)
    ).all()
    return [{"user_id": r[0], "display_name": r[1] or r[2], "role": r[3]} for r in rows]
