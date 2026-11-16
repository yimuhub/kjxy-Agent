"""农事台账 Agent 工具：让对话/助手/班组能真正读写字号台账。

背景（2026-11-16 走查）：聊天工具注册表只有 web/knowledge/doc/image/video 等 16 个
能力，**没有任何 farm.* 写工具**。于是"记一笔农事，再生成周报"这类复合指令只能
生成空模板文档、台账一条不动，且模型不会声明"我没权限写"。本模块补三件事：

- ``farm.record.create``：按地块（id 或名称模糊匹配）真实写入一笔农事记录；
- ``farm.plot.create``：地块不存在时可先建地块（名称唯一）；
- ``farm.report.generate``：直接返回台账真数据生成的 Markdown 周报/月报，
  供 ``doc.generate`` 作为 context 引用，避免"周报全是待补充"。

三者都走既有 farm service（校验/审计/幂等 client_op_id 全部复用），不另写一套写库逻辑。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import YardContext
from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.farm.models import RECORD_KIND_LABELS, RECORD_KINDS, FarmPlot
from app.modules.farm.schemas import PlotCreate, RecordCreate, ReportRequest

log = get_logger(__name__)

RECORD_TOOL_CODE = "farm.record.create"
PLOT_TOOL_CODE = "farm.plot.create"
REPORT_TOOL_CODE = "farm.report.generate"

_KIND_ALIASES: dict[str, str] = {
    "播种": "sowing",
    "移栽": "sowing",
    "定植": "sowing",
    "施肥": "fertilizing",
    "追肥": "fertilizing",
    "底肥": "fertilizing",
    "灌溉": "irrigation",
    "浇水": "irrigation",
    "滴灌": "irrigation",
    "用药": "pesticide",
    "打药": "pesticide",
    "喷药": "pesticide",
    "除草": "weeding",
    "巡田": "scouting",
    "巡查": "scouting",
    "查看": "scouting",
    "采收": "harvest",
    "收获": "harvest",
    "其他": "other",
}


def _normalize_kind(raw: str) -> str:
    text = str(raw or "").strip()
    if text in RECORD_KINDS:
        return text
    if text in _KIND_ALIASES:
        return _KIND_ALIASES[text]
    for key, value in RECORD_KIND_LABELS.items():
        if text == value:
            return key
    raise AppError(
        "INVALID_REQUEST",
        f"无法识别的农事类型：{text}。可用：{'/'.join(RECORD_KINDS)}；"
        "或中文（施肥/灌溉/巡田/用药/除草/播种/采收）。",
        http_status=400,
        details={"kinds": list(RECORD_KINDS)},
    )


def _open_session() -> Session:
    from app.db.session import SessionLocal

    return SessionLocal()


def _resolve_plot(
    db: Session, yard_id: str, plot_id: str | None, plot_name: str | None
) -> FarmPlot:
    if not yard_id:
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    if plot_id:
        plot = db.scalar(
            select(FarmPlot).where(FarmPlot.id == plot_id, FarmPlot.yard_id == yard_id)
        )
        if plot is None:
            raise AppError("NOT_FOUND", f"地块不存在：{plot_id}", http_status=404)
        return plot
    name = (plot_name or "").strip()
    if not name:
        raise AppError(
            "INVALID_REQUEST",
            "请提供地块名称（plot_name）或地块 id（plot_id）。",
            http_status=400,
        )
    exact = db.scalar(select(FarmPlot).where(FarmPlot.yard_id == yard_id, FarmPlot.name == name))
    if exact is not None:
        return exact
    # 模糊匹配：模型常把"西地块"说成"西地块-山药示范田-xxx"
    likable = db.scalars(
        select(FarmPlot).where(FarmPlot.yard_id == yard_id, FarmPlot.name.like(f"%{name}%"))
    ).all()
    if len(likable) == 1:
        return likable[0]
    if len(likable) > 1:
        raise AppError(
            "PLOT_AMBIGUOUS",
            f"地块名称「{name}」匹配到多个地块，请补充更精确的名称。",
            http_status=409,
            details={"candidates": [p.name for p in likable]},
        )
    all_names = db.scalars(select(FarmPlot.name).where(FarmPlot.yard_id == yard_id)).all()
    raise AppError(
        "NOT_FOUND",
        f"没有名为「{name}」的地块。可先调用 farm.plot.create 建地块。",
        http_status=404,
        details={"plots": list(all_names)},
    )


def _count_records(db: Session, yard_id: str) -> int:
    from sqlalchemy import func

    from app.modules.farm.models import FarmRecord

    return int(
        db.scalar(select(func.count(FarmRecord.id)).where(FarmRecord.yard_id == yard_id)) or 0
    )


def _record_create_func(params: dict, ctx: YardContext) -> dict[str, Any]:
    from app.modules.farm import service as farm_service

    payload = dict(params or {})
    db = _open_session()
    try:
        plot = _resolve_plot(
            db,
            ctx.yard_id,
            str(payload.get("plot_id") or "").strip() or None,
            str(payload.get("plot_name") or "").strip() or None,
        )
        raw_date = str(payload.get("occurred_on") or "").strip()
        occurred_on = date.fromisoformat(raw_date) if raw_date else date.today()
        req = RecordCreate(
            plot_id=plot.id,
            kind=_normalize_kind(str(payload.get("kind") or "other")),
            occurred_on=occurred_on,
            title=str(payload.get("title") or "").strip()[:120],
            content=str(payload.get("content") or "").strip()[:5000],
            quantity=payload.get("quantity"),
            unit=str(payload.get("unit") or "").strip()[:20],
            operator_name=str(payload.get("operator_name") or "").strip()[:60],
            client_op_id=str(payload.get("client_op_id") or "").strip()[:36] or None,
        )
        record = farm_service.create_record(db, ctx.yard_id, ctx.user_id, req)
        db.commit()
        total = _count_records(db, ctx.yard_id)
        label = RECORD_KIND_LABELS.get(record.get("kind"), record.get("kind"))
        summary = (
            f"已记 1 笔农事：{plot.name} · {label}"
            + (f" · {record.get('title')}" if record.get("title") else "")
            + f"；台账现共 {total} 条。"
        )
        return {
            "record": record,
            "plot_name": plot.name,
            "total_records": total,
            "summary_text": summary,
        }
    finally:
        db.close()


def _plot_create_func(params: dict, ctx: YardContext) -> dict[str, Any]:
    from app.modules.farm import service as farm_service

    payload = dict(params or {})
    db = _open_session()
    try:
        req = PlotCreate(
            name=str(payload.get("name") or "").strip()[:80],
            crop=str(payload.get("crop") or "").strip()[:60],
            area_mu=payload.get("area_mu"),
            location=str(payload.get("location") or "").strip()[:200],
            soil_type=str(payload.get("soil_type") or "").strip()[:40],
            irrigation_type=str(payload.get("irrigation_type") or "").strip()[:40],
            notes=str(payload.get("notes") or "").strip()[:2000],
            client_op_id=str(payload.get("client_op_id") or "").strip()[:36] or None,
        )
        plot = farm_service.create_plot(db, ctx.yard_id, ctx.user_id, req)
        db.commit()
        return {
            "plot": plot,
            "summary_text": f"已新建地块「{plot.get('name')}」，后续可直接记农事。",
        }
    finally:
        db.close()


def _report_func(params: dict, ctx: YardContext) -> dict[str, Any]:
    from app.modules.farm import service as farm_service

    payload = dict(params or {})
    db = _open_session()
    try:
        kind = str(payload.get("kind") or "weekly").strip()
        if kind not in ("log", "weekly", "quarterly"):
            raise AppError(
                "INVALID_REQUEST", "报表类型只能是 log/weekly/quarterly。", http_status=400
            )
        today = date.today()
        if kind == "weekly":
            default_start, default_end = today - timedelta(days=today.weekday()), today
        elif kind == "quarterly":
            q_start_month = (today.month - 1) // 3 * 3 + 1
            default_start, default_end = date(today.year, q_start_month, 1), today
        else:
            default_start, default_end = today, today
        start = (
            date.fromisoformat(str(payload.get("start")))
            if payload.get("start")
            else default_start
        )
        end = date.fromisoformat(str(payload.get("end"))) if payload.get("end") else default_end
        req = ReportRequest(
            kind=kind,
            start=start,
            end=end,
            plot_id=str(payload.get("plot_id") or "").strip() or None,
        )
        report = farm_service.build_report(db, ctx.yard_id, req)
        md = str(report.get("content_md") or "")
        return {
            "kind": kind,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "record_count": len(report.get("records") or []),
            "content_md": md,
            "summary_text": (
                f"已生成{kind}报表（{start.isoformat()}~{end.isoformat()}），"
                f"含 {len(report.get('records') or [])} 条农事记录。"
                "可把 content_md 交给 doc.generate。"
            ),
        }
    finally:
        db.close()


RECORD_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "plot_name": {
            "type": "string",
            "maxLength": 80,
            "description": "地块名称，可模糊匹配",
        },
        "plot_id": {
            "type": "string",
            "maxLength": 36,
            "description": "地块 id；与 plot_name 二选一",
        },
        "kind": {
            "type": "string",
            "description": "农事类型：sowing/fertilizing/irrigation/pesticide/weeding/"
            "scouting/harvest/other，或中文（施肥/灌溉/巡田/用药/除草/播种/采收）",
        },
        "occurred_on": {
            "type": "string",
            "description": "发生日期 YYYY-MM-DD；省略=今天",
        },
        "title": {"type": "string", "maxLength": 120, "description": "一句话事项"},
        "content": {
            "type": "string",
            "maxLength": 5000,
            "description": "明细（用量/方式/观察结果）",
        },
        "quantity": {"type": "number", "description": "用量数值"},
        "unit": {"type": "string", "maxLength": 20, "description": "用量单位，如 公斤/亩"},
        "operator_name": {"type": "string", "maxLength": 60, "description": "操作人"},
        "client_op_id": {
            "type": "string",
            "maxLength": 36,
            "description": "幂等键：同一操作重试时传相同值，避免重复落账",
        },
    },
    "required": ["kind"],
    "additionalProperties": False,
}

PLOT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "maxLength": 80},
        "crop": {"type": "string", "maxLength": 60},
        "area_mu": {"type": "number", "description": "面积（亩）"},
        "location": {"type": "string", "maxLength": 200},
        "soil_type": {"type": "string", "maxLength": 40},
        "irrigation_type": {"type": "string", "maxLength": 40},
        "notes": {"type": "string", "maxLength": 2000},
        "client_op_id": {"type": "string", "maxLength": 36},
    },
    "required": ["name"],
    "additionalProperties": False,
}

REPORT_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["log", "weekly", "quarterly"], "default": "weekly"},
        "start": {"type": "string", "description": "起始日期 YYYY-MM-DD；省略=本周一/当季首日"},
        "end": {"type": "string", "description": "结束日期 YYYY-MM-DD；省略=今天"},
        "plot_id": {"type": "string", "maxLength": 36},
    },
    "additionalProperties": False,
}


def register_farm_tools() -> None:
    """注册三个农事工具；注册表不可用时只告警，不影响应用启动。"""
    try:
        from app.modules.tools.registry import ToolDef, tool_registry
    except Exception as e:  # noqa: BLE001
        log.warning("工具注册表不可用，farm 工具未注册: %s", e)
        return

    specs = [
        ToolDef(
            code=RECORD_TOOL_CODE,
            version="1.0.0",
            name="记一笔农事",
            description=(
                "向本小院农业台账真实写入一笔农事记录（地块可按名称模糊匹配）。"
                "返回台账总条数；写入成功后必须如实告知用户，不得声称未发生的事。"
            ),
            input_schema=RECORD_INPUT_SCHEMA,
            required_permissions=["farm:write"],
            risk_level="write_reversible",
            side_effect=True,
            timeout_seconds=20,
            func=_record_create_func,
        ),
        ToolDef(
            code=PLOT_TOOL_CODE,
            version="1.0.0",
            name="新建地块",
            description="在本小院新建地块（名称唯一）；地块不存在时可先建后记。",
            input_schema=PLOT_INPUT_SCHEMA,
            required_permissions=["farm:write"],
            risk_level="write_reversible",
            side_effect=True,
            timeout_seconds=20,
            func=_plot_create_func,
        ),
        ToolDef(
            code=REPORT_TOOL_CODE,
            version="1.0.0",
            name="农事报表（真数据）",
            description=(
                "读取台账真实记录生成 Markdown 周报/月报/季报，返回 content_md。"
                "需要写 Word/PPT 时，把它作为 doc.generate 的 context 传入。"
            ),
            input_schema=REPORT_INPUT_SCHEMA,
            required_permissions=[],
            risk_level="read_only",
            side_effect=False,
            timeout_seconds=20,
            func=_report_func,
        ),
    ]
    for spec in specs:
        try:
            tool_registry.register(spec)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 注册失败: %s", spec.code, e)
    log.info("farm 工具已注册（record/plot/report）")


def farm_tools_meta() -> list[dict[str, Any]]:
    """工具元数据（供 ``seed_builtin_tools`` 落 ToolDefinition 行）。"""
    return [
        {
            "code": RECORD_TOOL_CODE,
            "version": "1.0.0",
            "name": "记一笔农事",
            "description": "向本小院农业台账真实写入一笔农事记录（地块可按名称模糊匹配）。",
            "input_schema_json": RECORD_INPUT_SCHEMA,
            "output_schema_json": {
                "type": "object",
                "properties": {
                    "record": {"type": "object"},
                    "plot_name": {"type": "string"},
                    "total_records": {"type": "integer"},
                    "summary_text": {"type": "string"},
                },
                "required": ["record", "total_records"],
            },
            "required_permissions_json": ["farm:write"],
            "risk_level": "write_reversible",
            "side_effect": True,
            "timeout_seconds": 20,
            "enabled": True,
        },
        {
            "code": PLOT_TOOL_CODE,
            "version": "1.0.0",
            "name": "新建地块",
            "description": "在本小院新建地块（名称唯一）；地块不存在时可先建后记。",
            "input_schema_json": PLOT_INPUT_SCHEMA,
            "output_schema_json": {
                "type": "object",
                "properties": {
                    "plot": {"type": "object"},
                    "summary_text": {"type": "string"},
                },
                "required": ["plot"],
            },
            "required_permissions_json": ["farm:write"],
            "risk_level": "write_reversible",
            "side_effect": True,
            "timeout_seconds": 20,
            "enabled": True,
        },
        {
            "code": REPORT_TOOL_CODE,
            "version": "1.0.0",
            "name": "农事报表（真数据）",
            "description": "读取台账真实记录生成 Markdown 周报/月报/季报，返回 content_md。",
            "input_schema_json": REPORT_INPUT_SCHEMA,
            "output_schema_json": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "record_count": {"type": "integer"},
                    "content_md": {"type": "string"},
                    "summary_text": {"type": "string"},
                },
                "required": ["content_md"],
            },
            "required_permissions_json": [],
            "risk_level": "read_only",
            "side_effect": False,
            "timeout_seconds": 20,
            "enabled": True,
        },
    ]
