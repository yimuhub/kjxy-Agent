"""B6：平台管理员实时权限源统一（持久标志 OR 部署名单）。

验收口径：初始化登录、刷新、发码、确认行为一致；撤权立即生效。
重点覆盖 v3.69 初始化向导超管（User.platform_admin=True，不在部署名单）：
- 登录/刷新都授予 platform_admin 角色（refresh 此前漏合并持久标志）；
- 发码/确认/scope=all 闸门认持久标志（require_platform_admin 此前只认名单）；
- 撤权（清标志）后旧 JWT 即时失效（数据库实时复核，不等令牌过期）；
- 部署名单路径不回归。

用 conftest 的 client/db_session（内存 SQLite + 全量路由），monkeypatch 名单
隔离，不污染全局设置（教训见 test_billing_api.platform_admin_gate）。
"""

from __future__ import annotations

import pytest
from app.core.security import decode_token
from app.modules.auth import service as auth_service
from app.modules.auth.repository import create_user
from app.modules.yards.repository import add_membership, create_yard

PREFIX = "/api/v1"
PASSWORD = "Passw0rd123"


@pytest.fixture()
def empty_admin_list(monkeypatch):
    """部署名单清空：超管身份只能来自持久标志，证明 flag 路径独立可用。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_admin_usernames", "")
    yield


@pytest.fixture()
def world(db_session):
    yard = create_yard(db_session, name="超管小院")
    owner = create_user(db_session, username="ord_owner", password=PASSWORD)
    persist = create_user(db_session, username="persist_admin", password=PASSWORD)
    persist.platform_admin = True  # 初始化向导超管：持久标志，不在部署名单
    yard.owner_user_id = owner.id
    add_membership(db_session, yard_id=yard.id, user_id=owner.id, role="yard_owner")
    db_session.commit()
    return {"yard": yard, "owner": owner, "persist": persist}


def _login(client, username: str, yard_id: str | None = None) -> dict:
    body = {"username": username, "password": PASSWORD}
    if yard_id is not None:
        body["yard_id"] = yard_id
    resp = client.post(f"{PREFIX}/auth/login", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_is_platform_admin_predicate(db_session, world, empty_admin_list):
    """唯一判定：持久标志 OR 部署名单；二者皆无则 False。"""
    from app.core.config import settings
    from app.modules.auth.service import is_platform_admin

    persist = world["persist"]
    assert is_platform_admin(persist) is True  # 仅凭标志，名单为空

    ordinary = create_user(db_session, username="ordinary", password=PASSWORD)
    db_session.commit()
    assert is_platform_admin(ordinary) is False

    # 名单来源（标志为 False）同样判真
    settings.platform_admin_usernames = "ordinary"
    try:
        assert is_platform_admin(ordinary) is True
    finally:
        settings.platform_admin_usernames = ""


def test_persistent_flag_admin_login_grants_role(client, world, empty_admin_list):
    data = _login(client, "persist_admin")
    payload = decode_token(data["access_token"])
    assert "platform_admin" in payload["roles"]


def test_persistent_flag_admin_can_create_codes(client, world, empty_admin_list):
    token = _login(client, "persist_admin")["access_token"]
    made = client.post(
        f"{PREFIX}/billing/codes",
        json={"plan_code": "basic", "count": 1},
        headers=_hdr(token),
    )
    assert made.status_code == 200, made.text
    assert len(made.json()["data"]) == 1


def test_persistent_flag_admin_keeps_role_after_refresh(
    client, db_session, world, empty_admin_list
):
    """刷新此前漏合并持久标志 → 超管降权；修复后刷新仍带 platform_admin 且能发码。"""
    data = _login(client, "persist_admin")
    refreshed = auth_service.refresh(db_session, data["refresh_token"], request_id="req_b6")
    payload = decode_token(refreshed.access_token)
    assert "platform_admin" in payload["roles"]

    made = client.post(
        f"{PREFIX}/billing/codes",
        json={"plan_code": "basic", "count": 1},
        headers=_hdr(refreshed.access_token),
    )
    assert made.status_code == 200, made.text


def test_persistent_flag_admin_can_confirm_order(client, db_session, world, empty_admin_list):
    owner_token = _login(client, "ord_owner", world["yard"].id)["access_token"]
    created = client.post(
        f"{PREFIX}/billing/orders",
        json={"plan_code": "pro", "voucher_note": "回单 B6-001"},
        headers=_hdr(owner_token),
    )
    assert created.status_code == 200, created.text
    order = created.json()["data"]
    assert order["status"] == "pending"

    admin_token = _login(client, "persist_admin")["access_token"]
    confirmed = client.post(
        f"{PREFIX}/billing/orders/{order['id']}/confirm",
        json={"note": "已到账"},
        headers=_hdr(admin_token),
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["data"]["status"] == "paid"


def test_revocation_immediate_for_codes(client, db_session, world, empty_admin_list):
    """撤权（清持久标志）后，旧 JWT 仍声明 platform_admin，但发码即时 403。"""
    token = _login(client, "persist_admin")["access_token"]
    assert "platform_admin" in decode_token(token)["roles"]
    ok = client.post(
        f"{PREFIX}/billing/codes",
        json={"plan_code": "basic", "count": 1},
        headers=_hdr(token),
    )
    assert ok.status_code == 200, ok.text

    world["persist"].platform_admin = False
    db_session.commit()

    denied = client.post(
        f"{PREFIX}/billing/codes",
        json={"plan_code": "basic", "count": 1},
        headers=_hdr(token),
    )
    assert denied.status_code == 403, denied.text


def test_scope_all_realtime_recheck_and_revocation(client, db_session, world, empty_admin_list):
    token = _login(client, "persist_admin")["access_token"]
    listed = client.get(f"{PREFIX}/billing/orders?scope=all", headers=_hdr(token))
    assert listed.status_code == 200, listed.text

    world["persist"].platform_admin = False
    db_session.commit()

    denied = client.get(f"{PREFIX}/billing/orders?scope=all", headers=_hdr(token))
    assert denied.status_code == 403, denied.text


def test_deployment_list_admin_still_works(client, db_session, world, monkeypatch):
    """回归：部署名单超管（无持久标志）发码仍 200，名单路径未被破坏。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "platform_admin_usernames", "list_admin")
    create_user(db_session, username="list_admin", password=PASSWORD)
    db_session.commit()

    token = _login(client, "list_admin")["access_token"]
    assert "platform_admin" in decode_token(token)["roles"]
    made = client.post(
        f"{PREFIX}/billing/codes",
        json={"plan_code": "basic", "count": 1},
        headers=_hdr(token),
    )
    assert made.status_code == 200, made.text


def test_non_admin_cannot_create_codes(client, world, empty_admin_list):
    token = _login(client, "ord_owner", world["yard"].id)["access_token"]
    denied = client.post(
        f"{PREFIX}/billing/codes",
        json={"plan_code": "basic", "count": 1},
        headers=_hdr(token),
    )
    assert denied.status_code == 403, denied.text
