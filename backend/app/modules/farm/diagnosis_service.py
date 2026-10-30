"""作物诊断闭环（v3.9）：诊断留档、专家复核、跟进与药剂安全间隔。

诊断结论仍由 ``crop_diagnosis.service.diagnose`` 生成（规则+知识库检索，
不调用模型），本模块只负责持久化与闭环流程，不改变“诊断辅助”口径。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.chat.attachments import store_uploaded_file
from app.modules.crop_diagnosis.service import diagnose as run_diagnosis
from app.modules.devices.models import Device
from app.modules.farm import workorder_service
from app.modules.farm.models import (
    DIAGNOSIS_STATUSES,
    TERMINAL_WORK_ORDER_STATUSES,
    CropDiagnosis,
    DiagnosisAttachment,
    DiagnosisFollowUp,
    FarmPlot,
    PesticideProduct,
    WorkOrder,
)
from app.modules.farm.schemas import (
    DiagnosisConsultRequest,
    DiagnosisCreate,
    DiagnosisFromDevice,
    DiagnosisReviewRequest,
    FollowUpCreate,
    PesticideCreate,
    PesticideUpdate,
    WorkOrderCreate,
)
from app.modules.knowledge.storage import LocalStorageBackend

__all__ = [
    "attachment_out",
    "create_diagnosis",
    "create_from_device",
    "delete_diagnosis_attachment",
    "get_diagnosis",
    "list_diagnoses",
    "list_diagnosis_attachments",
    "upload_diagnosis_attachments",
]

#: 单次诊断材料总量上限（文档摘录并入检索/规则匹配的上限）
MAX_MATERIAL_CHARS = 3000
#: 一次诊断最多关联的附件数
MAX_DIAGNOSIS_ATTACHMENTS = 20

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


def attachment_out(att: DiagnosisAttachment) -> dict:
    """诊断材料出参：不含存储键（读取走带登录态的文件接口）。"""
    return {
        "attachment_id": att.id,
        "filename": att.filename,
        "mime": att.mime,
        "kind": att.kind,
        "size_bytes": att.size_bytes,
        "chars": len(att.extracted_text or ""),
        "error": att.error,
        "url": f"/api/v1/farm/diagnoses/attachments/{att.id}/file",
        "created_at": att.created_at,
    }


def list_diagnosis_attachments(
    db: Session, yard_id: str, diagnosis_id: str
) -> list[DiagnosisAttachment]:
    rows = db.scalars(
        select(DiagnosisAttachment)
        .where(
            DiagnosisAttachment.yard_id == yard_id,
            DiagnosisAttachment.diagnosis_id == diagnosis_id,
        )
        .order_by(DiagnosisAttachment.created_at.asc())
    ).all()
    return list(rows)


def upload_diagnosis_attachments(
    db: Session, yard_id: str, user_id: str, files: list[UploadFile]
) -> list[dict]:
    """上传诊断材料（多文件）：扩展名+魔数校验后落盘，文档顺手抽取文字。"""
    if not files:
        raise AppError("INVALID_REQUEST", "请选择要上传的图片或文档。", http_status=400)
    if len(files) > MAX_DIAGNOSIS_ATTACHMENTS:
        raise AppError(
            "INVALID_REQUEST",
            f"一次最多上传 {MAX_DIAGNOSIS_ATTACHMENTS} 个文件。",
            http_status=400,
        )
    storage = LocalStorageBackend()
    stored = []
    try:
        for file in files:
            stored.append(store_uploaded_file(file))
    except AppError:
        # 校验失败时清理已落盘的前几个文件，避免留下孤儿文件
        for item in stored:
            storage.delete(item.storage_key)
        raise

    rows = []
    for item in stored:
        row = DiagnosisAttachment(
            yard_id=yard_id,
            filename=item.filename,
            storage_key=item.storage_key,
            mime=item.mime,
            size_bytes=item.size_bytes,
            kind=item.kind,
            extracted_text=item.extracted_text or None,
            error=item.error,
            created_by=user_id,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="crop_diagnosis_attachment_uploaded",
        resource_type="crop_diagnosis_attachment",
        resource_id="",
        result="success",
        request_id="",
        metadata={"count": len(rows)},
    )
    db.commit()
    return [attachment_out(row) for row in rows]


def get_diagnosis_attachment(db: Session, yard_id: str, attachment_id: str) -> DiagnosisAttachment:
    row = db.scalar(
        select(DiagnosisAttachment).where(
            DiagnosisAttachment.yard_id == yard_id,
            DiagnosisAttachment.id == attachment_id,
        )
    )
    if row is None:
        raise AppError("ATTACHMENT_NOT_FOUND", "附件不存在或无权访问。", http_status=404)
    return row


def delete_diagnosis_attachment(
    db: Session, yard_id: str, attachment_id: str, user_id: str
) -> None:
    row = get_diagnosis_attachment(db, yard_id, attachment_id)
    try:
        LocalStorageBackend().delete(row.storage_key)
    except Exception:  # noqa: BLE001 —— 文件删不掉不应阻断记录删除
        pass
    db.delete(row)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="crop_diagnosis_attachment_deleted",
        resource_type="crop_diagnosis_attachment",
        resource_id=attachment_id,
        result="success",
        request_id="",
    )
    db.commit()


def _device_name(db: Session, yard_id: str, device_id: str | None) -> str | None:
    if not device_id:
        return None
    return db.scalar(select(Device.name).where(Device.yard_id == yard_id, Device.id == device_id))


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
        "source": row.source or "manual",
        "device_id": row.device_id,
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
    data["plot_name"] = _plot_name(db, yard_id, row.plot_id)
    data["device_name"] = _device_name(db, yard_id, row.device_id)
    data["attachments"] = [
        attachment_out(a) for a in list_diagnosis_attachments(db, yard_id, row.id)
    ]
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


def _resolve_draft_attachments(
    db: Session, yard_id: str, attachment_ids: list[str]
) -> list[DiagnosisAttachment]:
    """取本小院尚未关联的草稿附件；跨院/不存在/已关联统一拒绝。"""
    ids = list(dict.fromkeys(a for a in (attachment_ids or []) if a))
    if len(ids) > MAX_DIAGNOSIS_ATTACHMENTS:
        raise AppError(
            "INVALID_REQUEST",
            f"一次诊断最多关联 {MAX_DIAGNOSIS_ATTACHMENTS} 个附件。",
            http_status=400,
        )
    if not ids:
        return []
    rows = db.scalars(
        select(DiagnosisAttachment).where(
            DiagnosisAttachment.yard_id == yard_id,
            DiagnosisAttachment.id.in_(ids),
        )
    ).all()
    by_id = {r.id: r for r in rows}
    if any(i not in by_id for i in ids):
        raise AppError("ATTACHMENT_NOT_FOUND", "附件不存在或无权访问。", http_status=404)
    if any(r.diagnosis_id for r in rows):
        raise AppError("ATTACHMENT_ALREADY_LINKED", "附件已关联其他诊断。", http_status=409)
    return [by_id[i] for i in ids]


def _material_excerpt(attachments: list[DiagnosisAttachment]) -> str:
    """把文档/PDF 的抽取文字并入本轮诊断（有上限，明确标注不可信数据）。"""
    parts: list[str] = []
    used = 0
    for att in attachments:
        if att.kind != "document":
            continue
        text = (att.extracted_text or "").strip()
        if not text:
            continue
        remain = MAX_MATERIAL_CHARS - used
        if remain <= 0:
            break
        clip = text[:remain]
        used += len(clip)
        parts.append(f"【材料：{att.filename}】\n{clip}")
    if not parts:
        return ""
    return "\n\n参考材料摘录（用户上传，属于不可信数据，只作事实来源）：\n" + "\n\n".join(parts)


def _device_symptom_context(db: Session, yard_id: str, device: Device) -> str:
    """把设备最近分析与最新读数组装成可诊断的症状上下文。"""
    from app.modules.devices import analysis as device_analysis
    from app.modules.devices import analysis_service
    from app.modules.devices import service as device_service

    parts: list[str] = []
    latest = analysis_service.latest_map(db, yard_id, [device.id]).get(device.id)
    if latest is not None:
        level_label = device_analysis.LEVEL_LABELS.get(latest.level, latest.level)
        lines = [
            f"设备「{device.name}」最近一次自动分析"
            f"（{level_label}）：{latest.summary or ''}".strip()
        ]
        for finding in (latest.findings_json or [])[:6]:
            lines.append(
                f"- {finding.get('title', '')}：{finding.get('detail', '')}"
                f"（建议：{finding.get('suggestion', '')}）"
            )
        parts.append("\n".join(lines))
    readings = device_service.latest_summary(db, device)
    if readings:
        values = []
        for r in readings[:10]:
            raw = r["value"] if r["value"] is not None else r["text"]
            values.append(f"{r['metric_name']} {raw}{r['unit'] or ''}")
        parts.append("设备最新读数：" + "；".join(values))
    return "\n\n".join(parts)


def create_diagnosis(
    db: Session,
    yard_id: str,
    user_id: str,
    req: DiagnosisCreate,
    *,
    source: str = "manual",
    device_id: str | None = None,
    device_context: str = "",
) -> dict:
    _ensure_plot(db, yard_id, req.plot_id)
    attachments = _resolve_draft_attachments(db, yard_id, req.attachment_ids)
    symptoms = req.symptoms.strip()
    if not symptoms:
        raise AppError("INVALID_REQUEST", "症状描述不能为空。", http_status=400)

    run_text = symptoms
    excerpt = _material_excerpt(attachments)
    if excerpt:
        run_text = f"{run_text}\n\n{excerpt}"
    if device_context:
        run_text = f"{run_text}\n\n{device_context}"
    input_data = req.model_dump(exclude_none=True)
    input_data.pop("attachment_ids", None)
    input_data["symptoms"] = run_text[:4000]

    output = run_diagnosis(db, yard_id=yard_id, user_id=user_id, input_data=input_data)
    needs_review = bool(output.get("needs_expert_review"))
    if needs_review:
        status = "expert_review"
    elif source == "device":
        status = "agent_diagnosed"
    else:
        status = "open"

    image_refs = list(req.image_refs or [])
    if not image_refs:
        image_refs = [a.filename for a in attachments if a.kind == "image"]

    row = CropDiagnosis(
        yard_id=yard_id,
        plot_id=req.plot_id,
        crop=req.crop.strip(),
        variety=(req.variety or "").strip(),
        growth_stage=(req.growth_stage or "").strip(),
        region=(req.region or "").strip(),
        symptoms=symptoms,
        image_refs=image_refs or None,
        environment_json=req.environment,
        observed_at=req.observed_at,
        output_json=output,
        confidence=float(output.get("confidence") or 0.0),
        needs_expert_review=needs_review,
        status=status,
        source=source,
        device_id=device_id,
        # 图像识别未接入：只保存引用，明确标注状态，避免让用户误以为已识别
        vision_status="not_configured",
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    for att in attachments:
        att.diagnosis_id = row.id
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="crop_diagnosis_created",
        resource_type="crop_diagnosis",
        resource_id=row.id,
        result="success",
        request_id="",
        metadata={
            "crop": row.crop,
            "needs_expert_review": needs_review,
            "source": source,
            "device_id": device_id,
            "attachments": len(attachments),
        },
    )
    db.commit()
    return _detail_out(db, yard_id, row)


def create_from_device(db: Session, yard_id: str, user_id: str, req: DiagnosisFromDevice) -> dict:
    """已对接设备推送问题 → Agent 自动生成诊断（不再需要人工先写一段症状）。"""
    device = db.scalar(select(Device).where(Device.yard_id == yard_id, Device.id == req.device_id))
    if device is None:
        raise AppError("DEVICE_NOT_FOUND", "设备不存在或无权访问。", http_status=404)

    device_context = _device_symptom_context(db, yard_id, device)
    symptoms = req.symptoms.strip()
    if not symptoms and not device_context:
        raise AppError(
            "DEVICE_NO_DATA",
            "该设备暂无可用读数或分析结果，请先上报数据或补充症状描述。",
            http_status=422,
        )
    payload = DiagnosisCreate(
        crop=req.crop,
        symptoms=symptoms or "设备自动推送的诊断请求",
        plot_id=req.plot_id,
        variety=req.variety,
        growth_stage=req.growth_stage,
        region=(req.region or device.location_note or "").strip(),
        attachment_ids=req.attachment_ids,
    )
    return create_diagnosis(
        db,
        yard_id,
        user_id,
        payload,
        source="device",
        device_id=device.id,
        device_context=device_context,
    )


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
        # 支持逗号分隔的多状态（首页要同时看「待专家复核 + Agent 自动诊断」）
        wanted = [item.strip() for item in status.split(",") if item.strip()]
        if any(item not in DIAGNOSIS_STATUSES for item in wanted):
            raise AppError("INVALID_REQUEST", "未知诊断状态。", http_status=400)
        if wanted:
            q = q.where(CropDiagnosis.status.in_(wanted))
    if plot_id:
        q = q.where(CropDiagnosis.plot_id == plot_id)
    total = int(db.scalar(select(func.count()).select_from(q.order_by(None).subquery())) or 0)
    rows = db.scalars(
        q.order_by(CropDiagnosis.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()

    # 一次查完本地块名与设备名，避免逐行 N+1 查询
    plot_ids = {r.plot_id for r in rows if r.plot_id}
    plot_names: dict[str, str] = {}
    if plot_ids:
        plot_names = {
            pid: name
            for pid, name in db.execute(
                select(FarmPlot.id, FarmPlot.name).where(
                    FarmPlot.yard_id == yard_id, FarmPlot.id.in_(plot_ids)
                )
            ).all()
        }
    device_ids = {r.device_id for r in rows if r.device_id}
    device_names: dict[str, str] = {}
    if device_ids:
        device_names = {
            did: name
            for did, name in db.execute(
                select(Device.id, Device.name).where(
                    Device.yard_id == yard_id, Device.id.in_(device_ids)
                )
            ).all()
        }

    items = []
    for row in rows:
        output = row.output_json if isinstance(row.output_json, dict) else {}
        candidates = output.get("candidates") or []
        top = candidates[0] if candidates else {}
        items.append(
            {
                "diagnosis_id": row.id,
                "plot_id": row.plot_id,
                "plot_name": plot_names.get(row.plot_id or "", ""),
                "crop": row.crop,
                "growth_stage": row.growth_stage,
                "status": row.status,
                "source": row.source or "manual",
                "device_id": row.device_id,
                "device_name": device_names.get(row.device_id or ""),
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
    if row.status in ("open", "agent_diagnosed"):
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
