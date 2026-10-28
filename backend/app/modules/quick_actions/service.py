"""一句话业务：预览与确认。

- 预览零副作用：只解析，不写库。
- 确认必须带预览返回的指纹；确认后统一走既有服务层（权限、校验、审计、乐观锁）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import date
from typing import Any

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.farm import diagnosis_service, workorder_service
from app.modules.farm import service as farm_service
from app.modules.farm.schemas import (
    DiagnosisCreate,
    PlotCreate,
    RecordCreate,
    WorkOrderCreate,
    WorkOrderUpdate,
)
from app.modules.quick_actions import parser
from app.modules.quick_actions.schemas import QuickActionConfirmIn, QuickActionPreviewIn

ALLOWED_ACTIONS = parser.MODULE_ACTIONS


def _canonical(value: Any) -> Any:
    """指纹归一化：12 与 12.0 必须同值（浏览器 JSON 会把 12.0 读成 12）。"""
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int | float):
        number = float(value)
        return int(number) if number.is_integer() else round(number, 6)
    if isinstance(value, dict):
        return {str(key): _canonical(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(item) for item in value]
    return str(value)


def fingerprint(module: str, action: str, payload: dict[str, Any]) -> str:
    raw = json.dumps(
        {"module": module, "action": action, "payload": _canonical(payload)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def preview(
    db: Session,
    *,
    yard_id: str,
    user_id: str,
    request_id: str,
    req: QuickActionPreviewIn,
) -> dict[str, Any]:
    result = parser.parse(
        db,
        yard_id=yard_id,
        user_id=user_id,
        request_id=request_id,
        module=req.module,
        text=req.text,
        plot_id=req.plot_id,
        today=req.today,
    )
    action = str(result["action"])
    if action not in ALLOWED_ACTIONS[req.module]:
        raise AppError("QUICK_ACTION_UNSUPPORTED", "这句话暂不支持直接办理。", 422)
    result["module"] = req.module
    result["fingerprint"] = fingerprint(req.module, action, result["payload"])
    result["ready"] = not result["missing"]
    return result


def _validation_error(action: str, exc: ValidationError) -> AppError:
    fields = [
        {
            "field": ".".join(str(part) for part in err["loc"]),
            "message": err["msg"],
        }
        for err in exc.errors()
    ]
    return AppError(
        "QUICK_ACTION_AMBIGUOUS",
        "还缺必要信息或内容不合法，请补全后再确认。",
        422,
        {"action": action, "fields": fields},
    )


def _require_ready(action: str, payload: dict[str, Any]) -> None:
    """预览标记未就绪时，确认端也不能落库（前端跳过确认仍要拦得住）。"""
    missing: list[str] = []
    if action == "plot.create":
        if not str(payload.get("name") or "").strip():
            missing.append("地块名称")
        if not payload.get("area_mu"):
            missing.append("面积")
    elif action == "record.create":
        if not payload.get("plot_id"):
            missing.append("地块")
        if str(payload.get("kind") or "") in ("", "other"):
            missing.append("农事类型")
    elif action == "diagnosis.create":
        if not str(payload.get("crop") or "").strip():
            missing.append("作物")
        if len(str(payload.get("symptoms") or "").strip()) < 4:
            missing.append("症状描述")
    elif action == "alert.convert":
        if not payload.get("alert_id"):
            missing.append("告警")
    elif action == "work_order.create":
        if not str(payload.get("title") or "").strip():
            missing.append("工单标题")
    if missing:
        raise AppError(
            "QUICK_ACTION_AMBIGUOUS",
            f"还缺：{'、'.join(missing)}，请补全后再确认。",
            422,
            {"action": action, "missing": missing},
        )


def _summary_created(action: str, created: dict[str, Any]) -> str:
    if action == "plot.create":
        return f"已新增地块「{created.get('name')}」"
    if action == "record.create":
        kind = created.get("kind_label") or "农事记录"
        return f"已在「{created.get('plot_name')}」记录{kind}"
    if action == "diagnosis.create":
        return f"已为「{created.get('crop')}」登记诊断单"
    if action == "alert.convert":
        return f"已把告警转成工单「{created.get('title')}」"
    return f"已创建工单「{created.get('title')}」"


def _confirm_alert(
    db: Session,
    *,
    yard_id: str,
    user_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    alert_id = str(payload.get("alert_id") or "")
    if not alert_id:
        raise AppError("QUICK_ACTION_AMBIGUOUS", "缺少要转换的告警。", 422)
    created = workorder_service.convert_alert(db, yard_id, alert_id, user_id)
    changes: dict[str, Any] = {}
    assignee_id = payload.get("assignee_id")
    if assignee_id:
        changes["assignee_id"] = str(assignee_id)
    due_on = payload.get("due_on")
    if due_on:
        try:
            changes["due_on"] = date.fromisoformat(str(due_on))
        except ValueError as e:
            raise AppError("QUICK_ACTION_AMBIGUOUS", "到期日期格式不正确。", 422) from e
    priority = str(payload.get("priority") or "")
    if priority and priority != created.get("priority"):
        changes["priority"] = priority
    if changes:
        created = workorder_service.update_work_order(
            db,
            yard_id,
            str(created["work_order_id"]),
            user_id,
            WorkOrderUpdate(**changes),
        )
    return created


def confirm(
    db: Session,
    *,
    yard_id: str,
    user_id: str,
    request_id: str,
    req: QuickActionConfirmIn,
) -> dict[str, Any]:
    if req.action not in ALLOWED_ACTIONS[req.module]:
        raise AppError("QUICK_ACTION_UNSUPPORTED", "该动作暂不支持。", 422)
    expected = fingerprint(req.module, req.action, req.payload)
    if not hmac.compare_digest(expected, req.fingerprint):
        raise AppError(
            "QUICK_ACTION_STALE",
            "预览内容已变化或过期，请重新解析后再确认。",
            409,
        )

    action = req.action
    _require_ready(action, req.payload)
    try:
        if action == "plot.create":
            created = farm_service.create_plot(db, yard_id, user_id, PlotCreate(**req.payload))
            resource_type, resource_id = "farm_plot", created["plot_id"]
        elif action == "record.create":
            created = farm_service.create_record(db, yard_id, user_id, RecordCreate(**req.payload))
            resource_type, resource_id = "farm_record", created["record_id"]
        elif action == "diagnosis.create":
            created = diagnosis_service.create_diagnosis(
                db, yard_id, user_id, DiagnosisCreate(**req.payload)
            )
            resource_type, resource_id = "farm_diagnosis", created["diagnosis_id"]
        elif action == "alert.convert":
            created = _confirm_alert(db, yard_id=yard_id, user_id=user_id, payload=req.payload)
            resource_type, resource_id = "work_order", created["work_order_id"]
        else:
            created = workorder_service.create_work_order(
                db, yard_id, user_id, WorkOrderCreate(**req.payload)
            )
            resource_type, resource_id = "work_order", created["work_order_id"]
    except ValidationError as e:
        raise _validation_error(action, e) from e

    summary = _summary_created(action, created)
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="quick_action_confirmed",
        resource_type=resource_type,
        resource_id=resource_id,
        result="success",
        request_id=request_id,
        metadata={
            "module": req.module,
            "quick_action": action,
            "fingerprint": req.fingerprint,
        },
    )
    db.commit()
    return {
        "module": req.module,
        "action": action,
        "created": created,
        "summary": summary,
    }
