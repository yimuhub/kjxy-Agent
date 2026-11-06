"""农业台账路由（v3.8）：地块、种植周期、农事记录与报表。

权限点：farm:read 读取；farm:write 写入。均按 DB 有效权限判定。
"""

from datetime import date

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.responses import ok, paged
from app.modules.farm import diagnosis_service, service, weather, workorder_service
from app.modules.farm.schemas import (
    AlertCreate,
    CycleCreate,
    CycleUpdate,
    DiagnosisConsultRequest,
    DiagnosisCreate,
    DiagnosisFromDevice,
    DiagnosisReviewRequest,
    FollowUpCreate,
    PesticideCreate,
    PesticideUpdate,
    PlotCreate,
    PlotUpdate,
    RecordCreate,
    RecordUpdate,
    ReportRequest,
    WorkOrderComment,
    WorkOrderCreate,
    WorkOrderReceipt,
    WorkOrderUpdate,
)
from app.modules.yards.settings_service import get_setting

router = APIRouter(prefix="/farm", tags=["farm"])


def _yard_id(user: CurrentUser) -> str:
    if user.yard_id is None:  # require_yard_permission 已保证，防御性兜底
        raise AppError("YARD_REQUIRED", "该操作需要小院上下文。", http_status=400)
    return user.yard_id


# ---------------------------------------------------------------------------
# 地块
# ---------------------------------------------------------------------------


@router.get("/plots")
def list_plots(
    request: Request,
    include_archived: bool = Query(default=False),
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    return ok(service.list_plots(db, _yard_id(user), include_archived=include_archived), request)


@router.post("/plots", status_code=201)
def create_plot(
    payload: PlotCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = service.create_plot(db, _yard_id(user), user.user_id, payload)
    return ok(data, request)


@router.patch("/plots/{plot_id}")
def update_plot(
    plot_id: str,
    payload: PlotUpdate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    return ok(service.update_plot(db, _yard_id(user), plot_id, user.user_id, payload), request)


@router.delete("/plots/{plot_id}")
def delete_plot(
    plot_id: str,
    request: Request,
    expected_revision: int | None = Query(default=None, ge=1),
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    service.delete_plot(
        db, _yard_id(user), plot_id, user.user_id, expected_revision=expected_revision
    )
    return ok({"ok": True}, request)


# ---------------------------------------------------------------------------
# 种植周期
# ---------------------------------------------------------------------------


@router.get("/plots/{plot_id}/cycles")
def list_cycles(
    plot_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    return ok(service.list_cycles(db, _yard_id(user), plot_id), request)


@router.post("/plots/{plot_id}/cycles", status_code=201)
def create_cycle(
    plot_id: str,
    payload: CycleCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = service.create_cycle(db, _yard_id(user), plot_id, user.user_id, payload)
    return ok(data, request)


@router.patch("/cycles/{cycle_id}")
def update_cycle(
    cycle_id: str,
    payload: CycleUpdate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    return ok(service.update_cycle(db, _yard_id(user), cycle_id, user.user_id, payload), request)


@router.delete("/cycles/{cycle_id}")
def delete_cycle(
    cycle_id: str,
    request: Request,
    expected_revision: int | None = Query(default=None, ge=1),
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    service.delete_cycle(
        db, _yard_id(user), cycle_id, user.user_id, expected_revision=expected_revision
    )
    return ok({"ok": True}, request)


# ---------------------------------------------------------------------------
# 农事记录
# ---------------------------------------------------------------------------


@router.get("/records")
def list_records(
    request: Request,
    plot_id: str | None = Query(default=None, max_length=36),
    kind: str | None = Query(default=None, max_length=20),
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    items, total = service.list_records(
        db,
        _yard_id(user),
        plot_id=plot_id,
        kind=kind,
        start=start,
        end=end,
        page=page,
        page_size=page_size,
    )
    return paged(items, page, page_size, total, request)


@router.post("/records", status_code=201)
def create_record(
    payload: RecordCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = service.create_record(db, _yard_id(user), user.user_id, payload)
    return ok(data, request)


@router.patch("/records/{record_id}")
def update_record(
    record_id: str,
    payload: RecordUpdate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    return ok(service.update_record(db, _yard_id(user), record_id, user.user_id, payload), request)


@router.delete("/records/{record_id}")
def delete_record(
    record_id: str,
    request: Request,
    expected_revision: int | None = Query(default=None, ge=1),
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    service.delete_record(
        db, _yard_id(user), record_id, user.user_id, expected_revision=expected_revision
    )
    return ok({"ok": True}, request)


# ---------------------------------------------------------------------------
# 报表
# ---------------------------------------------------------------------------


@router.post("/reports")
def build_report(
    payload: ReportRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    data = service.build_report(db, _yard_id(user), payload)
    return ok(
        {
            **data,
            "start": data["start"].isoformat(),
            "end": data["end"].isoformat(),
        },
        request,
    )


# ---------------------------------------------------------------------------
# 作物诊断闭环（v3.9）
# ---------------------------------------------------------------------------


@router.post("/diagnoses", status_code=201)
def create_diagnosis(
    payload: DiagnosisCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = diagnosis_service.create_diagnosis(db, _yard_id(user), user.user_id, payload)
    return ok(data, request)


@router.post("/diagnoses/attachments", status_code=201)
def upload_diagnosis_attachments(
    request: Request,
    files: list[UploadFile] = File(
        ..., description="图片（PNG/JPG/WEBP）或文档（PDF/DOCX/TXT/MD/CSV）"
    ),
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    """先上传材料拿附件 id，创建诊断时再按 attachment_ids 关联。"""
    data = diagnosis_service.upload_diagnosis_attachments(db, _yard_id(user), user.user_id, files)
    return ok(data, request)


@router.delete("/diagnoses/attachments/{attachment_id}")
def delete_diagnosis_attachment(
    attachment_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    diagnosis_service.delete_diagnosis_attachment(db, _yard_id(user), attachment_id, user.user_id)
    return ok({"attachment_id": attachment_id}, request)


@router.get("/diagnoses/attachments/{attachment_id}/file")
def download_diagnosis_attachment(
    attachment_id: str,
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    """按附件 id 取原文件（登录态 + 小院校验）；存储键不直接暴露为公开 URL。"""
    from app.modules.knowledge.storage import LocalStorageBackend

    row = diagnosis_service.get_diagnosis_attachment(db, _yard_id(user), attachment_id)
    try:
        path = LocalStorageBackend().path(row.storage_key)
    except ValueError as exc:
        raise AppError("ATTACHMENT_NOT_FOUND", "附件文件不存在。", http_status=404) from exc
    if not path.exists():
        raise AppError("ATTACHMENT_NOT_FOUND", "附件文件不存在。", http_status=404)
    return FileResponse(
        path,
        media_type=row.mime,
        filename=row.filename,
        content_disposition_type="inline",
    )


@router.post("/diagnoses/from-device", status_code=201)
def create_diagnosis_from_device(
    payload: DiagnosisFromDevice,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    """已对接设备推送问题 → Agent 自动诊断（source=device，状态 agent_diagnosed）。"""
    data = diagnosis_service.create_from_device(db, _yard_id(user), user.user_id, payload)
    return ok(data, request)


@router.get("/diagnoses")
def list_diagnoses(
    request: Request,
    status: str | None = Query(default=None, max_length=64, description="单个状态或逗号分隔多状态"),
    plot_id: str | None = Query(default=None, max_length=36),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    items, total = diagnosis_service.list_diagnoses(
        db,
        _yard_id(user),
        status=status,
        plot_id=plot_id,
        page=page,
        page_size=page_size,
    )
    return paged(items, page, page_size, total, request)


@router.get("/diagnoses/{diagnosis_id}")
def get_diagnosis(
    diagnosis_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    return ok(diagnosis_service.get_diagnosis(db, _yard_id(user), diagnosis_id), request)


@router.post("/diagnoses/{diagnosis_id}/consult", status_code=201)
def consult_diagnosis(
    diagnosis_id: str,
    payload: DiagnosisConsultRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    """把诊断升级为专家会诊工单：复用工单闭环，避免会诊只停在口头。"""
    data = diagnosis_service.request_consult(
        db, _yard_id(user), diagnosis_id, user.user_id, payload
    )
    return ok(data, request)


@router.post("/diagnoses/{diagnosis_id}/review")
def review_diagnosis(
    diagnosis_id: str,
    payload: DiagnosisReviewRequest,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("review:approve")),
    db=Depends(get_db_session),
):
    data = diagnosis_service.review_diagnosis(
        db, _yard_id(user), diagnosis_id, user.user_id, payload
    )
    return ok(data, request)


@router.post("/diagnoses/{diagnosis_id}/follow-ups", status_code=201)
def add_follow_up(
    diagnosis_id: str,
    payload: FollowUpCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = diagnosis_service.add_follow_up(db, _yard_id(user), diagnosis_id, user.user_id, payload)
    return ok(data, request)


# ---------------------------------------------------------------------------
# 药剂登记与安全间隔期
# ---------------------------------------------------------------------------


@router.get("/pesticides")
def list_pesticides(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    return ok(diagnosis_service.list_pesticides(db, _yard_id(user)), request)


@router.post("/pesticides", status_code=201)
def create_pesticide(
    payload: PesticideCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = diagnosis_service.create_pesticide(db, _yard_id(user), user.user_id, payload)
    return ok(data, request)


@router.patch("/pesticides/{pesticide_id}")
def update_pesticide(
    pesticide_id: str,
    payload: PesticideUpdate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = diagnosis_service.update_pesticide(
        db, _yard_id(user), pesticide_id, user.user_id, payload
    )
    return ok(data, request)


@router.delete("/pesticides/{pesticide_id}")
def delete_pesticide(
    pesticide_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    diagnosis_service.delete_pesticide(db, _yard_id(user), pesticide_id, user.user_id)
    return ok({"ok": True}, request)


@router.get("/pesticides/{pesticide_id}/safe-harvest")
def safe_harvest(
    pesticide_id: str,
    request: Request,
    applied_on: date = Query(...),
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    data = diagnosis_service.safe_harvest(db, _yard_id(user), pesticide_id, applied_on)
    return ok(
        {
            **data,
            "applied_on": data["applied_on"].isoformat(),
            "earliest_harvest_on": data["earliest_harvest_on"].isoformat(),
        },
        request,
    )


# ---------------------------------------------------------------------------
# 天气摘要（v3.70）：桌宠/概览轮播用，未配置供应商时 configured=False 不报错
# ---------------------------------------------------------------------------


@router.get("/weather/brief")
def weather_brief(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    """未来 24/48 小时天气摘要。复用 farm_agent 设置里的天气供应商配置与缓存。"""
    yard = _yard_id(user)
    setting = get_setting(db, yard, "farm_agent")
    data, warnings = weather.forecast(yard, setting)
    if data is None:
        return ok(
            {
                "configured": False,
                "brief": None,
                "warning": warnings[0] if warnings else None,
            },
            request,
        )
    day = data.get("next_24h") or {}
    horizon = data.get("next_48h") or {}
    return ok(
        {
            "configured": True,
            "brief": {
                "temp_max": day.get("temp_max"),
                "temp_min": day.get("temp_min"),
                "rain_mm": day.get("rain_mm"),
                "wind_max_ms": day.get("wind_max_ms"),
                "humidity_mean": day.get("humidity_mean"),
                "tomorrow_temp_min": horizon.get("temp_min"),
                "generated_at": data.get("generated_at"),
            },
            "warning": None,
        },
        request,
    )


# ---------------------------------------------------------------------------
# 告警与工单（v3.10）
# ---------------------------------------------------------------------------


@router.post("/alerts", status_code=201)
def create_alert(
    payload: AlertCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = workorder_service.create_alert(db, _yard_id(user), user.user_id, payload)
    return ok(data, request)


@router.get("/alerts")
def list_alerts(
    request: Request,
    status: str | None = Query(default=None, max_length=16),
    severity: str | None = Query(default=None, max_length=16),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    items, total = workorder_service.list_alerts(
        db,
        _yard_id(user),
        status=status,
        severity=severity,
        page=page,
        page_size=page_size,
    )
    return paged(items, page, page_size, total, request)


@router.post("/alerts/{alert_id}/convert", status_code=201)
def convert_alert(
    alert_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = workorder_service.convert_alert(db, _yard_id(user), alert_id, user.user_id)
    return ok(data, request)


@router.get("/work-orders/assignable")
def list_assignable(
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    return ok(workorder_service.list_assignable_members(db, _yard_id(user)), request)


@router.post("/work-orders", status_code=201)
def create_work_order(
    payload: WorkOrderCreate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = workorder_service.create_work_order(db, _yard_id(user), user.user_id, payload)
    return ok(data, request)


@router.get("/work-orders")
def list_work_orders(
    request: Request,
    status: str | None = Query(default=None, max_length=16),
    priority: str | None = Query(default=None, max_length=16),
    assignee_id: str | None = Query(default=None, max_length=36),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    items, total = workorder_service.list_work_orders(
        db,
        _yard_id(user),
        status=status,
        priority=priority,
        assignee_id=assignee_id,
        page=page,
        page_size=page_size,
    )
    return paged(items, page, page_size, total, request)


@router.get("/work-orders/{order_id}")
def get_work_order(
    order_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:read")),
    db=Depends(get_db_session),
):
    return ok(workorder_service.get_work_order(db, _yard_id(user), order_id), request)


@router.patch("/work-orders/{order_id}")
def update_work_order(
    order_id: str,
    payload: WorkOrderUpdate,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = workorder_service.update_work_order(db, _yard_id(user), order_id, user.user_id, payload)
    return ok(data, request)


@router.post("/work-orders/{order_id}/comments")
def add_work_order_comment(
    order_id: str,
    payload: WorkOrderComment,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = workorder_service.add_comment(db, _yard_id(user), order_id, user.user_id, payload.note)
    return ok(data, request)


@router.post("/work-orders/{order_id}/receipt")
def submit_work_order_receipt(
    order_id: str,
    payload: WorkOrderReceipt,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = workorder_service.submit_receipt(db, _yard_id(user), order_id, user.user_id, payload)
    return ok(data, request)


@router.post("/work-orders/{order_id}/escalate")
def escalate_work_order(
    order_id: str,
    request: Request,
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    data = workorder_service.escalate(db, _yard_id(user), order_id, user.user_id)
    return ok(data, request)
