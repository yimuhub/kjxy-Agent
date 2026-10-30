"""农业台账业务逻辑：地块、周期、农事记录与报表生成。

所有入口都要求 ``yard_id``，跨院 id 一律按“不存在”处理（404），避免枚举。
报表是确定性文本生成，不调用模型：同一批记录每次生成结果一致，便于核对。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.farm.models import (
    CYCLE_STATUSES,
    PLOT_STATUSES,
    RECORD_KIND_LABELS,
    RECORD_KINDS,
    FarmCycle,
    FarmPlot,
    FarmRecord,
)
from app.modules.farm.schemas import (
    CycleCreate,
    CycleUpdate,
    PlotCreate,
    PlotUpdate,
    RecordCreate,
    RecordUpdate,
    ReportRequest,
)


def _plot_out(plot: FarmPlot) -> dict:
    return {
        "plot_id": plot.id,
        "name": plot.name,
        "crop": plot.crop,
        "area_mu": plot.area_mu,
        "location": plot.location,
        "soil_type": plot.soil_type,
        "irrigation_type": plot.irrigation_type,
        "status": plot.status,
        "notes": plot.notes,
        "revision": int(plot.revision or 1),
        "created_at": plot.created_at,
        "updated_at": plot.updated_at,
    }


def _cycle_out(cycle: FarmCycle) -> dict:
    return {
        "cycle_id": cycle.id,
        "plot_id": cycle.plot_id,
        "crop": cycle.crop,
        "variety": cycle.variety,
        "planted_on": cycle.planted_on,
        "expected_harvest_on": cycle.expected_harvest_on,
        "status": cycle.status,
        "notes": cycle.notes,
        "revision": int(cycle.revision or 1),
        "created_at": cycle.created_at,
        "updated_at": cycle.updated_at,
    }


def _record_out(record: FarmRecord, plot_name: str) -> dict:
    return {
        "record_id": record.id,
        "plot_id": record.plot_id,
        "plot_name": plot_name,
        "cycle_id": record.cycle_id,
        "kind": record.kind,
        "kind_label": RECORD_KIND_LABELS.get(record.kind, record.kind),
        "occurred_on": record.occurred_on,
        "title": record.title,
        "content": record.content,
        "quantity": record.quantity,
        "unit": record.unit,
        "operator_name": record.operator_name,
        "metadata_json": record.metadata_json,
        "source": getattr(record, "source", "manual"),
        "agent_run_id": getattr(record, "agent_run_id", None),
        "revision": int(record.revision or 1),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _bump(row) -> None:
    """成功写入后版本 +1：离线客户端用旧版本保存时能可靠检出冲突。"""
    row.revision = int(row.revision or 1) + 1


def _ensure_revision(row, expected: int | None, current: dict, label: str) -> None:
    if expected is None:
        return
    actual = int(row.revision or 1)
    if actual != expected:
        raise AppError(
            "FARM_REVISION_CONFLICT",
            f"这条{label}已被他人修改，请查看最新版本后再保存。",
            http_status=409,
            details={
                "expected_revision": expected,
                "current_revision": actual,
                "current": current,
            },
        )


# ---------------------------------------------------------------------------
# 地块
# ---------------------------------------------------------------------------


def list_plots(db: Session, yard_id: str, *, include_archived: bool = False) -> list[dict]:
    q = select(FarmPlot).where(FarmPlot.yard_id == yard_id)
    if not include_archived:
        q = q.where(FarmPlot.status != "archived")
    rows = db.scalars(q.order_by(FarmPlot.created_at.desc())).all()
    return [_plot_out(p) for p in rows]


def _get_plot(db: Session, yard_id: str, plot_id: str) -> FarmPlot:
    plot = db.scalar(select(FarmPlot).where(FarmPlot.yard_id == yard_id, FarmPlot.id == plot_id))
    if plot is None:
        raise AppError("FARM_PLOT_NOT_FOUND", "地块不存在或无权访问。", http_status=404)
    return plot


def create_plot(db: Session, yard_id: str, user_id: str, req: PlotCreate) -> dict:
    if req.client_op_id:
        existing = db.scalar(
            select(FarmPlot).where(
                FarmPlot.yard_id == yard_id, FarmPlot.client_op_id == req.client_op_id
            )
        )
        if existing is not None:  # 离线重放：同一操作只落一条
            return _plot_out(existing)
    _validate_plot_status(req.status)
    name = req.name.strip()
    if not name:
        raise AppError("INVALID_REQUEST", "地块名称不能为空。", http_status=400)
    exists = db.scalar(
        select(FarmPlot.id).where(FarmPlot.yard_id == yard_id, FarmPlot.name == name)
    )
    if exists:
        raise AppError("DUPLICATE_NAME", "该地块名称已存在。", http_status=409)
    plot = FarmPlot(
        yard_id=yard_id,
        name=name,
        crop=req.crop.strip(),
        area_mu=req.area_mu,
        location=req.location.strip(),
        soil_type=req.soil_type.strip(),
        irrigation_type=req.irrigation_type.strip(),
        status=req.status,
        notes=req.notes.strip(),
        created_by=user_id,
        client_op_id=req.client_op_id,
    )
    db.add(plot)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise AppError("DUPLICATE_NAME", "该地块名称已存在。", http_status=409) from e
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_plot_created",
        resource_type="farm_plot",
        resource_id=plot.id,
        result="success",
        request_id="",
    )
    db.commit()
    return _plot_out(plot)


def update_plot(db: Session, yard_id: str, plot_id: str, user_id: str, req: PlotUpdate) -> dict:
    plot = _get_plot(db, yard_id, plot_id)
    changes = req.model_dump(exclude_unset=True)
    expected = changes.pop("expected_revision", None)
    _ensure_revision(plot, expected, _plot_out(plot), "地块")
    if "status" in changes and changes["status"] is not None:
        _validate_plot_status(changes["status"])
    if "name" in changes and changes["name"] is not None:
        name = changes["name"].strip()
        if not name:
            raise AppError("INVALID_REQUEST", "地块名称不能为空。", http_status=400)
        clash = db.scalar(
            select(FarmPlot.id).where(
                FarmPlot.yard_id == yard_id, FarmPlot.name == name, FarmPlot.id != plot.id
            )
        )
        if clash:
            raise AppError("DUPLICATE_NAME", "该地块名称已存在。", http_status=409)
        changes["name"] = name
    for field in ("crop", "location", "soil_type", "irrigation_type", "notes"):
        if field in changes and isinstance(changes[field], str):
            changes[field] = changes[field].strip()
    for field, value in changes.items():
        if value is not None:
            setattr(plot, field, value)
    _bump(plot)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_plot_updated",
        resource_type="farm_plot",
        resource_id=plot.id,
        result="success",
        request_id="",
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    return _plot_out(plot)


def delete_plot(
    db: Session, yard_id: str, plot_id: str, user_id: str, expected_revision: int | None = None
) -> None:
    plot = _get_plot(db, yard_id, plot_id)
    _ensure_revision(plot, expected_revision, _plot_out(plot), "地块")
    records = db.scalar(
        select(func.count()).select_from(FarmRecord).where(FarmRecord.plot_id == plot.id)
    )
    if records:
        raise AppError(
            "FARM_PLOT_HAS_RECORDS",
            "该地块已有农事记录，不能删除；如需停用请将状态改为归档。",
            http_status=409,
        )
    db.query(FarmCycle).filter(FarmCycle.yard_id == yard_id, FarmCycle.plot_id == plot.id).delete(
        synchronize_session=False
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_plot_deleted",
        resource_type="farm_plot",
        resource_id=plot.id,
        result="success",
        request_id="",
    )
    db.delete(plot)
    db.commit()


def _validate_plot_status(status: str) -> None:
    if status not in PLOT_STATUSES:
        raise AppError(
            "INVALID_REQUEST",
            f"未知地块状态：{status}，可选 {'/'.join(PLOT_STATUSES)}。",
            http_status=400,
        )


# ---------------------------------------------------------------------------
# 种植周期
# ---------------------------------------------------------------------------


def create_cycle(db: Session, yard_id: str, plot_id: str, user_id: str, req: CycleCreate) -> dict:
    if req.client_op_id:
        existing = db.scalar(
            select(FarmCycle).where(
                FarmCycle.yard_id == yard_id, FarmCycle.client_op_id == req.client_op_id
            )
        )
        if existing is not None:
            return _cycle_out(existing)
    plot = _get_plot(db, yard_id, plot_id)
    if req.status not in CYCLE_STATUSES:
        raise AppError("INVALID_REQUEST", "未知周期状态。", http_status=400)
    if req.planted_on and req.expected_harvest_on and req.expected_harvest_on < req.planted_on:
        raise AppError("INVALID_REQUEST", "预计收获日期不能早于播种日期。", http_status=400)
    cycle = FarmCycle(
        yard_id=yard_id,
        plot_id=plot.id,
        crop=req.crop.strip(),
        variety=req.variety.strip(),
        planted_on=req.planted_on,
        expected_harvest_on=req.expected_harvest_on,
        status=req.status,
        notes=req.notes.strip(),
        created_by=user_id,
        client_op_id=req.client_op_id,
    )
    db.add(cycle)
    db.flush()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_cycle_created",
        resource_type="farm_cycle",
        resource_id=cycle.id,
        result="success",
        request_id="",
    )
    db.commit()
    return _cycle_out(cycle)


def list_cycles(db: Session, yard_id: str, plot_id: str) -> list[dict]:
    _get_plot(db, yard_id, plot_id)
    rows = db.scalars(
        select(FarmCycle)
        .where(FarmCycle.yard_id == yard_id, FarmCycle.plot_id == plot_id)
        .order_by(FarmCycle.created_at.desc())
    ).all()
    return [_cycle_out(c) for c in rows]


def _get_cycle(db: Session, yard_id: str, cycle_id: str) -> FarmCycle:
    cycle = db.scalar(
        select(FarmCycle).where(FarmCycle.yard_id == yard_id, FarmCycle.id == cycle_id)
    )
    if cycle is None:
        raise AppError("FARM_CYCLE_NOT_FOUND", "种植周期不存在或无权访问。", http_status=404)
    return cycle


def update_cycle(db: Session, yard_id: str, cycle_id: str, user_id: str, req: CycleUpdate) -> dict:
    cycle = _get_cycle(db, yard_id, cycle_id)
    changes = req.model_dump(exclude_unset=True)
    expected = changes.pop("expected_revision", None)
    _ensure_revision(cycle, expected, _cycle_out(cycle), "种植周期")
    status = changes.get("status")
    if status is not None and status not in CYCLE_STATUSES:
        raise AppError("INVALID_REQUEST", "未知周期状态。", http_status=400)
    planted_on = changes.get("planted_on", cycle.planted_on)
    harvest_on = changes.get("expected_harvest_on", cycle.expected_harvest_on)
    if planted_on and harvest_on and harvest_on < planted_on:
        raise AppError("INVALID_REQUEST", "预计收获日期不能早于播种日期。", http_status=400)
    for field in ("crop", "variety", "notes"):
        if field in changes and isinstance(changes[field], str):
            changes[field] = changes[field].strip()
    for field, value in changes.items():
        if value is not None:
            setattr(cycle, field, value)
    _bump(cycle)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_cycle_updated",
        resource_type="farm_cycle",
        resource_id=cycle.id,
        result="success",
        request_id="",
    )
    db.commit()
    return _cycle_out(cycle)


def delete_cycle(
    db: Session, yard_id: str, cycle_id: str, user_id: str, expected_revision: int | None = None
) -> None:
    cycle = _get_cycle(db, yard_id, cycle_id)
    _ensure_revision(cycle, expected_revision, _cycle_out(cycle), "种植周期")
    linked = db.scalar(
        select(func.count()).select_from(FarmRecord).where(FarmRecord.cycle_id == cycle.id)
    )
    if linked:
        raise AppError(
            "FARM_CYCLE_HAS_RECORDS",
            "该周期下已有农事记录，不能删除；可改状态为失败或已收获。",
            http_status=409,
        )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_cycle_deleted",
        resource_type="farm_cycle",
        resource_id=cycle.id,
        result="success",
        request_id="",
    )
    db.delete(cycle)
    db.commit()


# ---------------------------------------------------------------------------
# 农事记录
# ---------------------------------------------------------------------------


def _get_record(db: Session, yard_id: str, record_id: str) -> FarmRecord:
    record = db.scalar(
        select(FarmRecord).where(FarmRecord.yard_id == yard_id, FarmRecord.id == record_id)
    )
    if record is None:
        raise AppError("FARM_RECORD_NOT_FOUND", "农事记录不存在或无权访问。", http_status=404)
    return record


def _validate_kind(kind: str) -> None:
    if kind not in RECORD_KINDS:
        raise AppError(
            "INVALID_REQUEST",
            f"未知记录类型：{kind}，可选 {'/'.join(RECORD_KINDS)}。",
            http_status=400,
        )


def _validate_cycle(db: Session, yard_id: str, plot_id: str, cycle_id: str | None) -> None:
    if not cycle_id:
        return
    cycle = db.scalar(
        select(FarmCycle).where(
            FarmCycle.yard_id == yard_id,
            FarmCycle.id == cycle_id,
            FarmCycle.plot_id == plot_id,
        )
    )
    if cycle is None:
        raise AppError("INVALID_REQUEST", "种植周期不属于该地块。", http_status=400)


def create_record(db: Session, yard_id: str, user_id: str, req: RecordCreate) -> dict:
    if req.client_op_id:
        existing = db.scalar(
            select(FarmRecord).where(
                FarmRecord.yard_id == yard_id, FarmRecord.client_op_id == req.client_op_id
            )
        )
        if existing is not None:
            plot = db.scalar(select(FarmPlot).where(FarmPlot.id == existing.plot_id))
            return _record_out(existing, plot.name if plot else "")
    _validate_kind(req.kind)
    plot = _get_plot(db, yard_id, req.plot_id)
    _validate_cycle(db, yard_id, plot.id, req.cycle_id)
    record = FarmRecord(
        yard_id=yard_id,
        plot_id=plot.id,
        cycle_id=req.cycle_id,
        kind=req.kind,
        occurred_on=req.occurred_on,
        title=req.title.strip(),
        content=req.content.strip(),
        quantity=req.quantity,
        unit=req.unit.strip(),
        operator_name=req.operator_name.strip(),
        metadata_json=req.metadata_json,
        created_by=user_id,
        client_op_id=req.client_op_id,
    )
    db.add(record)
    db.flush()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_record_created",
        resource_type="farm_record",
        resource_id=record.id,
        result="success",
        request_id="",
        metadata={"plot_id": plot.id, "kind": req.kind},
    )
    db.commit()
    return _record_out(record, plot.name)


def list_records(
    db: Session,
    yard_id: str,
    *,
    plot_id: str | None = None,
    kind: str | None = None,
    start: date | None = None,
    end: date | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    q = (
        select(FarmRecord, FarmPlot.name)
        .join(FarmPlot, FarmPlot.id == FarmRecord.plot_id, isouter=True)
        .where(FarmRecord.yard_id == yard_id)
    )
    if plot_id:
        q = q.where(FarmRecord.plot_id == plot_id)
    if kind:
        _validate_kind(kind)
        q = q.where(FarmRecord.kind == kind)
    if start:
        q = q.where(FarmRecord.occurred_on >= start)
    if end:
        q = q.where(FarmRecord.occurred_on <= end)
    total = int(db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0)
    rows = db.execute(
        q.order_by(FarmRecord.occurred_on.desc(), FarmRecord.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [_record_out(record, name or "") for record, name in rows], total


def update_record(
    db: Session, yard_id: str, record_id: str, user_id: str, req: RecordUpdate
) -> dict:
    record = _get_record(db, yard_id, record_id)
    changes = req.model_dump(exclude_unset=True)
    expected = changes.pop("expected_revision", None)
    current_plot = db.scalar(select(FarmPlot).where(FarmPlot.id == record.plot_id))
    _ensure_revision(
        record, expected, _record_out(record, current_plot.name if current_plot else ""), "农事记录"
    )
    if "kind" in changes and changes["kind"] is not None:
        _validate_kind(changes["kind"])
    if "cycle_id" in changes:
        _validate_cycle(db, yard_id, record.plot_id, changes["cycle_id"])
    for field in ("title", "content", "unit", "operator_name"):
        if field in changes and isinstance(changes[field], str):
            changes[field] = changes[field].strip()
    for field, value in changes.items():
        if value is not None or field == "cycle_id":
            setattr(record, field, value)
    _bump(record)
    plot = db.scalar(select(FarmPlot).where(FarmPlot.id == record.plot_id))
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_record_updated",
        resource_type="farm_record",
        resource_id=record.id,
        result="success",
        request_id="",
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    return _record_out(record, plot.name if plot else "")


def delete_record(
    db: Session, yard_id: str, record_id: str, user_id: str, expected_revision: int | None = None
) -> None:
    record = _get_record(db, yard_id, record_id)
    plot = db.scalar(select(FarmPlot).where(FarmPlot.id == record.plot_id))
    _ensure_revision(
        record, expected_revision, _record_out(record, plot.name if plot else ""), "农事记录"
    )
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="farm_record_deleted",
        resource_type="farm_record",
        resource_id=record.id,
        result="success",
        request_id="",
        metadata={"plot_id": record.plot_id, "kind": record.kind},
    )
    db.delete(record)
    db.commit()


# ---------------------------------------------------------------------------
# 报表
# ---------------------------------------------------------------------------


def _fmt_quantity(record: FarmRecord) -> str:
    if record.quantity is None:
        return ""
    qty = f"{record.quantity:g}"
    return f"{qty} {record.unit}".strip()


def _one_line(record: FarmRecord, plot_name: str, *, include_operator: bool = True) -> str:
    parts = [f"**{plot_name or '未命名地块'}**", RECORD_KIND_LABELS.get(record.kind, record.kind)]
    if record.title:
        parts.append(record.title)
    qty = _fmt_quantity(record)
    if qty:
        parts.append(qty)
    if record.operator_name and include_operator:
        parts.append(f"操作人：{record.operator_name}")
    line = " · ".join(parts)
    if record.content:
        # 压平换行，避免记录正文破坏报表结构
        content = " ".join(record.content.split())
        line += f"\n  - {content}"
    return line


def _group_key(record: FarmRecord, kind: str) -> str:
    if kind == "weekly":
        iso = record.occurred_on.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if kind == "quarterly":
        quarter = (record.occurred_on.month - 1) // 3 + 1
        return f"{record.occurred_on.year} 年第 {quarter} 季度"
    return record.occurred_on.isoformat()


def build_report(
    db: Session, yard_id: str, req: ReportRequest, *, redact_operators: bool = False
) -> dict:
    rows = db.execute(
        select(FarmRecord, FarmPlot.name)
        .join(FarmPlot, FarmPlot.id == FarmRecord.plot_id, isouter=True)
        .where(
            FarmRecord.yard_id == yard_id,
            FarmRecord.occurred_on >= req.start,
            FarmRecord.occurred_on <= req.end,
        )
        .order_by(FarmRecord.occurred_on.asc(), FarmRecord.created_at.asc())
    ).all()
    if req.plot_id:
        rows = [row for row in rows if row[0].plot_id == req.plot_id]
    names = {record.id: name or "" for record, name in rows}

    if req.kind not in ("log", "weekly", "quarterly"):
        raise AppError("INVALID_REQUEST", "报表类型只能是 log/weekly/quarterly。", http_status=400)
    kind = req.kind
    labels = {"log": "农事日志", "weekly": "农事周报", "quarterly": "农事季报"}
    title = f"{labels[kind]} · {req.start.isoformat()} 至 {req.end.isoformat()}"

    by_kind: dict[str, int] = defaultdict(int)
    by_plot: dict[str, int] = defaultdict(int)
    for record, _ in rows:
        by_kind[record.kind] += 1
        by_plot[record.plot_id] += 1

    lines = [f"# {title}", ""]
    lines.append(
        f"- 记录总数：{len(rows)}"
        f"；涉及地块：{len(by_plot)}"
        f"；区间：{req.start.isoformat()} ~ {req.end.isoformat()}"
    )
    if rows:
        kind_text = "、".join(
            f"{RECORD_KIND_LABELS.get(k, k)} {n}" for k, n in sorted(by_kind.items())
        )
        lines.append(f"- 类型分布：{kind_text}")

    if not rows:
        lines += ["", "> 所选区间没有农事记录。可在「台账」中补录后再生成。"]
    else:
        grouped: dict[str, list[FarmRecord]] = defaultdict(list)
        for record, _ in rows:
            grouped[_group_key(record, kind)].append(record)
        for key in sorted(grouped):
            items = grouped[key]
            total = len(items)
            lines += ["", f"## {key}（{total} 条）", ""]
            for record in items:
                line = _one_line(
                    record, names.get(record.id, ""), include_operator=not redact_operators
                )
                if kind == "log":
                    lines.append(f"- {record.occurred_on.isoformat()}：{line}")
                else:
                    lines.append(f"- {line}")

    stats = {
        "by_kind": dict(sorted(by_kind.items())),
        "by_plot": dict(sorted(by_plot.items())),
    }
    return {
        "title": title,
        "kind": kind,
        "start": req.start,
        "end": req.end,
        "records": len(rows),
        "plots": len(by_plot),
        "content_md": "\n".join(lines) + "\n",
        "stats": stats,
    }
