"""官方模型通道（v3.33）：默认关闭、试用一次、订单金额、回调幂等与篡改拒绝。"""

import json
from datetime import UTC, datetime

import pytest
from app.core.errors import AppError
from app.modules.llm import official_service, relay_service
from app.modules.llm.supplier_models import (
    OfficialEntitlement,
    OfficialOrder,
    OfficialPlan,
    RelayChannel,
    Supplier,
    SupplierModel,
)
from app.modules.llm.supplier_schemas import (
    OfficialTrialBody,
    RelayChannelCreate,
)

YARD = "yard-official"
SECRET = "official-webhook-secret"


def _seed_plan(
    db,
    *,
    model_key: str = "gpt-official",
    price: float = 88.0,
    trial_tokens: float = 1000,
    trial_days: int = 7,
) -> OfficialPlan:
    row = OfficialPlan(
        model_key=model_key,
        name=f"官方 {model_key}",
        billing_unit="tokens",
        price=price,
        currency="CNY",
        trial_tokens=trial_tokens,
        trial_days=trial_days,
        active=True,
    )
    db.add(row)
    db.flush()
    return row


def _enable_official(db, *, secret: str | None = SECRET) -> RelayChannel:
    out = relay_service.create_channel(
        db,
        None,
        RelayChannelCreate(
            name="官方通道",
            kind="official",
            base_url="https://official.example.com",
            api_key="official-key",
            webhook_secret=secret,
            scope="global",
        ),
        is_platform_admin=True,
    )
    return db.get(RelayChannel, out.id)


def _paid_order(db, plan: OfficialPlan) -> OfficialOrder:
    out = official_service.create_order(
        db, YARD, "user-1", OfficialTrialBody(model_key=plan.model_key)
    )
    return db.get(OfficialOrder, out.id)


def _webhook(
    db,
    payload: dict,
    *,
    secret: str = SECRET,
    timestamp: str | None = None,
    signature: str | None = None,
) -> dict:
    raw = json.dumps(payload).encode("utf-8")
    ts = timestamp if timestamp is not None else str(int(datetime.now(UTC).timestamp()))
    sig = (
        signature if signature is not None else official_service.webhook_signature(secret, ts, raw)
    )
    return official_service.handle_payment_webhook(db, raw_body=raw, signature=sig, timestamp=ts)


def test_disabled_by_default_returns_503(db_session):
    channel = official_service.get_channel(db_session, YARD)
    assert isinstance(channel, official_service.DisabledOfficialChannel)
    with pytest.raises(AppError) as exc:
        official_service.create_trial(
            db_session, YARD, "user-1", OfficialTrialBody(model_key="gpt-official")
        )
    assert exc.value.code == "OFFICIAL_CHANNEL_DISABLED"
    assert exc.value.http_status == 503
    status = official_service.status_payload(db_session, YARD)
    assert status["enabled"] is False


def test_trial_can_only_be_used_once(db_session):
    plan = _seed_plan(db_session)
    _enable_official(db_session)

    entitlement = official_service.create_trial(
        db_session, YARD, "user-1", OfficialTrialBody(model_key=plan.model_key)
    )
    assert entitlement.quota_total == 1000
    assert entitlement.quota_used == 0
    assert entitlement.source == "trial"
    assert entitlement.expires_at is not None

    supplier = (
        db_session.query(Supplier)
        .filter(Supplier.yard_id == YARD, Supplier.channel_kind == "official")
        .one()
    )
    assert supplier.provider_type == "official_relay"
    model = db_session.query(SupplierModel).filter(SupplierModel.supplier_id == supplier.id).one()
    assert model.source == "official"
    assert model.model_key == plan.model_key

    with pytest.raises(AppError) as exc:
        official_service.create_trial(
            db_session, YARD, "user-2", OfficialTrialBody(model_key=plan.model_key)
        )
    assert exc.value.code == "OFFICIAL_TRIAL_USED"
    assert exc.value.http_status == 409

    models = official_service.official_models(db_session, YARD)
    assert [item.model_key for item in models] == [plan.model_key]


def test_order_amount_comes_from_server_plan(db_session):
    plan = _seed_plan(db_session, price=88.0)
    _enable_official(db_session)

    order = official_service.create_order(
        db_session, YARD, "user-1", OfficialTrialBody(model_key=plan.model_key)
    )
    assert order.status == "pending"
    assert order.amount == 88.0
    assert order.currency == "CNY"
    assert order.payment_url == f"/llm/official/orders/{order.id}"


def test_payment_webhook_is_idempotent_and_activates_entitlement(db_session):
    plan = _seed_plan(db_session)
    _enable_official(db_session)
    order = _paid_order(db_session, plan)

    payload = {
        "event_id": "evt-paid-1",
        "event_type": "order.paid",
        "payload": {"order_id": order.id, "quota_total": 500},
    }
    result = _webhook(db_session, payload)
    assert result["duplicate"] is False

    db_session.expire_all()
    paid = db_session.get(OfficialOrder, order.id)
    assert paid.status == "paid"
    assert paid.paid_at is not None
    entitlement = (
        db_session.query(OfficialEntitlement)
        .filter(OfficialEntitlement.external_ref == order.id)
        .one()
    )
    assert entitlement.source == "purchase"
    assert entitlement.status == "active"
    assert entitlement.quota_total == 500

    again = _webhook(db_session, payload)
    assert again["duplicate"] is True
    assert (
        db_session.query(OfficialEntitlement)
        .filter(OfficialEntitlement.external_ref == order.id)
        .count()
        == 1
    )


def test_tampered_signature_is_rejected(db_session):
    plan = _seed_plan(db_session)
    _enable_official(db_session)
    order = _paid_order(db_session, plan)

    payload = {
        "event_id": "evt-tampered",
        "event_type": "order.paid",
        "payload": {"order_id": order.id, "quota_total": 500},
    }
    raw = json.dumps(payload).encode("utf-8")
    ts = str(int(datetime.now(UTC).timestamp()))
    signature = official_service.webhook_signature(SECRET, ts, raw)
    tampered = raw.replace(b"500", b"999")

    with pytest.raises(AppError) as exc:
        official_service.handle_payment_webhook(
            db_session, raw_body=tampered, signature=signature, timestamp=ts
        )
    assert exc.value.code == "OFFICIAL_WEBHOOK_BAD_SIGNATURE"
    assert exc.value.http_status == 401
    db_session.expire_all()
    assert db_session.get(OfficialOrder, order.id).status == "pending"


def test_expired_timestamp_is_rejected(db_session):
    plan = _seed_plan(db_session)
    _enable_official(db_session)
    order = _paid_order(db_session, plan)
    stale = str(int(datetime.now(UTC).timestamp()) - 1000)

    with pytest.raises(AppError) as exc:
        _webhook(
            db_session,
            {
                "event_id": "evt-stale",
                "event_type": "order.paid",
                "payload": {"order_id": order.id},
            },
            timestamp=stale,
        )
    assert exc.value.code == "OFFICIAL_WEBHOOK_EXPIRED"


def test_refund_revokes_entitlement(db_session):
    plan = _seed_plan(db_session)
    _enable_official(db_session)
    order = _paid_order(db_session, plan)
    _webhook(
        db_session,
        {
            "event_id": "evt-paid-2",
            "event_type": "order.paid",
            "payload": {"order_id": order.id, "quota_total": 200},
        },
    )

    result = _webhook(
        db_session,
        {
            "event_id": "evt-refund-1",
            "event_type": "order.refunded",
            "payload": {"order_id": order.id},
        },
    )
    assert result["revoked"] == 1
    db_session.expire_all()
    assert db_session.get(OfficialOrder, order.id).status == "refunded"
    entitlement = (
        db_session.query(OfficialEntitlement)
        .filter(OfficialEntitlement.external_ref == order.id)
        .one()
    )
    assert entitlement.status == "revoked"


def test_webhook_without_secret_is_disabled(db_session):
    _seed_plan(db_session)
    _enable_official(db_session, secret=None)
    with pytest.raises(AppError) as exc:
        _webhook(
            db_session,
            {"event_id": "evt-x", "event_type": "order.paid", "payload": {}},
        )
    assert exc.value.code == "OFFICIAL_CHANNEL_DISABLED"
