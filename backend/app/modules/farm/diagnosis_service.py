"""作物诊断闭环（v3.9）：诊断留档、专家复核、跟进与药剂安全间隔。

诊断结论仍由 ``crop_diagnosis.service.diagnose`` 生成（规则+知识库检索，
不调用模型），本模块只负责持久化与闭环流程，不改变“诊断辅助”口径。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.crop_diagnosis.service import diagnose as run_diagnosis
from app.modules.farm import workorder_service
from app.modules.farm.models import (
    DIAGNOSIS_STATUSES,
    TERMINAL_WORK_ORDER_STATUSES,
    CropDiagnosis,
    DiagnosisFollowUp,
    FarmPlot,
    PesticideProduct,
    WorkOrder,
)
from app.modules.farm.schemas import (
    DiagnosisConsultRequest,
    DiagnosisCreate,
    DiagnosisReviewRequest,
    FollowUpCreate,
    PesticideCreate,
    PesticideUpdate,
    WorkOrderCreate,
)

REVIEW_STATUSES = ("confirmed", "closed")
PESTICIDE_STATUSES = ("active", "archived")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _plot_name(db: Session, yard_id: str, plot_id: str | None) -> str:
    if not plot_id:
        return ""
    name = db.scalar(
        select(FarmPlot.name).where(FarmPlot.yard_id == yard_id, FarmPlot.id == plot_id)
    )
    return name or ""


def _ensure_plot(db: Session, yard_id: str, plot_id: str | None) -> None:
    if not plot_id:
        return
    exists = db.scalar(
        select(FarmPlot.id).where(FarmPlot.yard_id == yard_id, FarmPlot.id == plot_id)
    )
    if exists is None:
        raise AppError("FARM_PLOT_NOT_FOUND", "地块不存在或无权访问。", http_status=404)


def _guard(db: Session, yard_id: str, crop: str, output: dict | None) -> dict | None:
    """涉及用药时返回“已登记药剂 + 安全间隔期”提示；绝不生成用药方案。"""
    if not output or not output.get("medication_intent"):
        return None
    rows = db.scalars(
        select(PesticideProduct).where(
            PesticideProduct.yard_id == yard_id,
            PesticideProduct.status == "active",
        )
    ).all()
    crop_key = (crop or "").strip()
    matched = [
        p
        for p in rows
        if crop_key and crop_key in [str(c).strip() for c in (p.registered_crops or [])]
    ]
    return {
        "requires_expert_confirmation": True,
        "registered_products": [
            {
                "pesticide_id": p.id,
                "name": p.name,
                "active_ingredient": p.active_ingredient,
                "pre_harvest_interval_days": p.pre_harvest_interval_days,
            }
            for p in matched
        ],
        "note": (
            "涉及用药必须先经专家确认诊断，并按标签与当地登记要求执行；"
            "此处只列出本院登记信息，不构成推荐或用量方案。"
        ),
    }


def _follow_up_out(row: DiagnosisFollowUp) -> dict:
    return {
        "followup_id": row.id,
        "diagnosis_id": row.diagnosis_id,
        "occurred_on": row.occurred_on,
        "note": row.note,
        "image_refs": row.image_refs,
        "created_by": row.created_by,
        "created_at": row.created_at,
    }


def _basis_out(row: CropDiagnosis) -> dict:
    return {
        "diagnosis_id": row.id,
        "plot_id": row.plot_id,
        "crop": row.crop,
        "variety": row.variety,
        "growth_stage": row.growth_stage,
        "region": row.region,
        "symptoms": row.symptoms,
        "image_refs": row.image_refs,
        "environment": row.environment_json,
        "observed_at": row.observed_at,
        "output": row.output_json,
        "confidence": row.confidence,
        "needs_expert_review": row.needs_expert_review,
        "status": row.status,
        "vision_status": row.vision_status,
        "expert_reviewer_id": row.expert_reviewer_id,
        "expert_note": row.expert_note,
        "final_diagnosis": row.final_diagnosis,
        "expert_reviewed_at": row.expert_reviewed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _detail_out(db: Session, yard_id: str, row: CropDiagnosis) -> dict:
    follow_ups = db.scalars(
        select(DiagnosisFollowUp)
        .where(
            DiagnosisFollowUp.yard_id == yard_id,
            DiagnosisFollowUp.diagnosis_id == row.id,
        )
        .order_by(DiagnosisFollowUp.occurred_on.asc(), DiagnosisFollowUp.created_at.asc())
    ).all()
    data = _basis_out(row)
    data["medication_guard"] = _guard(db, yard_id, row.crop, row.output_json)
    data["follow_ups"] = [_follow_up_out(f) for f in follow_ups]
    return data


def _get_diagnosis(db: Session, yard_id: str, diagnosis_id: str) -> CropDiagnosis:
    row = db.scalar(
        select(CropDiagnosis).where(
            CropDiagnosis.yard_id == yard_id, CropDiagnosis.id == diagnosis_id
        )
    )
    if row is None:
        raise AppError("DIAGNOSIS_NOT_FOUND", "诊断记录不存在或无权访问。", http_status=404)
    return row


def create_diagnosis(db: Session, yard_id: str, user_id: str, req: DiagnosisCreate) -> dict:
    _ensure_plot(db, yard_id, req.plot_id)
    output = run_diagnosis(
        db,
        yard_id=yard_id,
        user_id=user_id,
        input_data=req.model_dump(exclude_none=True),
    )
    needs_review = bool(output.get("needs_expert_review"))
    row = CropDiagnosis(
        yard_id=yard_id,
        plot_id=req.plot_id,
        crop=req.crop.strip(),
        variety=(req.variety or "").strip(),
        growth_stage=(req.growth_stage or "").strip(),
        region=(req.region or "").strip(),
        symptoms=req.symptoms.strip(),
        image_refs=req.image_refs or None,
        environment_json=req.environment,
        observed_at=req.observed_at,
        output_json=output,
        confidence=float(output.get("confidence") or 0.0),
        needs_expert_review=needs_review,
        status="expert_review" if needs_review else "open",
        # 图像识别未接入：只保存引用，明确标注状态，避免让用户误以为已识别
        vision_status="not_configured",
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="crop_diagnosis_created",
        resource_type="crop_diagnosis",
        resource_id=row.id,
        result="success",
        request_id="",
        metadata={"crop": row.crop, "needs_expert_review": needs_review},
    )
    db.commit()
    return _detail_out(db, yard_id, row)


def list_diagnoses(
    db: Session,
    yard_id: str,
    *,
    status: str | None = None,
    plot_id: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    q = select(CropDiagnosis).where(CropDiagnosis.yard_id == yard_id)
    if status:
        if status not in DIAGNOSIS_STATUSES:
            raise AppError("INVALID_REQUEST", "未知诊断状态。", http_status=400)
        q = q.where(CropDiagnosis.status == status)
    if plot_id:
        q = q.where(CropDiagnosis.plot_id == plot_id)
    total = int(db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0)
    rows = db.scalars(
        q.order_by(CropDiagnosis.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = []
    for row in rows:
        output = row.output_json if isinstance(row.output_json, dict) else {}
        candidates = output.get("candidates") or []
        top = candidates[0] if candidates else {}
        items.append(
            {
                "diagnosis_id": row.id,
                "plot_id": row.plot_id,
                "plot_name": _plot_name(db, yard_id, row.plot_id),
                "crop": row.crop,
                "growth_stage": row.growth_stage,
                "status": row.status,
                "confidence": row.confidence,
                "needs_expert_review": row.needs_expert_review,
                "top_candidate": top.get("name"),
                "top_confidence": top.get("confidence"),
                "vision_status": row.vision_status,
                "created_at": row.created_at,
            }
        )
    return items, total


def get_diagnosis(db: Session, yard_id: str, diagnosis_id: str) -> dict:
    return _detail_out(db, yard_id, _get_diagnosis(db, yard_id, diagnosis_id))


def request_consult(
    db: Session, yard_id: str, diagnosis_id: str, user_id: str, req: DiagnosisConsultRequest
) -> dict:
    """诊断转专家会诊工单（复用 3.10 工单闭环）。

    - 已关闭的诊断不再受理；
    - 同一诊断已有未结束的会诊工单时返回 409，避免重复派单；
    - 工单来源标记 ``diagnosis`` 并回指诊断，处置结论仍走工单回执。
    """
    row = _get_diagnosis(db, yard_id, diagnosis_id)
    if row.status == "closed":
        raise AppError("DIAGNOSIS_CLOSED", "诊断已关闭，不能再发起会诊。", http_status=409)
    existing = db.scalar(
        select(WorkOrder).where(
            WorkOrder.yard_id == yard_id,
            WorkOrder.source == "diagnosis",
            WorkOrder.source_ref == row.id,
            WorkOrder.status.not_in(TERMINAL_WORK_ORDER_STATUSES),
        )
    )
    if existing is not None:
        raise AppError(
            "CONSULT_ALREADY_OPEN",
            "该诊断已有未结束的会诊工单，请直接在工单中跟进。",
            http_status=409,
            details={"work_order_id": existing.id},
        )
    output = row.output_json or {}
    candidates = output.get("candidates") or []
    top = str(candidates[0].get("name") or "") if candidates else ""
    title = f"专家会诊：{row.crop}{'｜' + top if top else ''}"
    lines = [
        f"症状：{row.symptoms or '（未填写）'}",
        f"生长阶段：{row.growth_stage or '未填写'}；品种：{row.variety or '未填写'}",
        f"综合置信度：{round(float(row.confidence or 0) * 100)}%",
    ]
    if req.note.strip():
        lines.append(f"发起人补充：{req.note.strip()}")
    order = workorder_service.create_work_order(
        db,
        yard_id,
        user_id,
        WorkOrderCreate(
            title=title[:160],
            description="\n".join(lines)[:4000],
            priority="high",
            source="diagnosis",
            source_ref=row.id,
            assignee_id=req.expert_id,
            due_on=datetime.now(UTC).date() + timedelta(days=3),
        ),
    )
    if row.status == "open":
        row.status = "expert_review"
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="diagnosis_consult_requested",
        resource_type="crop_diagnosis",
        resource_id=row.id,
        result="success",
        request_id="",
        metadata={"work_order_id": order["work_order_id"]},
    )
    db.commit()
    return {"diagnosis": get_diagnosis(db, yard_id, row.id), "work_order": order}


def review_diagnosis(
    db: Session, yard_id: str, diagnosis_id: str, user_id: str, req: DiagnosisReviewRequest
) -> dict:
    row = _get_diagnosis(db, yard_id, diagnosis_id)
    if req.status not in REVIEW_STATUSES:
        raise AppError("INVALID_REQUEST", "复核结论只能是 confirmed/closed。", http_status=400)
    row.expert_reviewer_id = user_id
    row.expert_note = req.expert_note.strip()
    row.final_diagnosis = req.final_diagnosis.strip()
    row.expert_reviewed_at = _utcnow()
    row.status = req.status
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="crop_diagnosis_reviewed",
        resource_type="crop_diagnosis",
        resource_id=row.id,
        result="success",
        request_id="",
        metadata={"status": req.status},
    )
    db.commit()
    return _detail_out(db, yard_id, row)


def add_follow_up(
    db: Session, yard_id: str, diagnosis_id: str, user_id: str, req: FollowUpCreate
) -> dict:
    _get_diagnosis(db, yard_id, diagnosis_id)
    if len(req.image_refs) > 9:
        raise AppError("INVALID_REQUEST", "图片引用最多 9 条。", http_status=400)
    row = DiagnosisFollowUp(
        yard_id=yard_id,
        diagnosis_id=diagnosis_id,
        occurred_on=req.occurred_on,
        note=req.note.strip(),
        image_refs=req.image_refs or None,
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="crop_diagnosis_followed_up",
        resource_type="crop_diagnosis",
        resource_id=diagnosis_id,
        result="success",
        request_id="",
    )
    db.commit()
    return _follow_up_out(row)


# ---------------------------------------------------------------------------
# 药剂登记
# ---------------------------------------------------------------------------


def _pesticide_out(row: PesticideProduct) -> dict:
    return {
        "pesticide_id": row.id,
        "name": row.name,
        "active_ingredient": row.active_ingredient,
        "registered_crops": row.registered_crops,
        "pre_harvest_interval_days": row.pre_harvest_interval_days,
        "label_notes": row.label_notes,
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _get_pesticide(db: Session, yard_id: str, pesticide_id: str) -> PesticideProduct:
    row = db.scalar(
        select(PesticideProduct).where(
            PesticideProduct.yard_id == yard_id, PesticideProduct.id == pesticide_id
        )
    )
    if row is None:
        raise AppError("PESTICIDE_NOT_FOUND", "药剂登记不存在或无权访问。", http_status=404)
    return row


def list_pesticides(db: Session, yard_id: str) -> list[dict]:
    rows = db.scalars(
        select(PesticideProduct)
        .where(PesticideProduct.yard_id == yard_id)
        .order_by(PesticideProduct.created_at.desc())
    ).all()
    return [_pesticide_out(r) for r in rows]


def create_pesticide(db: Session, yard_id: str, user_id: str, req: PesticideCreate) -> dict:
    if req.status not in PESTICIDE_STATUSES:
        raise AppError("INVALID_REQUEST", "未知药剂状态。", http_status=400)
    name = req.name.strip()
    exists = db.scalar(
        select(PesticideProduct.id).where(
            PesticideProduct.yard_id == yard_id, PesticideProduct.name == name
        )
    )
    if exists:
        raise AppError("DUPLICATE_NAME", "该药剂名称已存在。", http_status=409)
    row = PesticideProduct(
        yard_id=yard_id,
        name=name,
        active_ingredient=req.active_ingredient.strip(),
        registered_crops=[c.strip() for c in req.registered_crops if c.strip()],
        pre_harvest_interval_days=req.pre_harvest_interval_days,
        label_notes=req.label_notes.strip(),
        status=req.status,
        created_by=user_id,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise AppError("DUPLICATE_NAME", "该药剂名称已存在。", http_status=409) from e
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="pesticide_created",
        resource_type="pesticide",
        resource_id=row.id,
        result="success",
        request_id="",
    )
    db.commit()
    return _pesticide_out(row)


def update_pesticide(
    db: Session, yard_id: str, pesticide_id: str, user_id: str, req: PesticideUpdate
) -> dict:
    row = _get_pesticide(db, yard_id, pesticide_id)
    changes = req.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] is not None:
        if changes["status"] not in PESTICIDE_STATUSES:
            raise AppError("INVALID_REQUEST", "未知药剂状态。", http_status=400)
    if "name" in changes and changes["name"] is not None:
        name = changes["name"].strip()
        clash = db.scalar(
            select(PesticideProduct.id).where(
                PesticideProduct.yard_id == yard_id,
                PesticideProduct.name == name,
                PesticideProduct.id != row.id,
            )
        )
        if clash:
            raise AppError("DUPLICATE_NAME", "该药剂名称已存在。", http_status=409)
        changes["name"] = name
    if "registered_crops" in changes and changes["registered_crops"] is not None:
        changes["registered_crops"] = [c.strip() for c in changes["registered_crops"] if c.strip()]
    for field in ("active_ingredient", "label_notes"):
        if field in changes and isinstance(changes[field], str):
            changes[field] = changes[field].strip()
    for field, value in changes.items():
        if value is not None:
            setattr(row, field, value)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="pesticide_updated",
        resource_type="pesticide",
        resource_id=row.id,
        result="success",
        request_id="",
        metadata={"fields": sorted(changes)},
    )
    db.commit()
    return _pesticide_out(row)


def delete_pesticide(db: Session, yard_id: str, pesticide_id: str, user_id: str) -> None:
    row = _get_pesticide(db, yard_id, pesticide_id)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="pesticide_deleted",
        resource_type="pesticide",
        resource_id=row.id,
        result="success",
        request_id="",
    )
    db.delete(row)
    db.commit()


def safe_harvest(db: Session, yard_id: str, pesticide_id: str, applied_on: date) -> dict:
    row = _get_pesticide(db, yard_id, pesticide_id)
    earliest = applied_on + timedelta(days=int(row.pre_harvest_interval_days or 0))
    return {
        "pesticide_id": row.id,
        "name": row.name,
        "pre_harvest_interval_days": row.pre_harvest_interval_days,
        "applied_on": applied_on,
        "earliest_harvest_on": earliest,
        "note": "按登记的安全间隔期推算，实际以产品标签和当地监管要求为准。",
    }
