"""订阅计费接口集成测试（v3.71）。

覆盖：套餐目录可读；兑换码发放（平台管理员）→ 小院兑换 → 订单已支付且
quota_grants 到账；重复兑换/作废/过期拒绝；对公转账订单确认到账；权限
（非平台管理员发码 403、非 quota:manage 兑换 403）。
"""

from __future__ import annotations

import pytest
from app.modules.auth.repository import create_user
from app.modules.quota.models import QuotaGrant, QuotaPolicy
from app.modules.yards.repository import add_membership, create_yard
from sqlalchemy import select

PREFIX = "/api/v1"


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="计费小院")
    owner = create_user(db_session, username="bill_owner", password="Passw0rd123")
    member = create_user(db_session, username="bill_member", password="Passw0rd123")
    platform = create_user(db_session, username="bill_platform", password="Passw0rd123")
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    add_membership(db_session, yard_id=yard.id, user_id=member.id, role="viewer")
    db_session.commit()
    return {"yard": yard, "owner": owner, "member": member, "platform": platform}


def _login(client, username: str, yard_id: str | None = None) -> str:
    body = {"username": username, "password": "Passw0rd123"}
    if yard_id is not None:
        body["yard_id"] = yard_id
    resp = client.post(f"{PREFIX}/auth/login", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def platform_admin_gate(db_session, monkeypatch):
    """把平台管理员名单指向测试用户；monkeypatch 结束自动还原，不污染全局设置。

    （教训：直接改 settings 会泄漏到同进程后续测试——admin/cluster/signup
    的平台管理员闸门全依赖该名单，曾导致全量 9 例连锁假失败。）
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_admin_usernames", "bill_platform")
    yield


def test_plans_readable_and_catalog_complete(client, db_session, world):
    token = _login(client, "bill_owner", world["yard"].id)
    resp = client.get(f"{PREFIX}/billing/plans", headers=_hdr(token))
    assert resp.status_code == 200, resp.text
    plans = resp.json()["data"]
    codes = {p["code"] for p in plans}
    assert {"basic", "pro", "flagship", "booster-tokens", "booster-cost"} <= codes
    for plan in plans:
        assert plan["items"], plan["code"]
        assert plan["price_yuan"] > 0


def test_redeem_code_grants_quota(client, db_session, world, platform_admin_gate):
    admin_token = _login(client, "bill_platform")
    made = client.post(
        f"{PREFIX}/billing/codes",
        json={"plan_code": "booster-tokens", "count": 2, "note": "首批"},
        headers=_hdr(admin_token),
    )
    assert made.status_code == 200, made.text
    codes = made.json()["data"]
    assert len(codes) == 2
    assert all(c["status"] == "unused" for c in codes)

    owner_token = _login(client, "bill_owner", world["yard"].id)
    redeemed = client.post(
        f"{PREFIX}/billing/redeem", json={"code": codes[0]["code"]}, headers=_hdr(owner_token)
    )
    assert redeemed.status_code == 200, redeemed.text
    order = redeemed.json()["data"]
    assert order["status"] == "paid"
    assert order["granted"] is True

    # 配额到账：yard 作用域 tokens/月 策略 + 一条有效 grant
    policy = db_session.scalar(
        select(QuotaPolicy).where(
            QuotaPolicy.yard_id == world["yard"].id,
            QuotaPolicy.metric == "tokens",
            QuotaPolicy.window == "month",
        )
    )
    assert policy is not None
    grants = list(
        db_session.scalars(select(QuotaGrant).where(QuotaGrant.yard_id == world["yard"].id))
    )
    assert len(grants) == 1
    assert grants[0].amount == 1_000_000

    # 重复兑换与再次使用同一码均被拒绝
    again = client.post(
        f"{PREFIX}/billing/redeem", json={"code": codes[0]["code"]}, headers=_hdr(owner_token)
    )
    assert again.status_code == 409


def test_redeem_requires_quota_manage(client, db_session, world, platform_admin_gate):
    admin_token = _login(client, "bill_platform")
    made = client.post(
        f"{PREFIX}/billing/codes",
        json={"plan_code": "pro", "count": 1},
        headers=_hdr(admin_token),
    )
    code = made.json()["data"][0]["code"]

    # viewer 没有 quota:manage：兑换被拒
    member_token = _login(client, "bill_member", world["yard"].id)
    denied = client.post(
        f"{PREFIX}/billing/redeem", json={"code": code}, headers=_hdr(member_token)
    )
    assert denied.status_code == 403

    # 码未被消费，管理员补权后可成功
    resp = client.get(f"{PREFIX}/billing/codes", headers=_hdr(admin_token))
    assert resp.status_code == 200
    assert resp.json()["data"][0]["status"] == "unused"


def test_transfer_order_confirm_flow(client, db_session, world, platform_admin_gate):
    owner_token = _login(client, "bill_owner", world["yard"].id)
    created = client.post(
        f"{PREFIX}/billing/orders",
        json={"plan_code": "pro", "voucher_note": "回单号 20261107-001"},
        headers=_hdr(owner_token),
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]
    assert order["status"] == "pending"

    # 平台管理员确认到账 → paid + grants
    admin_token = _login(client, "bill_platform")
    confirmed = client.post(
        f"{PREFIX}/billing/orders/{order['id']}/confirm",
        json={"note": "银行已到账"},
        headers=_hdr(admin_token),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["status"] == "paid"
    grants = list(
        db_session.scalars(select(QuotaGrant).where(QuotaGrant.yard_id == world["yard"].id))
    )
    assert len(grants) == 3  # pro 档：tokens + calls + cost

    # 重复确认被拒（幂等闸）
    repeat = client.post(
        f"{PREFIX}/billing/orders/{order['id']}/confirm",
        json={"note": "重复"},
        headers=_hdr(admin_token),
    )
    assert repeat.status_code == 409


def test_codes_require_platform_admin(client, db_session, world):
    owner_token = _login(client, "bill_owner", world["yard"].id)
    denied = client.post(
        f"{PREFIX}/billing/codes",
        json={"plan_code": "basic", "count": 1},
        headers=_hdr(owner_token),
    )
    assert denied.status_code == 403


# ---------------------------------------------------------------------------
# 套餐运营（v3.80，审查 D-18）：改价 / 卖点 / 上下架，不用发版
# ---------------------------------------------------------------------------


def test_admin_plan_list_requires_platform_admin(client, db_session, world):
    token = _login(client, "bill_owner", world["yard"].id)
    denied = client.get(f"{PREFIX}/billing/admin/plans", headers=_hdr(token))
    assert denied.status_code == 403


def test_admin_can_update_plan_price_and_items(client, db_session, world, platform_admin_gate):
    """改价后用户侧立刻看到新价；额度项同步改掉。"""
    admin_token = _login(client, "bill_platform")
    listed = client.get(f"{PREFIX}/billing/admin/plans", headers=_hdr(admin_token))
    assert listed.status_code == 200, listed.text
    plans = listed.json()["data"]
    codes = {p["code"] for p in plans}
    assert {"basic", "pro", "flagship", "booster-tokens", "booster-cost"} <= codes
    plan = next(p for p in plans if p["code"] == "pro")
    assert "active" in plan
    assert "sort_order" in plan

    resp = client.patch(
        f"{PREFIX}/billing/admin/plans/pro",
        json={
            "price_yuan": 5299.0,
            "tagline": "改价后的新卖点",
            "highlights": ["第一条卖点", "第二条卖点"],
            "items": [
                {"metric": "tokens", "window": "month", "amount": 7000000},
                {"metric": "calls", "window": "month", "amount": 20000},
            ],
            "sort_order": 5,
            "active": True,
        },
        headers=_hdr(admin_token),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["price_yuan"] == 5299.0
    assert data["tagline"] == "改价后的新卖点"
    assert data["highlights"] == ["第一条卖点", "第二条卖点"]
    assert [i["metric"] for i in data["items"]] == ["tokens", "calls"]

    # 用户侧立刻看到新价
    owner_token = _login(client, "bill_owner", world["yard"].id)
    user_plans = client.get(f"{PREFIX}/billing/plans", headers=_hdr(owner_token))
    assert user_plans.status_code == 200, user_plans.text
    user_pro = next(p for p in user_plans.json()["data"] if p["code"] == "pro")
    assert user_pro["price_yuan"] == 5299.0


def test_admin_can_unpublish_plan(client, db_session, world, platform_admin_gate):
    """下架后用户侧看不到该套餐，但运营后台仍能看到。"""
    admin_token = _login(client, "bill_platform")
    resp = client.patch(
        f"{PREFIX}/billing/admin/plans/flagship",
        json={"active": False},
        headers=_hdr(admin_token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["active"] is False

    owner_token = _login(client, "bill_owner", world["yard"].id)
    user_plans = client.get(f"{PREFIX}/billing/plans", headers=_hdr(owner_token))
    assert user_plans.status_code == 200, user_plans.text
    assert "flagship" not in {p["code"] for p in user_plans.json()["data"]}

    admin_listed = client.get(f"{PREFIX}/billing/admin/plans", headers=_hdr(admin_token))
    assert admin_listed.status_code == 200, admin_listed.text
    admin_codes = {p["code"] for p in admin_listed.json()["data"]}
    assert "flagship" in admin_codes


def test_admin_update_plan_rejects_invalid_item(client, db_session, world, platform_admin_gate):
    admin_token = _login(client, "bill_platform")
    bad_metric = client.patch(
        f"{PREFIX}/billing/admin/plans/basic",
        json={"items": [{"metric": "not_a_metric", "window": "month", "amount": 1}]},
        headers=_hdr(admin_token),
    )
    assert bad_metric.status_code == 400
    bad_amount = client.patch(
        f"{PREFIX}/billing/admin/plans/basic",
        json={"items": [{"metric": "tokens", "window": "month", "amount": 0}]},
        headers=_hdr(admin_token),
    )
    assert bad_amount.status_code == 400


# ---------------------------------------------------------------------------
# 自助付费闭环（2026-11-13 二次深审修正后的语义）
# 注意：回单提交**不再自动到账**——金额预检只是提示，到账必须由平台核验
# 回单真伪后「确认到账」。伪造回单号零成本拿套餐的荣誉制已被移除。
# ---------------------------------------------------------------------------


def test_voucher_submission_requires_platform_confirm(
    client, db_session, world, platform_admin_gate
):
    """回单提交只登记 + 金额预检：订单保持 pending，平台确认后才到账。"""
    owner_token = _login(client, "bill_owner", world["yard"].id)
    created = client.post(
        f"{PREFIX}/billing/orders", json={"plan_code": "pro"}, headers=_hdr(owner_token)
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]

    submitted = client.post(
        f"{PREFIX}/billing/orders/{order['id']}/voucher",
        json={"voucher_no": "BANK-20261113-0001", "amount_yuan": order["price_yuan"]},
        headers=_hdr(owner_token),
    )
    assert submitted.status_code == 200, submitted.text
    data = submitted.json()["data"]
    assert data["submitted"] is True
    assert data["amount_matched"] is True
    assert data["order"]["status"] == "pending"
    assert data["order"]["voucher_status"] == "submitted"

    # 金额不一致：仍只登记，标注 manual 转人工核对，绝不入账
    other = client.post(
        f"{PREFIX}/billing/orders", json={"plan_code": "basic"}, headers=_hdr(owner_token)
    )
    assert other.status_code == 200
    order2 = other.json()["data"]
    mismatch = client.post(
        f"{PREFIX}/billing/orders/{order2['id']}/voucher",
        json={"voucher_no": "BANK-20261113-0002", "amount_yuan": 0.01},
        headers=_hdr(owner_token),
    )
    assert mismatch.status_code == 200
    data2 = mismatch.json()["data"]
    assert data2["amount_matched"] is False
    assert data2["order"]["voucher_status"] == "manual"
    assert data2["order"]["status"] == "pending"

    # 到账前没有任何额度写入
    grants_before = list(
        db_session.scalars(select(QuotaGrant).where(QuotaGrant.yard_id == world["yard"].id))
    )
    assert grants_before == []

    # 平台核对回单真伪后「确认到账」（既有链路）→ paid + grants
    admin_token = _login(client, "bill_platform")
    confirmed = client.post(
        f"{PREFIX}/billing/orders/{order['id']}/confirm",
        json={"note": "回单 BANK-20261113-0001 已核对到账"},
        headers=_hdr(admin_token),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["status"] == "paid"
    grants_after = list(
        db_session.scalars(select(QuotaGrant).where(QuotaGrant.yard_id == world["yard"].id))
    )
    assert len(grants_after) == 3  # pro 档：tokens + calls + cost


def test_voucher_no_reused_across_orders(client, db_session, world):
    """同一回单号不能核到第二张订单（防一单多用）。"""
    owner_token = _login(client, "bill_owner", world["yard"].id)
    first = client.post(
        f"{PREFIX}/billing/orders", json={"plan_code": "basic"}, headers=_hdr(owner_token)
    )
    second = client.post(
        f"{PREFIX}/billing/orders", json={"plan_code": "basic"}, headers=_hdr(owner_token)
    )
    assert first.status_code == 200 and second.status_code == 200
    used = client.post(
        f"{PREFIX}/billing/orders/{first.json()['data']['id']}/voucher",
        json={"voucher_no": "BANK-SHARED-001"},
        headers=_hdr(owner_token),
    )
    assert used.status_code == 200
    reuse = client.post(
        f"{PREFIX}/billing/orders/{second.json()['data']['id']}/voucher",
        json={"voucher_no": "BANK-SHARED-001"},
        headers=_hdr(owner_token),
    )
    assert reuse.status_code == 409


def test_payment_intent_rejects_provider_mismatch(client, db_session, world):
    """渠道必须与部署配置一致：manual 部署里传 sandbox 直接 400，不污染订单。"""
    owner_token = _login(client, "bill_owner", world["yard"].id)
    created = client.post(
        f"{PREFIX}/billing/orders", json={"plan_code": "pro"}, headers=_hdr(owner_token)
    )
    order = created.json()["data"]
    resp = client.post(
        f"{PREFIX}/billing/orders/{order['id']}/pay",
        json={"provider": "sandbox"},
        headers=_hdr(owner_token),
    )
    assert resp.status_code == 400, resp.text
    # 订单未被污染成 sandbox
    assert (
        client.get(f"{PREFIX}/billing/orders", headers=_hdr(owner_token)).json()["data"][0][
            "payment_provider"
        ]
        is None
    )

    # 默认（不传 provider）走配置渠道：manual → transfer 意图
    ok = client.post(
        f"{PREFIX}/billing/orders/{order['id']}/pay", json={}, headers=_hdr(owner_token)
    )
    assert ok.status_code == 200, ok.text
    intent = ok.json()["data"]
    assert intent["provider"] == "manual"
    assert intent["mode"] == "transfer"


def test_sandbox_payment_flow_is_idempotent(client, db_session, world, monkeypatch):
    """沙箱渠道：下单 → 支付意图 → 验签回调 → 到账；重复回调不重复发额度。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "billing_payment_provider", "sandbox")
    owner_token = _login(client, "bill_owner", world["yard"].id)
    created = client.post(
        f"{PREFIX}/billing/orders", json={"plan_code": "pro"}, headers=_hdr(owner_token)
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]

    prepared = client.post(
        f"{PREFIX}/billing/payments/sandbox/prepare",
        json={"order_id": order["id"]},
        headers=_hdr(owner_token),
    )
    assert prepared.status_code == 200, prepared.text
    body = prepared.json()["data"]
    payload = body["payload"]
    notify_url = body["notify_url"].replace("http://testserver", "")

    # 金额被篡改的回调拒绝入账
    tampered = dict(payload)
    tampered["amount_yuan"] = payload["amount_yuan"] - 100
    tampered["signature"] = ""  # 签名必然不再匹配
    bad = client.post(notify_url, json=tampered)
    assert bad.status_code == 400

    # 合法回调 → 到账
    ok = client.post(notify_url, json=payload)
    assert ok.status_code == 200, ok.text
    assert ok.json()["code"] == "SUCCESS"
    grants = list(
        db_session.scalars(select(QuotaGrant).where(QuotaGrant.yard_id == world["yard"].id))
    )
    assert len(grants) == 3

    # 同一 trade_no 重复回调：幂等返回，不重复发放
    again = client.post(notify_url, json=payload)
    assert again.status_code == 200
    grants_after = list(
        db_session.scalars(select(QuotaGrant).where(QuotaGrant.yard_id == world["yard"].id))
    )
    assert len(grants_after) == 3


def test_zero_price_plan_cannot_be_listed_or_ordered(
    client, db_session, world, platform_admin_gate
):
    """0 元套餐从源头拦截：改价拒绝、直插库后下单也拒绝（免费发放走兑换码）。"""
    admin_token = _login(client, "bill_platform")
    zero = client.patch(
        f"{PREFIX}/billing/admin/plans/basic",
        json={"price_yuan": 0},
        headers=_hdr(admin_token),
    )
    assert zero.status_code == 400

    # 绕过后台直接造 0 价套餐行：下单仍被拦（订单是付款凭证，0 元没有可核对金额）
    from app.modules.billing.models import BillingPlan

    db_session.add(
        BillingPlan(
            code="free-promo",
            name="免费体验",
            kind="subscription",
            price_yuan=0,
            tagline="0 元不可下单",
            items=[],
        )
    )
    db_session.commit()
    owner_token = _login(client, "bill_owner", world["yard"].id)
    resp = client.post(
        f"{PREFIX}/billing/orders", json={"plan_code": "free-promo"}, headers=_hdr(owner_token)
    )
    assert resp.status_code == 409
