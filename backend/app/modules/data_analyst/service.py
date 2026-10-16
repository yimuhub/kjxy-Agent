"""报表生成服务：只读授权数据源（tasks / knowledge_documents / llm_calls）。

无状态、纯查询：除读取三张表的租户内数据外不产生任何副作用，
不建新表。所有查询强制带 tenant_id 过滤。
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.data_analyst.schemas import (
    ALLOWED_SOURCES,
    DEFAULT_LOOKBACK_DAYS,
    REPORT_VERSION,
    Metric,
    ReportInput,
    ReportOutput,
    ReportTable,
)
from app.modules.knowledge.models import KnowledgeDocument
from app.modules.llm.models import LLMCall
from app.modules.tasks.models import Task

#: 任务"成功"终态（对应任务状态机 STATUS_SUCCEEDED）。
TASK_SUCCESS_STATUS = "succeeded"
#: 文档"就绪"状态。
DOC_READY_STATUS = "ready"
#: LLM 调用成功状态。
LLM_SUCCESS_STATUS = "success"

_TABLE_BY_SOURCE = {
    "tasks": Task,
    "knowledge": KnowledgeDocument,
    "llm_calls": LLMCall,
}


def _parse_date(value: str | None, field: str, *, default: date) -> date:
    if value is None:
        return default
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise AppError(
            "INVALID_REQUEST",
            f"time_range.{field} 非法：{value!r}，需为 ISO 日期（YYYY-MM-DD）。",
            http_status=400,
        ) from None


def _resolve_time_range(input: ReportInput) -> tuple[date, date]:
    """解析时间范围：默认近 30 天（含今天）；非法抛 AppError 400。"""
    today = datetime.now(UTC).date()
    start = _parse_date(
        input.time_range.start, "start", default=today - timedelta(days=DEFAULT_LOOKBACK_DAYS - 1)
    )
    end = _parse_date(input.time_range.end, "end", default=today)
    if start > end:
        raise AppError(
            "INVALID_REQUEST",
            f"time_range 非法：start({start.isoformat()}) 晚于 end({end.isoformat()})。",
            http_status=400,
        )
    return start, end


def _validate_input(input: ReportInput) -> None:
    unknown = [s for s in input.sources if s not in ALLOWED_SOURCES]
    if unknown:
        raise AppError(
            "INVALID_REQUEST",
            f"未知数据源：{unknown}，可选值：{', '.join(ALLOWED_SOURCES)}。",
            http_status=400,
        )
    if input.group_by != "day":
        raise AppError(
            "INVALID_REQUEST",
            f"不支持的 group_by：{input.group_by!r}，目前仅支持 'day'。",
            http_status=400,
        )


def _trace(source_table: str, filters: dict[str, Any], start: date, end: date) -> dict[str, Any]:
    return {
        "source": source_table,
        "filters": filters,
        "time_range": {"start": start.isoformat(), "end": end.isoformat()},
    }


def _in_range(model: Any, start: date, end: date) -> Any:
    """created_at 落在 [start 00:00, end 23:59:59.999999] 的过滤条件。"""
    start_dt = datetime.combine(start, time.min)
    end_dt = datetime.combine(end, time.max)
    return (model.created_at >= start_dt) & (model.created_at <= end_dt)


def _iter_days(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _daily_counts(
    db: Session,
    model: Any,
    tenant_id: str,
    start: date,
    end: date,
    extra_filters: tuple = (),
    status_column: Any = None,
) -> dict[str, dict[str, int]]:
    """按（日期[, 状态]）分组计数，返回 {date_iso: {status: count}}。"""
    day_col = func.date(model.created_at).label("day")
    if status_column is not None:
        stmt = (
            select(day_col, status_column, func.count())
            .where(model.tenant_id == tenant_id, _in_range(model, start, end), *extra_filters)
            .group_by(day_col, status_column)
        )
        rows = db.execute(stmt).all()
        result: dict[str, dict[str, int]] = {}
        for day, status, count in rows:
            result.setdefault(str(day), {})[str(status)] = int(count)
        return result
    stmt = (
        select(day_col, func.count())
        .where(model.tenant_id == tenant_id, _in_range(model, start, end), *extra_filters)
        .group_by(day_col)
    )
    return {str(day): {"total": int(count)} for day, count in db.execute(stmt).all()}


def _build_tasks_metrics(
    db: Session, tenant_id: str, start: date, end: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """任务指标：总数 / 各状态计数 / 成功率 + 按日统计表。"""
    base_filters: dict[str, Any] = {"tenant_id": tenant_id}
    total = (
        db.scalar(
            select(func.count())
            .select_from(Task)
            .where(Task.tenant_id == tenant_id, _in_range(Task, start, end))
        )
        or 0
    )
    status_rows = db.execute(
        select(Task.status, func.count())
        .where(Task.tenant_id == tenant_id, _in_range(Task, start, end))
        .group_by(Task.status)
    ).all()
    status_counts = {str(s): int(c) for s, c in status_rows}
    succeeded = status_counts.get(TASK_SUCCESS_STATUS, 0)
    success_rate = round(succeeded / total * 100, 2) if total else 0.0
    metrics = [
        {
            "name": "tasks_total",
            "value": total,
            "unit": "个",
            "trace": _trace("tasks", base_filters, start, end),
        },
        {
            "name": "tasks_success_rate",
            "value": success_rate,
            "unit": "%",
            "trace": _trace(
                "tasks", {**base_filters, "success_status": TASK_SUCCESS_STATUS}, start, end
            ),
        },
    ]
    for status, count in sorted(status_counts.items()):
        metrics.append(
            {
                "name": f"tasks_status_{status}",
                "value": count,
                "unit": "个",
                "trace": _trace("tasks", {**base_filters, "status": status}, start, end),
            }
        )
    daily = _daily_counts(db, Task, tenant_id, start, end, status_column=Task.status)
    rows = [
        [
            day.isoformat(),
            sum(daily.get(day.isoformat(), {}).values()),
            daily.get(day.isoformat(), {}).get(TASK_SUCCESS_STATUS, 0),
        ]
        for day in _iter_days(start, end)
    ]
    tables = [{"title": "任务-按日统计", "columns": ["日期", "任务总数", "成功数"], "rows": rows}]
    return metrics, tables


def _build_knowledge_metrics(
    db: Session, tenant_id: str, start: date, end: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """文档指标：总数 / 各状态计数（排除软删除）。"""
    not_deleted = KnowledgeDocument.deleted_at.is_(None)
    base_filters: dict[str, Any] = {"tenant_id": tenant_id, "deleted_at": "is_null"}
    total = (
        db.scalar(
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(
                KnowledgeDocument.tenant_id == tenant_id,
                not_deleted,
                _in_range(KnowledgeDocument, start, end),
            )
        )
        or 0
    )
    status_rows = db.execute(
        select(KnowledgeDocument.status, func.count())
        .where(
            KnowledgeDocument.tenant_id == tenant_id,
            not_deleted,
            _in_range(KnowledgeDocument, start, end),
        )
        .group_by(KnowledgeDocument.status)
    ).all()
    status_counts = {str(s): int(c) for s, c in status_rows}
    metrics = [
        {
            "name": "knowledge_documents_total",
            "value": total,
            "unit": "个",
            "trace": _trace("knowledge_documents", base_filters, start, end),
        }
    ]
    for status, count in sorted(status_counts.items()):
        metrics.append(
            {
                "name": f"knowledge_status_{status}",
                "value": count,
                "unit": "个",
                "trace": _trace(
                    "knowledge_documents", {**base_filters, "status": status}, start, end
                ),
            }
        )
    daily = _daily_counts(
        db,
        KnowledgeDocument,
        tenant_id,
        start,
        end,
        extra_filters=(not_deleted,),
        status_column=KnowledgeDocument.status,
    )
    rows = [
        [
            day.isoformat(),
            sum(daily.get(day.isoformat(), {}).values()),
            daily.get(day.isoformat(), {}).get(DOC_READY_STATUS, 0),
        ]
        for day in _iter_days(start, end)
    ]
    tables = [{"title": "文档-按日统计", "columns": ["日期", "文档总数", "就绪数"], "rows": rows}]
    return metrics, tables


def _build_llm_metrics(
    db: Session, tenant_id: str, start: date, end: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """LLM 调用指标：调用次数 / 成功率 / 总 token。"""
    base_filters: dict[str, Any] = {"tenant_id": tenant_id}
    cond = [LLMCall.tenant_id == tenant_id, _in_range(LLMCall, start, end)]
    total = db.scalar(select(func.count()).select_from(LLMCall).where(*cond)) or 0
    ok = (
        db.scalar(
            select(func.count())
            .select_from(LLMCall)
            .where(*cond, LLMCall.status == LLM_SUCCESS_STATUS)
        )
        or 0
    )
    total_tokens = (
        db.scalar(
            select(
                func.coalesce(func.sum(LLMCall.input_tokens), 0)
                + func.coalesce(func.sum(LLMCall.output_tokens), 0)
            )
            .select_from(LLMCall)
            .where(*cond)
        )
        or 0
    )
    success_rate = round(ok / total * 100, 2) if total else 0.0
    metrics = [
        {
            "name": "llm_calls_total",
            "value": total,
            "unit": "次",
            "trace": _trace("llm_calls", base_filters, start, end),
        },
        {
            "name": "llm_calls_success_rate",
            "value": success_rate,
            "unit": "%",
            "trace": _trace(
                "llm_calls", {**base_filters, "success_status": LLM_SUCCESS_STATUS}, start, end
            ),
        },
        {
            "name": "llm_total_tokens",
            "value": int(total_tokens),
            "unit": "tokens",
            "trace": _trace(
                "llm_calls",
                {**base_filters, "aggregate": "sum(input_tokens)+sum(output_tokens)"},
                start,
                end,
            ),
        },
    ]
    day_col = func.date(LLMCall.created_at).label("day")
    day_rows = db.execute(
        select(
            day_col,
            func.count(),
            func.coalesce(func.sum(LLMCall.input_tokens), 0)
            + func.coalesce(func.sum(LLMCall.output_tokens), 0),
        )
        .where(*cond)
        .group_by(day_col)
    ).all()
    daily = {str(day): (int(c), int(t)) for day, c, t in day_rows}
    rows = [
        [day.isoformat(), *daily.get(day.isoformat(), (0, 0))] for day in _iter_days(start, end)
    ]
    tables = [
        {"title": "LLM调用-按日统计", "columns": ["日期", "调用次数", "总Token"], "rows": rows}
    ]
    return metrics, tables


_BUILDERS = {
    "tasks": _build_tasks_metrics,
    "knowledge": _build_knowledge_metrics,
    "llm_calls": _build_llm_metrics,
}


def build_report(db: Session, tenant_id: str, input: ReportInput) -> dict:
    """生成可回溯报表（纯查询，无副作用）。"""
    _validate_input(input)
    start, end = _resolve_time_range(input)
    metrics: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    warnings: list[str] = []
    for source in input.sources:
        builder = _BUILDERS[source]
        source_metrics, source_tables = builder(db, tenant_id, start, end)
        metrics.extend(source_metrics)
        tables.extend(source_tables)
        if source_metrics and source_metrics[0]["value"] == 0:
            warnings.append(f"{start.isoformat()} 至 {end.isoformat()} 期间 {source} 无数据。")
    # 内部流水线用 dict 组装，出口统一校验为 Pydantic 模型（schema 与实现保持一致）
    output = ReportOutput(
        report_id=f"rep_{uuid.uuid4().hex[:12]}",
        generated_at=datetime.now(UTC).isoformat(),
        parameters={
            "sources": list(input.sources),
            "time_range": {"start": start.isoformat(), "end": end.isoformat()},
            "group_by": input.group_by,
        },
        version=REPORT_VERSION,
        metrics=[Metric(**m) for m in metrics],
        tables=[ReportTable(**t) for t in tables],
        warnings=warnings,
    )
    return output.model_dump()
