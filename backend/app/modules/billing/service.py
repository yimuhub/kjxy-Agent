"""订阅计费服务（v3.71）：套餐/兑换码/订单，到账统一写 quota_grants。

到账口径：每个套餐项找到「全小院 × 指标 × 窗口」的额度策略（没有就建，
on_exceed=approve 与配额页默认一致），追加一条带有效期的 QuotaGrant——
不动客户已配置的策略上限，超限语义不因购买改变。
"""

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

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
from app.modules.billing.plans import KIND_BOOSTER, PLANS, Plan, get_plan
from app.modules.quota.engine import _window_end
from app.modules.quota.models import QuotaGrant, QuotaPolicy

CODE_PREFIX = "KY-"
#: 兑换码默认 90 天内必须用掉
DEFAULT_CODE_TTL_DAYS = 90


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(moment: datetime | None) -> datetime | None:
    """SQLite 会把 tz-aware 读成 naive：比较前统一补 UTC（与 official_service 同口径）。"""
    if moment is not None and moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def _gen_code() -> str:
    body = secrets.token_hex(6).upper()
    return f"{CODE_PREFIX}{body[0:4]}-{body[4:8]}-{body[8:12]}"


def plan_payload(plan: Plan) -> dict:
    return {
        "code": plan.code,
        "name": plan.name,
        "kind": plan.kind,
        "price_yuan": plan.price_yuan,
        "tagline": plan.tagline,
        "items": [
            {"metric": item.metric, "window": item.window, "amount": item.amount, "note": item.note}
            for item in plan.items
        ],
    }


def list_plans() -> list[dict]:
    return [plan_payload(plan) for plan in PLANS]


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
    plan = get_plan(plan_code)
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
                expires_at=expires,
                created_by=actor_id,
            )
        )
    db.flush()
    return moment


def order_payload(row: BillingOrder) -> dict:
    return {
        "id": row.id,
        "yard_id": row.yard_id,
        "plan_code": row.plan_code,
        "status": row.status,
        "price_yuan": row.price_yuan,
        "voucher_note": row.voucher_note,
        "requester_name": row.requester_name,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
        "decision_note": row.decision_note,
        "granted": row.granted_at is not None,
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
    plan = get_plan(row.plan_code)
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
    plan = get_plan(plan_code)
    if plan is None:
        raise AppError("BILLING_PLAN_NOT_FOUND", "套餐不存在。", http_status=404)
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
    plan = get_plan(order.plan_code)
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
