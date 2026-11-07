"""计费并发幂等（审查 B2）：CAS 状态迁移 + code_id 一对一唯一约束。

多线程多 Session 经文件型 SQLite（WAL + busy_timeout）真并发，断言「仅一个
合法终态 + 一组额度」：
- 并发兑换同一码：仅一个赢家建单入账，其余 409，额度只一组；
- 并发确认同一订单：仅一个赢家入账，其余 409，额度只一组；
- 取消 vs 确认互斥：终态唯一（cancelled+0 额度 或 paid+一组额度），绝不并存；
- 作废 vs 兑换互斥：已兑换的码不被作废覆盖，终态唯一；
- code_id 唯一索引：同码第二张订单被数据库直接拒绝（服务层 CAS 之外的兜底）。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.core.errors import AppError
from app.db.base import Base
from app.modules.billing import service
from app.modules.billing.models import (
    CODE_DISABLED,
    CODE_REDEEMED,
    CODE_UNUSED,
    ORDER_CANCELLED,
    ORDER_PAID,
    ORDER_PENDING,
    BillingCode,
    BillingOrder,
)
from app.modules.quota.models import QuotaGrant
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

YARD = "yard-b2"
USER = "user-b2"
CODE = "KY-B2C0-DEAD-BEEF"


@pytest.fixture()
def maker(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'billing-concurrent.db'}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    yield factory
    engine.dispose()


def _seed_code(maker, *, plan_code="booster-tokens", status=CODE_UNUSED) -> str:
    db = maker()
    try:
        row = BillingCode(code=CODE, plan_code=plan_code, status=status, created_by=USER)
        db.add(row)
        db.commit()
        return row.id
    finally:
        db.close()


def _seed_order(maker, *, plan_code="pro", status=ORDER_PENDING, code_id=None) -> str:
    db = maker()
    try:
        order = BillingOrder(
            yard_id=YARD,
            plan_code=plan_code,
            status=status,
            price_yuan=4999.0,
            code_id=code_id,
            requester_id=USER,
        )
        db.add(order)
        db.commit()
        return order.id
    finally:
        db.close()


def _race(maker, fns):
    """每个线程独立 Session，Barrier 齐发；返回 (ok, 结果) 或 (app_error, code, status)。"""
    barrier = threading.Barrier(len(fns))

    def worker(fn):
        barrier.wait()
        db = maker()
        try:
            return ("ok", fn(db))
        except AppError as exc:
            return ("app_error", exc.code, exc.http_status)
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=len(fns)) as pool:
        futures = [pool.submit(worker, fn) for fn in fns]
        return [f.result() for f in futures]


def _grant_count(maker) -> int:
    db = maker()
    try:
        return db.scalar(
            select(func.count()).select_from(QuotaGrant).where(QuotaGrant.yard_id == YARD)
        )
    finally:
        db.close()


def test_concurrent_redeem_single_winner(maker):
    code_id = _seed_code(maker)
    redeem = lambda db: service.redeem(  # noqa: E731
        db, yard_id=YARD, code=CODE, user_id=USER, user_name=None
    )
    results = _race(maker, [redeem] * 8)

    oks = [r for r in results if r[0] == "ok"]
    errors = [r for r in results if r[0] == "app_error"]
    assert len(oks) == 1
    assert len(errors) == 7
    assert all(r[1] == "BILLING_CODE_USED" and r[2] == 409 for r in errors)

    db = maker()
    try:
        orders = list(db.scalars(select(BillingOrder).where(BillingOrder.code_id == code_id)))
        assert len(orders) == 1
        assert orders[0].status == ORDER_PAID
        assert orders[0].granted_at is not None
        code = db.get(BillingCode, code_id)
        assert code.status == CODE_REDEEMED
    finally:
        db.close()
    # booster-tokens 单项 → 恰好一组（一条）额度，绝不因并发翻倍
    assert _grant_count(maker) == 1


def test_concurrent_confirm_single_winner(maker):
    order_id = _seed_order(maker, plan_code="pro")
    confirm = lambda db: service.confirm_order(  # noqa: E731
        db, order_id=order_id, actor_id=USER, note="银行已到账"
    )
    results = _race(maker, [confirm] * 8)

    oks = [r for r in results if r[0] == "ok"]
    errors = [r for r in results if r[0] == "app_error"]
    assert len(oks) == 1
    assert len(errors) == 7
    assert all(r[1] == "BILLING_ORDER_CLOSED" and r[2] == 409 for r in errors)

    db = maker()
    try:
        order = db.get(BillingOrder, order_id)
        assert order.status == ORDER_PAID
        assert order.granted_at is not None
    finally:
        db.close()
    # pro 三项（tokens/calls/cost）→ 恰好一组（三条）额度
    assert _grant_count(maker) == 3


def test_cancel_races_confirm_single_terminal(maker):
    order_id = _seed_order(maker, plan_code="pro")
    cancel = lambda db: service.cancel_order(  # noqa: E731
        db, yard_id=YARD, order_id=order_id, user_id=USER
    )
    confirm = lambda db: service.confirm_order(  # noqa: E731
        db, order_id=order_id, actor_id=USER, note="到账"
    )
    results = _race(maker, [cancel] * 4 + [confirm] * 4)

    oks = [r for r in results if r[0] == "ok"]
    errors = [r for r in results if r[0] == "app_error"]
    assert len(oks) == 1
    assert len(errors) == 7
    assert all(r[1] == "BILLING_ORDER_CLOSED" and r[2] == 409 for r in errors)

    db = maker()
    try:
        order = db.get(BillingOrder, order_id)
        assert order.status in (ORDER_CANCELLED, ORDER_PAID)
        if order.status == ORDER_CANCELLED:
            assert order.granted_at is None
            assert _grant_count(maker) == 0
        else:
            assert order.granted_at is not None
            assert _grant_count(maker) == 3
    finally:
        db.close()


def test_disable_races_redeem_no_clobber(maker):
    code_id = _seed_code(maker)
    redeem = lambda db: service.redeem(  # noqa: E731
        db, yard_id=YARD, code=CODE, user_id=USER, user_name=None
    )
    disable = lambda db: service.disable_code(db, code_id=code_id, actor_id=USER)  # noqa: E731
    results = _race(maker, [redeem] * 4 + [disable] * 4)
    # _race 按输入顺序返回：前 4 个兑换、后 4 个作废
    redeem_results, disable_results = results[:4], results[4:]
    redeem_oks = [r for r in redeem_results if r[0] == "ok"]
    disable_oks = [r for r in disable_results if r[0] == "ok"]

    db = maker()
    try:
        code = db.get(BillingCode, code_id)
        orders = list(db.scalars(select(BillingOrder).where(BillingOrder.code_id == code_id)))
        if redeem_oks:
            # 兑换赢：恰好一个赢家，码已兑换，作废绝不可覆盖（全部 409）
            assert len(redeem_oks) == 1
            assert all(
                r[0] == "app_error" and r[1] == "BILLING_CODE_USED" and r[2] == 409
                for r in disable_results
            )
            assert code.status == CODE_REDEEMED
            assert len(orders) == 1
            assert orders[0].status == ORDER_PAID
            assert _grant_count(maker) == 1
        else:
            # 作废赢：兑换全败，码未兑换、无订单无额度；作废对已 disabled 幂等可多赢
            assert len(disable_oks) >= 1
            assert all(r[0] == "app_error" and r[2] == 409 for r in redeem_results)
            assert code.status == CODE_DISABLED
            assert len(orders) == 0
            assert _grant_count(maker) == 0
    finally:
        db.close()


def test_code_id_unique_index_rejects_second_order(maker):
    code_id = _seed_code(maker)
    _seed_order(maker, plan_code="booster-tokens", code_id=code_id)
    db = maker()
    try:
        db.add(
            BillingOrder(
                yard_id=YARD,
                plan_code="booster-tokens",
                status=ORDER_PENDING,
                price_yuan=299.0,
                code_id=code_id,
                requester_id=USER,
            )
        )
        with pytest.raises(IntegrityError):
            db.flush()
    finally:
        db.close()
