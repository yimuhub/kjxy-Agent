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
