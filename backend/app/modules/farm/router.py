"""农业台账路由（v3.8）：地块、种植周期、农事记录与报表。

权限点：farm:read 读取；farm:write 写入。均按 DB 有效权限判定。
"""

from datetime import date

from fastapi import APIRouter, Depends, Query, Request

from app.core.deps import CurrentUser, get_db_session, require_yard_permission
from app.core.errors import AppError
from app.core.responses import ok, paged
from app.modules.farm import diagnosis_service, service
from app.modules.farm.schemas import (
    CycleCreate,
    CycleUpdate,
    DiagnosisCreate,
    DiagnosisReviewRequest,
    FollowUpCreate,
    PesticideCreate,
    PesticideUpdate,
    PlotCreate,
    PlotUpdate,
    RecordCreate,
    RecordUpdate,
    ReportRequest,
)

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
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    service.delete_plot(db, _yard_id(user), plot_id, user.user_id)
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
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    service.delete_cycle(db, _yard_id(user), cycle_id, user.user_id)
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
    user: CurrentUser = Depends(require_yard_permission("farm:write")),
    db=Depends(get_db_session),
):
    service.delete_record(db, _yard_id(user), record_id, user.user_id)
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


@router.get("/diagnoses")
def list_diagnoses(
    request: Request,
    status: str | None = Query(default=None, max_length=16),
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
