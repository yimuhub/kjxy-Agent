"""跨小院隔离安全测试。

验证小院 A 的用户无法通过任何接口越权访问小院 B 的数据：
小院上下文完全来自 JWT（服务端签发），客户端无任何可提交的 yard_id 入口。
"""

import pytest
from app.core.deps import get_db_session
from app.main import create_app
from app.modules.audit.models import AuditLog  # noqa: F401  注册模型
from app.modules.auth import repository as auth_repo
from app.modules.auth import router as auth_router_module
from app.modules.auth.models import User  # noqa: F401  注册模型
from app.modules.yards import repository as yard_repo
from app.modules.yards import router as yards_router_module
from app.modules.yards.models import Yard, YardMembership  # noqa: F401  注册模型
from fastapi.testclient import TestClient

PREFIX = "/api/v1"


@pytest.fixture()
def api_client(db_session):
    """基于 conftest 的 db_session fixture，挂载 auth/yards 路由的测试应用。

    注：conftest 的 client fixture 未挂载业务路由（由集成阶段统一接入），
    此处为安全测试单独挂载；db_session 仍复用 conftest 的 sqlite 内存库。
    """
    app = create_app()
    app.include_router(auth_router_module.router, prefix=PREFIX)
    app.include_router(yards_router_module.router, prefix=PREFIX)

    def override_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def world(db_session):
    """小院 A：alice(yard_admin)；小院 B：bob(operator)。"""
    yard_a = yard_repo.create_yard(db_session, name="小院A")
    yard_b = yard_repo.create_yard(db_session, name="小院B")
    alice = auth_repo.create_user(db_session, username="alice", password="pw-alice")
    bob = auth_repo.create_user(db_session, username="bob", password="pw-bob")
    yard_repo.add_membership(db_session, yard_id=yard_a.id, user_id=alice.id, role="yard_admin")
    yard_repo.add_membership(db_session, yard_id=yard_b.id, user_id=bob.id, role="operator")
    db_session.commit()
    return {"a": yard_a, "b": yard_b, "alice": alice, "bob": bob}


def _login(api_client, username, password, yard_id=None):
    body = {"username": username, "password": password}
    if yard_id:
        body["yard_id"] = yard_id
    r = api_client.post(f"{PREFIX}/auth/login", json=body)
    assert r.status_code == 200, r.text
    return r.json()["data"]


def test_member_list_is_yard_isolated(api_client, world):
    """A 的成员列表里看不到 B 的任何成员。"""
    token_a = _login(api_client, "alice", "pw-alice")["access_token"]
    r = api_client.get(
        f"{PREFIX}/yards/current/members", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert r.status_code == 200, r.text
    usernames = {m["username"] for m in r.json()["data"]}
    assert usernames == {"alice"}
    assert "bob" not in usernames


def test_cannot_login_into_other_yard(api_client, world):
    """A 用户指定 B 的 yard_id 登录 → 403 YARD_FORBIDDEN。"""
    r = api_client.post(
        f"{PREFIX}/auth/login",
        json={"username": "alice", "password": "pw-alice", "yard_id": world["b"].id},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "YARD_FORBIDDEN"


def test_b_user_sees_only_b_members(api_client, world):
    token_b = _login(api_client, "bob", "pw-bob")["access_token"]
    r = api_client.get(
        f"{PREFIX}/yards/current/members", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert r.status_code == 200
    usernames = {m["username"] for m in r.json()["data"]}
    assert usernames == {"bob"}


def test_b_user_cannot_add_member_to_a(api_client, world, db_session):
    """B 的 operator 没有 members:manage 权限 → 403，且无法以任何方式影响 A。"""
    token_b = _login(api_client, "bob", "pw-bob")["access_token"]
    carol = auth_repo.create_user(db_session, username="carol", password="pw-carol")
    db_session.commit()
    r = api_client.post(
        f"{PREFIX}/yards/current/members",
        json={"username": "carol", "role": "viewer"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"
    # carol 未被加入任何小院
    assert yard_repo.get_memberships_by_user(db_session, carol.id) == []


def test_a_admin_adds_member_visible_only_in_a(api_client, world, db_session):
    """A 的 yard_admin 添加成员后，该成员只出现在 A 的成员列表中。"""
    token_a = _login(api_client, "alice", "pw-alice")["access_token"]
    auth_repo.create_user(db_session, username="dave", password="pw-dave")
    db_session.commit()
    r = api_client.post(
        f"{PREFIX}/yards/current/members",
        json={"username": "dave", "role": "viewer"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert r.status_code == 201, r.text

    r = api_client.get(
        f"{PREFIX}/yards/current/members", headers={"Authorization": f"Bearer {token_a}"}
    )
    assert {m["username"] for m in r.json()["data"]} == {"alice", "dave"}

    token_b = _login(api_client, "bob", "pw-bob")["access_token"]
    r = api_client.get(
        f"{PREFIX}/yards/current/members", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert {m["username"] for m in r.json()["data"]} == {"bob"}


def test_tampered_token_rejected(api_client, world):
    token_a = _login(api_client, "alice", "pw-alice")["access_token"]
    forged = token_a[:-4] + "abcd"
    r = api_client.get(
        f"{PREFIX}/yards/current/members", headers={"Authorization": f"Bearer {forged}"}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHORIZED"


def test_no_token_rejected(api_client, world):
    r = api_client.get(f"{PREFIX}/yards/current/members")
    assert r.status_code == 401


def test_cross_yard_refresh_token_rejected(api_client, world, db_session):
    """B 用户的 refresh 令牌即使被 A 拿到也解不出 A 的上下文；伪造 yard 的 refresh 被拒。"""
    from app.core.security import create_refresh_token

    forged_refresh = create_refresh_token(user_id=world["alice"].id, yard_id=world["b"].id)
    r = api_client.post(f"{PREFIX}/auth/refresh", json={"refresh_token": forged_refresh})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "YARD_FORBIDDEN"


def test_me_returns_roles_and_yards(api_client, world):
    token_a = _login(api_client, "alice", "pw-alice")["access_token"]
    r = api_client.get(f"{PREFIX}/auth/me", headers={"Authorization": f"Bearer {token_a}"})
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["user"]["username"] == "alice"
    assert data["roles"] == ["yard_admin"]
    assert {t["yard_name"] for t in data["yards"]} == {"小院A"}
