"""devices HTTP 接口。

权限：读=小院成员（get_yard_context）；写=yard_admin（platform_admin 直通）。
ingest 为设备侧通道：不走登录态，用 X-Device-Token/body.token 的 sha256 比对，
禁用 403、令牌错 401、超频 429。

注意路由顺序：/devices/alerts、/devices/alert-rules、/devices/capabilities
必须先于 /devices/{device_id} 注册（FastAPI 按声明顺序匹配）。
"""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.deps import YardContext, get_db_session, get_yard_context
from app.core.errors import AppError
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.devices import analysis_service, connector, service
from app.modules.devices.metrics import METRICS
from app.modules.devices.models import Device, DeviceAlert, DeviceAlertRule, Reading
from app.modules.devices.schemas import (
    AlertOut,
    DeviceCreate,
    DevicePatch,
    IngestBody,
    RuleCreate,
    RuleOut,
    RulePatch,
)

router = APIRouter(prefix="/devices", tags=["devices"])

_ADMIN_ROLES = {"platform_admin", "yard_admin"}


def _member(ctx: YardContext = Depends(get_yard_context)) -> YardContext:
    return ctx


def _admin(ctx: YardContext = Depends(get_yard_context)) -> YardContext:
    if not set(ctx.roles) & _ADMIN_ROLES:
        raise AppError("FORBIDDEN", "该操作需要小院管理员权限。", http_status=403)
    return ctx


def _iso(dt: datetime | None) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat()


def _audit(ctx: YardContext, db: Session, action: str, resource_id: str | None, meta: dict) -> None:
    log_audit(
        db,
        yard_id=ctx.yard_id,
        actor_id=ctx.user_id,
        action=action,
        resource_type="device",
        resource_id=resource_id,
        result="success",
        request_id=ctx.request_id,
        metadata=meta,
    )


def _device_out(db: Session, device: Device) -> dict[str, Any]:
    return service.device_to_dict(device, connector.config_to_out(device.connector_config_json))


# ---------------------------------------------------------------------------
# 能力字典（前端渲染用）
# ---------------------------------------------------------------------------


@router.get("/capabilities", response_model=None)
def capabilities(ctx: YardContext = Depends(_member)):
    return ok(
        {
            "metrics": [{"code": m.code, "name": m.name, "unit": m.unit} for m in METRICS.values()],
            "device_types": [
                {"value": "weather", "name": "气象站"},
                {"value": "soil", "name": "墒情"},
                {"value": "pest", "name": "虫情"},
                {"value": "greenhouse", "name": "棚室"},
                {"value": "other", "name": "其他"},
            ],
            "source_types": [
                {"value": "webhook", "name": "设备推送（token 鉴权）"},
                {"value": "connector", "name": "平台定时拉取（自定义连接器）"},
            ],
            "ops": [
                {"value": "lt", "name": "低于阈值"},
                {"value": "gt", "name": "高于阈值"},
            ],
        },
    )


@router.get("/insights", response_model=None)
def device_insights(
    ctx: YardContext = Depends(_member),
    db: Session = Depends(get_db_session),
    limit: int = Query(default=10, ge=1, le=50),
):
    """概览卡：只回需要人处理的设备发现（watch/warning/critical）。"""
    return ok(analysis_service.insights(db, ctx.yard_id, limit=limit))


# ---------------------------------------------------------------------------
# 告警查询（先于 /{device_id} 注册）
# ---------------------------------------------------------------------------


@router.get("/alerts", response_model=None)
def list_alerts(
    ctx: YardContext = Depends(_member),
    db: Session = Depends(get_db_session),
    device_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
):
    stmt = select(DeviceAlert).where(DeviceAlert.yard_id == ctx.yard_id)
    if device_id:
        stmt = stmt.where(DeviceAlert.device_id == device_id)
    rows = list(db.scalars(stmt.order_by(DeviceAlert.triggered_at.desc()).limit(limit)))
    names = {d.id: d.name for d in db.scalars(select(Device).where(Device.yard_id == ctx.yard_id))}
    items = []
    for a in rows:
        info = METRICS.get(a.metric)
        items.append(
            AlertOut(
                id=a.id,
                yard_id=a.yard_id,
                rule_id=a.rule_id,
                device_id=a.device_id,
                device_name=names.get(a.device_id),
                metric=a.metric,
                metric_name=info.name if info else a.metric,
                value=a.value,
                triggered_at=_iso(a.triggered_at),
                notified_text=a.notified_text,
                workorder_id=a.workorder_id,
                # 展示编号=工单任务 id（平台无独立工单序列表）
                workorder_no=a.workorder_id,
            ).model_dump()
        )
    return ok(items)


@router.post("/alerts/{alert_id}/create-workorder", response_model=None)
def create_alert_workorder(
    alert_id: str,
    ctx: YardContext = Depends(_member),
    db: Session = Depends(get_db_session),
):
    """告警一键开工单（小院全员可用；工单执行仍过高危审批闸门）。

    幂等：已开过工单的告警直接返回既有工单号（created=false），不重复创建。
    跨小院与不存在统一 404（不泄露其他小院告警是否存在）。
    """
    alert = db.get(DeviceAlert, alert_id)
    if alert is None or alert.yard_id != ctx.yard_id:
        raise AppError("NOT_FOUND", "告警不存在。", http_status=404)
    existed = bool(alert.workorder_id)
    task = service.create_alert_workorder(db, ctx, alert)
    _audit(
        ctx,
        db,
        "alert_create_workorder",
        alert.id,
        {"workorder_id": task.id, "existed": existed, "metric": alert.metric},
    )
    if not existed:
        # W4 事件推送：新开工单才推（幂等重放不轰炸）；dispatch 自带异常护栏
        from app.modules.push import service as push_service

        push_service.dispatch(
            db,
            yard_id=ctx.yard_id,
            event_type="workorder_created",
            payload={
                "title": f"工单 {task.id} 已创建并进入处理队列",
                "workorder_no": task.id,
                "alert_id": alert.id,
            },
            request_id=ctx.request_id,
        )
    db.commit()
    return ok(
        {
            "workorder_id": task.id,
            "workorder_no": task.id,
            "status": task.status,
            "created": not existed,
            "link": "/tasks",
            "message": (
                f"工单 {task.id} 已存在，未重复创建。"
                if existed
                else f"工单 {task.id} 已创建并进入处理队列。"
            ),
        }
    )


# ---------------------------------------------------------------------------
# 告警规则 CRUD
# ---------------------------------------------------------------------------


def _rule_out(r: DeviceAlertRule) -> dict[str, Any]:
    return RuleOut(
        id=r.id,
        yard_id=r.yard_id,
        device_id=r.device_id,
        metric=r.metric,
        op=r.op,
        threshold=r.threshold,
        enabled=r.enabled,
        notify_note=r.notify_note,
        created_at=_iso(r.created_at),
        updated_at=_iso(r.updated_at),
    ).model_dump()


@router.get("/alert-rules", response_model=None)
def list_rules(ctx: YardContext = Depends(_member), db: Session = Depends(get_db_session)):
    rows = list(
        db.scalars(
            select(DeviceAlertRule)
            .where(DeviceAlertRule.yard_id == ctx.yard_id)
            .order_by(DeviceAlertRule.created_at)
        )
    )
    return ok([_rule_out(r) for r in rows])


@router.post("/alert-rules", response_model=None)
def create_rule(
    payload: RuleCreate,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    if payload.metric not in METRICS:
        raise AppError(
            "INVALID_REQUEST",
            "规则指标必须是内置字典中的 code（见 /devices/capabilities）。",
            http_status=400,
        )
    if payload.device_id:
        service.get_device_or_404(db, ctx.yard_id, payload.device_id)  # 越权/不存在统一 404
    rule = DeviceAlertRule(
        yard_id=ctx.yard_id,
        device_id=payload.device_id,
        metric=payload.metric,
        op=payload.op,
        threshold=payload.threshold,
        enabled=payload.enabled,
        notify_note=payload.notify_note,
    )
    db.add(rule)
    db.flush()
    _audit(ctx, db, "alert_rule_create", rule.id, {"metric": rule.metric, "op": rule.op})
    db.commit()
    return ok(_rule_out(rule))


@router.patch("/alert-rules/{rule_id}", response_model=None)
def patch_rule(
    rule_id: str,
    payload: RulePatch,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    rule = db.get(DeviceAlertRule, rule_id)
    if rule is None or rule.yard_id != ctx.yard_id:
        raise AppError("NOT_FOUND", "告警规则不存在。", http_status=404)
    changes = payload.model_dump(exclude_unset=True)
    if "metric" in changes and changes["metric"] not in METRICS:
        raise AppError("INVALID_REQUEST", "规则指标必须是内置字典中的 code。", http_status=400)
    for field, value in changes.items():
        setattr(rule, field, value)
    _audit(ctx, db, "alert_rule_update", rule.id, {"fields": sorted(changes)})
    db.commit()
    return ok(_rule_out(rule))


@router.delete("/alert-rules/{rule_id}", response_model=None)
def delete_rule(
    rule_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    rule = db.get(DeviceAlertRule, rule_id)
    if rule is None or rule.yard_id != ctx.yard_id:
        raise AppError("NOT_FOUND", "告警规则不存在。", http_status=404)
    db.delete(rule)
    _audit(ctx, db, "alert_rule_delete", rule_id, {})
    db.commit()
    return ok({"deleted": rule_id})


# ---------------------------------------------------------------------------
# 设备 CRUD（管理端）
# ---------------------------------------------------------------------------


@router.get("", response_model=None)
def list_devices(
    ctx: YardContext = Depends(_member),
    db: Session = Depends(get_db_session),
    enabled: bool | None = None,
):
    stmt = select(Device).where(Device.yard_id == ctx.yard_id)
    if enabled is not None:
        stmt = stmt.where(Device.enabled.is_(enabled))
    rows = list(db.scalars(stmt.order_by(Device.created_at.desc())))
    latest = analysis_service.latest_map(db, ctx.yard_id, [d.id for d in rows])
    items = []
    for device in rows:
        out = _device_out(db, device)
        out["latest_analysis"] = analysis_service.brief(latest.get(device.id))
        items.append(out)
    return ok(items)


@router.post("", response_model=None)
def create_device(
    payload: DeviceCreate,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    payload.validate_shape()
    dup = db.scalars(
        select(Device.id).where(Device.yard_id == ctx.yard_id, Device.name == payload.name)
    ).first()
    if dup:
        raise AppError("DUPLICATE_NAME", "同小院下已存在同名设备。", http_status=409)
    device = Device(
        yard_id=ctx.yard_id,
        name=payload.name,
        device_type=payload.device_type,
        source_type=payload.source_type,
        model_hint=payload.model_hint,
        longitude=payload.longitude,
        latitude=payload.latitude,
        location_note=payload.location_note,
        created_by=ctx.user_id,
    )
    device_token: str | None = None
    if payload.source_type == "webhook":
        device_token, device.token_hash = service.issue_device_token()
    else:
        assert payload.connector is not None  # validate_shape 已保证
        device.connector_config_json = connector.prepare_config(payload.connector)
    db.add(device)
    db.flush()
    _audit(
        ctx,
        db,
        "device_create",
        device.id,
        {"name": device.name, "source_type": device.source_type},
    )
    db.commit()
    out = _device_out(db, device)
    if device_token is not None:
        # 一次性明文 token：仅本次响应出现，之后只能 rotate
        out["device_token"] = device_token
    return ok(out)


@router.get("/{device_id}", response_model=None)
def get_device(
    device_id: str,
    ctx: YardContext = Depends(_member),
    db: Session = Depends(get_db_session),
):
    device = service.get_device_or_404(db, ctx.yard_id, device_id)
    out = _device_out(db, device)
    out["latest"] = service.latest_summary(db, device)
    out["latest_analysis"] = analysis_service.brief(
        analysis_service.latest_map(db, ctx.yard_id, [device.id]).get(device.id)
    )
    return ok(out)


@router.patch("/{device_id}", response_model=None)
def patch_device(
    device_id: str,
    payload: DevicePatch,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    device = service.get_device_or_404(db, ctx.yard_id, device_id)
    changes = payload.model_dump(exclude_unset=True)
    connector_cfg = changes.pop("connector", None)
    if connector_cfg is not None:
        if device.source_type != "connector":
            raise AppError(
                "INVALID_REQUEST", "仅 connector 设备可更新连接器配置。", http_status=400
            )
        from app.modules.devices.schemas import ConnectorConfigIn

        device.connector_config_json = connector.prepare_config(ConnectorConfigIn(**connector_cfg))
        # 换配置视为重新开始：失败计数清零，手动禁用过的保持禁用需另行启用
        device.consecutive_failures = 0
        device.last_error = None
        device.last_polled_at = None
    for field, value in changes.items():
        if field == "name" and value != device.name:
            dup = db.scalars(
                select(Device.id).where(
                    Device.yard_id == ctx.yard_id, Device.name == value, Device.id != device.id
                )
            ).first()
            if dup:
                raise AppError("DUPLICATE_NAME", "同小院下已存在同名设备。", http_status=409)
        setattr(device, field, value)
    updated_fields = sorted([*changes, *(["connector"] if connector_cfg else [])])
    _audit(ctx, db, "device_update", device.id, {"fields": updated_fields})
    db.commit()
    return ok(_device_out(db, device))


@router.delete("/{device_id}", response_model=None)
def delete_device(
    device_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    """删除设备：级联删除其 readings / 告警 / 指向它的规则（决策：硬删，不做 90 天保留）。

    理由：readings 是设备生命周期附属遥测，设备删除后无独立查询场景；平台无归档
    设施，保留孤儿数据只会造成跨小院重名困惑。需要历史留存的应先导出 readings。
    """
    device = service.get_device_or_404(db, ctx.yard_id, device_id)
    db.execute(delete(Reading).where(Reading.device_id == device.id))
    db.execute(delete(DeviceAlert).where(DeviceAlert.device_id == device.id))
    db.execute(delete(DeviceAlertRule).where(DeviceAlertRule.device_id == device.id))
    db.delete(device)
    _audit(ctx, db, "device_delete", device_id, {"name": device.name})
    db.commit()
    return ok({"deleted": device_id})


@router.post("/{device_id}/rotate-token", response_model=None)
def rotate_token(
    device_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    device = service.get_device_or_404(db, ctx.yard_id, device_id)
    if not device.token_hash:
        raise AppError("INVALID_REQUEST", "该设备未启用 webhook 令牌。", http_status=400)
    plain, device.token_hash = service.issue_device_token()
    _audit(ctx, db, "device_rotate_token", device.id, {})
    db.commit()
    return ok({"device_token": plain})


@router.get("/{device_id}/readings", response_model=None)
def device_readings(
    device_id: str,
    metric: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    ctx: YardContext = Depends(_member),
    db: Session = Depends(get_db_session),
):
    device = service.get_device_or_404(db, ctx.yard_id, device_id)
    rows = service.list_readings(
        db,
        device,
        metric=metric,
        start=service.parse_reported_at(start) if start else None,
        end=service.parse_reported_at(end) if end else None,
        limit=limit,
    )
    items = [
        {
            "id": r.id,
            "metric": r.metric,
            "value": r.value,
            "text": r.text,
            "unit": r.unit,
            "raw_json": r.raw_json,
            "collected_at": _iso(r.collected_at),
        }
        for r in reversed(rows)  # 时序接口按时间升序返回
    ]
    return ok(items)


@router.get("/{device_id}/analyses", response_model=None)
def list_device_analyses(
    device_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    ctx: YardContext = Depends(_member),
    db: Session = Depends(get_db_session),
):
    device = service.get_device_or_404(db, ctx.yard_id, device_id)
    return ok(analysis_service.list_analyses(db, ctx.yard_id, device.id, limit=limit))


@router.post("/{device_id}/analyses", response_model=None)
def run_device_analysis(
    device_id: str,
    ctx: YardContext = Depends(_admin),
    db: Session = Depends(get_db_session),
):
    """立即分析（管理员）：规则结论必出，LLM 解释受设置与每日预算约束。

    同一天同一设备重复运行幂等返回既有结果（created=false）。
    设备近期没有任何读数时返回 422 DEVICE_ANALYSIS_INSUFFICIENT_DATA。
    """
    device = service.get_device_or_404(db, ctx.yard_id, device_id)
    row, created = analysis_service.run_analysis(
        db,
        device,
        trigger="manual",
        actor_id=ctx.user_id,
        request_id=ctx.request_id,
    )
    db.commit()
    return ok({**analysis_service.analysis_to_dict(db, row), "created": created})


# ---------------------------------------------------------------------------
# 设备侧推送（无登录态；X-Device-Token 或 body.token）
# ---------------------------------------------------------------------------


@router.post("/{device_id}/ingest", response_model=None)
def ingest(
    device_id: str,
    payload: IngestBody,
    request: Request,
    db: Session = Depends(get_db_session),
):
    """设备数据上报：token 错 401 / 设备禁用 403 / 超频 429。

    小院边界：device_id 本身即资源定位，token 与 token_hash 比对是唯一鉴权面。
    """
    device = db.get(Device, device_id)
    if device is None:
        raise AppError("NOT_FOUND", "设备不存在。", http_status=404)
    token = request.headers.get("X-Device-Token") or payload.token
    if not service.token_matches(device, token):
        raise AppError("UNAUTHORIZED", "设备令牌无效。", http_status=401)
    field_map = (device.connector_config_json or {}).get("field_map")
    result = service.ingest_values(
        db,
        device,
        payload.data,
        reported_at=payload.reported_at,
        field_map=field_map if isinstance(field_map, dict) else None,
        request_id=getattr(request.state, "request_id", "req_unknown"),
        source="webhook",
    )
    db.commit()
    return ok(result)
