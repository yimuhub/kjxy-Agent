"""官方模型通道服务（v3.33）。

设计原则：
- **默认关闭**：没有启用中的官方 ``relay_channels`` 时，所有官方业务接口返回
  ``503 OFFICIAL_CHANNEL_DISABLED``（``status`` 探针除外，前端据此渲染未开通态）。
- 金额只信服务端：下单金额来自 ``official_plans``，请求体只选套餐；
- 权益记在 ``official_entitlements``（quota_total/quota_used），试用每小院每模型一次；
- 支付回调：HMAC-SHA256 校验 ``timestamp + "." + raw_body``，±300 秒窗口，
  ``event_id`` 幂等；paid 激活权益、refund 撤销权益；
- 官方供应商（``provider_type=official_relay`` / ``channel_kind=official``）由平台维护，
  小院只读；没有有效权益时模型列表为空。
"""

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.logging import get_logger
from app.modules.llm import relay_service, supplier_service
from app.modules.llm.supplier_crypto import decrypt_api_key
from app.modules.llm.supplier_models import (
    CHANNEL_KIND_OFFICIAL,
    PROVIDER_TYPE_OFFICIAL_RELAY,
    OfficialEntitlement,
    OfficialOrder,
    OfficialPlan,
    OfficialWebhookEvent,
    RelayChannel,
    Supplier,
    SupplierModel,
)
from app.modules.llm.supplier_schemas import (
    OfficialEntitlementOut,
    OfficialOrderOut,
    OfficialPlanOut,
    OfficialTrialBody,
    SupplierModelOut,
)

log = get_logger(__name__)

WEBHOOK_TOLERANCE_SECONDS = 300
OFFICIAL_SUPPLIER_NAME = "官方模型通道"

_MSG_DISABLED = "官方模型通道尚未开通。"


def _disabled() -> AppError:
    return AppError("OFFICIAL_CHANNEL_DISABLED", _MSG_DISABLED, http_status=503)


def _ensure_enabled(db: Session, yard_id: str | None) -> None:
    if relay_service.get_official_channel(db, yard_id) is None:
        raise _disabled()


def require_enabled(db: Session, yard_id: str | None) -> None:
    """路由层统一闸门：未配置启用中的官方通道 → 503 OFFICIAL_CHANNEL_DISABLED。"""
    _ensure_enabled(db, yard_id)


class OfficialChannel(Protocol):
    """官方通道能力协议：真实实现接入时只需替换 provider，不动路由与服务调用方。"""

    async def list_models(self, db: Session, yard_id: str) -> list[SupplierModelOut]: ...

    async def create_trial(
        self, db: Session, yard_id: str, user_id: str, body: OfficialTrialBody
    ) -> OfficialEntitlementOut: ...

    async def quote(
        self, db: Session, model_key: str, plan_code: str | None = None
    ) -> OfficialPlanOut: ...

    async def create_order(
        self, db: Session, yard_id: str, user_id: str, body: OfficialTrialBody
    ) -> OfficialOrderOut: ...

    async def get_entitlement(
        self, db: Session, yard_id: str, model_key: str | None = None
    ) -> list[OfficialEntitlementOut]: ...


class DisabledOfficialChannel:
    """未开通：所有官方能力统一 503（与默认关闭策略一致）。"""

    async def list_models(self, db: Session, yard_id: str) -> list[SupplierModelOut]:
        raise _disabled()

    async def create_trial(
        self, db: Session, yard_id: str, user_id: str, body: OfficialTrialBody
    ) -> OfficialEntitlementOut:
        raise _disabled()

    async def quote(
        self, db: Session, model_key: str, plan_code: str | None = None
    ) -> OfficialPlanOut:
        raise _disabled()

    async def create_order(
        self, db: Session, yard_id: str, user_id: str, body: OfficialTrialBody
    ) -> OfficialOrderOut:
        raise _disabled()

    async def get_entitlement(
        self, db: Session, yard_id: str, model_key: str | None = None
    ) -> list[OfficialEntitlementOut]:
        raise _disabled()


# ---------- 套餐与权益 ----------


def _plan_to_out(plan: OfficialPlan) -> OfficialPlanOut:
    return OfficialPlanOut(
        id=plan.id,
        model_key=plan.model_key,
        name=plan.name,
        billing_unit=plan.billing_unit,
        price=float(plan.price or 0),
        currency=plan.currency or "CNY",
        trial_tokens=float(plan.trial_tokens or 0),
        trial_days=int(plan.trial_days or 0),
        active=bool(plan.active),
    )


def _entitlement_to_out(row: OfficialEntitlement) -> OfficialEntitlementOut:
    return OfficialEntitlementOut(
        id=row.id,
        yard_id=row.yard_id,
        user_id=row.user_id,
        plan_id=row.plan_id,
        model_key=row.model_key,
        source=row.source,
        status=row.status,
        quota_total=float(row.quota_total or 0),
        quota_used=float(row.quota_used or 0),
        starts_at=row.starts_at.isoformat(),
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
    )


def _order_to_out(row: OfficialOrder) -> OfficialOrderOut:
    return OfficialOrderOut(
        id=row.id,
        yard_id=row.yard_id,
        user_id=row.user_id,
        plan_id=row.plan_id,
        amount=float(row.amount or 0),
        currency=row.currency or "CNY",
        status=row.status,
        external_order_id=row.external_order_id,
        payment_url=row.payment_url,
        idempotency_key=row.idempotency_key,
        paid_at=row.paid_at.isoformat() if row.paid_at else None,
        created_at=row.created_at.isoformat(),
    )


def ensure_plans(db: Session) -> list[OfficialPlanOut]:
    """读取在售套餐（只读表，运营维护；不在代码里硬编码价格）。"""
    rows = (
        db.query(OfficialPlan)
        .filter(OfficialPlan.active.is_(True))
        .order_by(OfficialPlan.price.asc())
        .all()
    )
    return [_plan_to_out(row) for row in rows]


def _resolve_plan(db: Session, model_key: str, plan_code: str | None = None) -> OfficialPlan:
    rows = (
        db.query(OfficialPlan)
        .filter(OfficialPlan.model_key == model_key, OfficialPlan.active.is_(True))
        .all()
    )
    if not rows:
        raise AppError(
            "OFFICIAL_PLAN_NOT_FOUND",
            f"模型「{model_key}」尚未上架官方套餐。",
            http_status=404,
        )
    if plan_code:
        for row in rows:
            if plan_code in (row.id, row.model_key, row.name):
                return row
        raise AppError(
            "OFFICIAL_PLAN_NOT_FOUND",
            f"套餐「{plan_code}」不存在或已下架。",
            http_status=404,
        )
    return rows[0]


def _expire_stale(db: Session, yard_id: str) -> None:
    now = datetime.now(UTC)
    rows = (
        db.query(OfficialEntitlement)
        .filter(
            OfficialEntitlement.yard_id == yard_id,
            OfficialEntitlement.status == "active",
            OfficialEntitlement.expires_at.is_not(None),
        )
        .all()
    )
    changed = False
    for row in rows:
        expires_at = row.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at is not None and expires_at <= now:
            row.status = "expired"
            changed = True
    if changed:
        db.flush()


def list_entitlements(
    db: Session, yard_id: str, *, model_key: str | None = None, user_id: str | None = None
) -> list[OfficialEntitlementOut]:
    _ensure_enabled(db, yard_id)
    _expire_stale(db, yard_id)
    query = db.query(OfficialEntitlement).filter(OfficialEntitlement.yard_id == yard_id)
    if model_key:
        query = query.filter(OfficialEntitlement.model_key == model_key)
    if user_id:
        query = query.filter(OfficialEntitlement.user_id == user_id)
    rows = query.order_by(OfficialEntitlement.created_at.desc()).all()
    return [_entitlement_to_out(row) for row in rows]


def _ensure_official_supplier(db: Session, yard_id: str) -> Supplier:
    """确保小院存在官方供应商（platform 维护、小院只读）。"""
    channel = relay_service.get_official_channel(db, yard_id)
    if channel is None:
        raise _disabled()
    if not channel.api_key_enc:
        raise AppError(
            "OFFICIAL_CHANNEL_NOT_CONFIGURED",
            "官方通道缺少 API Key，请联系平台管理员补全配置。",
            http_status=503,
        )
    supplier = (
        db.query(Supplier)
        .filter(
            Supplier.yard_id == yard_id,
            Supplier.channel_kind == CHANNEL_KIND_OFFICIAL,
        )
        .first()
    )
    if supplier is None:
        supplier = Supplier(
            yard_id=yard_id,
            name=OFFICIAL_SUPPLIER_NAME,
            provider_type=PROVIDER_TYPE_OFFICIAL_RELAY,
            base_url=channel.base_url,
            model_id="placeholder",
            api_key_enc=channel.api_key_enc or "",
            models=None,
            priority=0,
            status=1,
            channel_kind=CHANNEL_KIND_OFFICIAL,
            relay_channel_id=channel.id,
        )
        db.add(supplier)
        db.flush()
    else:
        supplier.base_url = channel.base_url
        supplier.api_key_enc = channel.api_key_enc or supplier.api_key_enc
        supplier.relay_channel_id = channel.id
        supplier.status = 1
    return supplier


def _ensure_official_model(db: Session, supplier: Supplier, plan: OfficialPlan) -> SupplierModel:
    modality = supplier_service.guess_modality(plan.model_key)
    row = (
        db.query(SupplierModel)
        .filter(
            SupplierModel.supplier_id == supplier.id,
            SupplierModel.model_key == plan.model_key,
        )
        .first()
    )
    if row is None:
        row = SupplierModel(
            supplier_id=supplier.id,
            model_key=plan.model_key[:100],
            upstream_model_id=plan.model_key[:200],
            display_name=(plan.name or plan.model_key)[:120],
            modality=modality,
            protocol=supplier_service.protocol_for_modality(modality),
            pricing={
                "unit_price": float(plan.price or 0),
                "currency": plan.currency or "CNY",
                "billing_unit": plan.billing_unit,
            },
            status=1,
            source="official",
        )
        db.add(row)
        db.flush()
    else:
        row.status = 1
    supplier_service.sync_supplier_model_defaults(db, supplier)
    supplier_service.invalidate_supplier_cache(supplier_id=supplier.id)
    return row


def official_models(db: Session, yard_id: str) -> list[SupplierModelOut]:
    """只展示「有有效权益」的官方模型（没有权益 = 列表为空，而不是给个点不开的按钮）。"""
    _ensure_enabled(db, yard_id)
    _expire_stale(db, yard_id)
    entitlements = (
        db.query(OfficialEntitlement)
        .filter(
            OfficialEntitlement.yard_id == yard_id,
            OfficialEntitlement.status == "active",
        )
        .all()
    )
    keys = {row.model_key for row in entitlements}
    if not keys:
        return []
    supplier = (
        db.query(Supplier)
        .filter(
            Supplier.yard_id == yard_id,
            Supplier.channel_kind == CHANNEL_KIND_OFFICIAL,
            Supplier.status == 1,
        )
        .first()
    )
    if supplier is None:
        return []
    rows = (
        db.query(SupplierModel)
        .filter(
            SupplierModel.supplier_id == supplier.id,
            SupplierModel.model_key.in_(sorted(keys)),
            SupplierModel.status == 1,
        )
        .order_by(SupplierModel.model_key.asc())
        .all()
    )
    return [supplier_service.supplier_model_to_out(row) for row in rows]


def create_trial(
    db: Session, yard_id: str, user_id: str | None, body: OfficialTrialBody
) -> OfficialEntitlementOut:
    _ensure_enabled(db, yard_id)
    _expire_stale(db, yard_id)
    plan = _resolve_plan(db, body.model_key, body.plan_code)
    existing = (
        db.query(OfficialEntitlement)
        .filter(
            OfficialEntitlement.yard_id == yard_id,
            OfficialEntitlement.model_key == plan.model_key,
            OfficialEntitlement.source == "trial",
            OfficialEntitlement.status == "active",
        )
        .first()
    )
    if existing is not None:
        raise AppError(
            "OFFICIAL_TRIAL_USED",
            f"本小院已试用过「{plan.model_key}」，每个模型只能试用一次。",
            http_status=409,
        )
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=plan.trial_days) if plan.trial_days else None
    entitlement = OfficialEntitlement(
        yard_id=yard_id,
        user_id=user_id,
        plan_id=plan.id,
        model_key=plan.model_key,
        source="trial",
        status="active",
        quota_total=float(plan.trial_tokens or 0),
        quota_used=0.0,
        starts_at=now,
        expires_at=expires_at,
    )
    db.add(entitlement)
    db.flush()
    supplier = _ensure_official_supplier(db, yard_id)
    _ensure_official_model(db, supplier, plan)
    return _entitlement_to_out(entitlement)


def create_order(
    db: Session, yard_id: str, user_id: str, body: OfficialTrialBody
) -> OfficialOrderOut:
    _ensure_enabled(db, yard_id)
    plan = _resolve_plan(db, body.model_key, body.plan_code)
    order_id = str(uuid.uuid4())
    order = OfficialOrder(
        id=order_id,
        yard_id=yard_id,
        user_id=user_id,
        plan_id=plan.id,
        # 金额只信服务端套餐，请求体没有价格字段
        amount=float(plan.price or 0),
        currency=plan.currency or "CNY",
        status="pending",
        external_order_id=None,
        # 支付网关未接入前的占位地址：明确指向本订单，不伪造第三方链接
        payment_url=f"/llm/official/orders/{order_id}",
        # 幂等键列长 100：模型名前缀（≤40）+ 随机串，避免超长被 PG 拒绝
        idempotency_key=f"{plan.model_key[:40]}:{uuid.uuid4().hex}",
    )
    db.add(order)
    db.flush()
    return _order_to_out(order)


def get_order(db: Session, yard_id: str, order_id: str) -> OfficialOrderOut:
    _ensure_enabled(db, yard_id)
    row = (
        db.query(OfficialOrder)
        .filter(OfficialOrder.id == order_id, OfficialOrder.yard_id == yard_id)
        .first()
    )
    if row is None:
        raise AppError("OFFICIAL_ORDER_NOT_FOUND", "订单不存在。", http_status=404)
    return _order_to_out(row)


# ---------- 支付回调 ----------


def _webhook_channel(db: Session) -> RelayChannel:
    channel = relay_service.get_official_channel(db, None)
    if channel is None or not channel.webhook_secret_enc:
        channel = (
            db.query(RelayChannel)
            .filter(
                RelayChannel.kind == relay_service.KIND_OFFICIAL,
                RelayChannel.enabled.is_(True),
                RelayChannel.webhook_secret_enc.is_not(None),
            )
            .first()
        )
    if channel is None or not channel.webhook_secret_enc:
        raise _disabled()
    return channel


def _parse_timestamp(raw: str) -> float:
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError) as e:
        raise AppError("OFFICIAL_WEBHOOK_BAD_SIGNATURE", "回调时间戳不合法。", 401) from e
    if value > 1e12:  # 毫秒时间戳
        value /= 1000.0
    return value


def webhook_signature(secret: str, timestamp: str, raw_body: bytes) -> str:
    """签名口径：HMAC-SHA256(secret, f"{timestamp}." + raw_body) 的十六进制。"""
    message = f"{timestamp}.".encode() + raw_body
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _activate_purchase(
    db: Session, order: OfficialOrder, payload: dict[str, Any]
) -> OfficialEntitlement:
    plan = db.get(OfficialPlan, order.plan_id) if order.plan_id else None
    model_key = (plan.model_key if plan else None) or str(payload.get("model_key") or "")
    if not model_key:
        raise AppError("OFFICIAL_WEBHOOK_INVALID", "回调缺少模型信息。", 400)
    quota_total = payload.get("quota_total")
    try:
        # 回调没给额度时按套餐试用额度兜底；绝不把「金额」当额度
        default_quota = float(plan.trial_tokens or 0) if plan else 0.0
        quota = float(quota_total) if quota_total is not None else default_quota
    except (TypeError, ValueError):
        quota = float(plan.trial_tokens or 0) if plan else 0.0
    existing = (
        db.query(OfficialEntitlement)
        .filter(
            OfficialEntitlement.yard_id == order.yard_id,
            OfficialEntitlement.model_key == model_key,
            OfficialEntitlement.external_ref == order.id,
        )
        .first()
    )
    if existing is not None:
        existing.status = "active"
        existing.quota_total = max(float(existing.quota_total or 0), quota)
        db.flush()
        return existing
    row = OfficialEntitlement(
        yard_id=order.yard_id,
        user_id=order.user_id,
        plan_id=plan.id if plan else None,
        model_key=model_key,
        source="purchase",
        status="active",
        quota_total=quota,
        quota_used=0.0,
        starts_at=datetime.now(UTC),
        expires_at=None,
        external_ref=order.id,
    )
    db.add(row)
    db.flush()
    if plan is not None:
        supplier = _ensure_official_supplier(db, order.yard_id)
        _ensure_official_model(db, supplier, plan)
    return row


def _find_order(db: Session, payload: dict[str, Any]) -> OfficialOrder:
    order_id = str(payload.get("order_id") or "").strip()
    row = db.get(OfficialOrder, order_id) if order_id else None
    if row is None:
        external = str(payload.get("external_order_id") or "").strip()
        if external:
            row = (
                db.query(OfficialOrder).filter(OfficialOrder.external_order_id == external).first()
            )
    if row is None:
        raise AppError("OFFICIAL_ORDER_NOT_FOUND", "回调对应的订单不存在。", 404)
    return row


def handle_payment_webhook(
    db: Session,
    *,
    raw_body: bytes,
    signature: str,
    timestamp: str,
    event_id: str | None = None,
    event_type: str | None = None,
) -> dict[str, Any]:
    """支付回调：验签 → 时间窗 → event_id 幂等 → paid/refund/failed 状态机。"""
    channel = _webhook_channel(db)
    if not signature or not timestamp:
        raise AppError("OFFICIAL_WEBHOOK_BAD_SIGNATURE", "回调缺少签名或时间戳。", 401)
    ts_value = _parse_timestamp(timestamp)
    if abs(datetime.now(UTC).timestamp() - ts_value) > WEBHOOK_TOLERANCE_SECONDS:
        raise AppError("OFFICIAL_WEBHOOK_EXPIRED", "回调时间戳超出 ±300 秒窗口。", 401)
    secret = decrypt_api_key(channel.webhook_secret_enc or "")
    expected = webhook_signature(secret, str(timestamp), raw_body)
    provided = signature.strip()
    if provided.lower().startswith("sha256="):
        provided = provided.split("=", 1)[1]
    if not hmac.compare_digest(expected, provided):
        raise AppError("OFFICIAL_WEBHOOK_BAD_SIGNATURE", "回调签名校验失败。", 401)
    try:
        payload = json.loads(raw_body or b"{}")
    except ValueError as e:
        raise AppError("OFFICIAL_WEBHOOK_INVALID", "回调报文不是合法 JSON。", 400) from e
    if not isinstance(payload, dict):
        raise AppError("OFFICIAL_WEBHOOK_INVALID", "回调报文结构不合法。", 400)

    event_id = str(event_id or payload.get("event_id") or "").strip()
    event_type = str(event_type or payload.get("event_type") or "").strip()
    if not event_id or not event_type:
        raise AppError("OFFICIAL_WEBHOOK_INVALID", "回调缺少 event_id 或 event_type。", 400)
    digest = hashlib.sha256(raw_body).hexdigest()
    seen = db.query(OfficialWebhookEvent).filter(OfficialWebhookEvent.event_id == event_id).first()
    if seen is not None:
        return {
            "duplicate": True,
            "event_id": event_id,
            "event_type": seen.event_type,
        }

    event = OfficialWebhookEvent(
        id=str(uuid.uuid4()),
        event_id=event_id,
        event_type=event_type,
        payload_digest=digest,
        received_at=datetime.now(UTC),
    )
    db.add(event)

    result: dict[str, Any] = {"duplicate": False, "event_id": event_id, "event_type": event_type}
    normalized = event_type.lower()
    raw_payload = payload.get("payload")
    data: dict[str, Any] = raw_payload if isinstance(raw_payload, dict) else payload
    if normalized in ("order.paid", "paid", "payment.succeeded"):
        order = _find_order(db, data)
        order.status = "paid"
        order.paid_at = order.paid_at or datetime.now(UTC)
        if data.get("external_order_id"):
            order.external_order_id = str(data["external_order_id"])[:200]
        entitlement = _activate_purchase(db, order, data)
        result["order_id"] = order.id
        result["entitlement_id"] = entitlement.id
    elif normalized in ("order.refunded", "refunded", "refund.succeeded"):
        order = _find_order(db, data)
        order.status = "refunded"
        revoked = 0
        for row in (
            db.query(OfficialEntitlement)
            .filter(
                OfficialEntitlement.yard_id == order.yard_id,
                OfficialEntitlement.external_ref == order.id,
            )
            .all()
        ):
            row.status = "revoked"
            revoked += 1
        result["order_id"] = order.id
        result["revoked"] = revoked
    elif normalized in ("order.failed", "failed", "payment.failed"):
        order = _find_order(db, data)
        order.status = "failed"
        result["order_id"] = order.id
    else:
        result["ignored"] = True

    event.processed_at = datetime.now(UTC)
    db.flush()
    return result


def status_payload(db: Session, yard_id: str | None) -> dict[str, Any]:
    """官方通道探针：未开通时给 200 + enabled=false，便于前端渲染未开通态。"""
    channel = relay_service.get_official_channel(db, yard_id)
    plans = ensure_plans(db) if channel is not None else []
    return {
        "enabled": channel is not None,
        "channel_id": channel.id if channel else None,
        "channel_name": channel.name if channel else None,
        "base_url": channel.base_url if channel else None,
        "plan_count": len(plans),
        "message": "官方模型通道已开通。" if channel is not None else _MSG_DISABLED,
    }


# ---------- 通道实现（当前仅 Mock；真实实现接入时替换 get_channel 的返回） ----------


class MockOfficialChannel:
    """测试/联调用实现：套餐来自 official_plans，试用与下单直接成功。"""

    async def list_models(self, db: Session, yard_id: str) -> list[SupplierModelOut]:
        return official_models(db, yard_id)

    async def create_trial(
        self, db: Session, yard_id: str, user_id: str, body: OfficialTrialBody
    ) -> OfficialEntitlementOut:
        return create_trial(db, yard_id, user_id, body)

    async def quote(
        self, db: Session, model_key: str, plan_code: str | None = None
    ) -> OfficialPlanOut:
        _ensure_enabled(db, None)
        return _plan_to_out(_resolve_plan(db, model_key, plan_code))

    async def create_order(
        self, db: Session, yard_id: str, user_id: str, body: OfficialTrialBody
    ) -> OfficialOrderOut:
        return create_order(db, yard_id, user_id, body)

    async def get_entitlement(
        self, db: Session, yard_id: str, model_key: str | None = None
    ) -> list[OfficialEntitlementOut]:
        return list_entitlements(db, yard_id, model_key=model_key)


def get_channel(db: Session, yard_id: str | None = None) -> OfficialChannel:
    if relay_service.get_official_channel(db, yard_id) is None:
        return DisabledOfficialChannel()
    return MockOfficialChannel()
