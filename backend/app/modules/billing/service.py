"""订阅计费服务（v3.71）：套餐/兑换码/订单，到账统一写 quota_grants。

到账口径：每个套餐项找到「全小院 × 指标 × 窗口」的额度策略（没有就建，
on_exceed=approve 与配额页默认一致），追加一条带有效期的 QuotaGrant——
不动客户已配置的策略上限，超限语义不因购买改变。
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import AppError
from app.modules.audit.service import log_audit
from app.modules.billing.models import (
    CODE_DISABLED,
    CODE_EXPIRED,
    CODE_REDEEMED,
    CODE_UNUSED,
    ORDER_CANCELLED,
    ORDER_PAID,
    ORDER_PENDING,
    BillingCode,
    BillingOrder,
)
from app.modules.billing.payment import PROVIDER_MANUAL, PaymentEvent, get_payment_provider
from app.modules.billing.plans import KIND_BOOSTER, PLANS, Plan
from app.modules.quota.engine import _window_end
from app.modules.quota.models import QuotaGrant, QuotaPolicy

CODE_PREFIX = "KY-"
#: 兑换码默认 90 天内必须用掉
DEFAULT_CODE_TTL_DAYS = 90

#: 回单核验状态：submitted=已提交（金额预检一致），待平台核验真伪后确认到账；
#: manual=金额与订单不一致，需人工核对。**不存在"自动到账"状态**——
#: 回单真实性只有平台对账能判定（2026-11-13 二次深审修正，见 submit_voucher）。
VOUCHER_SUBMITTED = "submitted"
VOUCHER_MANUAL = "manual"

#: 金额比对容差（元）：渠道回调四舍五入到分
AMOUNT_EPSILON = 0.01


def _now() -> datetime:
    return datetime.now(UTC)


def _plan_of(db: Session, code: str) -> Plan:
    """按 code 取套餐（以数据库为准；表空时回落代码内置值）。

    找不到时抛 BILLING_PLAN_NOT_FOUND —— 与旧行为一致，
    避免运营下架某档后仍能按旧目录兑换。
    """
    from app.modules.billing.catalog import get_plan_by_code

    plan = get_plan_by_code(db, code)
    if plan is None:
        raise AppError("BILLING_PLAN_NOT_FOUND", "套餐不存在。", http_status=404)
    return plan


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite 会把 tz-aware 读成 naive：比较前统一补 UTC（与 official_service 同口径）。"""
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def _gen_code() -> str:
    body = secrets.token_hex(6).upper()
    return f"{CODE_PREFIX}{body[0:4]}-{body[4:8]}-{body[8:12]}"


def plan_payload(plan: Plan) -> dict:
    """兼容旧调用：无小院上下文时的套餐载荷（不含按环境算出的总额度）。"""
    from app.modules.billing.catalog import plan_payload as _plan_payload

    return _plan_payload(plan)


def list_plans(db: Session | None = None, *, yard_id: str | None = None) -> list[dict]:
    """套餐目录（v3.80 起以数据库为准，运营可改价）。"""
    if db is None:
        return [plan_payload(plan) for plan in PLANS]
    from app.modules.billing import catalog

    if yard_id:
        return catalog.list_plan_payloads(db, yard_id)
    return [catalog.plan_payload(plan) for plan in catalog.load_plans(db)]


# ---------------------------------------------------------------------------
# 兑换码（平台管理员）
# ---------------------------------------------------------------------------


def create_codes(
    db: Session,
    *,
    plan_code: str,
    count: int,
    actor_id: str,
    note: str | None = None,
    ttl_days: int = DEFAULT_CODE_TTL_DAYS,
) -> list[BillingCode]:
    plan = _plan_of(db, plan_code)
    if plan is None:
        raise AppError("BILLING_PLAN_NOT_FOUND", "套餐不存在。", http_status=404)
    count = max(1, min(int(count), 200))
    expires_at = _now() + timedelta(days=max(1, int(ttl_days)))
    rows: list[BillingCode] = []
    for _ in range(count):
        row = BillingCode(
            code=_gen_code(),
            plan_code=plan.code,
            status=CODE_UNUSED,
            note=(note or "")[:200] or None,
            expires_at=expires_at,
            created_by=actor_id,
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def code_payload(row: BillingCode) -> dict:
    return {
        "id": row.id,
        "code": row.code,
        "plan_code": row.plan_code,
        "status": row.status,
        "note": row.note,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "redeemed_yard_id": row.redeemed_yard_id,
        "redeemed_at": row.redeemed_at.isoformat() if row.redeemed_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_codes(db: Session, *, status: str | None = None, limit: int = 100) -> list[dict]:
    stmt = (
        select(BillingCode).order_by(BillingCode.created_at.desc()).limit(max(1, min(limit, 500)))
    )
    if status:
        stmt = stmt.where(BillingCode.status == status)
    return [code_payload(row) for row in db.scalars(stmt)]


# ---------------------------------------------------------------------------
# 到账：订单 → quota_grants
# ---------------------------------------------------------------------------


def _apply_plan_grants(db: Session, *, yard_id: str, plan: Plan, actor_id: str) -> datetime:
    moment = _now()
    for item in plan.items:
        policy = db.scalar(
            select(QuotaPolicy).where(
                QuotaPolicy.yard_id == yard_id,
                QuotaPolicy.scope_type == "yard",
                QuotaPolicy.scope_key == "",
                QuotaPolicy.metric == item.metric,
                QuotaPolicy.window == item.window,
            )
        )
        if policy is None:
            policy = QuotaPolicy(
                yard_id=yard_id,
                scope_type="yard",
                scope_key="",
                metric=item.metric,
                window=item.window,
                limit_value=0,
                on_exceed="approve",
                note="订阅套餐到账自动创建",
            )
            db.add(policy)
            db.flush()
        expires = (
            moment + timedelta(days=item.valid_days)
            if item.valid_days > 0
            else _window_end(item.window, moment)
        )
        db.add(
            QuotaGrant(
                yard_id=yard_id,
                policy_id=policy.id,
                amount=item.amount,
                remaining=item.amount,
                expires_at=expires,
                created_by=actor_id,
            )
        )
    db.flush()
    return moment


def order_code_of(order: BillingOrder) -> str:
    """对外展示的订单号：人类可读、可作为转账备注、长度可控。

    真正的匹配靠 order_id（回调里带），这个码只用于用户与银行之间对账。
    """
    return f"TY{str(order.id).replace('-', '')[:16].upper()}"


def order_payload(row: BillingOrder) -> dict:
    return {
        "id": row.id,
        "order_code": order_code_of(row),
        "yard_id": row.yard_id,
        "plan_code": row.plan_code,
        "status": row.status,
        "price_yuan": row.price_yuan,
        "voucher_note": row.voucher_note,
        "requester_name": row.requester_name,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_note": row.decision_note,
        "granted": row.granted_at is not None,
        "payment_provider": row.payment_provider,
        "payment_trade_no": row.payment_trade_no,
        "payment_paid_at": row.payment_paid_at.isoformat() if row.payment_paid_at else None,
        "voucher_no": row.voucher_no,
        "voucher_status": row.voucher_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _mark_paid_and_grant(
    db: Session, order: BillingOrder, *, plan: Plan, actor_id: str, note: str
) -> None:
    """到账统一入口：CAS 抢占 pending→paid，再在同一事务内写配额。

    并发确认/兑换只有 rowcount==1 的赢家能入账，输家收到 409，绝不重复写
    额度。抢占与入账同事务提交：写配额失败则整体回滚，订单回到 pending 可
    重试（审查 B2）。granted_at IS NULL 双闸防任何已入账订单被再次抢占。
    """
    moment = _now()
    claimed = db.execute(
        update(BillingOrder)
        .where(
            BillingOrder.id == order.id,
            BillingOrder.status == ORDER_PENDING,
            BillingOrder.granted_at.is_(None),
        )
        .values(
            status=ORDER_PAID,
            granted_at=moment,
            decided_by=actor_id,
            decided_at=moment,
            decision_note=note,
        )
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        raise AppError("BILLING_ORDER_CLOSED", "该订单已处理，无需重复操作。", http_status=409)
    _apply_plan_grants(db, yard_id=order.yard_id, plan=plan, actor_id=actor_id)
    db.commit()


# ---------------------------------------------------------------------------
# 小院侧：兑换 / 下单 / 取消
# ---------------------------------------------------------------------------


def redeem(db: Session, *, yard_id: str, code: str, user_id: str, user_name: str | None) -> dict:
    """兑换码换套餐：CAS 原子占用码（unused→redeemed），订单直接进入已支付并到账。

    并发兑换同一码只有 rowcount==1 的赢家建订单入账；码与订单一对一另由
    code_id 唯一索引兜底（审查 B2）。占用、建单、入账同事务提交，任一步
    失败整体回滚，码回到 unused 可重试。
    """
    normalized = (code or "").strip().upper()
    if not normalized:
        raise AppError("BILLING_CODE_INVALID", "请输入兑换码。", http_status=400)
    row = db.scalar(select(BillingCode).where(BillingCode.code == normalized))
    if row is None:
        raise AppError("BILLING_CODE_INVALID", "兑换码不存在，请核对后重试。", http_status=404)
    if row.status == CODE_REDEEMED:
        raise AppError("BILLING_CODE_USED", "该兑换码已被使用。", http_status=409)
    if row.status == CODE_DISABLED:
        raise AppError("BILLING_CODE_DISABLED", "该兑换码已作废。", http_status=409)
    expires_at = _aware(row.expires_at)
    if expires_at is not None and expires_at <= _now():
        row.status = CODE_EXPIRED
        db.commit()
        raise AppError("BILLING_CODE_EXPIRED", "该兑换码已过有效期。", http_status=409)
    if row.status != CODE_UNUSED:
        raise AppError("BILLING_CODE_INVALID", "该兑换码当前不可用。", http_status=409)
    plan = _plan_of(db, row.plan_code)
    if plan is None:
        raise AppError("BILLING_PLAN_NOT_FOUND", "兑换码对应的套餐不存在。", http_status=404)

    # CAS 抢占兑换码：并发兑换只有 rowcount==1 的赢家能继续建订单入账
    claimed = db.execute(
        update(BillingCode)
        .where(BillingCode.id == row.id, BillingCode.status == CODE_UNUSED)
        .values(
            status=CODE_REDEEMED,
            redeemed_yard_id=yard_id,
            redeemed_by=user_id,
            redeemed_at=_now(),
        )
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        raise AppError("BILLING_CODE_USED", "该兑换码已被使用。", http_status=409)

    order = BillingOrder(
        yard_id=yard_id,
        plan_code=plan.code,
        status=ORDER_PENDING,
        price_yuan=plan.price_yuan,
        voucher_note=f"兑换码 {row.code}",
        code_id=row.id,
        requester_id=user_id,
        requester_name=user_name,
    )
    db.add(order)
    db.flush()
    _mark_paid_and_grant(db, order, plan=plan, actor_id=user_id, note=f"兑换码 {row.code} 自动到账")
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="billing_redeem",
        resource_type="billing_order",
        resource_id=order.id,
        result="success",
        request_id="",
        metadata={"plan": plan.code, "code_id": row.id},
    )
    return order_payload(order)


def create_order(
    db: Session,
    *,
    yard_id: str,
    plan_code: str,
    user_id: str,
    user_name: str | None,
    voucher_note: str | None = None,
) -> dict:
    plan = _plan_of(db, plan_code)
    if plan is None:
        raise AppError("BILLING_PLAN_NOT_FOUND", "套餐不存在。", http_status=404)
    if float(plan.price_yuan) <= 0:
        # 订单是"为付款而建"的凭证：0 元套餐没有可核对的金额，沙箱/回单/回调
        # 三条支付链路都会零成本到账。免费发放请走兑换码（码由平台管理员发放，
        # 发放动作本身可控），而不是把套餐价格改成 0（2026-11-13 二次深审）。
        raise AppError(
            "BILLING_PLAN_PRICE_INVALID",
            "该套餐价格未配置（须大于 0），暂不可下单；请联系平台管理员。",
            http_status=409,
        )
    if plan.kind == KIND_BOOSTER and not (voucher_note or "").strip():
        # 加油包主要走兑换码；线下下单也允许，但要求留凭证说明
        raise AppError("BILLING_VOUCHER_REQUIRED", "加油包下单请附转账凭证说明。", http_status=400)
    order = BillingOrder(
        yard_id=yard_id,
        plan_code=plan.code,
        status=ORDER_PENDING,
        price_yuan=plan.price_yuan,
        voucher_note=(voucher_note or "").strip() or None,
        requester_id=user_id,
        requester_name=user_name,
    )
    db.add(order)
    db.commit()
    log_audit(
        db,
        yard_id=yard_id,
        actor_id=user_id,
        action="billing_order_created",
        resource_type="billing_order",
        resource_id=order.id,
        result="success",
        request_id="",
        metadata={"plan": plan.code},
    )
    return order_payload(order)


def list_orders(db: Session, *, yard_id: str | None = None, limit: int = 100) -> list[dict]:
    stmt = (
        select(BillingOrder).order_by(BillingOrder.created_at.desc()).limit(max(1, min(limit, 500)))
    )
    if yard_id is not None:
        stmt = stmt.where(BillingOrder.yard_id == yard_id)
    return [order_payload(row) for row in db.scalars(stmt)]


def cancel_order(db: Session, *, yard_id: str, order_id: str, user_id: str) -> dict:
    order = db.get(BillingOrder, order_id)
    if order is None or order.yard_id != yard_id:
        raise AppError("BILLING_ORDER_NOT_FOUND", "订单不存在。", http_status=404)
    # CAS 抢占 pending→cancelled：与确认到账互斥，并发只有一个赢家（审查 B2）
    claimed = db.execute(
        update(BillingOrder)
        .where(
            BillingOrder.id == order_id,
            BillingOrder.yard_id == yard_id,
            BillingOrder.status == ORDER_PENDING,
        )
        .values(status=ORDER_CANCELLED, decided_by=user_id, decided_at=_now())
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        raise AppError("BILLING_ORDER_CLOSED", "仅待确认订单可以取消。", http_status=409)
    db.commit()
    return order_payload(order)


# ---------------------------------------------------------------------------
# 平台侧：确认到账 / 作废兑换码
# ---------------------------------------------------------------------------


def confirm_order(db: Session, *, order_id: str, actor_id: str, note: str) -> dict:
    order = db.get(BillingOrder, order_id)
    if order is None:
        raise AppError("BILLING_ORDER_NOT_FOUND", "订单不存在。", http_status=404)
    plan = _plan_of(db, order.plan_code)
    if plan is None:
        raise AppError("BILLING_PLAN_NOT_FOUND", "订单对应的套餐不存在。", http_status=404)
    _mark_paid_and_grant(db, order, plan=plan, actor_id=actor_id, note=note)
    log_audit(
        db,
        yard_id=order.yard_id,
        actor_id=actor_id,
        action="billing_order_confirmed",
        resource_type="billing_order",
        resource_id=order.id,
        result="success",
        request_id="",
        metadata={"plan": plan.code},
    )
    return order_payload(order)


def disable_code(db: Session, *, code_id: str, actor_id: str) -> dict:
    row = db.get(BillingCode, code_id)
    if row is None:
        raise AppError("BILLING_CODE_INVALID", "兑换码不存在。", http_status=404)
    if row.status == CODE_REDEEMED:
        raise AppError("BILLING_CODE_USED", "已兑换的码不能作废。", http_status=409)
    # CAS 作废：仅当码仍未兑换时置 disabled，与并发兑换互斥，绝不覆盖已兑换
    # 状态（审查 B2）；unused/expired/disabled 均可作废，保留原幂等语义。
    claimed = db.execute(
        update(BillingCode)
        .where(BillingCode.id == code_id, BillingCode.status != CODE_REDEEMED)
        .values(status=CODE_DISABLED)
    )
    if claimed.rowcount != 1:  # type: ignore[attr-defined]
        db.rollback()
        raise AppError("BILLING_CODE_USED", "已兑换的码不能作废。", http_status=409)
    db.commit()
    return code_payload(row)


# ---------------------------------------------------------------------------
# 自助付费闭环（v3.79，审查 D-17）
# ---------------------------------------------------------------------------


def _get_order(db: Session, *, order_id: str, yard_id: str | None = None) -> BillingOrder:
    order = db.get(BillingOrder, order_id)
    if order is None or (yard_id is not None and order.yard_id != yard_id):
        raise AppError("BILLING_ORDER_NOT_FOUND", "订单不存在或无权访问。", http_status=404)
    return order


def create_payment_intent(
    db: Session,
    *,
    yard_id: str,
    order_id: str,
    user_id: str,
    base_url: str,
    provider_code: str | None = None,
) -> dict:
    """为待支付订单生成支付意图。

    这是"用户自己点一下就能付"的入口：不再依赖管理员发码/确认。
    渠道只允许用**部署配置**的那一个（2026-11-13 二次深审收紧）：用户传
    ``provider`` 时必须与 ``BILLING_PAYMENT_PROVIDER`` 一致——否则会出现
    "manual 部署里造出 sandbox 意图"这类收银台死路，且 ``order.payment_provider``
    会被污染成永远不会被核销的渠道。
    """
    order = _get_order(db, order_id=order_id, yard_id=yard_id)
    if order.status != ORDER_PENDING:
        raise AppError("BILLING_ORDER_CLOSED", "该订单当前不可支付。", http_status=409)
    configured = (settings.billing_payment_provider or PROVIDER_MANUAL).strip().lower()
    requested = (provider_code or "").strip().lower()
    if requested and requested != configured:
        provider = get_payment_provider(configured)
        raise AppError(
            "BILLING_PAYMENT_PROVIDER_MISMATCH",
            f"当前部署仅支持「{provider.label}」渠道，请勿选择其他支付方式。",
            http_status=400,
        )
    plan = _plan_of(db, order.plan_code)
    if plan is None:
        raise AppError("BILLING_PLAN_NOT_FOUND", "订单对应的套餐不存在。", http_status=404)
    provider = get_payment_provider(provider_code)
    root = base_url.rstrip("/")
    intent = provider.create_intent(
        order_id=order.id,
        order_code=order_code_of(order),
        plan_code=plan.code,
        plan_name=plan.name,
        amount_yuan=order.price_yuan,
        notify_url=f"{root}/api/v1/billing/payments/{provider.code}/notify",
        return_url=root,
    )
    order.payment_provider = provider.code
    db.commit()
    log_audit(
        db,
        yard_id=order.yard_id,
        actor_id=user_id,
        action="billing_payment_intent_created",
        resource_type="billing_order",
        resource_id=order.id,
        result="success",
        request_id="",
        metadata={"plan": plan.code, "provider": provider.code, "amount": order.price_yuan},
    )
    return intent.to_payload()


def settle_payment_event(
    db: Session, *, event: PaymentEvent, provider_code: str, actor_id: str | None = None
) -> dict:
    """渠道回调到账：验签已在渠道层完成，这里只做幂等 + 金额校验 + 发放额度。

    - 同一 ``trade_no`` 重复回调 → 幂等返回当前订单状态，不重复发额度。
    - 金额与订单不一致 → 拒绝入账（保留订单待人工处理）。
    - 发放额度复用 :func:`_mark_paid_and_grant` 的 CAS，双闸保证不重复。
    """
    order = _get_order(db, order_id=event.order_id)
    if order.payment_trade_no and order.payment_trade_no == event.trade_no:
        # 同一笔渠道支付的重复回调：直接幂等返回，不重复入账
        return order_payload(order)
    if order.status != ORDER_PENDING:
        raise AppError("BILLING_ORDER_CLOSED", "该订单已处理，无需重复支付。", http_status=409)
    if abs(float(order.price_yuan) - float(event.amount_yuan)) > AMOUNT_EPSILON:
        raise AppError(
            "BILLING_PAYMENT_AMOUNT",
            f"支付金额 {event.amount_yuan} 与订单金额 {order.price_yuan} 不一致，已拒绝入账。",
            http_status=400,
        )
    if event.trade_no:
        dup = db.scalar(
            select(BillingOrder).where(
                BillingOrder.payment_trade_no == event.trade_no,
                BillingOrder.id != order.id,
            )
        )
        if dup is not None:
            raise AppError("BILLING_PAYMENT_DUPLICATE", "该支付流水已核销过。", http_status=409)
    plan = _plan_of(db, order.plan_code)
    if plan is None:
        raise AppError("BILLING_PLAN_NOT_FOUND", "订单对应的套餐不存在。", http_status=404)

    order.payment_provider = provider_code
    order.payment_trade_no = event.trade_no
    order.payment_paid_at = event.paid_at
    _mark_paid_and_grant(
        db,
        order,
        plan=plan,
        actor_id=actor_id or order.requester_id,
        note=f"{provider_code} 支付回调自动到账（流水 {event.trade_no}）",
    )
    log_audit(
        db,
        yard_id=order.yard_id,
        actor_id=actor_id,
        action="billing_payment_settled",
        resource_type="billing_order",
        resource_id=order.id,
        result="success",
        request_id="",
        metadata={"provider": provider_code, "trade_no": event.trade_no, "plan": plan.code},
    )
    return order_payload(order)


def submit_voucher(
    db: Session,
    *,
    yard_id: str,
    order_id: str,
    user_id: str,
    voucher_no: str,
    amount_yuan: float | None = None,
) -> dict:
    """对公转账回单自助提交：登记回单 + 金额预检，**到账由平台核验后确认**。

    安全边界（2026-11-13 二次深审修正）：银行回单的真实性只有平台对账才能
    核验——此前「用户自报金额匹配即自动到账」等于荣誉制，伪造任意回单号
    即可零成本拿任意套餐。现在本函数只做三件事：登记回单号（防重号）、
    金额预检（给运营的核验提示）、落待核验状态。订单保持 ``pending``，
    由平台管理员走既有的「确认到账」（``confirm_order``）入账。自助闭环
    仍然成立（用户不必先找运营要码），只是把「钱是否真到账」的判定权
    还给平台——金额预检结果只是提示，不构成入账依据。
    """
    order = _get_order(db, order_id=order_id, yard_id=yard_id)
    if order.status != ORDER_PENDING:
        raise AppError("BILLING_ORDER_CLOSED", "该订单已处理。", http_status=409)
    normalized = (voucher_no or "").strip()
    if not normalized:
        raise AppError("BILLING_VOUCHER_REQUIRED", "请填写银行回单号。", http_status=400)
    dup = db.scalar(
        select(BillingOrder).where(
            BillingOrder.voucher_no == normalized,
            BillingOrder.id != order.id,
        )
    )
    if dup is not None:
        raise AppError("BILLING_VOUCHER_DUPLICATE", "该回单号已用于其他订单。", http_status=409)

    amount = float(order.price_yuan) if amount_yuan is None else float(amount_yuan)
    amount_matched = abs(amount - float(order.price_yuan)) <= AMOUNT_EPSILON
    order.voucher_no = normalized
    order.voucher_note = (order.voucher_note or "") or f"回单 {normalized}"
    # submitted=金额预检一致，待平台核验真伪；manual=金额不一致，需人工核对
    order.voucher_status = VOUCHER_SUBMITTED if amount_matched else VOUCHER_MANUAL
    db.commit()
    log_audit(
        db,
        yard_id=order.yard_id,
        actor_id=user_id,
        action="billing_voucher_submitted",
        resource_type="billing_order",
        resource_id=order.id,
        result="success",
        request_id="",
        metadata={
            "voucher_no": normalized,
            "amount": amount,
            "amount_matched": amount_matched,
        },
    )
    message = (
        "回单已提交，平台核对通过后额度自动到账。"
        if amount_matched
        else f"回单金额 {amount} 与订单金额 {order.price_yuan} 不一致，已标注转平台人工核验。"
    )
    return {
        "submitted": True,
        "amount_matched": amount_matched,
        "order": order_payload(order),
        "message": message,
    }
