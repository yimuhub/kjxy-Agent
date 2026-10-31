"""官方模型通道路由（v3.33）：``/llm/official/*``。

- 默认关闭：``GET /official/status`` 恒可用（200 + enabled=false），其余接口在
  没有启用中的官方通道时统一 503 ``OFFICIAL_CHANNEL_DISABLED``；
- 试用/下单/权益只允许 platform_admin/admin/yard_admin/yard_owner；
- 支付回调不要求登录，但必须通过 HMAC-SHA256 验签 + ±300 秒时间窗 + event_id 幂等；
- 所有写操作记审计，metadata 不含密钥。
"""

import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.deps import CurrentUser, get_db_session, require_roles
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.responses import ok
from app.modules.audit.service import log_audit
from app.modules.llm import official_service
from app.modules.llm.supplier_schemas import (
    OfficialOrderCreate,
    OfficialTrialBody,
)

log = get_logger(__name__)

router = APIRouter(tags=["llm-official"])

_admin_only = require_roles("platform_admin", "admin", "yard_admin", "yard_owner")


def _actor(
    user: CurrentUser = Depends(_admin_only),
) -> CurrentUser:
    if not user.yard_id:
        raise AppError("YARD_REQUIRED", "官方模型通道需要小院上下文。", http_status=400)
    return user


def _audit(db: Session, user: CurrentUser, action: str, resource_id: str | None, meta: dict):
    log_audit(
        db,
        yard_id=user.yard_id,
        actor_id=user.user_id,
        action=action,
        resource_type="official_model",
        resource_id=resource_id,
        result="success",
        request_id=user.request_id,
        metadata=meta,
    )


@router.get("/official/status", response_model=None)
def official_status(
    user: CurrentUser = Depends(_admin_only),
    db=Depends(get_db_session),
):
    """通道探针：未开通也返回 200，前端据此显示「未开通」而不是错误页。"""
    return ok(official_service.status_payload(db, user.yard_id))


@router.get("/official/models", response_model=None)
def list_official_models(
    user: CurrentUser = Depends(_actor),
    db=Depends(get_db_session),
):
    official_service.require_enabled(db, user.yard_id)
    items = official_service.official_models(db, user.yard_id)
    return ok([item.model_dump() for item in items])


@router.get("/official/plans", response_model=None)
def list_official_plans(
    user: CurrentUser = Depends(_actor),
    db=Depends(get_db_session),
):
    official_service.require_enabled(db, user.yard_id)
    plans = official_service.ensure_plans(db)
    return ok([plan.model_dump() for plan in plans])


@router.post("/official/trials", response_model=None)
def create_official_trial(
    payload: OfficialTrialBody,
    user: CurrentUser = Depends(_actor),
    db=Depends(get_db_session),
):
    try:
        out = official_service.create_trial(db, user.yard_id, user.user_id, payload)
        _audit(
            db,
            user,
            "official_trial_create",
            out.id,
            {"model_key": out.model_key, "quota_total": out.quota_total},
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.get("/official/entitlements", response_model=None)
def list_official_entitlements(
    user: CurrentUser = Depends(_actor),
    db=Depends(get_db_session),
    model_key: str | None = None,
):
    official_service.require_enabled(db, user.yard_id)
    items = official_service.list_entitlements(db, user.yard_id, model_key=model_key)
    return ok([item.model_dump() for item in items])


@router.post("/official/orders", response_model=None)
def create_official_order(
    payload: OfficialOrderCreate,
    user: CurrentUser = Depends(_actor),
    db=Depends(get_db_session),
):
    try:
        out = official_service.create_order(
            db,
            user.yard_id,
            user.user_id,
            OfficialTrialBody(model_key=payload.model_key, plan_code=payload.plan_code),
        )
        # 金额/币种只来自服务端套餐；审计只记金额与套餐，不含任何密钥
        _audit(
            db,
            user,
            "official_order_create",
            out.id,
            {
                "order_id": out.id,
                "amount": out.amount,
                "currency": out.currency,
                "status": out.status,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(out.model_dump())


@router.get("/official/orders/{order_id}", response_model=None)
def get_official_order(
    order_id: str,
    user: CurrentUser = Depends(_actor),
    db=Depends(get_db_session),
):
    return ok(official_service.get_order(db, user.yard_id, order_id).model_dump())


@router.post("/official/webhooks/payment", response_model=None)
async def official_payment_webhook(
    request: Request,
    db: Session = Depends(get_db_session),
):
    """支付回调：不要求登录，但必须验签（X-Signature/X-Timestamp 或报文字段）。"""
    raw = await request.body()
    signature = request.headers.get("x-signature", "")
    timestamp = request.headers.get("x-timestamp", "")
    payload: dict = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                payload = parsed
        except ValueError:
            payload = {}
    if not signature:
        signature = str(payload.get("signature") or "")
    if not timestamp:
        timestamp = str(payload.get("timestamp") or "")
    try:
        result = official_service.handle_payment_webhook(
            db,
            raw_body=raw,
            signature=signature,
            timestamp=timestamp,
            event_id=str(payload.get("event_id") or "") or None,
            event_type=str(payload.get("event_type") or "") or None,
        )
        log_audit(
            db,
            yard_id=None,
            actor_id=None,
            action="official_payment_webhook",
            resource_type="official_order",
            resource_id=result.get("order_id"),
            result="success",
            request_id=getattr(request.state, "request_id", "req_unknown"),
            metadata={
                "event_id": result.get("event_id"),
                "event_type": result.get("event_type"),
                "duplicate": result.get("duplicate", False),
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ok(result)
